# MondayOS — Memory System

**Version:** 0.1.0  
**Status:** Active  
**Last Updated:** 2026-06-27

---

## Purpose

The Memory System provides AI agents with persistent context across sessions, tasks, and model instances. It answers a foundational question: when an agent picks up a task today, what does it need to know that was established yesterday?

Memory is distinct from the Knowledge System. The Knowledge System stores learned facts and resolved problems — structured, permanent records. Memory stores operational context: what the project is currently doing, what decisions are pending, what state was in progress when the last session ended.

| System | Stores | Lifetime | Primary Consumer |
|---|---|---|---|
| Memory | Current state, active context, working decisions | Temporary to long-term | Active agents |
| Knowledge | Resolved bugs, decisions, patterns, runbooks | Permanent | Future agents and humans |

---

## Memory Tiers

### Tier 1: Session Memory

**Scope:** A single agent session (start to end of one execution run)  
**Persistence:** Cleared when the session ends  
**Storage:** In-process, optionally written to `memory/session/{session-id}.json` for debugging

Session memory holds everything an agent has accumulated during its current execution: intermediate reasoning, tool call results, partial work, and conversation context. It is the agent's working RAM.

Session memory is never shared across agents. Each agent instance has its own session memory.

**Contents:**
- Active task ID and description
- Tool call history for this session
- Intermediate reasoning steps
- References to memory and knowledge entries retrieved this session
- Partial outputs not yet committed

---

### Tier 2: Project Memory

**Scope:** The active project (MondayOS itself, or a project MondayOS is working on)  
**Persistence:** Cross-session, written to `memory/project/` and tracked in Git  
**Storage:** Structured Markdown with YAML frontmatter

Project memory captures the persistent state that all agents share: what the project is, what has been decided, what is currently in progress, and what the current priorities are.

**Contents:**
- Project goals and current phase
- Active tasks and their status
- Pending decisions awaiting resolution
- Current architectural context (concise summary, not full ARCHITECTURE.md)
- Recent significant events (last 30 days)
- Known risks and open questions

**Why Git-tracked:** Project memory must survive process crashes, machine restarts, and environment rebuilds. Storing it in Git means it is versioned, auditable, and recoverable from any point in history.

---

### Tier 3: Agent Memory

**Scope:** A specific named agent across its lifetime  
**Persistence:** Permanent, written to `memory/agent/{agent-id}.md`  
**Storage:** Structured Markdown

Agent memory captures what a specific agent has learned about itself and its domain over time. Not all agents have persistent agent memory — only agents that are given a persistent identity within the project (e.g., "the code review agent", "the documentation agent").

**Contents:**
- What tasks this agent has completed
- What capabilities this agent has demonstrated
- What types of tasks this agent struggles with (for routing calibration)
- Preferences and defaults established by the agent's prior behavior
- Feedback received from human reviewers

---

## Memory File Format

All persistent memory files use this structure:

```markdown
---
id: {tier}-{id}
type: {session | project | agent}
created: {YYYY-MM-DDThh:mm:ssZ}
updated: {YYYY-MM-DDThh:mm:ssZ}
expires: {YYYY-MM-DDThh:mm:ssZ | null}
version: {integer, increments on each write}
---

## {Section Title}

{Content}
```

The `version` field is used for optimistic concurrency — if two agents attempt to write the same memory file simultaneously, the one with the lower version number loses and must re-read and retry.

---

## Memory Operations

### Read

Agents request memory by ID or by query. Memory reads are always explicit — agents do not automatically receive all memory context. This is intentional:

1. Context window space is finite and expensive.
2. Automatic context injection makes memory access invisible and unauditable.
3. Agents that explicitly request memory can be observed doing so.

```python
# Agents call through the memory interface, never directly accessing files
context = memory.read(tier="project", key="current_priorities")
agent_history = memory.read(tier="agent", id="code-review-agent")
```

Every read is logged with: tier, key/id, requesting agent, timestamp, and whether the entry was found.

### Write

Agents write memory at task completion or at designated checkpoints. Writes go through the memory layer, never directly to the filesystem.

```python
memory.write(
    tier="project",
    key="active_tasks",
    content=updated_task_list,
    reason="Task TASK-0042 completed; removing from active list"
)
```

Every write is logged with: tier, key/id, writing agent, previous version, new version, and the reason for the write.

### Expire

Memory entries can have an expiry timestamp. The memory layer does not delete expired entries — it marks them as expired and excludes them from normal reads. Expired entries can be retrieved explicitly if needed.

This preserves the audit trail while preventing stale memory from polluting agent context.

### Invalidate

A memory entry can be explicitly invalidated by a human or authorized agent if it is known to be incorrect. Invalidation is logged with the reason and the invalidating party.

---

## Memory Access Control

In Phase 1, all agents have read access to all project memory. This is acceptable at small scale where all agents are working on the same project.

In Phase 2, memory is scoped: agents can only read and write memory relevant to their assigned task and component. An agent working on the integration layer does not automatically have read access to the memory of the task system agent.

This principle of least privilege for memory access reduces the risk of agents acting on irrelevant or misleading context.

---

## Session Continuity

When an agent session ends mid-task (crash, timeout, manual stop), the partially complete work must be recoverable. The continuity mechanism:

1. At the start of any significant operation, the agent writes a checkpoint to session memory.
2. On session start, the agent checks for an orphaned session file from a previous run.
3. If found, the agent reads the checkpoint and resumes from the last stable state rather than starting over.
4. The resumed session logs that it is a continuation of a previous session.

This prevents work loss and ensures that long-running tasks can survive interruption.

---

## Memory and the Knowledge System

Memory and knowledge are related but distinct. The distinction:

- **Memory:** "We are currently in Phase 1. The task queue has 12 items. The last session completed TASK-0037."
- **Knowledge:** "When integrating the Anthropic API, the rate limit is 60 RPM for claude-opus-4-8. See BUG-0003 for the retry-after handling pattern."

When a session ends and produces reusable insights, those insights are promoted from memory to the knowledge base as structured entries. Memory that is not promoted expires and is eventually pruned.

The promotion process:
1. Agent or human identifies a memory entry worth preserving permanently.
2. A knowledge entry is created from the memory content.
3. The memory entry is marked as `promoted: true` and references the knowledge entry.
4. The memory entry continues to expire on schedule.

---

## Storage Layout

```
memory/
├── project/
│   ├── current_state.md        ← current project phase, priorities, open decisions
│   ├── active_tasks.md         ← summary of in-progress tasks
│   ├── recent_events.md        ← last 30 days of significant events
│   └── risks.md                ← known risks and open questions
├── agent/
│   ├── orchestrator-agent.md
│   ├── code-review-agent.md
│   └── ...
└── session/
    └── {session-id}.json       ← ephemeral; for debugging only
```

---

## Design Decisions

### Why Markdown files instead of a database?

In Phase 1, the volume of memory writes is low (dozens per day). Markdown files:
- Are human-readable without tooling
- Are diffable in Git, providing a change history for free
- Require no database infrastructure to operate
- Are inspectable and editable in any text editor for debugging

When write volume or query complexity requires it (Phase 2), a SQLite backend will be introduced alongside the file store. The memory interface is unchanged — only the storage implementation changes.

### Why explicit memory reads instead of automatic context injection?

Two reasons:

1. **Context window cost.** Injecting all project memory into every agent call wastes tokens and can exceed context limits.
2. **Observability.** An agent that explicitly requests memory creates a log entry. Automatic injection is invisible and makes debugging harder.

The tradeoff is that agents must know to request memory. This is handled by the agent scaffolding: standard agent initialization always requests a defined set of project memory keys.

### Why track memory in Git?

Project memory in Git means that every change to the operational state of the project is versioned. If an agent writes incorrect memory and subsequent agents act on it, the history of that incorrect write is preserved and traceable. This is the same reason architectural decisions live in Git rather than a wiki.
