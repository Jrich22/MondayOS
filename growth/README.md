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
- Campaigns: the planning object between a project and its content, with its own lifecycle.
- The Content Library: a query layer over content that already exists, never a second copy.
- Growth onboarding, which marks a project ready to be *planned for* and explicitly not ready to
  publish for real.
- Performance events and the deterministic analytics computed from them: per-content, per-campaign,
  per-platform and whole-workspace aggregation, time series, trends, funnels and snapshots.
- The Growth Brain (`growth/brain/`): deterministic reasoning over those measurements —
  opportunities, evidence-backed recommendations, marketing memory, experiments, forecasts and
  health scores.
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
| `Campaign` / `CampaignStatus` | The planning object and its lifecycle |
| `ContentLibrary` / `LibraryEntry` | Query and projection over stored content |
| `ContentType` | What kind of artifact an item is (library metadata) |
| `Onboarding` / `PlatformIntent` / `WeeklyReview` | Growth onboarding; account LABELS only |
| `seed_workspace` / `is_synthetic` | Deterministic demo data, marked synthetic at rest |
| `PerformanceEvent` / `EventStore` / `EventSource` | Measured observations, append-only per project |
| `compute_all` / `MetricValue` | Metric formulas as pure functions |
| `GrowthAnalytics` / `Snapshot` / `TrendResult` | Aggregation, time series, trends, funnels |
| `GrowthBrain` | Deterministic reasoning over one workspace |
| `Observation` / `Hypothesis` / `Recommendation` / `ConfirmedLearning` | The four record kinds |
| `MarketingMemory` / `MemoryEntry` | Project-scoped learnings, tentative until confirmed |
| `Experiment` / `evaluate_result` | The only route from hypothesis to fact |
| `Forecast` / `Score` | Rule-based projections and reproducible health scores |
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
    ├── campaigns/CAMPAIGN-NNNN.md one Campaign
    ├── content/CONTENT-NNNN.md   one Content Item, including its publication record
    ├── pauses.json               project / platform / post pauses for THIS project
    ├── events/events.jsonl       append-only performance observations
    ├── snapshots/SNAPSHOT-N.json point-in-time metric captures
    ├── memory/memory.jsonl       marketing memory, append-only with revision history
    ├── aggregates.json           the ONLY file a portfolio view may read
    └── .sequences.json           CONTENT- id allocation, scoped to THIS workspace

logs/growth/<slug>/audit.jsonl    append-only audit trail (gitignored runtime state)
```

The publication record lives on the content item, not in a side table, so an item marked Published
always carries the external id that proves it — even in a fresh clone where the logs are absent.

Two rules keep the library and the approval contract from interfering with each other:

- **Library metadata is never fingerprinted.** `content_type`, `title`, `themes`, `audience`,
  `variant_group_id`, `reuse_eligible` and `last_reused_at` sit outside `canonical_payload`, so
  cataloguing an item can never invalidate a standing approval. A test asserts the hash is
  byte-identical before and after all of them are populated.
- **Per-platform variants are separate items** sharing a `variant_group_id`, never one item with
  many captions. Approval binds one platform, one account and one copy (ADR-013), so approving the
  LinkedIn variant must not approve the Instagram one — and a test asserts it does not.

`Marketing.campaigns` on the workspace remains a descriptive list of campaign *labels*. Real
`Campaign` records are authoritative; that field is not a foreign key and is not read as one.

Three rules govern analytics, and every one of them exists because the Growth Brain will treat this
layer as ground truth:

- **No metric is stored, only computed.** Metrics are pure functions of the events in one workspace,
  so a number can always be traced to the observations behind it and can never drift from them.
- **A rate with no denominator is `None`, never `0.0`.** Zero is a measurement; reporting it for
  "not measured" would let a project look like it is failing when nobody has looked.
- **Provenance survives aggregation.** No platform adapter exists, so `record()` refuses
  `source=platform` outright, and any metric touching a synthetic or imported event stays flagged
  synthetic all the way out to the CLI.

The Brain adds three more, and they are the reason it can be trusted at all:

- **Four record kinds, never conflated.** An `Observation` is a computed fact; a `Hypothesis` is a
  candidate explanation that always renders with an unconfirmed marker; a `Recommendation` is an
  action backed by evidence and a falsifier; a `ConfirmedLearning` is a hypothesis an experiment
  upheld. Below the minimum sample the engine returns a `Hypothesis` — a different class, not a
  softer wording.
- **Evidence and a falsifier are enforced by the type.** A `Recommendation` constructed without
  either raises. A recommendation without evidence is an opinion; one without a falsifier can
  never be retired on the facts.
- **Nothing is quantified without data.** An unmeasurable upside reports "not quantified" and the
  reason, never a plausible number — an invented figure outlives everyone's memory of inventing it.

`growth/brain/` calls no model, opens no socket, and reads only the workspace it was opened for.

The sequence file is per workspace on purpose. A shared counter would let one project infer
another's publishing volume from the gaps in its own ids.

## Configuration

No configuration of its own. It reads `MondayConfig.project_root` through `Monday`, and resolves
platform credentials from environment variables named by each binding's `secret_name` — the
credential itself is never read into a workspace file, a log, or an agent prompt.
