# growth

The Growth Bot service — project-isolated marketing workspaces, content, and approvals.

## Purpose

The Growth Bot plans, drafts, reviews, and approves marketing content on behalf of a MondayOS
project. It is one service used by many projects: sourcingBOT, Cue, and every project added later
share this engine and share none of their data. The full specification, including the increments
not yet built, is [docs/GROWTH_BOT.md](../docs/GROWTH_BOT.md).

Publishing is implemented against a **deterministic fake connector only**. There is no OAuth, no
real platform adapter, and no browser automation. `integrations/publishing/factory.REAL_ADAPTERS`
is empty, and a test asserts it stays that way until a credential framework exists.

The boundary that matters now is narrower and more important than "nothing can publish":
**nothing reaches a publishing state without passing through Approved**, and
`tests/test_growth_publishing.py` asserts it against the transition graph itself.

## Responsibilities

- Resolving a project name to exactly one Growth Workspace, and refusing anything else (ADR-011).
- Storing a project's business, brand, audience, and marketing state.
- Holding platform credentials **by reference** — a secret's name, never its value.
- Modelling the Content Item, its lifecycle, and the legal transitions between states.
- Computing and comparing approval fingerprints, so an edited item cannot stay approved (ADR-013).
- Running the deterministic publish gate sequence: pause scopes, status, fingerprint, isolation,
  scheduled time, idempotency, then the connector.
- Bounded retries with exponential backoff and jitter, and clock-free idempotency keys.
- Pause controls at post, platform, project, and portfolio-emergency scope.
- An append-only audit trail of every transition and connector attempt.

## What This Module Does NOT Own

- **The human-approval policy.** That is `agents/gates.py::ApprovalGate`. This module registers
  `publish_content` as a gated action and defers the decision (ADR-012).
- **Platform protocols.** How to talk to LinkedIn or X belongs in `integrations/publishing/`,
  behind the `PublishingConnector` interface. This package holds policy, never platform specifics.
- **Reasoning of any kind.** `dispatch.py` contains no model call and makes no content decision.
  A connector that needed to reason would be an agent, and publishing must stay deterministic.
- **The project registry.** Projects are registered through `monday/project.py`; growth reads it.
- **Agent execution.** Roles and providers belong to `agents/`.
- **Learned findings.** Growth Brain output will be knowledge entries in `knowledge/`.

## Public Interface

| Symbol | Description |
|---|---|
| `GrowthService` | The façade behind `Monday.growth()` |
| `GrowthStore` / `WorkspaceHandle` | Persistence; a handle is scoped to one project |
| `Workspace` + `Business` / `Brand` / `Audience` / `Marketing` | Workspace state |
| `PlatformBinding` / `SUPPORTED_PLATFORMS` | Credential references |
| `ContentItem` / `ContentStatus` / `ContentTransition` | The unit of approval |
| `compute_fingerprint` / `FINGERPRINTED_FIELDS` | The approval contract |
| `normalize_project_slug` / `resolve_project` | The isolation gate |
| `PublishDispatcher` / `DispatchResult` | The deterministic publish gate sequence |
| `PublicationRecord` / `PublicationAttempt` | Durable publishing outcome and attempt history |
| `idempotency_key` / `backoff_seconds` | Clock-free dedupe key; bounded retry curve |
| `PauseController` / `PauseState` | Post / platform / project / emergency pause scopes |
| `AuditTrail` / `AuditRecord` | Append-only transition and attempt log |
| `PUBLISH_ACTION` / `publish_action_is_gated` | The gated action name |

## Dependencies

- `monday.project` — the existing project registry; growth does not define a second one.
- `agents.gates` — the approval-gate policy and `GATED_ACTIONS`.
- `core.types` — shared `EntityId` and `Timestamp` aliases.
- `core.redaction` — the shared secret-scrubbing backstop, also used by the dashboard API.
- `integrations.publishing` — the provider-neutral connector interface and the fake adapter.

## Storage

Per-workspace, under the project root, following `tasks/` and `agents/`:

```
growth/
├── emergency_stop.json           portfolio-wide stop: a flag and a reason, no project data
└── workspaces/<slug>/
    ├── workspace.md              business, brand, audience, marketing, bindings
    ├── content/CONTENT-NNNN.md   one Content Item, including its publication record
    ├── pauses.json               project / platform / post pauses for THIS project
    └── .sequences.json           CONTENT- id allocation, scoped to THIS workspace

logs/growth/<slug>/audit.jsonl    append-only audit trail (gitignored runtime state)
```

The publication record lives on the content item, not in a side table, so an item marked Published
always carries the external id that proves it — even in a fresh clone where the logs are absent.

The sequence file is per workspace on purpose. A shared counter would let one project infer
another's publishing volume from the gaps in its own ids.

## Configuration

No configuration of its own. It reads `MondayConfig.project_root` through `Monday`, and resolves
platform credentials from environment variables named by each binding's `secret_name` — the
credential itself is never read into a workspace file, a log, or an agent prompt.
