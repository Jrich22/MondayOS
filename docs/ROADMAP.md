# MondayOS — Roadmap

**Version:** 0.1.0  
**Status:** Active  
**Last Updated:** 2026-06-27

---

## How This Roadmap Works

This roadmap is organized into phases, not calendar dates. Phases have clear entry and exit criteria. A phase is not complete when time runs out — it is complete when its exit criteria are met. Date estimates are included for planning purposes but are secondary to criteria.

Phases are designed to be independently valuable: at the end of each phase, MondayOS is a useful, deployable system — not a half-built project awaiting a future milestone.

---

## Current Status: Foundation (Pre-Phase 1)

The engineering foundation is being established:
- Core documentation written
- Directory structure defined
- Engineering standards adopted
- No production code yet

**Exit Criteria for Foundation Phase:**
- [ ] All nine foundation documents complete and reviewed
- [ ] Directory structure finalized with module READMEs
- [ ] `pyproject.toml` with tooling configured (Ruff, Mypy, pytest)
- [ ] First ADR written (capturing Phase 1 technology choices)
- [ ] Git repository initialized with `.gitignore` and pre-commit hooks

---

## Phase 1 — Single-User Local System

**Goal:** A single engineer can use MondayOS locally to coordinate AI agents on software tasks, with every action logged and every decision captured.

**Target completion:** ~3 months after foundation

### Phase 1 Milestones

#### 1.1 — Core Engine and Integration Layer

**Deliverables:**
- `core/` module with executor, gates, logger, events, and errors
- `integrations/claude/` — working Claude API client (Sonnet + Opus + Haiku)
- `integrations/openai/` — working OpenAI API client (GPT-4o)
- `integrations/ollama/` — working Ollama client (local models)
- Common `ModelClient` interface for all providers
- Structured JSON logging to `logs/`
- Unit tests for all core modules (≥90% coverage)

**Exit Criteria:**
- [ ] A single agent can be invoked with a task prompt and return a result
- [ ] All model calls are logged with token count, cost estimate, and latency
- [ ] Provider can be swapped without changing calling code
- [ ] Tests pass; coverage targets met

---

#### 1.2 — Task System

**Deliverables:**
- Task file schema and validation
- CLI commands: `task create`, `task list`, `task update`, `task complete`
- Task routing: assign tasks to Claude, GPT-4, or Ollama based on type and configuration
- Task archive: completed tasks moved to `tasks/completed/`
- Task index: auto-generated `tasks/active/index.md`

**Exit Criteria:**
- [ ] Tasks can be created by humans via CLI
- [ ] Tasks can be created by AI agents programmatically
- [ ] Task status lifecycle is enforced
- [ ] All status transitions are logged
- [ ] Task file format matches schema in TASK_SYSTEM.md

---

#### 1.3 — Memory System

**Deliverables:**
- Session memory (in-process, with debug write option)
- Project memory (file-based, Git-tracked)
- Memory read/write interface
- Session continuity (checkpoint and resume on crash)
- Memory expiry mechanism

**Exit Criteria:**
- [ ] Project state persists across agent sessions
- [ ] An interrupted session can be resumed from checkpoint
- [ ] Memory reads and writes are logged
- [ ] Expired entries are excluded from normal reads but remain accessible

---

#### 1.4 — Knowledge System

**Deliverables:**
- Knowledge entry schema and validation (bug, decision, pattern, runbook)
- CLI commands: `knowledge add`, `knowledge search`, `knowledge show`
- Index auto-generation from entry files
- Agent-authored entry workflow (write → human review → publish)
- Integration with task system (completed tasks prompt knowledge entry creation)

**Exit Criteria:**
- [ ] Knowledge entries can be created by humans and AI agents
- [ ] Index is automatically rebuilt after any entry change
- [ ] Agents query the knowledge base before starting a task
- [ ] At least 5 real knowledge entries created from Phase 1.1–1.3 work

---

#### 1.5 — Orchestrator

**Deliverables:**
- Task routing logic (model selection based on type, privacy flag, cost configuration)
- Agent registry (named agents with capability profiles)
- Basic task queue (FIFO with priority override)
- Approval gate enforcement (all three levels: none, review, approval)
- Human notification for approval-required tasks (CLI prompt in Phase 1)

**Exit Criteria:**
- [ ] Orchestrator routes tasks to the correct model with logged reasoning
- [ ] Approval gates block execution until human input is provided
- [ ] Human approval is logged as a first-class event
- [ ] Task queue respects priority ordering

---

#### 1.6 — End-to-End Workflow

**Deliverables:**
- At least one complete predefined workflow (e.g., "implement a function: research → write → test → document → review")
- Workflow definition schema (YAML)
- Workflow execution logs

**Exit Criteria:**
- [ ] Workflow executes successfully end-to-end without human intervention beyond initial trigger
- [ ] All workflow steps are logged
- [ ] Workflow version is recorded in the execution log
- [ ] Human approval gate fires correctly at the appropriate workflow step

---

### Phase 1 Exit Criteria (All Must Pass)

- [ ] All Phase 1 milestones complete
- [ ] Integration tests cover all integration layer clients
- [ ] End-to-end test covers the complete task lifecycle
- [ ] `main` branch is deployable (no known blocking bugs)
- [ ] Knowledge base has ≥10 entries accumulated from Phase 1 work
- [ ] CHANGELOG.md documents all Phase 1 changes
- [ ] All ADRs for Phase 1 decisions are written

---

## Phase 2 — Multi-Session, Multi-Agent Collaboration

**Goal:** Multiple agents can work concurrently on related tasks, sharing memory and knowledge, with humans observing and directing through a simple UI.

**Prerequisite:** Phase 1 exit criteria all met.

### Phase 2 Scope

- **Async task execution:** Multiple tasks run concurrently without blocking each other. Tasks that are independent execute in parallel; dependent tasks wait for their prerequisites.
- **Inter-agent communication:** Agents can pass context, questions, and partial results to each other through the task system.
- **Dashboard (v1):** Web interface for viewing task status, reviewing AI-generated work, and approving pending gates. Read-heavy; approval actions enabled.
- **SQLite backend (optional):** If file-based storage becomes a query bottleneck, introduce a SQLite backend behind the existing memory and task interfaces.
- **Ollama workflow integration:** Full workflows that use local models for privacy-sensitive steps without requiring cloud API calls.
- **Knowledge quality metrics:** Track how often knowledge entries are retrieved and applied; surface low-quality or stale entries for review.
- **Agent memory (per-agent persistence):** Named agents accumulate a history of tasks completed and feedback received; routing takes agent track record into account.

### Phase 2 Exit Criteria

- [ ] At least two agents can work on separate tasks concurrently without corrupting shared state
- [ ] Dashboard displays real-time task status
- [ ] Human approval actions can be taken through the dashboard (not just CLI)
- [ ] Agent routing accounts for agent-specific track record
- [ ] Knowledge retrieval hit rate is measured and reported

---

## Phase 3 — Team-Scale Deployment

**Goal:** A team of 2–10 engineers can share a MondayOS instance, with agents as participating team members.

**Prerequisite:** Phase 2 exit criteria all met.

### Phase 3 Scope

- **Multi-user support:** Role-based access; different engineers can have different permission levels for task creation, agent oversight, and knowledge base editing.
- **Shared knowledge base:** Knowledge accumulated by all agents on the team is visible to all agents and engineers.
- **External integrations:** GitHub PR creation and review, Slack notifications for approval gates, Linear/Jira task sync (bidirectional).
- **Workflow marketplace:** Community-contributed workflow definitions that can be imported and used.
- **Audit and compliance exports:** Full audit trail exported in structured format for security and compliance review.
- **Self-improvement loop:** MondayOS analyzes its own task history to identify recurring patterns and suggest knowledge entries that have not been written.

---

## Phase 4 — Enterprise and Autonomous Operations

**Goal:** Large organizations can deploy MondayOS as a managed engineering platform, with AI agents operating autonomously on multi-week projects.

**Prerequisite:** Phase 3 exit criteria met; product validated with real teams.

### Phase 4 Scope

- **Enterprise deployment:** Kubernetes-native deployment, multi-tenant isolation, SSO integration.
- **Expanded model support:** Integration with any OpenAI-compatible endpoint, Anthropic Bedrock, Azure OpenAI.
- **Autonomous long-horizon tasks:** Agents plan and execute multi-day tasks, creating subtasks as needed, surfacing blockers proactively, and resuming after interruption.
- **Proactive knowledge synthesis:** Agents periodically review the knowledge base and surface connections, contradictions, and gaps for human review.
- **SLA and cost management:** Per-team budgets for model usage; automatic model downgrading when cost limits approach.

---

## What Is Deliberately Out of Scope (Across All Phases)

These are things MondayOS will not build, regardless of requests:

| Out of Scope | Reason |
|---|---|
| Training or fine-tuning models | Not our core competency; use providers' fine-tuning APIs if needed |
| Replacing Git | Git is the source of truth; we complement it, not replace it |
| Building a code editor or IDE | We integrate with existing editors through language server protocols |
| Real-time collaborative editing | Document co-editing is an adjacent problem; out of scope |
| Social features (likes, comments for humans) | Not an engineering tool; we have the task system for collaboration |

---

## Revision Policy

This roadmap is a living document. It is updated when:
- A phase is completed
- A phase's exit criteria change meaningfully
- New information makes a phase's scope clearly wrong

Every significant change to the roadmap creates an ADR explaining why the plan changed.
