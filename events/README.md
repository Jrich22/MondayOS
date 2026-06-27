# events

Internal event bus for MondayOS component communication.

## Responsibility

The events module is the single communication channel between MondayOS components. When something significant happens — a task is created, a model call completes, a knowledge entry is written — the responsible component publishes an `Event`. Other components that care about that event subscribe and react.

## What This Module Does NOT Own

- Business logic. The bus delivers events; it does not act on them.
- Persistence of events to disk. That is the responsibility of the logging layer in `core/` (not yet implemented).
- External event systems (webhooks, Slack). Those are integration concerns.

## Public Interface

| Symbol | Description |
|---|---|
| `EventBus` | Subscribe, unsubscribe, publish, and query event history |
| `Event` | Immutable event record — the unit of communication |
| `EventType` | Enum of all events in the system |
| `EventHandler` | Type alias: `Callable[[Event], None]` |

## Usage Pattern

```python
from events import EventBus, Event, EventType
from datetime import datetime, timezone

bus = EventBus()

# Subscribe
def on_task_created(event: Event) -> None:
    print(f"Task created: {event.payload}")

bus.subscribe(EventType.TASK_CREATED, on_task_created)

# Publish
bus.publish(Event(
    event_type=EventType.TASK_CREATED,
    source="tasks",
    timestamp=datetime.now(tz=timezone.utc),
    payload={"task_id": "TASK-0001"},
))
```

## Dependencies

- `core.types` — `EntityId`, `Timestamp`

## Phase 2 Migration Note

The `EventBus` class is designed for synchronous, in-process delivery in Phase 1. In Phase 2, the internal delivery mechanism will be replaced with an async queue to support concurrent task execution. The public interface (`subscribe`, `publish`) will not change.
