"""Tests for the events module (EventBus, Event, EventType)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from events import Event, EventBus, EventType


def _make_event(event_type: EventType, **kwargs) -> Event:
    return Event(
        event_type=event_type,
        source="test",
        timestamp=datetime.now(tz=timezone.utc),
        **kwargs,
    )


class TestEventBus:
    def setup_method(self) -> None:
        self.bus = EventBus()

    # ------------------------------------------------------------------
    # Implemented behavior (tests must pass now)
    # ------------------------------------------------------------------

    def test_subscribe_and_publish_delivers_event(self) -> None:
        received: list[Event] = []
        self.bus.subscribe(EventType.TASK_CREATED, received.append)
        event = _make_event(EventType.TASK_CREATED)
        self.bus.publish(event)
        assert received == [event]

    def test_publish_with_no_subscribers_does_not_raise(self) -> None:
        self.bus.publish(_make_event(EventType.TASK_COMPLETED))

    def test_history_records_all_published_events(self) -> None:
        e1 = _make_event(EventType.TASK_CREATED)
        e2 = _make_event(EventType.KNOWLEDGE_ENTRY_CREATED)
        self.bus.publish(e1)
        self.bus.publish(e2)
        assert e1 in self.bus.history()
        assert e2 in self.bus.history()

    def test_history_filters_by_event_type(self) -> None:
        self.bus.publish(_make_event(EventType.TASK_CREATED))
        self.bus.publish(_make_event(EventType.TASK_COMPLETED))
        result = self.bus.history(EventType.TASK_CREATED)
        assert all(e.event_type == EventType.TASK_CREATED for e in result)
        assert len(result) == 1

    def test_unsubscribe_stops_delivery(self) -> None:
        received: list[Event] = []
        self.bus.subscribe(EventType.TASK_CREATED, received.append)
        self.bus.unsubscribe(EventType.TASK_CREATED, received.append)
        self.bus.publish(_make_event(EventType.TASK_CREATED))
        assert received == []

    def test_unsubscribe_nonexistent_handler_does_not_raise(self) -> None:
        self.bus.unsubscribe(EventType.TASK_CREATED, lambda e: None)

    def test_multiple_subscribers_all_receive(self) -> None:
        a: list[Event] = []
        b: list[Event] = []
        self.bus.subscribe(EventType.TASK_CREATED, a.append)
        self.bus.subscribe(EventType.TASK_CREATED, b.append)
        self.bus.publish(_make_event(EventType.TASK_CREATED))
        assert len(a) == 1
        assert len(b) == 1

    def test_clear_history_empties_history(self) -> None:
        self.bus.publish(_make_event(EventType.TASK_CREATED))
        self.bus.clear_history()
        assert self.bus.history() == []

    def test_event_history_is_recorded_before_handler_runs(self) -> None:
        """History must contain the event even if the handler raises."""
        recorded: list[Event] = []

        def bad_handler(event: Event) -> None:
            recorded.append(event)
            raise RuntimeError("handler error")

        self.bus.subscribe(EventType.TASK_CREATED, bad_handler)
        event = _make_event(EventType.TASK_CREATED)
        with pytest.raises(RuntimeError):
            self.bus.publish(event)
        assert event in self.bus.history()

    def test_event_payload_is_accessible(self) -> None:
        event = _make_event(EventType.TASK_CREATED, payload={"task_id": "TASK-0001"})
        self.bus.publish(event)
        history = self.bus.history(EventType.TASK_CREATED)
        assert history[0].payload["task_id"] == "TASK-0001"

    def test_event_is_frozen(self) -> None:
        """Event is a frozen dataclass — mutation must raise."""
        event = _make_event(EventType.TASK_CREATED)
        with pytest.raises((AttributeError, TypeError)):
            event.source = "mutated"  # type: ignore[misc]
