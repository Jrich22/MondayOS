# brain

Top-level orchestrator and single entry point for MondayOS.

## Responsibility

The brain module wires all MondayOS subsystems together and coordinates task execution end-to-end. It is the only module that depends on all other modules. Nothing outside brain imports subsystem internals — callers interact only with `Brain`.

The brain owns:
- The session lifecycle (start, execute, end)
- Task routing to the correct model via `Router`
- Approval gate enforcement before task execution
- Subsystem initialization from `BrainConfig`

## What This Module Does NOT Own

- Task persistence — that is `tasks.TaskManager`.
- Knowledge persistence — that is `knowledge.KnowledgeStore`.
- Model API calls — that is `integrations/` (Phase 1.2, not yet built).
- Event bus — `brain` uses the bus but does not own it.
- Search logic — that is `search.SearchEngine`.

## Public Interface

| Symbol | Description |
|---|---|
| `Brain` | The system coordinator — instantiate once, use everywhere |
| `BrainConfig` | Configuration: paths, approval settings, model defaults |
| `Router` | Selects model tier + model ID for a task |
| `RoutingDecision` | Result of routing: tier, model, reasoning (always populated) |
| `ModelTier` | `HIGH`, `STANDARD`, `FAST`, `LOCAL` |

## Usage Pattern

```python
from pathlib import Path
from brain import Brain, BrainConfig

config = BrainConfig.from_project_root(Path("."))
brain = Brain(config)

task_id = brain.create_task(
    title="Add rate limit retry logic to Claude integration",
    objective="Retry on 429 responses using the retry-after header value.",
)
brain.execute_task(task_id)
```

## Dependencies

All other MondayOS modules flow into `brain`:

- `tasks` — `TaskManager`
- `knowledge` — `KnowledgeStore`
- `memory` — `ProjectMemory`, `SessionMemory`
- `search` — `SearchEngine`
- `events` — `EventBus`
- `integrations/` — model API clients (Phase 1.2)
