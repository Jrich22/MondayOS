"""
The Content Library - a query layer over content that already exists.

This module stores nothing. Every item it returns was written by growth/store.py
and is read back from the same files, so there is exactly one copy of any piece
of content and no index that can drift out of date. The library is a set of
queries and a projection, not a second database.

Ranking degrades honestly. "Highest performing" needs performance data, and
performance events arrive in increment 5. Until then the ranking falls back to
recency and says so in the result, rather than inventing an order and letting a
caller mistake it for evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from growth.content import ContentItem, ContentStatus, ContentType
from growth.store import WorkspaceHandle

# What "recently" means for reuse queries when a caller does not say.
DEFAULT_REUSE_WINDOW_DAYS = 90


@dataclass
class LibraryEntry:
    """One content item as the library presents it."""

    content_id: str
    project: str
    campaign: str
    content_type: str
    platform: str
    title: str
    body: str
    cta: str
    destination_url: str
    media: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    audience: str = ""
    variant_group_id: str = ""
    status: str = ""
    approval_status: str = ""
    reuse_eligible: bool = False
    created_at: str = ""
    published_at: str = ""
    last_reused_at: str = ""
    performance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "content_id": self.content_id,
            "project": self.project,
            "campaign": self.campaign,
            "content_type": self.content_type,
            "platform": self.platform,
            "title": self.title,
            "body": self.body,
            "cta": self.cta,
            "destination_url": self.destination_url,
            "media": list(self.media),
            "tags": list(self.tags),
            "themes": list(self.themes),
            "audience": self.audience,
            "variant_group_id": self.variant_group_id,
            "status": self.status,
            "approval_status": self.approval_status,
            "reuse_eligible": self.reuse_eligible,
            "created_at": self.created_at,
            "published_at": self.published_at,
            "last_reused_at": self.last_reused_at,
            "performance": dict(self.performance),
        }


class ContentLibrary:
    """
    Search and projection over one workspace's content.

    Scoped to a single WorkspaceHandle, so a library can only ever see the
    project it was opened for (ADR-011).
    """

    def __init__(self, handle: WorkspaceHandle) -> None:
        self._handle = handle

    @property
    def project(self) -> str:
        return self._handle.slug

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def all(self) -> list[LibraryEntry]:
        """Every content item in this project, oldest id first."""
        return [self._project_item(i) for i in self._handle.list_content()]

    def search(
        self,
        text: str = "",
        theme: str = "",
        campaign: str = "",
        platform: str = "",
        content_type: ContentType | None = None,
        status: ContentStatus | None = None,
        tag: str = "",
        reusable_only: bool = False,
    ) -> list[LibraryEntry]:
        """
        Filter the library. Every argument narrows; none widens.

        ``text`` matches case-insensitively across title, body, CTA, tags and
        themes - enough to find a post someone half-remembers, without pretending
        to be a search engine.
        """
        items = self._handle.list_content(status)
        needle = text.strip().lower()

        results: list[LibraryEntry] = []
        for item in items:
            if campaign and item.campaign != campaign:
                continue
            if platform and item.platform != platform:
                continue
            if content_type is not None and item.content_type is not content_type:
                continue
            if theme and theme.lower() not in [t.lower() for t in item.themes]:
                continue
            if tag and tag.lower() not in [t.lower() for t in item.tags]:
                continue
            if reusable_only and not item.reuse_eligible:
                continue
            if needle and not self._matches_text(item, needle):
                continue
            results.append(self._project_item(item))
        return results

    def by_campaign(self, campaign_id: str) -> list[LibraryEntry]:
        """All content for one campaign."""
        return self.search(campaign=campaign_id)

    def by_theme(self, theme: str) -> list[LibraryEntry]:
        """All content carrying a theme."""
        return self.search(theme=theme)

    def by_platform(self, platform: str) -> list[LibraryEntry]:
        """All content targeting a platform."""
        return self.search(platform=platform)

    def variants(self, variant_group_id: str) -> list[LibraryEntry]:
        """Every per-platform variant of one idea."""
        if not variant_group_id:
            return []
        return [
            self._project_item(i)
            for i in self._handle.list_content()
            if i.variant_group_id == variant_group_id
        ]

    def reusable(self) -> list[LibraryEntry]:
        """Evergreen content flagged as eligible for reuse."""
        return self.search(reusable_only=True)

    def not_reused_since(
        self, days: int = DEFAULT_REUSE_WINDOW_DAYS, now: datetime | None = None
    ) -> list[LibraryEntry]:
        """
        Reusable content that has not been reused within ``days``.

        Never-reused content qualifies: the point of the query is to surface what
        is sitting unused, and something used once at launch and never again is
        the same opportunity as something never used at all.
        """
        current = now or datetime.now(tz=UTC)
        cutoff = current - timedelta(days=days)
        out: list[LibraryEntry] = []
        for item in self._handle.list_content():
            if not item.reuse_eligible:
                continue
            last = item.last_reused_at
            if last is None or _as_utc(last) < cutoff:
                out.append(self._project_item(item))
        return out

    def highest_performing(self, limit: int = 10) -> tuple[list[LibraryEntry], str]:
        """
        Best-performing content, with the basis for the ranking.

        Returns (entries, basis). ``basis`` is "performance" once metrics exist
        and "recency-fallback" until then. The basis is returned rather than
        hidden so a caller cannot mistake a recency list for an evidence-based
        one - which is exactly the kind of quiet fiction the Growth Brain must
        never be fed.
        """
        entries = [self._project_item(i) for i in self._handle.list_content()]
        scored = [e for e in entries if e.performance]
        if scored:
            scored.sort(key=lambda e: _score(e.performance), reverse=True)
            return scored[:limit], "performance"

        published = [e for e in entries if e.published_at]
        published.sort(key=lambda e: e.published_at, reverse=True)
        pool = published or sorted(entries, key=lambda e: e.created_at, reverse=True)
        return pool[:limit], "recency-fallback"

    def themes(self) -> dict[str, int]:
        """Theme -> item count, for spotting coverage gaps."""
        counts: dict[str, int] = {}
        for item in self._handle.list_content():
            for theme in item.themes:
                counts[theme] = counts.get(theme, 0) + 1
        return dict(sorted(counts.items()))

    def summary(self) -> dict[str, Any]:
        """Counts by type, platform and status for one project."""
        items = self._handle.list_content()
        by_type: dict[str, int] = {}
        by_platform: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for item in items:
            by_type[item.content_type.value] = by_type.get(item.content_type.value, 0) + 1
            if item.platform:
                by_platform[item.platform] = by_platform.get(item.platform, 0) + 1
            by_status[item.status.value] = by_status.get(item.status.value, 0) + 1
        return {
            "project": self.project,
            "total": len(items),
            "by_type": dict(sorted(by_type.items())),
            "by_platform": dict(sorted(by_platform.items())),
            "by_status": dict(sorted(by_status.items())),
            "themes": self.themes(),
            "reusable": sum(1 for i in items if i.reuse_eligible),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _matches_text(item: ContentItem, needle: str) -> bool:
        haystack = " ".join(
            [item.title, item.copy, item.cta, " ".join(item.tags), " ".join(item.themes)]
        ).lower()
        return needle in haystack

    @staticmethod
    def _project_item(item: ContentItem) -> LibraryEntry:
        """Project a stored item into a library entry. Reads, never writes."""
        record = item.publication
        return LibraryEntry(
            content_id=item.id,
            project=item.project,
            campaign=item.campaign,
            content_type=item.content_type.value,
            platform=item.platform,
            title=item.title,
            body=item.copy,
            cta=item.cta,
            destination_url=item.destination_url,
            media=list(item.media),
            tags=list(item.tags),
            themes=list(item.themes),
            audience=item.audience,
            variant_group_id=item.variant_group_id,
            status=item.status.value,
            approval_status=(
                "approved"
                if item.is_approved
                else ("stale" if item.approval_is_stale() else "unapproved")
            ),
            reuse_eligible=item.reuse_eligible,
            created_at=_fmt(item.created),
            published_at=(_fmt(record.published_at) if record and record.published_at else ""),
            last_reused_at=_fmt(item.last_reused_at) if item.last_reused_at else "",
            # Populated in increment 5. Empty here rather than zero-filled, so
            # "no data" never renders as "performed badly".
            performance={},
        )


def _score(performance: dict[str, Any]) -> float:
    """Rank key for a performance summary. Increment 5 supplies the inputs."""
    for key in ("conversions", "clicks", "engagement", "reach"):
        value = performance.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _fmt(value: datetime) -> str:
    return _as_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")
