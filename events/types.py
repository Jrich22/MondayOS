"""Event type definitions for the MondayOS event bus."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable

from core.types import EntityId, Timestamp


class EventType(Enum):
    """All event types that flow through the MondayOS event bus."""

    # Task lifecycle
    TASK_CREATED = auto()
    TASK_ASSIGNED = auto()
    TASK_STARTED = auto()
    TASK_COMPLETED = auto()
    TASK_BLOCKED = auto()
    TASK_CANCELLED = auto()

    # Knowledge
    KNOWLEDGE_ENTRY_CREATED = auto()
    KNOWLEDGE_ENTRY_UPDATED = auto()
    KNOWLEDGE_QUERIED = auto()

    # Memory
    MEMORY_WRITTEN = auto()
    MEMORY_READ = auto()
    MEMORY_EXPIRED = auto()

    # Model calls
    MODEL_CALL_STARTED = auto()
    MODEL_CALL_COMPLETED = auto()
    MODEL_CALL_FAILED = auto()

    # Human approval gates
    APPROVAL_REQUESTED = auto()
    APPROVAL_GRANTED = auto()
    APPROVAL_DENIED = auto()

    # Session lifecycle
    SESSION_STARTED = auto()
    SESSION_ENDED = auto()


@dataclass(frozen=True)
class Event:
    """
    Immutable record of something that happened in MondayOS.

    Every significant action — task status change, knowledge write, model call,
    approval gate — produces an Event. Events are the audit trail.

    Fields:
        event_type:     What happened.
        source:         Which component emitted this event (module name).
        timestamp:      When it happened (UTC).
        payload:        Type-specific data about the event.
        task_id:        Associated task, if any.
        session_id:     Associated session, if any.
        correlation_id: Links a chain of related events (e.g. request → response).
    """

    event_type: EventType
    source: str
    timestamp: Timestamp
    payload: dict[str, Any] = field(default_factory=dict)
    task_id: EntityId | None = None
    session_id: EntityId | None = None
    correlation_id: EntityId | None = None


# A handler is any callable that accepts an Event and returns nothing.
EventHandler = Callable[[Event], None]
