"""
Shared state and helpers for the Growth service facades.

Every domain mixin inherits this, so the store, the project root and the
injected publishing/clock seams live in one place rather than being redeclared
per module. Split out per issue #35; behaviour is unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from growth.analytics import GrowthAnalytics
from growth.brain.engine import GrowthBrain
from growth.campaign import Campaign
from growth.content import ContentItem, ContentStatus
from growth.dispatch import PublishDispatcher
from growth.store import GrowthStore, WorkspaceHandle
from integrations.publishing.connector import PublishingConnector
from monday.project import ProjectRegistry


class GrowthServiceBase:
    """
    State and helpers every Growth domain facade needs.

    The attributes are declared rather than assigned: ``GrowthService.__init__``
    sets them once, and every mixin reads them from the composed instance.
    """

    _root: Path
    _store: GrowthStore
    _connector: PublishingConnector | None
    _now: Callable[[], datetime] | None
    _jitter: float

    def __init__(
        self,
        project_root: Path = Path("."),
        registry: ProjectRegistry | None = None,
        connector: PublishingConnector | None = None,
        now: Callable[[], datetime] | None = None,
        jitter: float = 0.0,
    ) -> None:
        self._root = Path(project_root)
        self._store = GrowthStore(self._root, registry=registry)
        # Injected for tests and the CLI smoke path; None resolves through the
        # connector factory, which returns the fake until real adapters exist.
        self._connector = connector
        self._now = now
        self._jitter = jitter

    def _dispatcher(self, project: str, actor: str = "human:cli") -> PublishDispatcher:
        """A dispatcher bound to exactly one workspace."""
        return PublishDispatcher(
            handle=self._store.open(project),
            project_root=self._root,
            connector=self._connector,
            now=self._now,
            jitter=self._jitter,
            actor=actor,
        )

    def _brain(self, project: str) -> GrowthBrain:
        """A Brain bound to exactly one workspace."""
        handle = self._store.open(project)
        return GrowthBrain(handle, self._root, GrowthAnalytics(handle, handle.event_store()))

    def _analytics(self, project: str) -> GrowthAnalytics:
        """Analytics bound to exactly one workspace."""
        handle = self._store.open(project)
        return GrowthAnalytics(handle, handle.event_store())

    @staticmethod
    def _describe_campaign(handle: WorkspaceHandle, campaign: Campaign) -> dict[str, Any]:
        """Campaign payload plus counts derived from its attached content."""
        payload = campaign.to_dict()
        items = [i for i in handle.list_content() if i.campaign == campaign.id]
        payload["content_count"] = len(items)
        payload["approved_count"] = sum(1 for i in items if i.is_approved)
        payload["published_count"] = sum(1 for i in items if i.status is ContentStatus.PUBLISHED)
        payload["accepts_content"] = campaign.accepts_content()
        return payload

    @staticmethod
    def _describe(item: ContentItem) -> dict[str, Any]:
        """Item payload plus the derived approval facts callers actually need."""
        payload = item.to_dict()
        payload["current_fingerprint"] = item.current_fingerprint()
        payload["is_approved"] = item.is_approved
        payload["approval_is_stale"] = item.approval_is_stale()
        payload["missing_required_fields"] = item.missing_required_fields()
        payload["publishable"] = item.is_approved and item.status in (
            ContentStatus.APPROVED,
            ContentStatus.SCHEDULED,
        )
        payload["publishable_reason"] = (
            "Approved and admissible for publishing."
            if payload["publishable"]
            else f"Not publishable from {item.status.value} with is_approved={item.is_approved}."
        )
        return payload

    @staticmethod
    def handle_for(project: str, root: Path) -> WorkspaceHandle:
        """Convenience for callers that want direct handle access."""
        return GrowthStore(root).open(project)


def _as_datetime(value: Any) -> datetime | None:
    """Coerce an ISO string or datetime to a UTC datetime; None passes through."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    parsed = datetime.fromisoformat(str(value).rstrip("Z"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
