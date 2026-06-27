# MondayOS — Task System

**Version:** 0.1.0  
**Status:** Active  
**Last Updated:** 2026-06-27

---

## Purpose

The Task System is the operational core of MondayOS. It defines how work is described, assigned, tracked, executed, and archived. Every unit of work — whether initiated by a human or an AI agent — flows through the task system.

The task system answers: *what is being done, by whom, in what order, and what is the result?*

---

## Design Goals

1. **Complete audit trail.** Every task preserves its full history: who created it, who worked on it, every status change, every output produced.
2. **Human-readable.** Task files are plain Markdown. No special tooling is required to read or understand the task backlog.
3. **Agent-operable.** AI agents can create, read, update, and complete tasks through a well-defined interface.
4. **Composable.** Tasks can be broken into subtasks. Complex work is modeled as a hierarchy, not a flat list.
5. **Durable.** Tasks survive crashes and restarts. No task is lost because of an interrupted session.

---

## Task File Format

Every task is a Markdown file. The file is the task.

```markdown
---
id: TASK-{NNNN}
title: {short human-readable title}
type: {feature | fix | refactor | docs | research | ops | review}
status: {backlog | assigned | in-progress | blocked | review | completed | cancelled}
priority: {P0 | P1 | P2 | P3}
created: {YYYY-MM-DDThh:mm:ssZ}
updated: {YYYY-MM-DDThh:mm:ssZ}
created_by: {human:{name} | agent:{agent-id}}
assigned_to: {human:{name} | agent:{agent-id} | unassigned}
parent_task: {TASK-NNNN | null}
child_tasks: [{TASK-NNNN, ...}]
approval_required: {none | human-review | human-approval}
blocked_by: {description of blocker | null}
knowledge_refs: [{BUG-NNNN, DEC-NNNN, PAT-NNNN, ...}]
commit_refs: [{sha, ...}]
---

## Objective

{What needs to be accomplished? Write this as if briefing an engineer (human or AI) who has no context. 
Be specific. Include acceptance criteria.}

## Context

{Why does this task exist? What situation created the need? Link to relevant ADRs, prior tasks, or 
knowledge entries.}

## Acceptance Criteria

- [ ] {Specific, testable condition that must be true when this task is done}
- [ ] {Another condition}
- [ ] Tests cover the new behavior
- [ ] Documentation updated if interface changed

## Work Log

### {YYYY-MM-DD} — {agent-id or human name}

{What was done in this session. What was discovered. What remains.}

---

### {YYYY-MM-DD} — {next entry}

{...}

## Output

{The result of this task. For code tasks: commit SHA(s) and summary of changes. For research tasks: 
findings. For docs tasks: links to created/updated docs.}

## Status History

| Timestamp | From | To | By | Reason |
|---|---|---|---|---|
| {ISO timestamp} | backlog | assigned | {agent-id} | {reason} |
| {ISO timestamp} | assigned | in-progress | {agent-id} | Started execution |
| {ISO timestamp} | in-progress | completed | {agent-id} | All acceptance criteria met |
```

---

## Task Types

| Type | Description |
|---|---|
| `feature` | New functionality being added to the system |
| `fix` | Resolving a defect or incorrect behavior |
| `refactor` | Improving code structure without changing behavior |
| `docs` | Creating or updating documentation |
| `research` | Investigation to inform a future decision or task |
| `ops` | Operational work (deploy, configure, monitor) |
| `review` | Code or output review task |

---

## Priority Levels

| Priority | Meaning | Response Expectation |
|---|---|---|
| `P0` | System down or data loss risk | Drop everything; resolve immediately |
| `P1` | Critical functionality broken; blocking other work | Address within the current session |
| `P2` | Important but not blocking; normal priority | Address within the current sprint/cycle |
| `P3` | Nice to have; background work | Address when higher-priority work is clear |

---

## Task Status Lifecycle

```
         ┌──────────┐
         │ backlog  │ ← Task created but not yet assigned
         └────┬─────┘
              │ assigned_to set
              ▼
         ┌──────────┐
         │ assigned │ ← Agent or human accepted the task
         └────┬─────┘
              │ work begins
              ▼
         ┌─────────────┐
         │ in-progress │ ← Active work underway
         └──┬──────────┘
            │
            ├──── blocker encountered ────► ┌─────────┐
            │                               │ blocked │ ← Awaiting external resolution
            │                               └────┬────┘
            │                                    │ blocker resolved
            │ ◄──────────────────────────────────┘
            │
            ├──── output produced ────► ┌────────┐
            │                           │ review │ ← Awaiting human or peer review
            │                           └───┬────┘
            │                               │ approved
            │ ◄─────────────────────────────┘
            │
            ▼
       ┌───────────┐
       │ completed │ ← All acceptance criteria met; output archived
       └───────────┘

At any status: ──► cancelled (with reason)
```

---

## Directory Structure

```
tasks/
├── active/
│   ├── TASK-0001.md      ← backlog, assigned, in-progress, blocked, review
│   └── ...
└── completed/
    ├── TASK-0000.md      ← completed and cancelled tasks
    └── ...
```

Tasks are moved from `active/` to `completed/` when they reach `completed` or `cancelled` status. They are never deleted.

---

## Task Creation

### Human-Initiated Tasks

Humans create tasks by writing a task file or using the CLI task creation command. The minimum required fields are:

- `title`
- `type`
- `priority`
- `Objective` section content

The system fills in: `id`, `created`, `updated`, `status: backlog`, `created_by`.

### Agent-Initiated Tasks

Agents create tasks when they discover work that is outside the scope of their current task. An agent working on a feature that discovers a bug creates a `fix` task for the bug rather than fixing it inline. This keeps task scope clean and ensures all discovered work is tracked.

Agent-created tasks are logged with `created_by: agent:{agent-id}` and require `approval_required: human-review` by default — a human sees them before they are assigned.

### Task Decomposition

When a task is too large for a single agent session, it is decomposed into subtasks. The decomposition is itself logged as a work log entry on the parent task.

Subtasks reference their parent via `parent_task`. The parent task is not complete until all required subtasks are complete.

---

## Task Assignment and Routing

The Orchestrator assigns tasks based on:

1. **Task type** — certain agents specialize in certain task types.
2. **Required model** — some tasks require specific model capabilities.
3. **Component** — agents familiar with a component receive its tasks preferentially.
4. **Current load** — agents with fewer in-progress tasks are preferred.

Assignment is logged with reasoning. If an agent is assigned a task for a non-obvious reason, the reasoning is recorded in the status history.

---

## Blocked Tasks

A task becomes `blocked` when it cannot proceed due to an external dependency, a missing human decision, or an unresolved question. When a task is blocked:

1. The `blocked_by` field is set with a specific description of what is blocking it.
2. The blocking event is logged in the work log.
3. The human or designated resolver is notified.
4. The task waits — it does not accumulate work log entries until the blocker is resolved.

A task that is blocked for more than 48 hours is automatically escalated to `P1` regardless of its original priority.

---

## Approval Gates

Every task has an `approval_required` level:

| Level | Behavior |
|---|---|
| `none` | Task can be created and executed without human involvement |
| `human-review` | Human is notified when the task completes; can reject and reopen |
| `human-approval` | Human must approve before execution begins |

The default for agent-created tasks is `human-review`. The default for tasks that touch production systems is `human-approval`.

---

## Task Completion

A task is complete when:

1. All acceptance criteria are checked off.
2. The `Output` section is populated with the result.
3. Any knowledge entries prompted by the task have been created.
4. The task file is moved to `completed/`.
5. Any parent task's status is re-evaluated.

The completion event is logged in project memory and in the task's status history.

---

## Task Search and Retrieval

Agents query the task system at the start of each session to understand:
- What tasks are assigned to them
- What tasks are blocked waiting for them
- What related tasks exist (by component or tag)

The task index (`tasks/active/index.md`, auto-generated) provides a queryable summary of all active tasks by status, priority, and component.

---

## Design Decisions

### Why Markdown files instead of a task management tool?

Three reasons:

1. **Git-native.** Task files are diffable, versionable, and part of the same commit history as the code they relate to. A code commit and its associated task completion can be atomically linked.
2. **AI-readable.** Markdown files with structured frontmatter are trivially parseable by any AI model without special tooling.
3. **No external dependency.** Phase 1 has no infrastructure beyond a filesystem and Git. Adding a task management API or database would add operational complexity before the core system is proven.

When the task volume and querying needs grow beyond what the file-based system handles well, a lightweight database (SQLite) will be introduced. The task interface will not change.

### Why not use an existing issue tracker?

MondayOS needs AI agents to have programmatic, low-latency read/write access to the task system. Existing issue trackers (GitHub Issues, Linear, Jira) have API rate limits, authentication complexity, and data models that do not match the MondayOS task schema. Building on files first gives us full control and defers the integration complexity to Phase 2 when it is clearly needed.

### Why archive instead of delete?

The task archive is a record of everything MondayOS has done. A cancelled task that was cancelled for an interesting reason is a signal worth preserving. Completed tasks are sources of knowledge entries. Deletion destroys information that has no cost to keep.
