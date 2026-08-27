"""
Performance events - the measured facts every later metric is computed from.

An event records one observation about one content item: an impression, a click,
a purchase. Metrics are never stored; they are computed from these events on
demand, so a metric can always be traced back to the observations behind it and
can never drift from them.

Every event carries its **source**, and the source is load-bearing:

    platform    reported by a real platform API
    imported    supplied by an operator from an export or another system
    synthetic   generated for a demo or a test

No real platform adapter exists yet (``REAL_ADAPTERS`` is empty), so nothing in
this codebase can legitimately produce a ``platform`` event. ``record()`` refuses
to write one, and a test asserts it. Until adapters land, every number the
Growth Bot reports is derived from synthetic or imported data and is labelled as
such all the way out to the CLI. A metric that silently looked measured would be
worse than no metric at all - the Growth Brain is going to treat this layer as
ground truth.

Events live inside the workspace, so they fall under the same isolation boundary
as everything else a project owns (ADR-011). Storage is append-only JSONL: an
event is machine-generated, immutable once written, and arrives in volume, which
is the same reasoning that puts agent runs and publish history in JSON rather
than Markdown.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

_EVENTS_DIRNAME = "events"
_EVENTS_FILENAME = "events.jsonl"


class EventType(Enum):
    """What was observed."""

    IMPRESSION = "impression"
    REACH = "reach"
    ENGAGEMENT = "engagement"
    REACTION = "reaction"
    COMMENT = "comment"
    SHARE = "share"
    CLICK = "click"
    WEBSITE_VISIT = "website_visit"
    SIGNUP = "signup"
    PURCHASE = "purchase"
    CUSTOM_CONVERSION = "custom_conversion"


class EventSource(Enum):
    """Where the observation came from. Never inferred, always explicit."""

    PLATFORM = "platform"
    IMPORTED = "imported"
    SYNTHETIC = "synthetic"


# Sources that do not represent real measured platform data. Any metric touching
# one of these is flagged synthetic in its output.
UNVERIFIED_SOURCES: frozenset[EventSource] = frozenset(
    {EventSource.SYNTHETIC, EventSource.IMPORTED}
)

# Event types that count as an engagement action.
ENGAGEMENT_TYPES: frozenset[EventType] = frozenset(
    {EventType.ENGAGEMENT, EventType.REACTION, EventType.COMMENT, EventType.SHARE}
)

# Event types that count as a conversion.
CONVERSION_TYPES: frozenset[EventType] = frozenset(
    {EventType.SIGNUP, EventType.PURCHASE, EventType.CUSTOM_CONVERSION}
)

# The conversion name that means "a lead". Leads are a custom conversion rather
# than their own event type, because what counts as a lead differs per project.
LEAD_CONVERSION_NAME = "lead"


class PlatformSourceUnavailableError(ValueError):
    """Raised when something tries to record a platform-sourced event."""

    def __init__(self) -> None:
        super().__init__(
            "Cannot record a platform-sourced performance event: no real platform "
            "adapter exists (integrations.publishing.factory.REAL_ADAPTERS is empty), "
            "so no platform has reported anything. Use source=imported for operator-"
            "supplied data or source=synthetic for demo data."
        )


@dataclass
class PerformanceEvent:
    """
    One measured observation about one content item.

    Attributes:
        id:          Monotonic per-workspace ordinal, assigned at write time.
        project:     Workspace slug. Present on the record so a misfiled event is
                     detectable rather than silently adopted.
        content_id:  The item observed. May be empty for account-level events.
        campaign:    Campaign id at the time of observation, denormalized so a
                     later campaign reassignment cannot rewrite history.
        platform:    Platform the observation came from.
        event_type:  What was observed.
        source:      platform | imported | synthetic.
        value:       How many. Defaults to 1 so a single click need not say so.
        occurred_at: When the observation happened (UTC).
        recorded_at: When MondayOS wrote it down (UTC).
        name:        Qualifier for custom_conversion, e.g. "lead", "demo_request".
        metadata:    Free-form extension bag.
    """

    id: int
    project: str
    event_type: EventType
    source: EventSource
    occurred_at: datetime
    content_id: str = ""
    campaign: str = ""
    platform: str = ""
    value: float = 1.0
    recorded_at: datetime | None = None
    name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_verified(self) -> bool:
        """True only for observations a real platform actually reported."""
        return self.source is EventSource.PLATFORM

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project": self.project,
            "event_type": self.event_type.value,
            "source": self.source.value,
            "occurred_at": _fmt(self.occurred_at),
            "content_id": self.content_id,
            "campaign": self.campaign,
            "platform": self.platform,
            "value": self.value,
            "recorded_at": _fmt(self.recorded_at) if self.recorded_at else "",
            "name": self.name,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PerformanceEvent:
        recorded = data.get("recorded_at")
        return cls(
            id=int(data.get("id", 0)),
            project=str(data.get("project", "")),
            event_type=EventType(str(data["event_type"])),
            source=EventSource(str(data["source"])),
            occurred_at=_parse(data.get("occurred_at")),
            content_id=str(data.get("content_id", "")),
            campaign=str(data.get("campaign", "")),
            platform=str(data.get("platform", "")),
            value=float(data.get("value", 1.0)),
            recorded_at=_parse(recorded) if recorded else None,
            name=str(data.get("name", "")),
            metadata=dict(data.get("metadata") or {}),
        )


class EventStore:
    """
    Append-only event log for exactly one workspace.

    Constructed from a workspace directory, so it can only ever read or write the
    project it was opened for.
    """

    def __init__(self, workspace_dir: Path, project: str) -> None:
        self._dir = Path(workspace_dir) / _EVENTS_DIRNAME
        self._path = self._dir / _EVENTS_FILENAME
        self._project = project

    @property
    def path(self) -> Path:
        return self._path

    @property
    def project(self) -> str:
        return self._project

    def record(
        self,
        event_type: EventType,
        source: EventSource,
        occurred_at: datetime,
        content_id: str = "",
        campaign: str = "",
        platform: str = "",
        value: float = 1.0,
        name: str = "",
        metadata: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> PerformanceEvent:
        """
        Append one observation.

        Refuses ``source=platform``: no adapter exists to have reported it, and a
        fabricated platform event would make every downstream metric a lie that
        looks measured.
        """
        if source is EventSource.PLATFORM:
            raise PlatformSourceUnavailableError()

        event = PerformanceEvent(
            id=self._next_ordinal(),
            project=self._project,
            event_type=event_type,
            source=source,
            occurred_at=_as_utc(occurred_at),
            content_id=content_id,
            campaign=campaign,
            platform=platform,
            value=float(value),
            recorded_at=_as_utc(now) if now else datetime.now(tz=UTC),
            name=name,
            metadata=dict(metadata or {}),
        )
        self._append(event)
        return event

    def record_many(self, events: list[dict[str, Any]], now: datetime | None = None) -> int:
        """Append several observations. Returns how many were written."""
        written = 0
        for raw in events:
            self.record(
                event_type=EventType(str(raw["event_type"])),
                source=EventSource(str(raw.get("source", EventSource.IMPORTED.value))),
                occurred_at=_parse(raw.get("occurred_at")),
                content_id=str(raw.get("content_id", "")),
                campaign=str(raw.get("campaign", "")),
                platform=str(raw.get("platform", "")),
                value=float(raw.get("value", 1.0)),
                name=str(raw.get("name", "")),
                metadata=dict(raw.get("metadata") or {}),
                now=now,
            )
            written += 1
        return written

    def all(self) -> list[PerformanceEvent]:
        """
        Every event for this project, in write order.

        A malformed line is skipped rather than aborting the read: one corrupt
        record must not make an entire project's history unreadable.
        """
        if not self._path.exists():
            return []
        events: list[PerformanceEvent] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, dict):
                continue
            try:
                events.append(PerformanceEvent.from_dict(parsed))
            except (KeyError, ValueError):
                continue
        return events

    def query(
        self,
        content_id: str = "",
        campaign: str = "",
        platform: str = "",
        event_type: EventType | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[PerformanceEvent]:
        """Filter this project's events. Every argument narrows; none widens."""
        results = self.all()
        if content_id:
            results = [e for e in results if e.content_id == content_id]
        if campaign:
            results = [e for e in results if e.campaign == campaign]
        if platform:
            results = [e for e in results if e.platform == platform]
        if event_type is not None:
            results = [e for e in results if e.event_type is event_type]
        if since is not None:
            floor = _as_utc(since)
            results = [e for e in results if e.occurred_at >= floor]
        if until is not None:
            ceiling = _as_utc(until)
            results = [e for e in results if e.occurred_at <= ceiling]
        return results

    def count(self) -> int:
        """How many events this project holds."""
        return len(self.all())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _append(self, event: PerformanceEvent) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")

    def _next_ordinal(self) -> int:
        """
        Next event ordinal for this workspace.

        Derived from the file rather than a counter, so a lost sequence file
        cannot reissue an id, and so ordinals stay per-project - a shared counter
        would leak one project's event volume into another's ids.
        """
        existing = self.all()
        return (max((e.id for e in existing), default=0)) + 1


def sources_of(events: list[PerformanceEvent]) -> set[EventSource]:
    """The distinct sources present in a set of events."""
    return {e.source for e in events}


def contains_unverified(events: list[PerformanceEvent]) -> bool:
    """True when any event came from something other than a real platform."""
    return any(e.source in UNVERIFIED_SOURCES for e in events)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _parse(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.rstrip("Z")).replace(tzinfo=UTC)
    return datetime.now(tz=UTC)


def _fmt(value: datetime) -> str:
    return _as_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")
