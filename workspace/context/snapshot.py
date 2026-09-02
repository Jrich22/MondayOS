"""
The Context Snapshot — what MondayOS knew about a project at one moment.

A snapshot is deliberately not a blob of text. It is a list of attributed
sources, each recording where it came from, how much it contributed, and whether
it was cut short. That structure is what makes two questions answerable from
storage alone (ADR-016):

    "Why did Monday know this?"      -> the source that carried it
    "Why did Monday NOT know this?"  -> the source that was empty, failed, or
                                        was truncated before reaching it

A snapshot is immutable once built, and messages reference it by id. Explaining a
six-month-old answer means reading the context that actually produced it, not the
project as it looks today.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# Rough conversion from characters to tokens. Deliberately a constant rather than
# a tokenizer: an estimate that is stable and explainable beats one that is
# accurate for one vendor and wrong for the next, and nothing here is billed on
# it — it exists so a human can see whether a snapshot is 2k or 200k.
CHARS_PER_TOKEN = 4


@dataclass
class ContextSource:
    """
    One named contribution to a snapshot.

    ``error`` is the fail-closed record: an adapter that could not read its
    subsystem contributes an empty source that says why, so a thin snapshot is
    diagnosable rather than mysterious (ADR-017).
    """

    name: str
    label: str
    items: list[str] = field(default_factory=list)
    truncated: bool = False
    error: str = ""
    # Where this actually came from, for the "why did Monday know this" answer.
    origin: str = ""
    # Why each item survived selection, parallel to ``items``. Increment 1 could
    # answer "where did this come from"; ranking makes "why was this one chosen
    # over another" a separate question, and an unexplained ranking is exactly
    # the thing ADR-016 exists to prevent.
    reasons: list[str] = field(default_factory=list)

    def reason_for(self, index: int) -> str:
        """Why the item at ``index`` was included. Empty when unranked."""
        return self.reasons[index] if index < len(self.reasons) else ""

    def reason_counts(self) -> dict[str, int]:
        """How many items each reason contributed — the panel's summary line."""
        counts: dict[str, int] = {}
        for reason in self.reasons:
            counts[reason] = counts.get(reason, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def item_count(self) -> int:
        return len(self.items)

    @property
    def char_count(self) -> int:
        return sum(len(i) for i in self.items)

    @property
    def token_estimate(self) -> int:
        return self.char_count // CHARS_PER_TOKEN

    @property
    def ok(self) -> bool:
        """True when the adapter ran without error, even if it found nothing."""
        return not self.error

    def render(self) -> str:
        """The source as prompt text. Empty when there is nothing to say."""
        if not self.items:
            return ""
        lines = [f"## {self.label}"]
        for index, item in enumerate(self.items):
            reason = self.reason_for(index)
            lines.append(f"- {item}" + (f"  [{reason}]" if reason else ""))
        if self.truncated:
            lines.append("- (truncated: more exists that did not fit the context budget)")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "items": list(self.items),
            "item_count": self.item_count,
            "char_count": self.char_count,
            "token_estimate": self.token_estimate,
            "truncated": self.truncated,
            "error": self.error,
            "origin": self.origin,
            "ok": self.ok,
            "reasons": list(self.reasons),
            "reason_counts": self.reason_counts(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextSource:
        return cls(
            name=str(data.get("name", "")),
            label=str(data.get("label", "")),
            items=[str(i) for i in data.get("items") or []],
            truncated=bool(data.get("truncated", False)),
            error=str(data.get("error", "")),
            origin=str(data.get("origin", "")),
            reasons=[str(r) for r in data.get("reasons") or []],
        )


@dataclass
class ContextSnapshot:
    """Everything MondayOS assembled about one project, at one instant."""

    id: str
    project: str
    created_at: datetime
    sources: list[ContextSource] = field(default_factory=list)
    # Sources the budget refused to include at all, by name, so the omission is
    # recorded rather than invisible.
    omitted: list[str] = field(default_factory=list)
    # What the world looked like when this was assembled. Reuse compares
    # fingerprints; a difference means rebuild. Stored on the snapshot rather
    # than in a side table so a cached snapshot always carries its own validity
    # evidence — a cache whose key lives elsewhere is a cache that goes stale
    # silently.
    fingerprint: str = ""
    # The request text this was ranked for, when it was ranked for one.
    query: str = ""

    @property
    def token_estimate(self) -> int:
        return sum(s.token_estimate for s in self.sources)

    @property
    def char_count(self) -> int:
        return sum(s.char_count for s in self.sources)

    @property
    def truncated(self) -> bool:
        return any(s.truncated for s in self.sources) or bool(self.omitted)

    def source(self, name: str) -> ContextSource | None:
        for candidate in self.sources:
            if candidate.name == name:
                return candidate
        return None

    def render(self) -> str:
        """
        The snapshot as prompt text.

        Sources appear in budget priority order, and empty sources are skipped
        rather than rendered as headings with nothing under them.
        """
        blocks = [s.render() for s in self.sources]
        body = "\n\n".join(b for b in blocks if b)
        header = f"# Project context: {self.project}\n(assembled {_iso(self.created_at)})"
        return f"{header}\n\n{body}" if body else header

    def summary(self) -> str:
        """One line a human can read in the UI."""
        loaded = [s for s in self.sources if s.items]
        if not loaded:
            return "No project context could be loaded."
        parts = [f"{s.label} ({s.item_count})" for s in loaded]
        note = " · truncated" if self.truncated else ""
        return f"{', '.join(parts)} · ~{self.token_estimate} tokens{note}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project": self.project,
            "created_at": _iso(self.created_at),
            "sources": [s.to_dict() for s in self.sources],
            "omitted": list(self.omitted),
            "fingerprint": self.fingerprint,
            "query": self.query,
            "token_estimate": self.token_estimate,
            "char_count": self.char_count,
            "truncated": self.truncated,
            "summary": self.summary(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextSnapshot:
        return cls(
            id=str(data.get("id", "")),
            project=str(data.get("project", "")),
            created_at=_parse(str(data.get("created_at", ""))),
            sources=[ContextSource.from_dict(s) for s in data.get("sources") or []],
            omitted=[str(o) for o in data.get("omitted") or []],
            fingerprint=str(data.get("fingerprint", "")),
            query=str(data.get("query", "")),
        )


def snapshot_id(project: str, created_at: datetime, material: str) -> str:
    """
    A content-derived snapshot id.

    Derived from the project, the instant, and the assembled material, so two
    snapshots of genuinely different context can never collide, and the id itself
    is evidence of what it contained.
    """
    digest = hashlib.sha256(f"{project}|{_iso(created_at)}|{material}".encode()).hexdigest()
    return f"CTX-{digest[:12]}"


def _iso(value: datetime) -> str:
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: str) -> datetime:
    text = (value or "").strip()
    if not text:
        return datetime(1970, 1, 1, tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return datetime(1970, 1, 1, tzinfo=UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
