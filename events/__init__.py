"""
MondayOS Events — internal event bus and event type registry.

The event bus is the communication backbone of MondayOS. Components publish
events when significant things happen; subscribers react. No component imports
another component's internals directly — it publishes an event and trusts
that the right subscriber will handle it.

This decoupling is what makes each module independently replaceable.

Public interface:
    EventBus      — the bus instance (one per application)
    Event         — immutable event record (the unit of communication)
    EventType     — all event types in the system
    EventHandler  — type alias for subscriber callables
"""
from __future__ import annotations

from events.bus import EventBus
from events.types import Event, EventHandler, EventType

__all__ = [
    "EventBus",
    "Event",
    "EventType",
    "EventHandler",
]
