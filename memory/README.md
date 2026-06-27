# memory

Three-tier persistent context system for AI agents.

## Responsibility

The memory module gives AI agents access to context that persists across sessions. Without it, every agent session starts blank. With it, agents can recall what has been decided, what is currently in progress, and what they themselves have learned.

Memory is **operational state**, not learned facts. For learned facts (bugs, decisions, patterns), see `knowledge/`.

## What This Module Does NOT Own

- Knowledge entries (bugs, decisions, patterns, runbooks) — that is `knowledge/`.
- Task records — that is `tasks/`.
- Structured logging — that is `core/` (not yet implemented).
- Git operations — memory files are Git-tracked, but this module does not run git commands.

## Tiers

| Tier | Class | Lifetime | Storage | Shared? |
|---|---|---|---|---|
| Session | `SessionMemory` | One execution run | In-process dict | No |
| Project | `ProjectMemory` | Project lifetime | `memory/project/*.md` | All agents |
| Agent | `AgentMemory` | Agent lifetime | `memory/agent/{id}.md` | No |

## Public Interface

| Symbol | Description |
|---|---|
| `SessionMemory` | Volatile in-session context — fully implemented in Phase 1 |
| `ProjectMemory` | Cross-session project state — placeholder, Phase 1 |
| `AgentMemory` | Per-agent history and capability record — placeholder, Phase 1 |
| `MemoryRecord` | Value + provenance (who, when, why, version) |
| `MemoryStore` | Protocol all three tiers implement |

## Usage Pattern

```python
from memory import SessionMemory

mem = SessionMemory(session_id="sess-001")
mem.write("current_task", "TASK-0042", written_by="brain", reason="task assigned")

record = mem.read("current_task")
if record:
    print(record.value)   # "TASK-0042"
    print(record.version) # 1
```

## Dependencies

- `core.types` — `EntityId`, `Timestamp`
- `events` — `EventBus` (to publish MEMORY_WRITTEN / MEMORY_READ events; Phase 1 TODO)
