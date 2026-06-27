# tasks

Task lifecycle management and operational audit trail.

## Responsibility

The tasks module defines the unit of work in MondayOS and manages its full lifecycle. Every task — whether created by a human or an AI agent — flows through a defined state machine and accumulates a complete audit trail. The task record is the canonical answer to "what happened and why."

## What This Module Does NOT Own

- Routing tasks to agents — that is `brain.Router`.
- Executing tasks — that is `brain.Brain`.
- Storing knowledge learned from tasks — that is `knowledge/`.
- Workflow definitions (multi-step sequences) — those are in `workflows/`.

## Public Interface

| Symbol | Description |
|---|---|
| `TaskManager` | The only path to create, update, or archive tasks |
| `Task` | Task data model with full status history |
| `TaskType` | `feature`, `fix`, `refactor`, `docs`, `research`, `ops`, `review` |
| `TaskStatus` | `backlog` → `assigned` → `in-progress` → `review` → `completed` |
| `TaskPriority` | `P0` (critical) through `P3` (background) |
| `ApprovalLevel` | `none`, `human-review`, `human-approval` |
| `StatusTransition` | Immutable record of one status change (who, when, why) |

## Status Lifecycle

```
backlog → assigned → in-progress → review → completed
                         ↓
                       blocked → in-progress
Any status → cancelled (terminal)
```

## File Layout

```
tasks/
├── active/
│   ├── TASK-0001.md    ← backlog, assigned, in-progress, blocked, review
│   └── index.md        ← auto-generated summary (do not edit)
└── completed/
    └── TASK-0000.md    ← completed and cancelled (never deleted)
```

## Dependencies

- `core.types` — `EntityId`, `Timestamp`
- `events` — `EventBus` (to publish task lifecycle events)
