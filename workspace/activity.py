"""
Live activity — what Monday is actually doing, as it does it.

Every event here is emitted by real work at the point the work happens. Nothing
is synthesised to make the interface look busy: an activity feed that invents
plausible-looking steps is worse than no feed, because it trains the operator to
believe a display that is not measuring anything.

The recorder is deliberately tiny and in-memory. Activity is a live signal, not a
record — the durable record is the conversation, the context snapshot and the
knowledge entry. Persisting activity would create a second history to keep
consistent with the first.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class ActivityKind(Enum):
    """
    What kind of work an event describes.

    These map onto the Brain's visual states, which is why the set is small: a
    state the Brain cannot express is a state the operator cannot see.
    """

    CONTEXT = "context"
    KNOWLEDGE = "knowledge"
    TASK = "task"
    PROVIDER = "provider"
    PERSIST = "persist"
    ERROR = "error"


@dataclass
class ActivityEvent:
    """One thing that happened, with enough detail to be checked."""

    kind: ActivityKind
    message: str
    at: datetime
    project: str = ""
    detail: str = ""
    ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "message": self.message,
            "at": self.at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "project": self.project,
            "detail": self.detail,
            "ok": self.ok,
        }


# How many events to keep. A timeline is for orientation, not audit; the durable
# record lives elsewhere.
MAX_EVENTS = 60


class ActivityRecorder:
    """
    Collects activity events for one workspace session.

    Bounded on purpose: a long session must not accumulate an unbounded list
    nobody reads. Oldest events fall off the front.
    """

    def __init__(self, now: Callable[[], datetime] | None = None, limit: int = MAX_EVENTS) -> None:
        self._events: list[ActivityEvent] = []
        self._now = now or (lambda: datetime.now(tz=UTC))
        self._limit = limit

    def record(
        self,
        kind: ActivityKind,
        message: str,
        project: str = "",
        detail: str = "",
        ok: bool = True,
    ) -> ActivityEvent:
        event = ActivityEvent(
            kind=kind, message=message, at=self._now(), project=project, detail=detail, ok=ok
        )
        self._events.append(event)
        if len(self._events) > self._limit:
            del self._events[: len(self._events) - self._limit]
        return event

    def events(self, limit: int = 0) -> list[ActivityEvent]:
        """Most recent first."""
        ordered = list(reversed(self._events))
        return ordered[:limit] if limit > 0 else ordered

    def to_dicts(self, limit: int = 0) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.events(limit)]

    def clear(self) -> None:
        self._events.clear()


@dataclass
class NullRecorder:
    """
    A recorder that drops everything.

    The default, so nothing in the service is conditional on whether activity is
    being watched. A caller that wants the feed passes a real recorder.
    """

    _events: list[ActivityEvent] = field(default_factory=list)

    def record(
        self,
        kind: ActivityKind,
        message: str,
        project: str = "",
        detail: str = "",
        ok: bool = True,
    ) -> ActivityEvent:
        return ActivityEvent(kind=kind, message=message, at=datetime.now(tz=UTC))

    def events(self, limit: int = 0) -> list[ActivityEvent]:
        return []

    def to_dicts(self, limit: int = 0) -> list[dict[str, Any]]:
        return []

    def clear(self) -> None:
        return None
