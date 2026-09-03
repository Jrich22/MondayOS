# MondayOS — Changelog

All notable changes to MondayOS are documented here.

This file follows the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.  
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### MondayOS v3.4 — AI Workspace: Deep Project Intelligence (2026-09-03)

Increment 3 (TASK-0074). Monday can now answer questions *from the project* — what is being
built, why a decision was made, where something is implemented, what changed — and cite the
evidence for every answer.

New `intelligence/` package, an OS-level service beside `knowledge/` and `tasks/`:

- **Project indexer** covering source, tests, documentation, decision records, config and
  prompts, classified by role rather than extension.
- **Symbol index** — classes, functions, methods, interfaces, protocols, dataclasses, enums,
  constants and types — with a file and line range for each definition.
- **Term index** over real file content, with identifiers split on `snake_case` and `camelCase`
  and kept whole, so `render_context` is findable three ways.
- **Relationship graph**: Task → ADR → Code → Test → Commit → PR. Every edge derives from
  something the project wrote down and carries the literal reason; nothing is inferred by
  similarity. ADR ids are namespaced by their decision log, because MondayOS and sourcingBOT
  both define an ADR-017 and citing the wrong one is the failure the evidence trail exists to
  prevent.
- **Question engine** with eight intents, each with its own retrieval strategy.
- **Navigable evidence** — `workspace/context/engine.py lines 42-201`, `ADR-015`, `TASK-0073`,
  `PR #39`. A citation nobody can follow is one nobody verifies.

Two commitments hold throughout. **Deterministic**: no embeddings, no vector database, no model
in the indexing or retrieval path, so identical project state produces identical answers.
**Derived**: the index is a gitignored cache, never a system of record — every fact in it is
recoverable by re-reading the project.

Intelligence reaches conversation through the **existing Context Engine** as one more source, so
budgeting, attribution, redaction and project isolation apply without being reimplemented.
Retrieval runs before the model, so evidence is fixed before any prose is generated.

Conversations now carry their subject, so a follow-up need not restate it. The back-reference
pronoun is the signal — an earlier version relied on a verb stoplist, where one missing word
turned "find every place **it** is used" into a search for "used".

On this repository: 725 files and 5,688 symbols indexed in 924 ms cold, 99 ms warm; graph of 995
nodes and 1,628 edges in 41 ms; questions answered in 1.5 ms mean.

Known limitations, documented rather than hidden: TypeScript symbols are regex-extracted rather
than parsed; test→code links use naming convention rather than import analysis; task→commit
quality depends on task references and commit messages; and retrieval produces grounded evidence
while the configured provider still writes the final prose.

### MondayOS v3.1 — Growth Bot: model-backed content generation (2026-09-02)

Increment 7 (TASK-0067). Adds the generation subsystem on top of the deterministic Growth Brain:
planner, drafting, platform formatters, long-form and brief generators, claim-risk review, the
weekly marketing package and the approval inbox.

The division of responsibility, which nothing in this increment blurs: **the Brain decides what to
say and why** (deterministic, no model); **the writer decides how to phrase it**; **a human approves
the exact output**.

#### Added
- `growth/generation/` — twelve modules including `model_writer` and `claim_risk`.
- `ModelContentWriter` alongside `TemplateContentWriter` behind the existing `ContentWriter`
  protocol. Drafting goes through the MondayOS `AIProvider` abstraction: **no Anthropic or OpenAI
  import anywhere in `growth/`, and no vendor name in `growth/generation/`.** Model routing is
  `MondayConfig.provider_config`; `max_tokens` defaults to 1200 as a configurable initial default,
  not a product rule.
- Claim-risk classification across numeric, testimonial, customer, comparative, legal, medical,
  financial and external-fact shapes, feeding an `enhanced-review` inbox priority.
- Eighteen asset kinds, weekly packages, the approval inbox, and eleven `Monday.growth()` actions
  with matching CLI commands.
- `growth/services/` — `GrowthService` split into domain facades (issue #35), behaviour-preserving
  with no test changes.

#### Integrity
- **Generation mode is explicit.** `mode="model"` requires a configured provider and fails clearly
  without one; `mode="template"` is deterministic and consults no provider. There is no fallback in
  either direction, and omitting the mode is an error — a reviewer must know which path produced
  what they are reading.
- **Structured output, failing closed.** Prose, malformed JSON, missing required fields,
  out-of-schema keys and runaway bodies are refused; no `ContentItem` is created from an
  unvalidated response.
- **Workspace isolation at the prompt.** A test plants confidential copy, a title, a theme and a
  brand voice in a second workspace and asserts none reach the prompt.
- **Safety runs on output, not on the prompt.** A fake provider returning "94% of customers cut
  time-to-hire in half" is blocked, proving the prompt is not the enforcement.
- **Claim risk escalates rather than blocks.** A flagged draft is a normal draft that may reach
  ready-for-review; what it may not do is clear review because the regexes found nothing. This is a
  review escalation layer, not a fact-checking engine, and its own output says so.
- Provenance — campaign, recommendation and experiment ids, generation method, provider and
  generated-at — rides through to the `ContentItem`. Provider identity is **not** part of the
  approval fingerprint: approval is about the exact output, not about which model wrote it.
- Approving a week approves each post individually against its own fingerprint and authorises
  nothing outside the package. Rescheduling resets approval by the existing rule.

#### Unchanged
- The Growth Brain remains deterministic and model-free — a test greps it for provider imports.
- The publishing connector, `REAL_ADAPTERS` (still empty), OAuth (none), account connections
  (none), browser automation (none), and the approval fingerprint contract.

#### Known gaps
- Claim-risk detection is pattern-based and deliberately over-flags. It identifies claim shapes; it
  does not verify claims.

### MondayOS v3.0 — Growth Bot: the Growth Brain (2026-08-30)

Increment 6 (TASK-0066). Implements the deterministic reasoning layer ADR-014 described, now that
increment 5 gave it measured outcomes to reason over. **ADR-014 moves from Proposed to Accepted.**

No model is called. No content is generated. No network is touched. Every recommendation is
assembled from measured numbers by explicit rules with stated thresholds, so the same workspace
state yields byte-identical output — a test asserts exactly that.

#### Added
- `growth/brain/` — nine modules: `models`, `evidence`, `scoring`, `memory`, `forecasting`,
  `opportunity`, `experiments`, `recommendations`, `engine`.
- Four record kinds that are never conflated: Observation, Hypothesis, Recommendation,
  ConfirmedLearning.
- Marketing memory: project-scoped learnings across campaign, content, platform, audience,
  experiment, seasonal, recurring-pattern and historical-recommendation categories.
- Ten opportunity detectors across engagement trend, campaign pacing, platform comparison, theme
  performance, reuse backlog and funnel drop-off.
- Experiments across eight variables, forecasting by linear run-rate, and reproducible health
  scores for workspace, campaign, channel and content.
- Fourteen Brain and memory actions on `Monday.growth()` with matching `monday growth` commands.

#### Integrity
- Below `MIN_SAMPLE_FOR_RECOMMENDATION` the engine returns a `Hypothesis`, never a
  `Recommendation` — a different class, not a softer wording. Hypotheses always serialize with an
  `unconfirmed` marker and a caveat.
- A `Recommendation` constructed without evidence, a falsifier, or a success metric raises.
  Assumptions alone do not count as evidence.
- Unquantifiable upside reports "not quantified" and the reason, never an invented number.
- Memory is born tentative, refuses samples below 3 as anecdote, and cannot be validated below 10.
  Invalidated memory is kept, never deleted, and is never cited.
- An experiment resolves to inconclusive below its minimum sample or below the 10% minimum
  detectable effect, and never names a winner. Starting one that changes published content
  without human approval raises.
- Synthetic evidence caps recommendation priority at P2, so demo data cannot produce a P0.
- A test greps `growth/brain/` for provider and network imports and asserts none exist.

#### Known gaps
- Detector thresholds are stated conventions, not measured benchmarks.
- Forecasts are linear run-rate only, and say so on every projection.


### MondayOS v2.9 — Growth Bot: performance events and analytics (2026-08-27)

Increment 5 (TASK-0065). Adds the measurement layer every future Growth Brain recommendation will
be computed from. Deterministic throughout: no AI reasoning, no recommendations, no experiments.

#### Added
- `growth/events.py` — `PerformanceEvent` across eleven observation types, append-only JSONL per
  workspace, with an explicit `source` of platform / imported / synthetic.
- `growth/metrics.py` — every formula as a pure function with the formula in its docstring.
- `growth/analytics.py` — content, campaign, platform and workspace aggregation; time series;
  trends; conversion funnel; snapshots; and the portfolio aggregate.
- Fourteen analytics actions on `Monday.growth()` and matching `monday growth` commands, with a
  visible synthetic-data banner on every metrics screen.

#### Integrity
- `record()` refuses `source=platform`: `REAL_ADAPTERS` is empty, so no platform has reported
  anything and a fabricated platform event would make every downstream metric a lie that looks
  measured. A test asserts no production path can write one.
- Rates return `None` with a reason where the denominator is empty, never `0.0`. A genuine measured
  zero still reports as zero, and a test distinguishes the two.
- `approval_rate` and `publishing_success_rate` are counted from each item's own `status_history`
  and `publication` record rather than a parallel counter, so they cannot drift from the audit trail.
- The portfolio aggregate carries counts, rates and deltas only. A test plants confidential copy,
  media paths, an audience definition, an account id and a secret name in a workspace and asserts
  none reach the aggregate.
- Event ordinals and snapshots are per workspace; a test asserts project A's analytics read none of
  project B's events.

#### Known gaps
- `audience_growth` needs two snapshots carrying a follower count; follower count is a platform
  gauge, so until adapters exist a caller supplies it.
- No Growth Brain, recommendations, or experiments — increment 6.


### MondayOS v2.8 — Growth Bot: campaigns, content library, onboarding (2026-08-27)

Increment 4 of the Growth Bot product roadmap (TASK-0064). Adds the planning object between a
project and its content, a searchable library over the content that already exists, and growth
onboarding that is explicit about what a project is *not* yet ready for.

#### Added
- `growth/campaign.py` — Campaign with a draft/active/paused/completed/cancelled state machine,
  stored per workspace at `campaigns/CAMPAIGN-NNNN.md` with its own id sequence.
- `growth/library.py` — a query layer over existing content storage. It writes nothing; a test
  asserts file mtimes are unchanged after a full sweep of queries.
- `growth/onboarding.py` — audience, brand voice, platform intents, objectives, cadence, assets,
  prohibited content and the weekly review slot.
- `growth/demo.py` — deterministic synthetic demo data, marked `synthetic` at rest.
- `ContentType` plus non-fingerprinted library metadata on `ContentItem`.
- Campaign, library, onboarding and demo actions on `Monday.growth()` and `monday growth`.

#### Security / integrity
- Onboarding records account **labels** only. A credential-shaped label is refused, and
  `growth_ready_for_real_publishing` is never set True — a test greps `growth/` to prove no code
  path assigns it.
- Library metadata is outside the approval fingerprint; a test asserts the hash is byte-identical
  before and after every new field is populated.
- Content cannot be attached to a campaign in another workspace, or to a closed campaign.

#### Fixed
- `create_content()` never accepted `tags`, though `ContentItem` has carried them since
  increment 2. Tags are now settable at creation.


### MondayOS v2.7 — Growth Bot: publishing connector (2026-08-27)

Adds the deterministic publishing layer for approved Growth content: a provider-neutral connector
interface, the `PublishDispatcher` gate sequence, pause controls, bounded retries, idempotency, and
an append-only audit trail.

**Fake connector only.** No OAuth, no real platform adapter, no browser automation.
`integrations/publishing/factory.REAL_ADAPTERS` is empty and a test keeps it that way.

#### Added
- `integrations/publishing/` — `PublishingConnector` ABC, `PublishRequest` / `PublishOutcome`, the
  transient/permanent failure taxonomy, `FakePublishingConnector`, and `build_connector`.
- `growth/dispatch.py` — the ten-gate publish sequence. No model call, no content decision.
- `growth/publication.py` — publication records, attempt history, clock-free idempotency keys,
  bounded exponential backoff with injectable jitter.
- `growth/pause.py` — post / platform / project pauses plus the portfolio-wide emergency stop.
- `growth/audit.py` — append-only JSONL trail of every transition and connector attempt.
- `core/redaction.py` — the secret-scrubbing backstop, promoted from `dashboard_api/security.py`
  so the dispatcher and the API edge share one implementation.
- Content states `scheduled`, `publishing`, `published`, `failed`; `scheduled_timezone` on items.
- `Monday.growth()` actions: `schedule`, `publish`, `retry`, `publication-status`, `pause`,
  `resume`, `pauses`, with matching `monday growth` commands.

#### Security
- Credentials resolve at call time from the environment and are never stored, logged, echoed in a
  `PublishRequest.redacted()` view, or written to a publication record.
- Connector exceptions pass through `core.redaction` before they reach a stored failure detail or
  an audit line. A test injects a real secret into an exception and sweeps every file under
  `growth/` and `logs/` to prove it does not land.

#### Changed
- `is_approved` still means "approved and unedited". The publish gate now asks the narrower
  `approval_covers_current_content()`, because a Scheduled or Failed item carries a valid approval.
- Editing an item is refused once it is Publishing or Published.
- `dashboard_api.security` re-exports `redact_text` / `redact` from `core.redaction`; no behaviour
  change at the API edge.

#### Known gaps
- No real platform adapters and no OAuth. Next increment.
- No recurring scheduling orchestration; the dispatcher publishes when asked or when due.
- `Monday.publish()` (Confluence) still bypasses `ApprovalGate`. Pre-existing, tracked separately.


### MondayOS v2.6 — Growth Bot: workspaces and content approval (2026-08-26)

Adds the `growth/` service: project-isolated marketing workspaces, the Content Item and its
lifecycle, and approval fingerprinting. Specified in [GROWTH_BOT.md](GROWTH_BOT.md); decisions in
ADR-011, ADR-012, and ADR-013.

**Nothing can publish.** There is no connector, no scheduler, and no lifecycle state beyond
`approved`. That boundary is asserted by tests, not assumed.

#### Added
- `growth/` — Growth Workspace, platform bindings, Content Item, lifecycle, fingerprinting.
- `Monday.growth(action, **kwargs) -> GrowthResponse` and the `monday growth` CLI.
- `publish_content` in `agents.roles.GATED_ACTIONS`: an agent run declaring an intent to publish
  content is blocked without an explicit human approval.
- `tests/test_growth.py` — 54 tests covering isolation, credential handling, fingerprint
  behaviour, the lifecycle, and the no-publish boundary.

#### Security
- Platform credentials are stored **by reference only** — a binding holds the *name* of an
  environment variable, never a credential. A test walks every file under `growth/` asserting no
  credential value appears.
- Project isolation is structural: a project name is validated as a single safe path segment
  before it is joined to a path, and must already exist in the MondayOS project registry.
  Content id sequences are per workspace, so one project cannot infer another's volume.
- Approval is a computed fingerprint comparison, never a stored boolean. Editing an approved
  item's copy, media, destination, account, platform, or schedule revokes the approval
  automatically and is recorded in the item's audit history.

#### Known gaps
- `Monday.publish()` (Confluence) still pushes to an external system without passing through
  `ApprovalGate`. Pre-existing, out of scope here, tracked separately.


### MondayOS v2.5 — Structured Verdict Integrity (2026-08-13)

> **⚠️ BREAKING BEHAVIOUR CHANGE — read before upgrading.**
> Agent runs that previously passed may now stop the pipeline. This is
> intentional: they were passing without ever producing a verdict.

**The defect.** The verdict pipeline failed *open*. A QA, Security, or Reviewer
stage that produced no readable verdict was recorded as having **approved** the
work. Five compounding causes:

1. `agents/verdicts.py` — an absent verdict defaulted to `pass`
   ("ambiguous prose never blocks"). Sound for avoiding false vetoes; backwards
   for a quality gate, where "we could not read an answer" became "approved".
2. `AgentVerdict.verdict` defaulted to `PASS`, so an **empty** provider response
   passed.
3. `normalize_verdict()` mapped an **unrecognised** token to `PASS`, so a
   malformed structured verdict passed.
4. `agents/team.py::_derive_verdict()` defaulted to `PASS` when a run carried no
   verdict at all.
5. `orchestrator/executor.py` capped output at 2048 tokens **and** the verdict
   was requested *last*, so long reviews were cut off before emitting it — which
   cause (1) then converted into a pass.

Several real QA/Security/Reviewer runs recorded a `pass` this way. Verdicts
recorded before this release should not be treated as evidence of review.

**Fail closed.**
- New `invalid` verdict state. Missing, malformed, truncated, or unrecognised
  verdicts are `invalid` — **never** `pass`. `AgentVerdict` now defaults to
  `invalid`, and carries a `reason` explaining why, plus an `is_valid` property.
- **Only a well-formed JSON verdict object can grant a `pass`.** A labelled
  `verdict: block` / `needs_changes` in prose is still honoured — a *stop*
  signal is safe from any source — but a labelled `verdict: pass` is `invalid`.
  Prose cannot approve, in any form: not "PASS", "Checkpoint PASS", "looks
  good", nor the absence of veto language.
- **Gating roles fail closed.** QA, Security, and Reviewer must each produce a
  valid `pass` for the pipeline to advance. New team status `invalid-verdict`,
  deliberately distinct from `blocked`, so operators can tell "vetoed" from
  "never answered".

**Truncation-safe by design.**
- `VERDICT_INSTRUCTION` now demands the JSON block **first, before any prose**,
  so the verdict structurally survives truncation — the prose is what is lost,
  and prose is not what the workflow reads.
- Provider `stop_reason` (Anthropic) / `finish_reason` (OpenAI) is captured into
  `ProviderResponse.metadata`, carried through `ExecutionReport.truncated` and
  `.stop_reason`, and passed to `parse_verdict(truncated=…)`.
- `max_tokens` raised 2048 → 4096.

**Preserved unchanged.** CPO and Lead Engineer remain productive roles, not
gatekeepers — their verdict never halts the team, even a structured `block`.
The human ApprovalGate, review-required team mode, audit logging, and the
`FakeAgentProvider` harness all behave as before. Dry-run is exempt from verdict
gating (it makes no provider call, so there is no response to carry a verdict).

**Migration notes.**
- Providers that do not emit the JSON object will now **fail** blocking stages
  instead of passing. Update agent prompts or provider configuration before
  upgrading, or expect previously-green pipelines to stop with
  `invalid-verdict`.
- `agents.team._derive_verdict` is **removed**, replaced by `_stage_verdict`
  (what the stage returned, recorded for audit) and `_stops_pipeline` (whether
  that halts the run). External callers must migrate.
- `normalize_verdict()` returns `invalid` rather than `pass` for unknown input.
- Stage verdicts for CPO / Lead Engineer may now read `invalid` where they
  previously read `pass`. Audit fidelity improved; gating unchanged.
- `max_tokens` 2048 → 4096 raises per-call provider cost.
- Six existing tests changed semantics (rewritten, not deleted); the
  no-false-veto guarantee they protected is still asserted.

`FakeAgentProvider` gains three failure modes for testing this path —
`no_verdict`, `malformed`, `truncated` — each emitting convincing
approval-sounding prose with no usable verdict.

### MondayOS v2.3 — Confluence Publishing Integration (2026-07-07)
Adds Confluence as an outbound documentation publishing target. **MondayOS
remains the system of record** — content flows one way (MondayOS → Confluence),
the doc → page mapping is stored locally, and Confluence is never read as a
source of truth. Credentials come from the environment only; nothing is
committed to source.

- **Integration package** — new `integrations/confluence/`:
  `config.py` (env-var config + credential checks, token never exposed),
  `converter.py` (Markdown → Confluence storage-format XHTML: headings, bullets,
  numbered lists, code blocks, tables, links, inline emphasis),
  `client.py` (`HttpConfluenceClient` over the Cloud REST v1 API using only the
  standard library, plus an offline `FakeConfluenceClient`),
  `mapping.py` (local `PublishStore` — doc-ID → page-ID mapping + append-only
  history under `logs/publish/confluence.json`),
  `publisher.py` (`ConfluencePublisher` — create/update/dry-run with safety).
- **Public API** — `Monday.publish(action, …)` resolves a document from the
  system of record (knowledge entry or `docs/` file / explicit `--file`) and
  publishes it; `PublishResponse` added.
- **CLI** — `monday publish confluence [DOC_ID] [--file] [--space] [--parent]
  [--update-page] [--dry-run] [--force] [--title]` and `monday publish history`.
- **Safety** — a page is only overwritten when a mapping exists or
  `--update-page` is given (otherwise a new page is created); unchanged content
  is skipped as `up-to-date` unless `--force`; `--dry-run` previews the decision
  and makes no network calls (needs no credentials); missing credentials fail
  clearly before any call.
- **Metadata** — every publish records source, page ID, URL, space, checksum,
  timestamp, status, and version locally.
- **Docs** — `docs/CONFLUENCE.md` (setup, commands, safety model, conversion,
  offline testing seam).
- **Tests** — `tests/test_confluence.py` (37), fully offline via the fake client
  and the `MONDAYOS_CONFLUENCE_FAKE` seam: converter, credentials, mapping
  store, publisher create/update/dry-run/unchanged/explicit-page, and the API +
  CLI paths. No test requires a real Confluence account.

### MondayOS v2.2 — Real Agent Providers (2026-07-02)
Makes the multi-agent team use the real OpenAI/Anthropic providers instead of the
fake agent, with availability checks and graceful key-missing handling. Provider
mappings are unchanged (CPO/Research → openai/ChatGPT, Lead Engineer/QA/Security/
Reviewer → anthropic/Claude); review-required is preserved and nothing is
committed, pushed, changed as a secret, or executed live.

- **Provider availability** — new `ProviderAvailability` on `AIProvider`
  (`availability()`); OpenAI/Anthropic report unavailable (with the env var + SDK
  install hint) when the SDK is missing or the key is unset; Ollama/fake are
  always available. `agents.adapters.availability_for()` resolves it for an agent.
- **Graceful failure** — `AgentRuntime.run` gates on availability before calling a
  provider: a missing key/SDK stops with status `unavailable` and clear
  instructions, and does **not** touch the task. A team stage that resolves to an
  unavailable provider stops the pipeline with the same message (no live call).
  Dry-run is never blocked by a missing key.
- **Provider/model logging** — `model_used` recorded on the execution report;
  `provider_model` on `AgentRun` and `TeamStage`; `monday agent run` / `team run`
  print `provider (model)`, and `monday agent list` shows a readiness column.
- **Docs** — `docs/PROVIDERS.md` (role→provider map, required env vars —
  `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` — SDK installs, availability, graceful
  failure).
- **Tests** — `tests/test_agent_providers.py` (20), fully offline: availability
  (SDK/key present/absent via `sys.modules`/env mocks), resolution, the runtime
  and team availability gates, model logging, and `agent list` availability. No
  test requires live OpenAI/Anthropic credentials.

---

### MondayOS v2.1 — Agent Team Workflow (2026-07-02)
Makes the registered agents collaborate on a task end to end:
**CPO → Lead Engineer → QA → Security → Reviewer → human approval**. A thin
coordinator over the Multi-Agent Runtime — each stage is a normal role run, so
the approval gate, review-required default, and logging apply unchanged. Nothing
is committed, pushed, or executed live; on a full pass the task moves to REVIEW
for human approval.

- **New `agents/team.py`** — `TeamWorkflow` runs the fixed `TEAM_SEQUENCE`; QA /
  Security / Reviewer are blocking (`BLOCKING_ROLES`) and stop the pipeline early
  on a block verdict or a stage failure. Each stage receives the prior stages'
  summaries as extra prompt context. `TeamRun` (parent) references its child
  role-run IDs; `TeamStage` records role/verdict/summary.
- **Task lifecycle** owned by the team: stages run with `update_task=False` (new
  orchestrator/`Monday.execute` option) so they execute and capture knowledge
  without churning the task; the task is moved to IN_PROGRESS once at the start
  and REVIEW once at the end. New `extra_context` option threads prior summaries
  into the plan.
- **Public API:** `Monday.team("run"|"history", …)` → new `TeamResponse`.
- **CLI:** `monday team run TASK-ID [--provider][--mode review|dry-run][--json]`,
  `monday team history`.
- **Records:** parent `logs/agents/team-*.json` + child `logs/agents/run-*.json`.
- **Docs:** `docs/TEAM_WORKFLOW.md`.
- **Tests:** `tests/test_team.py` (30) — full fake-agent pipeline, prior-summary
  propagation, early stop at QA/Security/Reviewer, dry-run, autonomous guard,
  approval-completes-task, and CLI smoke. `FakeAgentProvider` gained a `verdict`
  knob to drive blocking stages offline.

---

### MondayOS v2 — Multi-Agent Runtime (2026-07-02)
A role-based agent system layered on the Execution Orchestrator. Work is routed
to a **role** (not a specific model); the registry resolves the role to an agent
and provider; a **review-required** approval gate governs every run; and each run
is logged as a reviewable record. MondayOS stays the system of record — agents
cannot commit, push, touch secrets, or live-trade without explicit human approval.
Autonomous live execution is intentionally **not** implemented; the runtime only
plans, captures knowledge, and moves tasks to REVIEW.

- **New `agents/` package** (built on `orchestrator/`, no provider-specific code):
  - `roles.py` — the six roles (CPO, Lead Engineer, QA, Security, Research,
    Reviewer) as pure data; adding a role is one table entry. Provider defaults:
    CPO → openai (ChatGPT), Lead Engineer → anthropic (Claude Code), Research →
    openai, QA/Security/Reviewer → anthropic (all overridable). `GATED_ACTIONS` =
    commit/push/secrets/live_trade/destructive.
  - `gates.py` — `ApprovalGate` enforcing review-required-by-default; blocks
    gated actions and autonomous completion without approval.
  - `registry.py` — `AgentRegistry` persisting agents as `agents/active/AGENT-*.md`
    (+ `.sequences.json`), mirroring `tasks/`; seeds the six default agents.
  - `runtime.py` — `AgentRuntime` (list/register/assign/run/review/history) that
    delegates execution to `Monday.execute` and logs each run to `logs/agents/`.
  - `adapters.py` — role→provider via `brain.providers.factory`, plus the offline
    `FakeAgentProvider` fake-agent test harness (the `fake` provider).
- **Public API:** `Monday.agent(action, …)` → `AgentResponse`; new `task("assign")`
  action (assign to `role:<slug>`).
- **CLI:** `monday agent list | register | run | review | history | assign`.
- **Docs:** `docs/AGENTS.md`, `docs/AGENT_ROLES.md`, `docs/APPROVAL_GATES.md`.
- **Tests:** `tests/test_agents.py` (55) — roles, gate, registry, runtime
  end-to-end, and CLI smoke, all offline via the fake provider. Suite: 828 passed.
- `agents*` added to packaged modules + coverage; `logs/agents/`, `agents/active/`,
  `agents/.sequences.json` gitignored as runtime state.

---

### Initiative 014 — WeatherBot Product Workspace (2026-06-28)
WeatherBot becomes the first project managed entirely by MondayOS. Its complete
project-management structure is populated **through the Monday public API** — no
hand-authored docs that could instead be structured knowledge or tasks. No new
platform capabilities, no architectural changes, no new subsystems (Architecture
Freeze respected).

- `projects/weatherbot/setup_workspace.py` — re-runnable populator (the single
  structured source of truth) that builds the workspace via
  `Monday.project()`, `Monday.learn()`, `Monday.task()`, and `Monday.workflow()`.
- `projects/weatherbot/PRODUCT_WORKSPACE.md` — generated index mapping the created
  knowledge entries and tasks; includes the "onboard via MondayOS alone" commands.
- Created in the MondayOS stores (scoped to the `weatherbot` component):
  - **Knowledge (8 entries):** Overview & current state (`DOC-0001`), Project
    Charter — vision/goals/constraints/metrics (`DOC-0002`), Product Roadmap
    (`DOC-0003`), Risk Register — technical/product/operational (`DOC-0004`),
    Definition of Done (`DEC-0001`), Engineering Backlog map (`DOC-0005`), Sprint
    Zero (`SPR-0001`), and an implementation pattern from the workflow demo (`PAT-0001`).
  - **Tasks (19):** 5 epics (`TASK-0001..0005`) + 13 features/tasks
    (`TASK-0006..0018`) with dependencies expressed in task context, plus one
    workflow-created task (`TASK-0019`).
  - **Workflow:** `implement-function` run end-to-end (auto-approved) to verify
    the managed engineering loop; produced `TASK-0019` + `PAT-0001`.
- Verified onboarding experience: `monday ask "What is WeatherBot…"` resolves the
  charter/overview at 82% confidence; `monday search weatherbot` returns all
  entries; `monday task list` shows the live backlog; `monday advise` ranks next
  work. Test suite unaffected (773 passed, 12 skipped).

---

## [1.0.0b1] — 2026-06-28 — Sprint 1.13: v1.0 Beta Release (Product Quality)

First public **beta-quality** release. Documentation, coherence, and
verification — no new platform capabilities, no architectural changes, no new
subsystems.

### Added
- `README.md` — full rewrite: vision, features, architecture diagram, quick
  start, CLI examples, WeatherBot onboarding example, roadmap, doc map
- `RELEASE.md` — beta features, limitations, known issues, upgrade path
- `CONTRIBUTING.md` — development workflow, coding standards, PR process,
  testing requirements, architecture boundaries
- `RELEASE_CHECKLIST.md` — repeatable release checklist with this beta's
  verification results recorded
- `docs/ARCHITECTURE_DIAGRAM.md` — as-built component + data-flow diagrams,
  provider abstraction, dependency rules
- `docs/BETA_ROADMAP.md` — Phase 1 status, beta→1.0 work, Phase 2/3 outlook

### Changed
- Version bumped `0.1.0` → `1.0.0b1` (`pyproject.toml`, `monday/api.py` `_VERSION`)
- Version-pinned tests updated (`tests/test_monday.py`, `tests/test_cli.py`)

### Verified
- Clean-room install confirmed: pristine source copy + fresh venv +
  `pip install -e ".[dev]"` → `monday status` healthy (`v1.0.0b1`); CLI task
  create/list, external project register + onboard (report generated), and
  `execute --dry-run` all working; `pytest` green from the clean copy.
- **Tests: 773 passed, 12 skipped, 0 failures** (in-tree and clean-copy).

### Notes
- No on-disk format changes; no data migration required from the 0.x line.
- Public `Monday` API remains additive and backward-compatible.

---

## [0.13.0] — 2026-06-28 — Sprint 1.12: Execution Orchestrator

### Added
- `orchestrator/` package — the Execution Orchestrator. Receives engineering work
  and delegates it to AI providers; coordinates, never implements, models. No new
  *core* subsystem was needed — it composes existing ones (Architecture Freeze respected).
  - `report.py` — `ExecutionMode` (DRY_RUN | REVIEW | AUTONOMOUS) and `ExecutionReport`
    (provider used, prompt summary, duration, success, files changed, knowledge captured,
    follow-up tasks, confidence, validation, plan); persists to `logs/executions/{id}.json`
  - `planner.py` — `ExecutionPlanner` / `ExecutionPlan`: deterministic, provider-agnostic
    plan built from the task + optional advisory context (runs before provider selection)
  - `queue.py` — `ExecutionQueue` / `ExecutionUnit`: stable priority queue (P0→P3, FIFO ties)
  - `validator.py` — `ResultValidator` / `ValidationResult`: deterministic checks
    (non-empty, sufficient length, not-a-refusal, addresses-objective) gating capture/update
  - `executor.py` — `ExecutionOrchestrator` (the pipeline) plus `ProviderSelectionPolicy`
    (PREFER_LOCAL | LOWEST_COST | HIGHEST_CAPABILITY | MANUAL) and `select_provider()`
- `Monday.execute(task_id, mode, policy, provider, providers, autonomous_enabled, max_tokens)`
  — runs the full pipeline: advisor prioritises → plan → queue → provider selection →
  provider executes (through `AIProvider` only) → validation → knowledge capture →
  task update → persisted execution report. Returns `ExecuteResponse`.
- `ExecuteResponse` dataclass (`monday/types.py`); exported from `monday/__init__.py`
- `Monday.task("review")` — IN_PROGRESS → REVIEW transition (public extension point used
  by the orchestrator's review-required mode)
- CLI `monday execute TASK-0001` with `--mode`, `--dry-run`, `--policy`, `--provider`,
  `--enable-autonomous`, `--json`
- Provider selection metadata on `AIProvider` (`is_local`, `cost_tier`, `capability_tier`)
  — concrete defaults on the base, overridden per provider. Keeps all provider-specific
  knowledge inside provider files so no provider-specific code lives in the orchestrator.
- `tests/test_orchestrator.py` — 57 tests: enum parsing, policy-based selection, queue
  ordering, planner, validator, report persistence, and end-to-end `Monday.execute`
  (dry-run / review / autonomous gating / no-provider / provider-failure / validation-failure /
  terminal-task / event emission)

### Safety
- Three execution modes; default is **Review Required**. No autonomous file modification
  unless `--mode autonomous` AND `--enable-autonomous` are both set; otherwise autonomous
  runs are **blocked**. Dry-run makes no provider call and no mutations.

### Design Decisions
- **Orchestrator coordinates; providers execute** — execution goes exclusively through the
  `AIProvider` abstraction; selection uses only generic provider metadata
- **Deterministic planning + validation** — planning and validation never call a model, so
  dry-runs are free and weak/empty AI results cannot silently mutate project state
- **Reuse, not duplication** — advise / learn / task flow through the Monday public API;
  audit events publish on the shared `EventBus` (MODEL_CALL_STARTED/COMPLETED/FAILED)
- **Foundation for multi-agent orchestration** — the queue + policy + provider abstraction
  generalise to batch and multi-provider execution

### Changed
- `brain/providers/base.py`, `anthropic.py`, `openai.py`, `ollama.py` — added selection metadata
- `monday/api.py` — `Monday.execute()`, `_task_review()`, `ExecuteResponse` import
- `monday/cli.py` — `execute` command registered
- `pyproject.toml` — `orchestrator*` added to packages and coverage source
- `.gitignore` — ignore `logs/executions/` (runtime reports)

---

## [0.12.0] — 2026-06-28 — Sprint 1.11: AI Provider Layer

### Added
- `brain/providers/` subpackage — AI provider abstraction layer (no Architecture Freeze violation — covered by `brain*` glob in pyproject.toml)
  - `base.py` — `AIProvider` ABC with `ask()`, `plan()`, `summarize()`, `review()` methods; `ProviderResponse` dataclass; `ProviderError` hierarchy (`ProviderAuthError`, `ProviderRateLimitError`, `ProviderUnavailableError`)
  - `anthropic.py` — `AnthropicProvider` — Claude models via `anthropic` SDK; default model `claude-sonnet-4-6`; full error mapping
  - `openai.py` — `OpenAIProvider` — GPT-* models via `openai` SDK; default model `gpt-4o-mini`; `base_url` override for Azure / LM Studio; full error mapping
  - `ollama.py` — `OllamaProvider` — local models via Ollama REST API (`/api/chat`); no SDK dependency; default model `llama3`; default base URL `http://localhost:11434`
  - `factory.py` — `ProviderConfig` dataclass (type, model, api_key, base_url, timeout, max_tokens, extra); `create_provider(config)` factory returning `AIProvider | None`
  - `__init__.py` — clean public surface re-exporting all provider types
- `brain/__init__.py` — exports `AIProvider`, `ProviderConfig`, `ProviderResponse`, `ProviderError` and subclasses, `create_provider`
- `monday/config.py` — `MondayConfig.provider_config: ProviderConfig | None = None` field; optional, defaults to None (fully deterministic mode)
- `monday/api.py` — `Monday.__init__` instantiates `self.__provider = create_provider(config.provider_config)`; `Monday.advise()` passes `provider=self.__provider` to `AdvisorEngine`
- `advisor/engine.py` — `AdvisorEngine.__init__` accepts `provider: AIProvider | None = None`; `_enrich_advisory_with_ai(advisory)` method: runs deterministic synthesis first, then overlays AI-enriched sprint goal if provider available; confidence ceiling raised from 0.85 → 0.95 when AI enrichment succeeds; all provider errors silently swallowed (best-effort enrichment)
- `tests/test_providers.py` — 54 tests across 9 test classes: `TestProviderResponse`, `TestProviderErrors`, `TestProviderConfig`, `TestCreateProviderFactory`, `TestAnthropicProvider`, `TestOpenAIProvider`, `TestOllamaProvider`, `TestMondayConfigProviderField`, `TestAdvisorAIEnrichment`

### Design Decisions
- **No provider-specific logic outside provider files** — `monday/api.py` and `advisor/engine.py` depend only on `AIProvider` / `ProviderConfig` from `brain.providers`; no SDK imports leak outside implementation modules
- **Null provider = deterministic** — `Monday()` with no `provider_config` behaves identically to before; AI enrichment is an additive overlay
- **Interchangeable via config** — changing `ProviderConfig(type=...)` is the only change needed to swap providers; no application code changes required
- **Foundation for multi-agent orchestration** — provider abstraction supports future multi-agent workflows without coupling to any specific SDK

### Changed
- `brain/__init__.py` — re-exports all provider types; docstring updated with provider abstraction summary

---

## [0.11.0] — 2026-06-28 — Sprint 1.10: WeatherBot Onboarding (Project Management)

### Added
- `monday/project.py` — `ProjectRegistry` class (no new package — Architecture Freeze respected)
  - `ProjectEntry` dataclass: name, source_path, description, registered_at; `to_dict()` / `from_dict()`
  - `ProjectRegistry(config_dir)`: `register()`, `get()`, `list()`, `remove()`, `exists()`
  - Registry persisted to `{mondayos_root}/config/projects.json`
  - `ProjectAlreadyExistsError` / `ProjectNotFoundError` error types
  - `overwrite=True` flag for re-registration
- `Monday.project(action, name, path, description, overwrite)` — register, list, get, remove external projects; returns `ProjectResponse`
- `Monday.onboard(project_name, reports_dir)` — full onboarding pipeline:
  1. Look up project in registry
  2. Create secondary `Monday` instance pointing at external project root
  3. Run `migrate` — import project documentation into WeatherBot's knowledge store
  4. Run `doctor` — health inspection of external project
  5. Run `advise` — engineering advisory using external project's data
  6. Generate comprehensive Markdown report at `{mondayos_root}/projects/{name}/ONBOARDING_REPORT.md`
  7. Return `OnboardResponse` with health_score, sprint_goal, confidence, report_path, composite data
- `ProjectResponse` dataclass — in `monday/types.py`; fields: action, success, project_name, data, message
- `OnboardResponse` dataclass — in `monday/types.py`; fields: action, success, project_name, migrate_summary, health_score, grade, sprint_goal, confidence, report_path, data, message
- `_generate_onboarding_report()` — pure function producing Markdown onboarding report with 9 sections answering all onboarding questions
- CLI `monday project register <name> <path> [--description DESC] [--overwrite]`
- CLI `monday project list`
- CLI `monday project get <name>`
- CLI `monday project remove <name>`
- CLI `monday onboard <project_name> [--output DIR]` — runs full pipeline, prints summary, shows report path
- WeatherBot external project at `/Users/jrich/AI-Labs/WeatherBot/` (v0.3.1):
  - Python weather CLI — `client.py`, `models.py`, `cache.py`, `alerts.py`, `cli.py`
  - Tests: `test_models.py`, `test_client.py`
  - Documentation: `docs/CHANGELOG.md` (4 versions), `docs/DECISIONS.md` (5 ADRs), `docs/ROADMAP.md` (4 phases)
  - 20 knowledge entries imported via `monday migrate`
  - First production project managed entirely by MondayOS

### Onboarding Report: WeatherBot
Generated at `projects/weatherbot/ONBOARDING_REPORT.md`. Key findings:
- Health Score: 100/100 (Excellent) — clean git, tests present, full documentation, valid config
- Advisory Confidence: 55% — knowledge base populated but single-type entries (sprint/decision/feature only)
- Knowledge base: 20 entries (7 feature, 5 decision, 4 documentation, 4 sprint)
- Knowledge gaps: no pattern, lesson, bug, or runbook entries captured yet
- Technical debt: 3 TODO/FIXME markers, no tracked bugs
- Sprint goal: "Continue forward momentum" (no critical or high issues)
- Recommended tasks: expand knowledge base with missing types; run `monday ask` to validate

### Changed
- `monday/__init__.py` — exports `ProjectResponse`, `OnboardResponse`
- `monday/api.py` — imports `ProjectResponse`, `OnboardResponse`; `Monday.project()` and `Monday.onboard()` added; `_generate_onboarding_report()` helper added
- `monday/cli.py` — `_register_project()`, `_register_onboard()`, `_cmd_project_register()`, `_cmd_project_list()`, `_cmd_project_get()`, `_cmd_project_remove()`, `_cmd_onboard()` added; `_build_parser()` registers both commands
- `pyproject.toml` — re-installed editable package to register `advisor*` in editable finder mapping

### Tests
- `tests/test_project.py` — 43 tests across 5 test classes:
  - `TestProjectEntry` (4 tests) — to_dict, from_dict, path property, missing field defaults
  - `TestProjectRegistry` (14 tests) — register/get, persistence, duplicate error, overwrite, missing-path error, not-found error, list, remove, exists, cross-instance persistence
  - `TestMondayProject` (9 tests) — register/list/get/remove actions, error cases, unknown action
  - `TestMondayOnboard` (6 tests) — unknown project, missing path, success, report file written, all sections present, custom reports_dir, data keys
  - `TestCLIProject` (6 tests) — register, list empty/with-entries, get found/not-found, remove
  - `TestCLIOnboard` (3 tests) — unknown project error, success output, report path shown
- **Total: 658 passed, 12 skipped, 0 failures**

---

## [0.10.0] — 2026-06-28 — Sprint 1.9: Engineering Advisor (monday advise)

### Added
- `advisor/` package — pure-data engineering advisory engine
  - `advisor/advisory.py` — `Risk` dataclass (title, severity, category, impact, recommendation, source), `Action` dataclass (title, priority, category, rationale, effort, command), `Advisory` dataclass with `to_dict()` — complete structured advisory output
  - `advisor/reasoning.py` — pure synthesis functions (no I/O, fully testable):
    - `synthesize_risks(doctor_report, knowledge_entries, active_tasks)` — merges findings from Doctor, Knowledge Store, and Tasks into ranked Risk list; deduplicates by `category:title[:40]`; sorts critical → high → medium → low
    - `synthesize_next_actions(risks, active_tasks, knowledge_entries, doctor_report, workflow_runs)` — 6-priority action ladder (fix-critical → unblock-blocked → compound-value → high-warnings → documentation → health-check); deduplicated, capped at 10, renumbered
    - `synthesize_sprint_goal(risks, active_tasks, knowledge_entries)` — decision tree returning `(goal, rationale)`: criticals → urgent backlog → empty KB → blocked tasks → in-progress → healthy default
    - `synthesize_debt(doctor_report, knowledge_entries)` — aggregates TODO markers, known bugs, deprecated entries, and failing tests
    - `synthesize_knowledge_gaps(knowledge_entries, active_tasks)` — identifies missing entry types and task topics with no KB coverage; capped at 8
    - `synthesize_documentation_gaps(doctor_report)` — extracts WARNING/CRITICAL documentation findings
    - `summarize_repository(name, doctor_report, knowledge_entries, active_tasks, workflow_runs)` — template-based natural-language paragraph; no external model
    - `compute_confidence(knowledge_entries, active_tasks, workflow_runs, doctor_ran_cleanly)` — scales 0.25 base → max 0.85; bonus for diverse KB types; never claims full certainty
  - `advisor/engine.py` — `AdvisorEngine`: composes Doctor, KnowledgeStore, TaskManager, workflow logs; accepts pre-computed `DoctorReport` to avoid double-running; `_load_workflow_runs()` reads last 20 `logs/workflows/*.json`
  - `advisor/__init__.py` — clean public exports
- `Monday.advise(doctor_report=None)` — public API; lazy-imports `advisor`; returns `AdviseResponse`
- `AdviseResponse` dataclass — in `monday/types.py`; fields: `action`, `success`, `confidence`, `sprint_goal`, `risks`, `next_actions`, `repository_summary`, `data`, `message`
- CLI `monday advise [--json] [--brief]`
  - Default: CTO-grade advisory report with confidence, health score, risks, next actions, sprint goal, debt summary, knowledge and documentation gaps
  - `--json`: machine-readable full advisory (Advisory.to_dict())
  - `--brief`: single-screen summary (sprint goal + top 3 actions + confidence)
  - 64-column wrapped output with `═` major dividers and `─` section dividers

### Changed
- `monday/__init__.py` — exports `AdviseResponse`
- `monday/api.py` — imports `AdviseResponse`; `Monday.advise()` method added
- `monday/cli.py` — `_register_advise()`, `_cmd_advise()`, `_print_advisory()`, `_print_brief()`, `_wrap()`, `_thin()` added; `_build_parser()` registers advise command
- `pyproject.toml` — `advisor*` added to `packages.find.include` and `coverage.run.source`

### Tests
- `tests/test_advisor.py` — tests across 12 test classes covering all reasoning functions, engine, Monday API, and CLI

---

## [0.9.0] — 2026-06-28 — Sprint 1.8: Repository Intelligence (monday doctor)

### Added
- `doctor/` package — pluggable repository health inspection engine
  - `doctor/finding.py` — `Finding` dataclass, `Severity` enum (CRITICAL / WARNING / INFO / OK)
  - `doctor/result.py` — `AnalyzerResult` dataclass, `DoctorReport` with `build()` factory, `_compute_health_score()` (deduction schedule: CRITICAL −15 each capped at 45, WARNING −5 each capped at 30), `_health_grade()` (Excellent / Good / Fair / Poor / Critical)
  - `doctor/base.py` — `BaseAnalyzer` ABC: `NAME` class var, `analyze() → AnalyzerResult`
  - `doctor/inspector.py` — `RepositoryInspector`: `available_analyzers()`, `run() → DoctorReport`; `_REGISTERED_ANALYZERS` list for pluggable discovery; exception isolation per analyzer
  - `doctor/analyzers/git.py` — `GitAnalyzer`: repo presence (CRITICAL if missing), current branch, dirty tree (WARNING), recent commits, unpushed commits
  - `doctor/analyzers/tests.py` — `TestAnalyzer`: test file count (CRITICAL if zero), `.pytest_cache` last-failed reading (CRITICAL if failures), clean run (OK), coverage configuration check — does not execute pytest
  - `doctor/analyzers/code_quality.py` — `CodeQualityAnalyzer`: TODO/FIXME/HACK/XXX scan (INFO ≤10, WARNING >10), large files >500 KB (WARNING), empty directories (INFO); excludes .venv/.git/__pycache__/node_modules
  - `doctor/analyzers/knowledge_health.py` — `KnowledgeHealthAnalyzer`: entry count, empty-body entries (WARNING), broken `superseded_by` references (WARNING), untagged entries (INFO), orphaned import-index entries (WARNING)
  - `doctor/analyzers/documentation.py` — `DocumentationAnalyzer`: README.md / CHANGELOG.md (WARNING if missing), DECISIONS.md (INFO if missing), module docstrings in top-level packages (INFO), broken internal Markdown links (WARNING); skips HTTP links and anchors
  - `doctor/analyzers/task_health.py` — `TaskHealthAnalyzer`: BLOCKED tasks (WARNING), tasks without objectives (WARNING), unassigned in-progress tasks (INFO), P0/P1 tasks still in BACKLOG (WARNING), status breakdown (INFO)
  - `doctor/analyzers/config.py` — `ConfigAnalyzer`: pyproject.toml TOML validity (CRITICAL if malformed), required sections check (WARNING if missing), `requires-python` vs. running interpreter (WARNING if mismatch), workflow YAML validity (WARNING per malformed file)
  - `doctor/__init__.py` — clean public exports
- `Monday.doctor(analyzers=None)` — public API; lazy-imports `doctor`; returns `DoctorResponse`
- `DoctorResponse` dataclass — in `monday/types.py`; fields: `action`, `success`, `health_score`, `grade`, `summary`, `recommendations`, `data`, `message`
- CLI `monday doctor [--json] [--verbose] [--only ANALYZER ...]`
  - Default: human-readable report with health score bar, findings by severity, top 5 recommendations
  - `--json`: machine-readable full report (DoctorReport.to_dict())
  - `--verbose`: shows passing (OK) findings and detail text for all findings
  - `--only git tests`: run subset of analyzers
  - Exit code: 0 if score ≥ 60, 1 otherwise

### Changed
- `monday/__init__.py` — exports `DoctorResponse`
- `monday/api.py` — imports `DoctorResponse`; `Monday.doctor()` method added
- `monday/cli.py` — `_register_doctor()`, `_cmd_doctor()`, `_print_doctor_report()`, `_all_findings()`, `_score_bar()` added; `_build_parser()` registers doctor command
- `pyproject.toml` — `doctor*` added to `packages.find.include` and `coverage.run.source`

### Tests
- `tests/test_doctor.py` — 69 tests across 9 test classes:
  - `TestFinding` (2 tests) — to_dict, severity values
  - `TestDoctorReport` (9 tests) — score perfect/critical-deduction/critical-cap/warning-deduction/warning-cap/floor/grade-labels/recommendations-ranked/to_dict-structure/all_findings-flattened
  - `TestRepositoryInspector` (5 tests) — available analyzers, run returns DoctorReport, analyzer subset, unknown name skipped, exception surfaced as CRITICAL, all analyzers smoke test
  - `TestGitAnalyzer` (6 tests) — no .git critical, git dir no-critical, dirty-tree warning, clean-tree ok, no-commits warning, result name
  - `TestTestAnalyzer` (5 tests) — no files critical, files found info, no cache info, lastfailed critical, clean cache ok
  - `TestCodeQualityAnalyzer` (7 tests) — no markers ok, few markers info, many markers warning, large file warning, no large files ok, empty dir info, venv excluded
  - `TestDocumentationAnalyzer` (9 tests) — missing README warning, present ok, missing CHANGELOG warning, missing DECISIONS info, broken link warning, valid link ok, HTTP links skip, missing docstring info, docstring ok
  - `TestConfigAnalyzer` (6 tests) — no pyproject critical, valid ok, missing section warning, valid YAML ok, invalid YAML warning, no workflow dir info
  - `TestTaskHealthAnalyzer` (5 tests) — no Monday skip, no tasks info, blocked warning, no-objective warning (mocked), clean ok
  - `TestMondayDoctor` (7 tests) — returns DoctorResponse, score range, grade present, summary nonempty, data is full report, analyzer subset, healthy repo high score
  - `TestCLIDoctor` (6 tests) — runs, JSON output, verbose, --only flag, JSON has recommendations, healthy repo exits zero
- **Total: 540 passed, 12 skipped, 0 failures**

---

## [0.8.0] — 2026-06-28 — Sprint 1.7: Knowledge Migration Engine

### Added
- `migrate/` package — knowledge migration engine: converts existing project documentation into canonical MondayOS Knowledge Objects
  - `migrate/engine.py` — `MigrationEngine`: `list_sources()`, `run(sources, dry_run, overwrite, progress_callback)` → `ImportReport`, `rollback(run_id)` → `RollbackReport`; import index at `knowledge/.import_index.json` for idempotency; SHA-256[:16] fingerprinting for change detection
  - `migrate/candidate.py` — `KnowledgeCandidate` dataclass: pre-validation knowledge object; auto-fills `fingerprint` and `summary` in `__post_init__`; `slugify()` helper for stable `source_ref` discriminators
  - `migrate/report.py` — `ImportReport` (start, write, load, round-trip), `RollbackReport`, `ImportedEntry`, `SkippedEntry`, `FailedEntry`; run reports written to `logs/migrations/{run_id[:8]}.json`
  - `migrate/errors.py` — typed error hierarchy: `MigrationError`, `SourceNotFoundError`, `UnknownSourceError`, `ParseError`, `RollbackError`
  - `migrate/parsers/` — 6 source document parsers, all extending `BaseParser`:
    - `ChangelogParser` — `docs/CHANGELOG.md` → Sprint entries (one per versioned section; skips `[Unreleased]`; deduplicates versions)
    - `DecisionsParser` — `docs/DECISIONS.md` → Decision/ADR entries
    - `SessionLogParser` — `logs/SESSION_LOG.md` → Sprint entries + Bug entries from "Known technical debt" bullets
    - `RoadmapParser` — `docs/ROADMAP.md` → Documentation (phase goals) + Feature (milestones) entries
    - `WorkflowsParser` — `docs/WORKFLOWS.md` → Pattern (step types) + Documentation + Runbook (CLI/API usage) entries
    - `SelfHostingParser` — `docs/SELF_HOSTING_PLAN.md` → Feature (opportunities) + Runbook (workflow designs) + Documentation (part summaries) entries
  - `migrate/__init__.py` — clean public exports
- `Monday.migrate(action, sources, dry_run, overwrite, run_id, progress_callback)` — public API for knowledge migration; actions: `list-sources`, `run`, `rollback`; returns `MigrateResponse`
- `MigrateResponse` dataclass — in `monday/types.py`; fields: `action`, `success`, `dry_run`, `run_id`, `sources_processed`, `candidates_found`, `imported_count`, `skipped_count`, `failed_count`, `data`, `message`
- `KnowledgeStore.remove(entry_id)` — hard delete of a knowledge entry from disk; rebuilds the in-memory index; used by migration rollback
- `Monday._remove_knowledge_entry(entry_id)` — thin shim exposing `KnowledgeStore.remove()` to the migration engine
- CLI `monday migrate [SOURCE ...] [--dry-run] [--overwrite] [--run-id ID] [--quiet]`
  - `monday migrate` — import all registered sources
  - `monday migrate changelog` — import a single named source
  - `monday migrate list` — list all registered source documents with exists/missing status
  - `monday migrate rollback <run-id>` — undo a prior run by run_id prefix
  - `monday migrate --dry-run` — preview candidates without writing entries
- `docs/SELF_HOSTING_PLAN.md` (Initiative 006) — 523-line self-hosting audit and migration plan: current-state file-write audit, 10 prioritised opportunities, 6 workflow designs, activity→workflow mapping, 4-phase migration plan

### Changed
- `monday/__init__.py` — exports `MigrateResponse`
- `monday/api.py` — imports `MigrateResponse`; `Monday.migrate()` method added; `Monday._remove_knowledge_entry()` shim added
- `monday/cli.py` — `_register_migrate()` and `_cmd_migrate*` command handlers added; `_build_parser()` registers migrate command
- `pyproject.toml` — `migrate*` added to `packages.find.include` and `coverage.run.source`

### Tests
- `tests/test_migrate.py` — 76 tests across 6 test classes:
  - `TestKnowledgeCandidate` (5 tests) — fingerprint auto-fill, summary extraction, explicit overrides, slugify
  - `TestImportReport` (4 tests) — start/counters/write+load round-trip/to_dict
  - `TestChangelogParser` (9 tests) — versioned sections, skip Unreleased, source_refs, entry_type, confidence, sprint tags, deduplication, empty text, source_info
  - `TestDecisionsParser` (5 tests), `TestSessionLogParser` (5 tests), `TestRoadmapParser` (4 tests), `TestWorkflowsParser` (4 tests), `TestSelfHostingParser` (5 tests) — parser-specific extraction and source_ref correctness
  - `TestMigrationEngine` (17 tests) — list_sources, source_exists, unknown source raises, dry-run (no writes, no index), real import, index persistence, idempotency, overwrite, missing file graceful skip, log written, progress callback, rollback (removes entries, unknown run_id raises, clears index), low-confidence skip, empty-content skip
  - `TestMondayMigrate` (10 tests) — list-sources, exists flag, run, dry-run, unknown source, rollback requires run_id, rollback unknown, rollback removes entries, unknown action, run with no files
  - `TestCLIMigrate` (6 tests) — list, dry-run, unknown source, rollback needs run-id, quiet flag, full run
- **Total: 471 passed, 12 skipped, 0 failures**

---

## [0.7.0] — 2026-06-28 — Sprint 1.6: End-to-End Workflow

### Added
- `workflows/` package — multi-step workflow system with YAML definitions, engine, execution state, and error hierarchy
  - `workflows/definition.py` — `WorkflowDefinition`, `WorkflowStep`, `WorkflowInput`, `StepType` enum (7 step types: `ask`, `search`, `learn`, `task_create`, `task_start`, `task_complete`, `human_approval`)
  - `workflows/execution.py` — `WorkflowExecution`, `StepExecution`, `WorkflowStatus` enum, `StepStatus` enum; `WorkflowExecution.write_log()` serialises to JSON after every run
  - `workflows/engine.py` — `WorkflowEngine`: `list_workflows()`, `get_workflow(name)`, `run(name, inputs, approval_handler)`; `{step_id.output_key}` and `{inputs.variable}` template substitution in all step input fields; `ApprovalHandler` callable protocol for human gate injection
  - `workflows/errors.py` — typed error hierarchy: `WorkflowError`, `WorkflowNotFoundError`, `WorkflowValidationError`, `StepExecutionError`, `ApprovalDenied`
  - `workflows/__init__.py` — clean public exports
- `workflows/definitions/implement_function.yaml` — bundled "implement a function" workflow: research → create-task → human-approval → start-task → capture-pattern → complete-task; exercises all 7 step types and demonstrates the full MondayOS coordination loop
- `Monday.workflow(action, name, inputs, approval_handler)` — public API for workflow management; actions: `list`, `show`, `run`; returns `WorkflowResponse`
- `WorkflowResponse` dataclass — in `monday/types.py`; fields: `action`, `success`, `workflow_name`, `execution_id`, `status`, `data`, `message`
- `Monday.task("start", task_id)` — convenience action that chains BACKLOG → ASSIGNED → IN_PROGRESS in two `update_status` calls; required for workflow `task_start` step type
- CLI `monday workflow list` — lists all available workflow definitions with version and step count
- CLI `monday workflow show <name>` — shows inputs and ordered steps for a named workflow
- CLI `monday workflow run <name> [--var KEY=VALUE ...] [--yes]` — executes a workflow; `--var` passes input variables; `--yes` auto-approves all human gates (non-interactive)
- ADR-010: YAML as the workflow definition format

### Changed
- `monday/__init__.py` — exports `WorkflowResponse`
- `monday/cli.py` — module docstring updated; `_register_workflow()` added; `_build_parser()` registers workflow command
- `pyproject.toml` — `workflows*` added to `packages.find.include` and `coverage.run.source`

### Tests
- `tests/test_workflows.py` — 74 tests across 6 test classes:
  - `TestWorkflowDefinition` (11 tests) — YAML loading, all required fields, bad YAML, unknown step type, missing step fields, non-mapping root
  - `TestWorkflowExecution` (7 tests) — initial state, context seeding, to_dict, write_log creates file, execution_id in log, filename format, directory creation
  - `TestTemplateResolution` (12 tests) — `_resolve_str` simple/dot/unknown/multi, `_resolve_dict` string/nested/list/non-string, `_to_list` list/str/none/other
  - `TestWorkflowEngine` (22 tests) — list empty/missing/populated/skip-malformed, get by name/not-found, run search step, version in execution, log written, context accumulation, inputs/defaults, approval approve/reject/stops-steps, step failure, learn output in context, log step ids, version in log, per-call handler override, bad ask/search steps
  - `TestMondayWorkflow` (11 tests) — list, show, show not found, run, not found, unknown action, full implement-function end-to-end
  - `TestCLIWorkflow` (8 tests) — list, list empty, show, show not found, run --yes, run --var, bad --var format, --help
- **Total: 395 passed, 12 skipped, 0 failures**

---

## [0.6.0] — 2026-06-27 — Sprint 1.5: Monday CLI

### Added
- `monday/cli.py` — `monday` command-line interface; zero business logic; all commands invoke `Monday()` through the public API
  - `monday status` — system health, module status, version, session ID, uptime
  - `monday ask "<prompt>"` — delegates to `Monday.ask()`; prints answer, confidence, engine, sources, supporting entries, related decisions, related tasks, suggested next actions
  - `monday search "<query>" [--limit N]` — delegates to `Monday.search()`; prints ranked results with type and tags
  - `monday learn [--title] [--type] [--tags] [--components] [--content]` — delegates to `Monday.learn()`; supports non-interactive (all flags), stdin pipe, and interactive guided prompts
  - `monday task list [--status] [--priority] [--type]` — delegates to `Monday.task("list")`
  - `monday task create --title --objective [--type] [--priority] [--created-by]` — delegates to `Monday.task("create")`
  - `monday task get TASK-ID` — delegates to `Monday.task("get")`
  - `monday task complete TASK-ID [--reason] [--changed-by]` — delegates to `Monday.task("complete")`
- `docs/CLI.md` — full CLI specification: installation, design principles, all commands, flags, output format, error handling, automation patterns
- `[project.scripts]` in `pyproject.toml` — registers `monday` as a pip-installable entry point (`monday = "monday.cli:main"`)

### Changed
- `pyproject.toml` — added `[project.scripts]`; added `monday*` to `packages.find` include list and coverage source list; fixed build backend from `setuptools.backends.legacy:build` to `setuptools.build_meta`

### Tests
- `tests/test_cli.py` — 52 integration tests across 7 test classes: `TestHelp`, `TestStatusCommand`, `TestAskCommand`, `TestSearchCommand`, `TestLearnCommand`, `TestTaskCommand`, `TestEndToEnd`; all tests invoke `monday.cli.main()` directly with explicit argv; no subprocess required; uses `capsys` and `tmp_path` throughout
- **Total: 321 passed, 12 skipped, 0 failures**

---

## [0.5.0] — 2026-06-27 — Sprint 1.4: Engineering Intelligence

### Added
- `brain/reasoner.py` — `ReasoningEngine`: answers engineering questions using only stored MondayOS knowledge; no external model calls
  - `QuestionIntent` enum — 9 question intent categories (GENERAL, HISTORICAL, TYPE_BUG, TYPE_DECISION, TYPE_TASK, BLOCKED_TASKS, RECENT_CHANGES, SUMMARY, ONBOARDING)
  - `ReasoningResult` dataclass — structured output: answer, sources, model_used, confidence, supporting_entries, related_tasks, related_decisions, suggested_next_actions
  - `_classify_intent()` — keyword pattern matching to route prompts into QuestionIntent
  - `_extract_terms()` — stop-word stripping and punctuation removal for clean search terms
  - `_search_knowledge()` — multi-word + per-term supplement strategy; type filtering per intent; RECENT_CHANGES uses `list_all()` sorted by `updated_at`
  - `_search_tasks()` — BLOCKED_TASKS uses `list_active(status=BLOCKED)`; TYPE_TASK/GENERAL match terms against title and objective
  - `_traverse_relationships()` — depth-1 BFS over entry relationship graph via `KnowledgeStore.get()`
  - `_synthesize()` — intent-specific answer templates
  - `_suggest_actions()` — up to 5 immediately-executable follow-up calls
  - `_calculate_confidence()` — additive signal model (quantity, summary quality, type alignment, relationship richness); hard cap at 0.95
- `docs/REASONING_ENGINE.md` — full specification: question processing pipeline, ranking strategy, relationship traversal, confidence model, future LLM/graph/vector integration points

### Changed
- `AskResponse` — extended with 4 new fields (all default to empty): `supporting_entries`, `related_tasks`, `related_decisions`, `suggested_next_actions`
- `Monday.ask()` — fully implemented: delegates to `ReasoningEngine`, populates all `AskResponse` fields; `model_used` is now `"monday-reasoning/1.0"`
- `Monday.__init__` — composes `ReasoningEngine(knowledge_store, task_manager)` as `__reasoner` (name-mangled, not accessible as public attribute)
- `brain/__init__.py` — exports `ReasoningEngine`, `ReasoningResult`, `QuestionIntent`

### Tests
- `tests/test_monday.py` — `TestAsk` converted to `tmp_path` autouse fixture; 3 previously-skipped tests implemented; 16 new behavioral tests added (empty knowledge, model_used, historical query, summary query, bug filter, decision filter, blocked tasks, confidence growth, sources populated, supporting entries, suggested actions); 1 new encapsulation test for `__reasoner`
- **Total: 269 passed, 12 skipped, 0 failures**

---

## [0.4.0] — 2026-06-27 — Sprint 1.3: Task Capture

### Added
- `tasks/errors.py` — typed error hierarchy: `TaskError`, `TaskNotFoundError`, `TaskValidationError`, `InvalidTransitionError`, `TaskParseError`
- `tasks/parser.py` — `TaskParser.parse()` and `TaskParser.serialize()`: Markdown + YAML frontmatter I/O for `Task` objects; round-trips cleanly; handles `status_history` with `from_status: null` for initial creation; alphabetically sorted keys for determinism
- `TaskManager.create()` — validates required fields, assigns stable ID (`TASK-NNNN`), builds initial `StatusTransition`, persists to `tasks/active/{ID}.md`, returns `Task`
- `TaskManager.get()` — retrieves by ID from `tasks/active/` then `tasks/completed/`; raises `TaskNotFoundError` if absent
- `TaskManager.update_status()` — validates transition via `Task.can_transition_to()`, appends `StatusTransition` to history, moves file to `tasks/completed/` on terminal status
- `TaskManager.list_active()` — scans `tasks/active/`, parses each file, applies optional filters (status, priority, assigned_to, task_type)
- `TaskManager.assign()`, `TaskManager.block()`, `TaskManager.append_work_log()`, `TaskManager.archive()` — convenience wrappers
- Sequence tracking in `tasks/.sequences.json` — survives restarts, consistent across instances
- `Monday.task(action='create')` — validates and delegates to `TaskManager.create()`; publishes `TASK_CREATED` event; returns `TaskResponse`
- `Monday.task(action='get')` — retrieves task by `task_id`; returns `TaskResponse` with full task dict
- `Monday.task(action='list')` / `action='list_active'` — lists active tasks; optional filters via kwargs; returns `TaskResponse` with `data.tasks` list
- `Monday.task(action='complete')` — transitions task to COMPLETED via `TaskManager.update_status()`; publishes `TASK_COMPLETED` event; returns `TaskResponse`

### Changed
- `tasks/__init__.py` — now exports all error types (`TaskError`, `TaskNotFoundError`, `TaskValidationError`, `InvalidTransitionError`, `TaskParseError`) and `TaskParser`
- `Monday.__init__` — `TaskManager` now receives `project_root` so task files are written to the configured project directory
- `Monday.task()` — fully implemented for `create`, `get`, `list`/`list_active`, and `complete`; unknown actions return `success=False` with descriptive message

### Tests
- `tests/test_tasks.py` — removed two `NotImplementedError` stubs; added `TestTaskParser` (7 tests) and `TestTaskManager` (19 tests) with full disk I/O via `tmp_path`; total 46 task tests
- `tests/test_monday.py` — `TestTask` converted from `setup_method` to `autouse` fixture with `tmp_path`; 7 new tests: create/get/list/complete end-to-end; removed 2 skipped stubs

### Total test counts
- **250 passed**, **15 skipped**, **0 failures**

---

## [0.3.0] — 2026-06-27 — Sprint 1.2: Knowledge Capture

### Added
- `knowledge/errors.py` — typed errors: `KnowledgeParseError`, `KnowledgeNotFoundError`, `KnowledgeConflictError`, `KnowledgeValidationError`
- `KnowledgeType` enum — 12 types from MKS 1.0 (Bug, Decision, Task, Sprint, Feature, Lesson, Pattern, Runbook, Documentation, Research, Weather, Experiment)
- `LifecycleStatus` enum — 5 lifecycle states (Draft, Active, Deprecated, Superseded, Archived)
- `RelationType` enum — 13 typed relationship directions
- `Relationship` dataclass — typed, directional link between entries
- `KnowledgeParser` — parses Markdown + YAML frontmatter into `KnowledgeEntry`; serialize round-trips cleanly
- `KnowledgeLoader` — walks knowledge directory; skips non-frontmatter files silently
- `KnowledgeIndex` — in-memory index with `build()`, `add()`, `lookup()`, `by_type()`, `by_tag()`, `by_component()`, `all_active()`
- `KnowledgeStore` — Markdown-on-disk backend: `add()`, `get()`, `search()`, `supersede()`, `list_all()`; ID sequence tracking via `.sequences.json`
- `Monday.learn()` — end-to-end implementation: validates type, builds CKO, persists via `KnowledgeStore`, publishes `KNOWLEDGE_ENTRY_CREATED` event, returns `LearnResponse`
- `Monday.search()` — keyword search across knowledge base; results include id, title, summary, entry_type, tags

### Changed
- `KnowledgeEntry` — aligned to MKS CKO schema: `created_at` (renamed from `created`), added `version`, `summary`, `created_by`, `updated_at`, `updated_by`, `relationships`, `type_fields`; `confidence` changed from `str` to `float` (default `1.0`)
- `KnowledgeStore.__init__` — now accepts `project_root: Path`; loads existing entries on init
- `Monday.__init__` — passes `project_root` to `KnowledgeStore`
- `EntryType`, `EntryStatus` — preserved as backward-compat aliases for `KnowledgeType` and `LifecycleStatus`

### Tests
- `tests/test_knowledge.py` — 64 tests (0 skipped); covers entry model, relationships, index, parser (with round-trip), and store (with disk I/O via `tmp_path`)
- `tests/test_monday.py` — 89 tests (5 skipped); `TestLearn` and `TestSearch` use isolated `tmp_path`; learn→search round-trip verified

### Total test counts
- **217 passed**, **22 skipped**, **0 failures**

---

## [0.2.1] — 2026-06-27 — Sprint 1.2 Pre-work: MKS

### Added
- `docs/MKS.md` — MondayOS Knowledge Specification v1.0; the canonical contract for all knowledge stored by MondayOS
- ADR-009: Decision to establish MKS as the formal product specification before Sprint 1.2 implementation

### Changed
- `docs/KNOWLEDGE_SYSTEM.md` superseded by MKS 1.0 (retained for reference)

### Notes
- MKS defines 12 knowledge types (Bug, Decision, Task, Sprint, Feature, Lesson, Pattern, Runbook, Documentation, Research, Weather, Experiment)
- MKS defines CKO base schema, ID rules, relationship graph model, lifecycle, versioning, 20 validation rules, and migration protocol across 5 storage backends
- Implementation of Sprint 1.2 (`KnowledgeParser`, `KnowledgeLoader`, `KnowledgeIndex`, `KnowledgeStore`) begins after this checkpoint

---

## [0.2.0] — 2026-06-27 — Sprint 1.1: Public API

### Added
- `monday/` package — stable public API boundary for MondayOS
- `Monday` class with five public methods: `ask()`, `learn()`, `search()`, `task()`, `status()`
- `MondayConfig` dataclass for configuring `Monday` instances
- Typed response dataclasses: `AskResponse`, `LearnResponse`, `SearchResponse`, `TaskResponse`, `StatusResponse`, `ModuleStatus`
- `monday/README.md` — external consumer documentation and the "import from monday only" rule
- `tests/test_monday.py` — 80 tests covering the public surface (71 passing, 9 skipped placeholders)
- ADR-007: Monday class as the stable public API boundary
- ADR-008: Typed response dataclasses for all public methods

### Changed
- `monday.status()` is the first substantially-implemented public method; returns live system health, version, session ID, per-module status, and uptime

---

## [0.1.1] — 2026-06-27 — Sprint 1: Package Architecture

### Added
- Six Python packages with clean public interfaces: `brain`, `events`, `knowledge`, `memory`, `search`, `tasks`
- `core/types.py` — shared primitive types (`EntityId`, `Timestamp`, `ModelId`, `ComponentName`)
- `EventBus` — fully implemented synchronous in-process event bus
- `SessionMemory` — fully implemented volatile in-process memory tier
- `Task.can_transition_to()` — status transition graph encoded and enforced
- `pyproject.toml` — project configuration with Ruff, Mypy, and pytest settings
- `conftest.py` — sys.path configuration for test discovery
- `tests/` — 111 test cases across 6 modules (82 passing, 29 skipped placeholders)
- `README.md` for each module documenting responsibility, interface, and boundaries

---

## [0.1.0] — 2026-06-27 — Foundation

### Added
- `docs/PROJECT_OVERVIEW.md` — What MondayOS is, the problem it solves, and core principles
- `docs/VISION.md` — Long-term product philosophy and success criteria
- `docs/ARCHITECTURE.md` — Layered system architecture, component design, data flow, and cross-cutting concerns
- `docs/ENGINEERING_STANDARDS.md` — Code style, type annotation requirements, testing coverage targets, Git standards, and security policy
- `docs/DOCUMENTATION_STANDARDS.md` — Documentation types, writing standards, ADR format, and freshness policy
- `docs/KNOWLEDGE_SYSTEM.md` — Schema and lifecycle for bug, decision, pattern, and runbook knowledge entries
- `docs/MEMORY_SYSTEM.md` — Three-tier memory architecture (session, project, agent) and storage design
- `docs/TASK_SYSTEM.md` — Task file schema, status lifecycle, priority system, and routing design
- `docs/ROADMAP.md` — Phase-based development plan with entry and exit criteria for Phases 1–4
- `docs/DECISIONS.md` — ADR log (ADR-001 through ADR-006): language choice, storage strategy, data format, model abstraction, Git-as-truth, and approval gates

---

## [0.1.0] — 2026-06-27 — Foundation

### Added
- `docs/PROJECT_OVERVIEW.md` — What MondayOS is, the problem it solves, and core principles
- `docs/VISION.md` — Long-term product philosophy and success criteria
- `docs/ARCHITECTURE.md` — Layered system architecture, component design, data flow, and cross-cutting concerns
- `docs/ENGINEERING_STANDARDS.md` — Code style, type annotation requirements, testing coverage targets, Git standards, and security policy
- `docs/DOCUMENTATION_STANDARDS.md` — Documentation types, writing standards, ADR format, and freshness policy
- `docs/KNOWLEDGE_SYSTEM.md` — Schema and lifecycle for bug, decision, pattern, and runbook knowledge entries
- `docs/MEMORY_SYSTEM.md` — Three-tier memory architecture (session, project, agent) and storage design
- `docs/TASK_SYSTEM.md` — Task file schema, status lifecycle, priority system, and routing design
- `docs/ROADMAP.md` — Phase-based development plan with entry and exit criteria for Phases 1–4
- `docs/DECISIONS.md` — ADR log (ADR-001 through ADR-006): language choice, storage strategy, data format, model abstraction, Git-as-truth, and approval gates
