"""Synchronous in-process event bus."""
from __future__ import annotations

from collections import defaultdict

from events.types import Event, EventHandler, EventType


class EventBus:
    """
    Synchronous, in-process event bus for MondayOS component communication.

    Components publish events when significant things happen; other components
    subscribe and react. This keeps modules decoupled: the task system does not
    import the memory system — it publishes TASK_COMPLETED and the memory system
    reacts.

    Phase 1: Synchronous, in-process, single-thread delivery.
    Phase 2: Replace delivery loop with an async queue for concurrent tasks.

    TODO: Persist every event to logs/ before dispatching.
    TODO: Wrap each handler call in error handling; log and continue on failure.
    TODO: Add wildcard subscription (subscribe to all EventTypes).
    TODO: Bound _history size; flush old entries to disk to prevent memory growth.
    """

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[EventHandler]] = defaultdict(list)
        self._history: list[Event] = []

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Register a handler to be called when event_type is published."""
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Remove a previously registered handler. No-op if not found."""
        handlers = self._handlers[event_type]
        if handler in handlers:
            handlers.remove(handler)

    def publish(self, event: Event) -> None:
        """
        Dispatch an event to all registered handlers.

        Handlers are called in registration order. History is recorded before
        dispatch so the event is always in history even if a handler raises.
        """
        self._history.append(event)
        for handler in self._handlers[event.event_type]:
            handler(event)

    def history(self, event_type: EventType | None = None) -> list[Event]:
        """Return published events, optionally filtered by type."""
        if event_type is None:
            return list(self._history)
        return [e for e in self._history if e.event_type == event_type]

    def clear_history(self) -> None:
        """Clear in-memory event history. Does not affect persisted logs."""
        self._history.clear()
