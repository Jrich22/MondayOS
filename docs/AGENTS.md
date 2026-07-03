# MondayOS Multi-Agent Runtime

The Multi-Agent Runtime lets MondayOS route work to **roles** — CPO, Lead
Engineer, QA, Security, Research, Reviewer — instead of to a specific person or
model. A role is resolved to a registered **agent** (a name bound to a provider),
the run is governed by a **review-required approval gate**, execution is delegated
to the existing Execution Orchestrator, and every run is logged as a reviewable
record.

> **MondayOS remains the system of record.** Agents do work, but MondayOS owns the
> tasks, knowledge, logs, approvals, and truth. Agents cannot commit, push, touch
> secrets, or live-trade without explicit human approval. Autonomous live
> execution is intentionally not implemented — see [APPROVAL_GATES.md](APPROVAL_GATES.md).

## How it fits together

```
Task ──assign──► Role ──registry──► Agent (name + provider)
                  │
   monday agent run TASK --role lead-engineer
                  │  resolve role → agent → AIProvider
                  ▼
        ApprovalGate  (review-required; blocks gated actions / autonomous
                        completion without approval)
                  ▼
        ExecutionOrchestrator  (Monday.execute, REVIEW mode by default:
                        plan → provider → validate → capture knowledge →
                        move task to REVIEW)
                  ▼
        AgentRun  →  logs/agents/run-*.json   (logged + reviewable)
```

The runtime adds **no** provider-specific code. It builds providers through
`brain.providers.factory` and talks only to the `AIProvider` abstraction, so it
reuses the same orchestrator, provider adapters, and safety modes that back
`monday execute`.

## Components (`agents/`)

| File | Responsibility |
|---|---|
| `roles.py` | The six roles as pure data (`ROLES`), provider defaults, `GATED_ACTIONS`. Adding a role is one entry. |
| `registry.py` | `AgentRegistry` — agents persisted as `agents/active/AGENT-*.md` (+ `.sequences.json`), mirroring `tasks/`. Seeds the six defaults. |
| `gates.py` | `ApprovalGate` — the review-required policy and gated-action enforcement. |
| `adapters.py` | Role/agent → `AIProvider`; the offline `FakeAgentProvider` (`fake` provider) test harness. |
| `runtime.py` | `AgentRuntime` — list / register / assign / run / review / history; delegates execution to `Monday.execute`. |
| `types.py` | `Agent` (registry entry) and `AgentRun` (logged run record). |

## Storage

Runtime data lives under the **project root** (like `tasks/` and `logs/`):

- `agents/active/AGENT-XXXX.md` — one Markdown+YAML-frontmatter file per agent.
- `agents/.sequences.json` — `AGENT-` id allocation.
- `logs/agents/run-*.json` — one JSON record per run (the audit trail).

All three are gitignored as runtime state.

## CLI

```bash
monday agent list [--role ROLE]
monday agent register --name NAME --role ROLE [--provider P] [--default] [--description TEXT]
monday agent assign TASK-ID --role ROLE [--assigned-by WHO]
monday agent run TASK-ID --role ROLE [--provider P] [--policy POLICY]
    [--mode dry-run|review|autonomous] [--dry-run] [--autonomous]
    [--enable-autonomous] [--approve] [--action ACTION ...] [--json]
monday agent review RUN-ID (--approve | --reject) [--by WHO] [--note TEXT]
monday agent history [--role ROLE] [--task TASK-ID] [--limit N]
```

`--provider fake` runs the deterministic offline harness — useful for demos and
CI with no API keys configured.

### Typical loop

```bash
monday agent assign TASK-0001 --role lead-engineer
monday agent run    TASK-0001 --role lead-engineer      # executes, task → REVIEW
monday agent review run-abc123 --approve                # human sign-off → task completed
monday agent history --task TASK-0001
```

## Public API

```python
from monday import Monday, MondayConfig
m = Monday(MondayConfig(project_root="."))

m.agent("list")
m.agent("register", name="Claude Code", role="lead-engineer")
m.agent("assign", task_id="TASK-0001", role="qa")
run = m.agent("run", task_id="TASK-0001", role="lead-engineer")   # AgentResponse
m.agent("review", run_id=run.run_id, approve=True)
m.agent("history", role="qa")
```

Every call returns an `AgentResponse`; the action-specific payload is in `.data`.

## Extending it

- **Add an agent:** `monday agent register …` — no code change.
- **Add a role:** one entry in `agents/roles.py::ROLES` — no runtime change.
- **Add a provider:** implement `AIProvider` in `brain/providers/` and register it
  in the factory; roles/agents reference it by name.

See [AGENT_ROLES.md](AGENT_ROLES.md) for the roles and [APPROVAL_GATES.md](APPROVAL_GATES.md)
for the safety model.
