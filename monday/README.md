# monday

The stable public API for MondayOS.

## Purpose

`monday` is the only package external consumers import. It defines the surface of MondayOS as a product. Everything inside `brain/`, `events/`, `knowledge/`, `memory/`, `search/`, and `tasks/` is an implementation detail. Those packages may be refactored, replaced, or restructured without any external code changing — as long as the `Monday` interface remains stable.

## The Rule

> External code imports from `monday`. Never from internal modules.

```python
# Correct
from monday import Monday
monday = Monday()

# Not acceptable — imports an internal module directly
from brain import Brain
from tasks import TaskManager
```

## Quick Start

```python
from monday import Monday

monday = Monday()

# Check system status
s = monday.status()
print(s.version)        # "0.1.0"
print(s.healthy)        # True
print(s.session_id)     # "a3f9..."
print(s.uptime_seconds) # 0.001...

# Submit a prompt
r = monday.ask("What tasks are currently blocked?")
print(r.answer)   # "" (not yet implemented)

# Add knowledge
r = monday.learn("Retry on 429 using the retry-after header.", title="Rate limit handling")
print(r.accepted) # False (not yet implemented)

# Search
r = monday.search("rate limit", sources=["knowledge"])
print(r.total_found) # 0 (not yet implemented)

# Task management
r = monday.task("create", title="Fix rate limit handling", objective="...")
print(r.success)  # False (not yet implemented)
```

## Configuration

`Monday()` works with no arguments. To customize:

```python
from monday import Monday, MondayConfig
from pathlib import Path

monday = Monday(MondayConfig(
    project_root=Path("/path/to/project"),
    model_tier="high",          # "high" | "standard" | "fast" | "local"
    require_human_approval=True,
    log_level="DEBUG",
))
```

## Public Interface

### `Monday` methods

| Method | Description | Status |
|---|---|---|
| `status()` | System health, version, uptime | Implemented |
| `ask(prompt, context)` | Submit a natural language query | Placeholder |
| `learn(content, title, entry_type, tags, components)` | Add a knowledge entry | Placeholder |
| `search(query, sources, limit)` | Search across all data sources | Placeholder |
| `task(action, ...)` | Create, get, list, update, or complete a task | Placeholder |

### Response types

All methods return typed dataclasses. Type signatures do not change between implementation phases.

| Type | Returned by |
|---|---|
| `AskResponse` | `Monday.ask()` |
| `LearnResponse` | `Monday.learn()` |
| `SearchResponse` | `Monday.search()` |
| `TaskResponse` | `Monday.task()` |
| `StatusResponse` | `Monday.status()` |
| `ModuleStatus` | Field inside `StatusResponse` |

### `MondayConfig` fields

| Field | Default | Description |
|---|---|---|
| `project_root` | `Path(".")` | Root of the MondayOS project |
| `model_tier` | `"standard"` | Default model tier for task routing |
| `require_human_approval` | `True` | Enforce approval gates on production actions |
| `log_level` | `"INFO"` | Logging verbosity |
| `session_id` | `None` (auto) | Explicit session ID; auto-generated if None |

## Why a Wrapper Class?

Without a stable API boundary, external consumers couple to internal module structure. When `knowledge/store.py` is refactored, all callers must change. With `Monday` as the boundary:

- Internal modules can be refactored freely.
- The `monday` package absorbs all breaking changes at the integration point.
- Versioning is meaningful — `Monday.VERSION` reflects the public contract version.
- Testing external consumers is simple — mock the `Monday` class, not six internal classes.

## What This Module Does NOT Own

- Business logic (routing, model calls, search ranking) — that lives in the internal modules.
- Data persistence — that is `knowledge/`, `memory/`, `tasks/`.
- The event bus lifecycle — `Monday` uses `EventBus` but does not own it.
- The REST API (future) — that will be a separate layer above `Monday`.

## Dependencies

`monday` depends on all internal modules:

- `brain` — `Brain`, `BrainConfig`
- `events` — `EventBus`
- `knowledge` — `KnowledgeStore`
- `memory` — `SessionMemory`
- `search` — `SearchEngine`
- `tasks` — `TaskManager`
- `core` — `EntityId`, `Timestamp` (transitively)
