# MondayOS — Architecture

**Version:** 0.1.0  
**Status:** Draft — Foundation Phase  
**Last Updated:** 2026-06-27

---

## Overview

MondayOS is structured as a layered platform. Each layer has a single, well-defined responsibility. Layers communicate through stable, versioned interfaces. No layer depends on the implementation details of another — only on its interface.

```
┌─────────────────────────────────────────────────────────────┐
│                        Dashboard / UI                       │  ← Human interface, approvals, observability
├─────────────────────────────────────────────────────────────┤
│                      Orchestrator Layer                     │  ← Task routing, agent coordination
├──────────────────────────┬──────────────────────────────────┤
│      Task System         │       Workflow Engine            │  ← What to do / how to do it
├──────────────────────────┴──────────────────────────────────┤
│                       Core Engine                           │  ← Execution, logging, approval gates
├──────────────┬────────────────────────┬─────────────────────┤
│  Memory Layer│   Knowledge Layer      │   Integration Layer │  ← Persistence, learning, external models
├──────────────┴────────────────────────┴─────────────────────┤
│                   Storage & Config                          │  ← Git, filesystem, config files
└─────────────────────────────────────────────────────────────┘
```

---

## Layers

### 1. Storage & Config Layer

**Responsibility:** Durable storage and system configuration.

**Components:**
- **Git repository** — source of truth for all code, prompts, workflow definitions, and versioned knowledge entries
- **Filesystem** — local file storage for logs, memory snapshots, and working artifacts
- **Config files** — environment-specific settings (YAML/TOML), never containing secrets

**Design Decisions:**
- Git is used as the primary persistence mechanism for anything that must survive process restarts. This gives us versioning, diff history, and rollback for free.
- Configuration is split: static config lives in files under version control; secrets are injected via environment variables and never written to disk by MondayOS.
- No database is introduced in Phase 1. Structured JSON/Markdown files in Git are sufficient and simpler to audit. A database backend (SQLite, then Postgres) is deferred to Phase 2 when query performance becomes a real bottleneck.

---

### 2. Integration Layer

**Responsibility:** Abstract all communication with external AI models and third-party tools.

**Components:**
- `integrations/claude/` — Anthropic Claude API client (claude-opus-4-8, claude-sonnet-4-6, claude-haiku-4-5)
- `integrations/openai/` — OpenAI API client (GPT-4o, GPT-4o-mini)
- `integrations/ollama/` — Local Ollama model client (Llama 3, Mistral, Phi-3, etc.)
- `integrations/tools/` — Filesystem, shell execution, web search, and other tool implementations

**Design Decisions:**
- Every external model is accessed through a common `ModelClient` interface. The orchestrator never imports a provider SDK directly — it calls the interface. This makes provider swapping transparent.
- All outbound API calls are logged with: model name, prompt token count, completion token count, latency, cost estimate, and response SHA. No AI call is invisible.
- Rate limiting, retry logic, and error normalization are handled inside the integration layer. The layers above see only success or typed failures.
- Tool implementations follow the same interface regardless of whether they are local filesystem operations or remote API calls.

**Model Selection Heuristic (Phase 1):**

| Task Type | Default Model | Reasoning |
|---|---|---|
| Complex reasoning, code review | claude-opus-4-8 | Highest quality for consequential decisions |
| General coding, summarization | claude-sonnet-4-6 | Best cost/quality ratio for routine work |
| Fast classification, routing | claude-haiku-4-5 | Lowest latency for simple decisions |
| Structured data extraction | gpt-4o | Strong at constrained JSON output |
| Privacy-sensitive, offline | ollama/llama3 | No data leaves the machine |

These defaults are configurable per-workflow and per-task.

---

### 3. Memory Layer

**Responsibility:** Persist and retrieve state across agent sessions and task executions.

See [MEMORY_SYSTEM.md](MEMORY_SYSTEM.md) for full design.

**Components:**
- `memory/session/` — per-session working context (volatile, cleared on session end)
- `memory/project/` — cross-session project state (persistent, Git-tracked)
- `memory/agent/` — per-agent persistent context (what this agent has learned)

**Design Decisions:**
- Memory is stored as structured Markdown files with YAML frontmatter. This keeps memory human-readable, diffable, and auditable in Git without requiring a separate database.
- Session memory is isolated from project memory. Agents can access and write both, but the storage backends are different.
- Memory reads are always explicit — agents do not automatically receive all memory. They request specific memory by key or query. This prevents context window bloat and makes memory access auditable.

---

### 4. Knowledge Layer

**Responsibility:** Accumulate, index, and retrieve engineering knowledge across the lifetime of the project.

See [KNOWLEDGE_SYSTEM.md](KNOWLEDGE_SYSTEM.md) for full design.

**Components:**
- `knowledge/decisions/` — Architectural Decision Records (ADRs)
- `knowledge/bugs/` — bug knowledge entries (symptom → cause → resolution → prevention)
- `knowledge/patterns/` — reusable solutions to recurring engineering problems
- `knowledge/runbooks/` — operational procedures
- `knowledge/index.md` — searchable index of all knowledge entries

**Design Decisions:**
- All knowledge is stored as structured Markdown. It is readable by humans without any tooling.
- Knowledge entries are immutable once written. Updates create new entries that supersede old ones. This preserves history and prevents silent loss of context.
- The index is regenerated deterministically from the knowledge files, not maintained by hand.

---

### 5. Task System

**Responsibility:** Define, assign, track, and complete units of engineering work.

See [TASK_SYSTEM.md](TASK_SYSTEM.md) for full design.

**Components:**
- `tasks/active/` — tasks currently in progress
- `tasks/completed/` — archived completed tasks
- `tasks/backlog/` — queued tasks awaiting assignment
- `tasks/blocked/` — tasks awaiting human input or external resolution

**Design Decisions:**
- Tasks are stored as structured Markdown files. The file is the task — there is no separate task database in Phase 1.
- Every task has: a unique ID, a human-readable title, a detailed description, an assigned agent or human, a status, a priority, a parent task (optional), a list of child tasks (optional), creation timestamp, and a log of all state transitions.
- Tasks are never deleted — they are archived. The complete task history is auditable.

---

### 6. Workflow Engine

**Responsibility:** Execute predefined multi-step sequences of agent actions.

**Components:**
- `workflows/definitions/` — YAML workflow definition files
- `workflows/engine/` — execution runtime that processes workflow definitions
- `workflows/templates/` — reusable workflow fragments

**Design Decisions:**
- Workflows are declared, not coded. A workflow definition describes what should happen; the engine decides how.
- Every workflow step declares: the action to take, the model to use (or `auto` for routing), the input schema, the output schema, and the approval requirement level (`none`, `human-review`, `human-approval`).
- Workflows are versioned. Running a workflow always records which version was used.

---

### 7. Core Engine

**Responsibility:** Execute agent actions, enforce approval gates, emit structured logs, and coordinate between all layers.

**Components:**
- `core/executor.py` — agent action executor
- `core/gates.py` — human approval gate enforcement
- `core/logger.py` — structured logging to `logs/`
- `core/events.py` — internal event bus for inter-component communication
- `core/errors.py` — typed error hierarchy

**Design Decisions:**
- The core engine is the only place where approval gates are evaluated. No other component can bypass them.
- All events flowing through the engine are logged before and after execution. Failures are logged with full context before propagation.
- The event bus is synchronous in Phase 1 (function calls). Async message passing is deferred to Phase 2 when concurrent task execution is needed.

**Approval Gate Levels:**

| Level | Description | Examples |
|---|---|---|
| `none` | Autonomous execution, no human involved | Reading a file, writing to knowledge base |
| `human-review` | Human is notified after action completes | Writing code to a branch, creating a task |
| `human-approval` | Human must approve before action executes | Merging to main, deploying to production, sending external messages |

---

### 8. Orchestrator Layer

**Responsibility:** Receive task requests, select the appropriate agent and model, delegate execution, and return results.

**Components:**
- `orchestrator/router.py` — task-to-agent-to-model routing logic
- `orchestrator/agents.py` — agent registry and lifecycle management
- `orchestrator/scheduler.py` — task queue and priority management

**Design Decisions:**
- The orchestrator is the single entry point for all task execution. Nothing bypasses it.
- Routing decisions are logged with reasoning. If a task is routed to GPT-4 instead of Claude, the log explains why (cost, capability, privacy requirement, etc.).
- The orchestrator does not contain business logic. It knows *who* should do work; it does not know *how* to do the work.

---

### 9. Dashboard / UI Layer

**Responsibility:** Provide human visibility into system state and a surface for approvals, monitoring, and task injection.

**Components:**
- `dashboard/` — web interface (Phase 2; Phase 1 uses CLI + log inspection)
- Human approval interface (email/Slack notification → approval link in Phase 1)

**Design Decisions:**
- The dashboard is read-heavy and read-only for the first iteration. Humans observe and approve; they do not manage state through the UI.
- Approval actions are themselves logged as first-class events.

---

## Data Flow: Task Execution

```
Human or Agent submits Task
        │
        ▼
Orchestrator receives Task
        │
        ├── Validates task schema
        ├── Checks for duplicate/similar existing task
        ├── Queries Memory for relevant context
        ├── Queries Knowledge for related prior resolutions
        └── Selects Agent + Model based on routing rules
        │
        ▼
Core Engine executes Task
        │
        ├── Logs: task_start event
        ├── Checks approval gate level
        │       ├── human-approval → notify human, pause execution
        │       └── none / human-review → proceed
        ├── Calls Integration Layer (model API)
        ├── Logs: model call (tokens, latency, cost)
        ├── Receives model output
        ├── Applies output (code write, knowledge entry, etc.)
        ├── Logs: task_complete event
        └── Writes result to Memory + Task record
        │
        ▼
Result returned to caller
        │
        ├── If task produced knowledge: write to Knowledge Layer
        └── If task produced code: create Git commit with audit metadata
```

---

## Cross-Cutting Concerns

### Logging

All log entries are structured JSON. Every entry includes:
- `timestamp` (ISO 8601)
- `event_type` (enum)
- `component` (which layer/module emitted it)
- `task_id` (if associated with a task)
- `agent_id` (if associated with an agent action)
- `model` (if an AI model was used)
- `reasoning` (free-text human-readable explanation)
- `severity` (DEBUG / INFO / WARN / ERROR)

### Security

- Secrets are never stored in files managed by MondayOS. They are loaded from environment variables at runtime.
- All external API calls go through the Integration Layer, which enforces TLS.
- Human approval gates prevent autonomous action on production-impacting operations.
- Log files contain reasoning traces, which may include sensitive project information — access control on the `logs/` directory is the operator's responsibility.

### Error Handling

- Typed errors are defined in `core/errors.py`. Every error has a code, a human-readable message, and a recoverable flag.
- Recoverable errors trigger retry logic in the Integration Layer.
- Non-recoverable errors surface to the Orchestrator, which logs them and marks the task as blocked.
- No error is swallowed silently. Errors without a handler cause the operation to fail loudly rather than proceed incorrectly.

### Testing

- Unit tests cover all business logic in Core, Memory, Knowledge, and Task layers.
- Integration tests cover all Integration Layer clients (using recorded API responses for determinism).
- End-to-end tests cover critical task execution paths.
- No code merges to `main` without passing tests.

---

## Architectural Decision Log

All significant architectural decisions are recorded in [DECISIONS.md](DECISIONS.md) using the ADR format. The decisions in this document are summaries; the ADR contains the full context, alternatives considered, and rationale.
