# MondayOS — Architecture Diagram

**Version:** 1.0.0b1 (Beta)
**Last updated:** 2026-06-28

This document is the visual companion to [ARCHITECTURE.md](ARCHITECTURE.md). It
shows the *as-built* component structure, how data flows through an execution,
and the dependency rules that hold the system together.

---

## 1. System overview

Every external caller — the CLI or a Python integration — talks to exactly one
class: `Monday`. Internal subsystems are private implementation detail.

```
   ┌───────────────┐        ┌────────────────────────┐
   │   monday CLI  │        │  Python: from monday   │
   │  (monday/cli) │        │       import Monday     │
   └───────┬───────┘        └───────────┬────────────┘
           │                            │
           └──────────────┬─────────────┘
                          ▼
            ╔═════════════════════════════╗
            ║      Monday (public API)     ║   the ONLY public surface
            ║  ask · learn · search · task ║   returns typed *Response objects
            ║  workflow · migrate · doctor ║
            ║  advise · project · onboard  ║
            ║  execute · status            ║
            ╚══════════════┬══════════════╝
                           │  composes & coordinates
   ┌───────────┬───────────┼────────────┬───────────┬────────────┐
   ▼           ▼           ▼            ▼           ▼            ▼
┌────────┐ ┌────────┐ ┌─────────┐ ┌────────┐ ┌────────┐ ┌────────────┐
│knowledge│ │ tasks  │ │workflows│ │ doctor │ │advisor │ │orchestrator│
│ store   │ │manager │ │ engine  │ │inspector│ │ engine │ │  executor  │
└────┬────┘ └───┬────┘ └────┬────┘ └───┬────┘ └───┬────┘ └─────┬──────┘
     │          │           │          │          │            │
     ▼          ▼           ▼          ▼          ▼            ▼
┌────────┐ ┌────────┐ ┌─────────┐ ┌────────┐ ┌──────────────────────┐
│ migrate│ │ memory │ │ search  │ │  brain  │ │   brain.providers    │
│ engine │ │session │ │ engine  │ │reasoner│ │  (AIProvider ABC)     │
└────────┘ └────────┘ └─────────┘ │ router │ └──────────┬───────────┘
                                  └────────┘            │
                                              ┌─────────┼──────────┐
                                              ▼         ▼          ▼
                                          Anthropic   OpenAI    Ollama
                                           (Claude)   (GPT-*)   (local)

   ┌──────────────────────── events (EventBus, audit) ─────────────────────────┐
   │  TASK_* · KNOWLEDGE_* · MODEL_CALL_* · APPROVAL_*  published by subsystems  │
   └────────────────────────────────────────────────────────────────────────────┘

   ┌──────────────────────────── core (shared types) ──────────────────────────┐
   │        Storage: Git + Markdown/JSON on the filesystem — no database         │
   └────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Subsystems at a glance

| Package | Role | Key public entry (via `Monday`) |
|---|---|---|
| `monday` | Public API + CLI | _the surface itself_ |
| `core` | Shared types (`EntityId`, `Timestamp`) | — |
| `events` | Synchronous audit event bus | _internal_ |
| `knowledge` | Typed knowledge store (Markdown) | `learn`, `search` |
| `memory` | Per-session working memory | _internal_ |
| `search` | Keyword search over sources | `search` |
| `tasks` | Task lifecycle + audit trail | `task` |
| `brain` | Reasoning, model routing, **providers** | `ask` (+ provider layer) |
| `workflows` | Declarative YAML workflow engine | `workflow` |
| `migrate` | Import existing docs → knowledge | `migrate` |
| `doctor` | Repository health analyzers | `doctor` |
| `advisor` | Engineering advisory synthesis | `advise` |
| `orchestrator` | Execution pipeline across providers | `execute` |

---

## 3. Data flow — `monday execute TASK-0001`

The orchestrator coordinates; it never implements a model. The full pipeline:

```
   monday execute TASK-0001
            │
            ▼
   ┌─────────────────────┐
   │ Monday.execute()    │  parses mode + policy, assembles providers
   └─────────┬───────────┘
             ▼
   ┌─────────────────────┐
   │ 1. Load task        │  Monday.task("get")  → must exist, not terminal
   └─────────┬───────────┘
             ▼
   ┌─────────────────────┐
   │ 2. Advisor          │  Monday.advise()  (best-effort prioritisation)
   └─────────┬───────────┘
             ▼
   ┌─────────────────────┐
   │ 3. Planner          │  ExecutionPlanner → deterministic ExecutionPlan
   │                     │  (prompt + steps + context); no model call
   └─────────┬───────────┘
             ▼
   ┌─────────────────────┐
   │ 4. Execution queue  │  ExecutionQueue (priority P0→P3, FIFO ties)
   └─────────┬───────────┘
             ▼
   ┌─────────────────────┐
   │ 5. Provider select  │  policy: prefer-local | lowest-cost |
   │                     │  highest-capability | manual
   └─────────┬───────────┘
             ▼
        ┌────────────── mode gate ──────────────┐
        │ DRY_RUN → stop here (plan only)        │
        │ AUTONOMOUS w/o enable → BLOCKED         │
        │ no provider → SKIPPED                   │
        └───────────────────┬────────────────────┘
                            ▼
   ┌─────────────────────┐
   │ 6. Provider executes│  AIProvider.ask()  ── MODEL_CALL_STARTED/COMPLETED
   │                     │  (through the abstraction ONLY)
   └─────────┬───────────┘
             ▼
   ┌─────────────────────┐
   │ 7. Validation       │  ResultValidator → deterministic checks + score
   │                     │  (fail → no capture, no task update)
   └─────────┬───────────┘
             ▼
   ┌─────────────────────┐
   │ 8. Knowledge capture│  Monday.learn()  → research entry (e.g. RES-0001)
   └─────────┬───────────┘
             ▼
   ┌─────────────────────┐
   │ 9. Task update      │  REVIEW → task to REVIEW   |   AUTONOMOUS → COMPLETED
   └─────────┬───────────┘
             ▼
   ┌─────────────────────┐
   │ 10. Execution report│  persisted → logs/executions/{execution_id}.json
   └─────────────────────┘
```

Each numbered stage is a separate, independently tested component
(`orchestrator/planner.py`, `queue.py`, `executor.py`, `validator.py`,
`report.py`).

---

## 4. Provider abstraction (model independence)

Model independence is a hard architectural boundary. No SDK or provider name
appears outside `brain/providers/`.

```
        callers (advisor, orchestrator, …)
                      │  depend only on the interface
                      ▼
            ┌───────────────────────┐
            │   AIProvider (ABC)    │   ask · plan · summarize · review
            │  + selection metadata │   is_local · cost_tier · capability_tier
            └───────────┬───────────┘
                        │  create_provider(ProviderConfig)
        ┌───────────────┼────────────────┐
        ▼               ▼                ▼
┌──────────────┐ ┌─────────────┐ ┌──────────────┐
│AnthropicProv.│ │OpenAIProvider│ │OllamaProvider│
│ anthropic SDK│ │  openai SDK  │ │ HTTP (local) │
└──────────────┘ └─────────────┘ └──────────────┘
```

Swapping providers is a configuration change (`ProviderConfig.type`); no calling
code changes.

---

## 5. Dependency rules

1. **Inward only.** The CLI depends on `monday`; `monday` depends on subsystems;
   subsystems depend on `core`/`events`. Nothing depends back on `monday`.
2. **One public surface.** External code imports `from monday import Monday` and
   the `*Response` types — nothing else.
3. **Provider isolation.** Provider SDKs are reachable only through
   `brain.providers`.
4. **Storage is files.** Every subsystem persists to Markdown/JSON under the
   project root. There is no shared database and no global mutable state.
5. **Events are the audit trail.** Cross-subsystem signalling happens by
   publishing typed events on the `EventBus`, not by direct coupling.

---

For the narrative design rationale, layer responsibilities, and the approval-gate
model, see [ARCHITECTURE.md](ARCHITECTURE.md).
