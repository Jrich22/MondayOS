# MondayOS — Architectural Decision Records

**Last Updated:** 2026-09-02

This file is the canonical log of all architectural decisions made for MondayOS. Decisions are recorded in the format described in [DOCUMENTATION_STANDARDS.md](DOCUMENTATION_STANDARDS.md).

ADRs are append-only. A superseded decision is marked as such and a new ADR is written. The history of decisions is as important as the current decisions.

---

## ADR-001: Python as the Primary Implementation Language

**Date:** 2026-06-27  
**Status:** Accepted  
**Deciders:** Lead Software Engineering

### Context

MondayOS needs a primary implementation language. The platform integrates with AI model APIs, handles file I/O and Git operations, and will eventually serve a web dashboard. It must be approachable for engineers who may join the project and AI agents that will read and generate code within it.

### Decision

Python 3.11+ is the primary language for all MondayOS components.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| TypeScript/Node.js | Weaker typing ecosystem for data-heavy AI integrations; team expertise is stronger in Python |
| Go | Excellent for performance-critical systems; but the AI ecosystem (SDKs, tooling) is Python-native, which would require constant translation |
| Rust | High performance, memory safety; too high a learning curve for the exploratory Phase 1; revisit if performance becomes a genuine bottleneck |

### Consequences

- We gain access to the full Python AI/ML ecosystem natively.
- All major model SDKs (Anthropic, OpenAI, Ollama Python client) are used without adaptation.
- Performance will be adequate for Phase 1 and Phase 2; revisit for Phase 3 if concurrent task throughput demands it.
- Type annotations and Mypy are required to compensate for Python's runtime type system.

---

## ADR-002: File-Based Storage for Phase 1 (No Database)

**Date:** 2026-06-27  
**Status:** Accepted  
**Deciders:** Lead Software Engineering

### Context

MondayOS needs to store tasks, memory, and knowledge entries persistently. The question is whether to use a database from the start or begin with file-based storage.

### Decision

Phase 1 uses file-based storage exclusively. All persistent state lives in Markdown files with YAML frontmatter, tracked in Git. No database (relational or otherwise) is introduced in Phase 1.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| SQLite from day one | Adds query capability but loses human readability and Git-native diffability; premature before query needs are known |
| PostgreSQL | Appropriate for Phase 3+; vastly over-engineered for Phase 1 single-user local operation |
| JSON files | Less human-readable than Markdown; harder to edit by hand for debugging |

### Consequences

- All persistent state is human-readable and Git-diffable without tooling.
- Query capability is limited to file search and index scanning. This will become a bottleneck as the knowledge base grows — we expect to hit this wall in Phase 2.
- When Phase 2 introduces SQLite, the storage interface must not change. The database is an implementation detail hidden behind the memory and knowledge layer interfaces.
- Developers and AI agents can read and debug state with any text editor.

---

## ADR-003: Structured Markdown with YAML Frontmatter as the Universal Data Format

**Date:** 2026-06-27  
**Status:** Accepted  
**Deciders:** Lead Software Engineering

### Context

MondayOS has multiple types of persistent data: tasks, knowledge entries, memory, configuration. A consistent format across all of these reduces the cognitive and tooling overhead of working with the system.

### Decision

All structured data that is stored as files uses Markdown with YAML frontmatter. Machine-parseable fields go in the frontmatter; human-readable prose goes in the Markdown body.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| Pure JSON | Not human-readable for prose content; harder to write by hand |
| Pure YAML | Verbose for long prose; less familiar format for most engineers |
| TOML | Good for configuration; not well-suited for prose-heavy documents |

### Consequences

- Any standard Markdown parser combined with a YAML parser can read all MondayOS data files.
- Both humans and AI agents can read and write the format without special tooling.
- Schema validation is implemented in `core/` to enforce frontmatter structure on read and write.
- The format is stable and widely understood, reducing the risk of format obsolescence.

---

## ADR-004: Multi-Provider Model Abstraction (Provider Independence)

**Date:** 2026-06-27  
**Status:** Accepted  
**Deciders:** Lead Software Engineering

### Context

MondayOS integrates with multiple AI model providers: Anthropic Claude, OpenAI GPT, and local Ollama models. Without an abstraction layer, calling code becomes entangled with provider-specific SDKs and data models.

### Decision

All AI model calls go through a common `ModelClient` interface defined in `integrations/`. Provider-specific clients implement this interface. No layer above the integration layer imports a provider SDK directly.

The interface defines:
- `complete(prompt: Prompt, config: ModelConfig) -> ModelResponse`
- `stream(prompt: Prompt, config: ModelConfig) -> Iterator[ModelResponseChunk]`

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| LangChain or LlamaIndex as the abstraction | Adds a large dependency with its own abstractions and update cadence; we want control over the interface |
| Direct SDK usage throughout | Binds the entire codebase to a single provider; swapping providers requires changing code everywhere |

### Consequences

- Adding a new provider requires implementing the `ModelClient` interface in a new integration module; no other code changes.
- Provider-specific capabilities (streaming, function calling, vision) must be exposed through the common interface or as optional extensions.
- Integration tests use recorded fixtures; provider outages do not break the test suite.
- Cost and capability tracking is centralized in the integration layer.

---

## ADR-005: Git as the Source of Truth for All Persistent State

**Date:** 2026-06-27  
**Status:** Accepted  
**Deciders:** Lead Software Engineering

### Context

MondayOS produces many types of persistent artifacts: tasks, knowledge entries, memory, prompts, workflow definitions. These artifacts need to be durable, auditable, and recoverable.

### Decision

All persistent artifacts that matter — tasks, knowledge entries, project memory, prompts, workflow definitions, and configuration — are stored as files in the Git repository. Git provides version history, rollback, diff, blame, and collaboration for free.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| Object storage (S3) | No versioning without additional tooling; not human-readable |
| A database | Powerful for queries; loses the version history and human readability that Git provides |
| A combination of Git + database | Right for Phase 2+; premature complexity for Phase 1 |

### Consequences

- Any state change to the system is a potential Git commit. The commit history is the audit log.
- Large binary artifacts (model weights, training data) are not stored in Git. These are excluded in `.gitignore`.
- Git performance degrades as repository size grows. We will hit this limit if the knowledge base grows very large — mitigated by periodic archiving of old completed tasks.
- New contributors and AI agents can understand system state by reading files, not querying APIs.

---

## ADR-006: Human Approval Gates Are Non-Negotiable for Production-Impacting Actions

**Date:** 2026-06-27  
**Status:** Accepted  
**Deciders:** Lead Software Engineering

### Context

AI agents in MondayOS can take actions that affect production systems, external services, and irreversible state. The question is: should any of these actions be permitted to execute without human review?

### Decision

No AI agent may execute an action classified as `human-approval` without explicit, logged human approval. This constraint is enforced in `core/gates.py` and cannot be bypassed by agent configuration or workflow definition. It may only be changed by modifying core engine code — a deliberate barrier to accidental removal.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| Allow agents to bypass gates if confidence score is high | Confidence scores are not reliable enough to stake production safety on; this creates a false sense of security |
| Make gates configurable (can be disabled) | Configuration drift makes it easy to accidentally remove safety constraints in production; the gate must be a hard code-level guarantee |
| Trust agents with demonstrated track record | Track record is evidence of past behavior, not a guarantee of future behavior; production safety requires a structural guarantee |

### Consequences

- Human involvement in all production-impacting actions is structurally guaranteed, not just conventional.
- The system may be slower for some workflows because it must wait for human input.
- Users who want full autonomy will be frustrated. This friction is intentional — they should be building a track record first.
- As agent reliability is demonstrated, the set of actions classified as `human-approval` can shrink (via a new ADR); it cannot silently shrink through configuration.

---

## ADR-007: Monday Class as the Stable Public API Boundary

**Date:** 2026-06-27  
**Status:** Accepted  
**Deciders:** Lead Software Engineering

### Context

As MondayOS grows, external consumers (CLI tools, scripts, future REST API, dashboard) need to import from it. Without an explicit boundary, they will couple to internal module structure. When internal modules are refactored — `knowledge/store.py` changes shape, `tasks/task.py` gains a field — all external callers must change with it.

### Decision

All external access to MondayOS goes through the `Monday` class in the `monday` package. The rule is: external code imports from `monday`, never from `brain`, `tasks`, `knowledge`, or any other internal module. The `monday` package is the versioned public surface.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| Expose internal modules directly | Any internal refactor becomes a breaking change for external consumers |
| Functional API (module-level functions) | Harder to test, mock, and configure; no clear ownership of subsystem lifecycle |
| Protocol-based API (abstract class) | Adds indirection without benefit in Phase 1; revisit if multiple implementations are needed |

### Consequences

- Internal modules can be refactored, replaced, or restructured without breaking external consumers.
- The `monday` package absorbs all integration-point changes.
- Adding a new capability requires a new method on `Monday`, which is a deliberate gate on surface expansion.
- Testing external consumers requires mocking only `Monday`, not six internal classes.
- `Monday.VERSION` is a meaningful version string for the public contract, independent of internal versioning.

---

## ADR-008: Typed Response Dataclasses for All Public Methods

**Date:** 2026-06-27  
**Status:** Accepted  
**Deciders:** Lead Software Engineering

### Context

Public methods must return something. The options are: raw dicts, a generic `Result[T]` type, or dedicated typed dataclasses per method. The choice affects discoverability, IDE support, stability, and how breaking changes are communicated.

### Decision

Each `Monday` public method returns a dedicated typed dataclass: `AskResponse`, `LearnResponse`, `SearchResponse`, `TaskResponse`, `StatusResponse`. These types are exported from `monday/__init__.py` and are part of the public contract.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| Return `dict` | No IDE autocomplete, no type safety, silent contract breaks |
| Generic `Result[T, E]` | Adds error-handling boilerplate at every call site; deferred until error patterns are known |
| Single `MondayResponse` type | One large type mixes fields from unrelated operations; confusing to consumers |

### Consequences

- Callers get IDE autocomplete and static type checking on responses.
- Adding a field to a response type is non-breaking (old callers ignore it).
- Removing or renaming a field is a breaking change — reflected in a version bump.
- The dataclass definitions establish the contract for future implementations before those implementations exist; tests can validate field presence and types before logic is wired up.

---

## ADR-009: MondayOS Knowledge Specification (MKS) as the Canonical Contract

**Date:** 2026-06-27  
**Status:** Accepted  
**Deciders:** Lead Software Engineering

### Context

The `knowledge/` module was scaffolded in Sprint 1 with only four entry types (Bug, Decision, Pattern, Runbook) and no formal data model contract. Sprint 1.2 requires implementing `KnowledgeParser`, `KnowledgeLoader`, `KnowledgeIndex`, and `KnowledgeStore`. Without a formal specification, each implementation would make independent field decisions, leading to incompatible data and no migration path between storage backends.

Additionally, MondayOS must support domain-specific knowledge beyond software engineering (weather observations, experiments) and complex cross-entry relationships (bugs leading to decisions leading to features). These requirements were not captured in the ad hoc KNOWLEDGE_SYSTEM.md v0.1.0 document.

### Decision

A formal product specification — the MondayOS Knowledge Specification (MKS) — is established as the canonical contract for all knowledge stored by MondayOS. The MKS defines:

- The Canonical Knowledge Object (CKO) with all fields and their rationale
- 12 knowledge types with required fields, optional fields, body structure, and validation rules
- ID format and the type prefix registry
- A first-class relationship model encoding a directed knowledge graph
- The lifecycle state machine and valid transitions
- Versioning rules and the supersession protocol
- A `StorageBackend` protocol enabling transparent migration between Markdown, SQLite, PostgreSQL, Neo4j, and vector databases
- A migration protocol that changes no public API

Implementations are **non-conforming** if they do not satisfy MKS validation rules. KNOWLEDGE_SYSTEM.md v0.1.0 is superseded by MKS 1.0.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| Expand KNOWLEDGE_SYSTEM.md informally | Produces documentation, not a contract; validation rules remain implicit; different implementers would make conflicting field decisions |
| Define schema in Python code only | Code is the implementation, not the contract; changing the implementation would silently change the contract; no human-readable source of truth |
| Adopt an existing knowledge schema (schema.org, etc.) | Too general; not aligned with software engineering and AI agent workflows; would require constant mapping overhead |

### Consequences

- All knowledge implementations (parser, loader, index, store) have an unambiguous field-by-field specification to conform to.
- The `StorageBackend` protocol means the knowledge backend can be changed at any time without changing `KnowledgeStore`, `Monday`, or any caller above them.
- Relationships are first-class in the data model from day one, making a future Neo4j migration a straight import rather than a data restructuring.
- The 12-type catalogue can be extended by adding a new type to the MKS; no existing entries are invalidated.
- Field-level validation rules (VAL-001 through VAL-020) give `KnowledgeParser` a clear acceptance test.
- `knowledge/entry.py` must be updated to reflect all 12 types before Sprint 1.2 implementation begins.

---

## ADR-010: YAML as the Workflow Definition Format

**Date:** 2026-06-28  
**Status:** Accepted  
**Deciders:** Lead Software Engineering

### Context

Sprint 1.6 introduces a workflow system — the ability to define multi-step sequences of MondayOS operations, execute them end-to-end, and log every step. A workflow definition format must be chosen.

Requirements:
- Human-readable and human-editable without a special tool
- Supports structured data (step lists, input declarations, nested config)
- Easily parsed in Python without external schema validation tools
- Compatible with the existing `PyYAML` dependency already in `pyproject.toml`
- Step definitions must be named, ordered, and independently typed

### Decision

Workflow definitions are stored as YAML files in `workflows/definitions/`. Each file defines one workflow. The schema is:
- `name`, `version`, `description` — workflow metadata
- `inputs` — named input variables with description, required flag, and default
- `steps` — ordered list of named step objects; each step has `id`, `type`, and type-specific `input` fields
- `triggers` — advisory list of trigger labels (not enforced in Phase 1)

Step types in Phase 1: `ask`, `search`, `learn`, `task_create`, `task_start`, `task_complete`, `human_approval`.

Template substitution in step inputs uses `{step_id.output_key}` and `{inputs.variable_name}` syntax, resolved at runtime from the accumulated execution context.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| JSON | Valid subset of YAML; less readable for humans writing multi-line strings and comments; already using YAML elsewhere |
| TOML | Good for config files; no native list-of-objects semantics — step ordering and per-step structure are awkward |
| Python code (decorated functions) | Maximally flexible; but tightly couples workflow authorship to Python knowledge; hard for AI agents to generate safely; not inspectable without running the code |
| Custom DSL | Full control; high implementation cost; no off-the-shelf parser; adds a language-maintenance burden |

### Consequences

- Any engineer (or AI agent) can read and write workflow definitions without understanding the execution engine.
- YAML's multi-line string support makes `message:` blocks in `human_approval` steps readable.
- The `WorkflowDefinition.from_yaml()` class method is the single loading point; schema changes are isolated there.
- Future phases can add new step types by extending `StepType` without changing the YAML format.
- Version field in every definition means execution logs can reference the exact definition version that ran.

---

## ADR-011: The Growth Workspace Is the Isolation Boundary for Marketing State

**Date:** 2026-08-26  
**Status:** Accepted  
**Deciders:** Lead Software Engineering

### Context

The [Growth Bot](GROWTH_BOT.md) is one service operating marketing for many MondayOS projects. Each project has its own brand, audience, campaigns, platform accounts, and metrics, and none of it may reach another project. The failure this must prevent is publishing one project's content to another project's audience — an error that is public, unrecoverable, and fatal to the product's credibility.

The design question is whether project scoping is a property of the data model or a filter applied to shared storage.

### Decision

Marketing state is partitioned by **Growth Workspace**, one per project, and the workspace is the addressable unit. Reasoning about a project loads that project's workspace and has no way to name another. There is no shared content store, no shared audience table, and no shared metrics table with a `project_id` column.

A workspace stores platform credentials by **reference**: platform, account handle, account ID, and the *name* of a secret. The secret resolves at publish time and appears in no workspace file, no log, and no agent prompt.

Portfolio-level reporting reads **per-project aggregates** (`aggregates.json`) — counts, rates, and deltas — and never reads a workspace. An aggregate carries no copy, no media, no audience definition, and no account binding.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| Shared storage with a `project_id` filter | One missing predicate leaks a client's content to another client's audience. The correctness of every read depends on every query author, forever. |
| Shared storage with row-level enforcement | Stronger than a filter, but presumes a database. Phase 1 is file-based per [ADR-002](#adr-002-file-based-storage-for-phase-1-no-database), and the enforcement point would have to be rebuilt anyway. |
| Separate service instance per project | Perfect isolation; abandons the reusable-service goal, and multiplies deployment and upgrade cost by the number of projects. |
| Store credentials in the workspace file | Makes the workspace self-contained. It also puts OAuth tokens in Git with full version history, so a rotated token stays readable forever in the object store. |
| Portfolio reads workspaces directly | Simplest to build; makes the portfolio view the one component that can read every project, which is precisely the component most likely to leak. |

### Consequences

- Cross-project leakage requires a deliberate act, not an omission. This is the property the whole design exists to buy.
- Genuinely cross-project features are constrained by design. Portfolio comparison works because aggregates are enough; "reuse this post across projects" is deliberately not supported and would need a new ADR.
- Credential handling deviates from [ADR-005](#adr-005-git-as-the-source-of-truth-for-all-persistent-state). Workspace state is Git-tracked and diffable; the secrets it names are not. The binding stays reviewable, which preserves the intent of ADR-005 without its cost here.
- `aggregates.json` becomes a security-relevant interface. Any field added to it must be checked for whether it discloses workspace content, and that check belongs in review.
- Isolation needs a test suite that asserts the negative — that project A's context cannot resolve project B's content. Cross-workspace access attempts are tracked as an incident metric with a target of zero.

---

## ADR-012: Publishing Is a Gated Action on the Existing Approval Gate

**Date:** 2026-08-26  
**Status:** Accepted  
**Deciders:** Lead Software Engineering

> **Amended 2026-08-26 during implementation.** The action is named `publish_content`, not
> `publish`. `Monday.publish()` already means Confluence *document* publishing, so a bare
> `publish` would be ambiguous about whether that path is covered — and it is not currently gated
> at all. `publish_content` names the Growth Bot's outbound action unambiguously and leaves
> `publish_document` available if the Confluence gap is closed later. That gap is real and is
> tracked separately; it is not a Growth Bot regression.

### Context

The Growth Bot specification requires that nothing publishes without human approval, with per-item approval of project, platform, account, media, copy, CTA, URL, date, and time. The Multi-Agent Runtime already implements a human approval gate for sensitive actions — `agents/gates.py::ApprovalGate`, documented in [APPROVAL_GATES.md](APPROVAL_GATES.md) — covering `commit`, `push`, `secrets`, `live_trade`, and `destructive`.

The question is whether the Growth Bot implements its own marketing-specific approval workflow or extends the existing gate.

### Decision

`publish_content` is registered as a gated action in `GATED_ACTIONS`. A Growth Bot run declaring an intent to publish is blocked unless a human approval is present, by the existing mechanism. The Growth Bot implements **no** approval logic of its own.

The publishing connector is the single choke point for outbound content, and it is deliberately not an agent: it takes an approved item and calls a platform API, making no judgements. Every pause scope is enforced there.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| A dedicated marketing approval workflow | Marketing approval feels domain-specific, but the security property is identical: a human authorizes an irreversible external action. Two implementations of one property means two places to audit and two places to get it wrong, and the second one is always the one that is out of date. |
| Approval as a Content Item status field only | A status field is data, not enforcement. Anything that can write the field can approve the content. |
| A `human_approval` workflow step ([ADR-010](#adr-010-yaml-as-the-workflow-definition-format)) | Correct for orchestrating a sequence; it gates a workflow run, not the publish call, so it cannot stop a publish reached by any other path. |
| Model the publishing connector as an agent role | Consistent with the specification's "Publishing Bot". It would put a language model on the one code path that must be deterministic and must reject anything unapproved. |

### Consequences

- The security-critical approval policy stays in one module with one test suite, and an audit of MondayOS approvals is one file.
- Publishing inherits the runtime's existing guarantees: a blocked run does not act, and every run — allowed or blocked — is logged and reviewable.
- The Growth Bot inherits the gate's ergonomics, including `--approve`. Whether marketing approval needs an interface beyond the CLI is a product question, not an enforcement question, and is deliberately left open.
- `GATED_ACTIONS` now spans two domains. Adding `publish_content` is one entry; the runtime remains marketing-unaware, which is the point.
- Approval delegation is unresolved: the gate models approval as present or absent, not as attributable to a named approver. Recorded as an open question against increment 2.

---

## ADR-013: An Approval Fingerprint Defines a Material Change

**Date:** 2026-08-26  
**Status:** Accepted  
**Deciders:** Lead Software Engineering

### Context

The Growth Bot specification states that if anything about an approved item changes, approval resets. The intent is unambiguous and the literal rule is unimplementable: taken strictly, adding an internal tag or correcting a typo in a private note revokes an approval, and users learn to work around the system. Taken loosely, "material" is decided case by case in whatever code path performs the edit, and eventually one path decides wrongly.

The system needs one mechanism that makes the boundary exact.

### Decision

Every approval records an **Approval Fingerprint**: a hash over the fields a human actually approved.

```
fingerprint = hash(project, platform, account, media, copy + CTA,
                   destination_url, scheduled_datetime)
```

An item is approved if and only if its current fingerprint equals its approved fingerprint. Any divergence returns it to Ready for Review automatically. The publishing connector recomputes the fingerprint at publish time and refuses any mismatch.

Fields outside the fingerprint — internal notes, tags, campaign labels, ordering hints — change freely without disturbing an approval.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| Reset approval on any write to the item | Faithful to the specification's letter. It makes routine internal edits destructive and trains users to avoid the workflow, which is a worse security outcome than the precision it buys. |
| A per-field `resets_approval` flag | Equivalent in effect, harder to read. The set of approval-relevant fields ends up scattered across a schema instead of stated in one line. |
| An explicit `invalidate_approval()` call at each edit site | Correctness depends on every current and future edit path remembering to call it. The first one that forgets publishes unapproved content silently. |
| Human re-confirmation prompt on edit | Puts the judgement of materiality on the user at the moment they are least likely to think carefully about it. |

### Consequences

- An approved-and-then-edited item cannot publish, and no code needs to remember to check. The check is a comparison at the one point that matters.
- The fingerprint field list becomes a security-relevant contract. Adding a field to a Content Item requires an explicit decision about whether it is fingerprinted, and that belongs in the review checklist.
- Rescheduling requires re-approval, because the scheduled time is fingerprinted. This is intended — a launch post approved for Tuesday morning is not approved for Friday evening — and it will be the rule users push back on first.
- Transient publish failures retry under the same fingerprint, since nothing about the approved content changed. Non-transient failures route to Manual Review, where the assumption behind the approval is re-examined.
- Approval integrity becomes measurable: items published with a non-matching fingerprint is a metric with a target of zero, and any non-zero value is an incident.

---

## ADR-014: The Growth Brain Is a Reasoning Layer, Not a Separate Bot

**Date:** 2026-08-26  
**Status:** Accepted  
**Deciders:** Lead Software Engineering

> **Accepted 2026-08-30 on implementation (TASK-0066).** This ADR sat Proposed for five
> increments because of its own stated dependency: the Brain "requires measured outcomes to
> reason over", and until increment 5 there were none. That layer now exists — performance
> events with explicit provenance, metric formulas as pure functions, and aggregation across
> content, campaign, platform and workspace — so the Brain has something to reason *from*
> rather than *about*.
>
> One thing the implementation settled that the ADR left open: the Brain is **fully
> deterministic**. It calls no model. Every recommendation is assembled from measured numbers
> by explicit rules with stated thresholds, so the same workspace state yields byte-identical
> output. The ADR's requirement that every recommendation carry evidence and a falsifier is
> enforced by the type — a `Recommendation` constructed without either raises — and the
> requirement that a performance explanation stay a hypothesis until an experiment confirms it
> is enforced by returning a different class: below the minimum sample, the engine returns a
> `Hypothesis` carrying an unconfirmed marker, never a `Recommendation`.

### Context

The Growth Bot's value depends on continuously answering why — what to talk about next, why last week's content performed as it did, what competitors discuss, what is trending, what the library is missing, and what to build to drive leads. Without that, the service generates content on a schedule and cannot improve.

The question is whether this reasoning is a sibling service alongside the Growth Bot or a layer inside it.

### Decision

The Growth Brain is a layer **inside** the Growth Bot, operating on a single Growth Workspace, subject to [ADR-011](#adr-011-the-growth-workspace-is-the-isolation-boundary-for-marketing-state). It coordinates the `research` and `analytics` roles for evidence gathering and owns the synthesis; it does not become a role or a service of its own.

Two constraints govern its output:

1. **Every recommendation carries its evidence** — observation, inference, confidence, and what would falsify it.
2. **A performance explanation is a hypothesis until an experiment confirms it.** Confirmed findings are written to `knowledge/` as knowledge entries; unconfirmed ones expire.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| A separate Growth Brain service | Reasoning needs the full workspace — brand, audience, library, metrics, campaign history. A separate service either duplicates that state or needs cross-workspace read access, and ADR-011 exists to prevent exactly that access. |
| A new `growth-strategy` agent role | Roles are stateless executors resolved to a provider. The Brain is stateful: it holds standing answers and revises them as evidence lands. That state has to live in the workspace. |
| Generate recommendations on demand during weekly planning | Simpler. It also means the Brain only thinks on Sunday, while trends, competitor activity, and performance data arrive continuously — the most valuable signals would be stale or missed. |
| Let the Brain state conclusions without evidence | Cheaper to build, and it produces confident narratives about small samples on opaque platform distribution. Unfalsifiable marketing advice at machine scale is the most expensive failure mode available to this service, because it costs human time to evaluate and is indistinguishable from insight. |

### Consequences

- Recommendation quality becomes auditable. A human can check the evidence rather than judging the assertion.
- The Brain's confidence is calibratable: stated confidence compared against confirmed outcomes exposes systematic overconfidence, which is tracked as a success metric.
- Confirmed findings compound in `knowledge/` and are available to the rest of MondayOS, consistent with [ADR-009](#adr-009-mondayos-knowledge-specification-mks-as-the-canonical-contract).
- The Brain requires measured outcomes to reason over, so it lands after measurement — increment 5, not increment 1. The service is deliberately less intelligent until it has evidence, rather than confidently wrong sooner.
- Cross-project pattern learning is foreclosed by ADR-011. A tactic proven on one project does not automatically inform another, which is a real cost accepted in exchange for isolation.

---

## ADR-015: Conversations Are Project-Scoped Files, and MondayOS Owns the Record

**Date:** 2026-09-02  
**Status:** Accepted  
**Deciders:** Lead Software Engineering

### Context

The AI Workspace makes MondayOS the primary interface for conversational work. Today that work happens in ChatGPT and Claude, where the conversation *is* the storage: close the tab and the reasoning is gone, and nothing in it is reachable from any other tool.

Moving that into MondayOS raises the question of where a conversation actually lives. A browser-side store would be the fastest thing to build, and the dashboard already keeps client state. It would also mean MondayOS could not read its own conversations — no retention, no knowledge capture, no briefing, no agent that can see what was already discussed.

### Decision

A conversation is a **file in MondayOS**, stored under `workspace/conversations/{project}/CONV-NNNN.md` as Markdown with YAML frontmatter, per [ADR-002](#adr-002-file-based-storage-for-phase-1-no-database) and [ADR-003](#adr-003-structured-markdown-with-yaml-frontmatter-as-the-universal-data-format).

Three properties follow, and they are the point of the decision:

1. **The browser is a cache, never the record.** The dashboard may hold conversation state for responsiveness, but it is reconstructed from MondayOS on load. Losing the browser loses nothing.
2. **Conversations are project-scoped by directory.** The project is a path segment, not a filter applied at read time, so a query cannot accidentally span projects.
3. **Only user-visible content is persisted.** Message text, role, timestamps, and the provider/model that produced a response. Not hidden reasoning, not provider-private chain-of-thought, not raw provider payloads.

Each project directory owns its own `.sequences.json`, following the same reasoning as [ADR-011](#adr-011-the-growth-workspace-is-the-isolation-boundary-for-marketing-state): a shared counter would let one project infer another's conversation volume from gaps in its own IDs.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| Browser `localStorage` as the canonical store | The stated goal is that information stops living inside one chat session. A browser-owned store reproduces exactly the failure being solved, and makes conversations invisible to every other part of MondayOS. |
| SQLite | A real option, and the right one eventually. It is premature here: ADR-002 chose files deliberately, conversations are naturally document-shaped, and the file format is diffable and greppable during the increments where the schema is still moving. |
| One file per message | Better write concurrency, far worse readability. A conversation is read as a whole far more often than one message is written, and 200 files per conversation makes the store hostile to inspection. |
| Persist provider reasoning traces | They are the highest-value debugging artifact and the highest-risk thing to store: provider-private, frequently containing verbatim context, and not something a human ever asked to keep. |

### Consequences

- Conversations survive browser refresh, dashboard restart, and process restart, because none of those own the data.
- Retention, knowledge capture, and future briefing all read conversations through the ordinary knowledge and file interfaces rather than a bespoke API.
- Conversation content is runtime state and is gitignored. It is user data, not source.
- Very long conversations will eventually need pagination or rollup; a single file is fine at increment-1 scale and will not be at ten thousand messages.
- Concurrent writes to one conversation are last-write-wins. Acceptable for a single-operator workstation, and a real constraint to revisit before multi-user.

---

## ADR-016: The Context Snapshot Is an Attributed, Budgeted Assembly

**Date:** 2026-09-02  
**Status:** Accepted  
**Deciders:** Lead Software Engineering

### Context

For MondayOS to answer "what are we currently building?" it must put project reality in front of a model. The tempting implementation is to gather everything available — repository, docs, tasks, knowledge — and send it. That fails on cost, on latency, and most importantly on trust: when the answer is wrong, nobody can tell whether the model reasoned badly or simply never received the relevant fact.

### Decision

Context is assembled into an **immutable, attributed, budgeted `ContextSnapshot`**.

- **Attributed.** The snapshot is a list of named `ContextSource`s, each recording what system it came from, how many items it contributed, its size, and whether it was truncated. The question "why did Monday know this?" is answerable from the stored snapshot alone, and so is "why did Monday *not* know this?"
- **Budgeted deterministically.** Sources are gathered in a fixed priority order with explicit per-source and total character caps. The same project state produces the same snapshot. There is no model call in the assembly path — the Context Engine is deterministic, like the Growth Brain before it.
- **Immutable and referenced.** A message records the snapshot id it was answered against, so a response can be explained months later against the context that actually produced it, not against what the project looks like now.
- **Truncation is visible, never silent.** A truncated source says so, in the snapshot and in the UI.

The Context Engine is an **OS-level service**, not a bot and not a project feature. It reads other subsystems through narrow adapters and owns no domain state of its own.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| Send the whole repository | Cost and latency aside, it buries the relevant fact in noise and still misses anything outside the tree (tasks, knowledge, git history). Large context is not the same as good context. |
| Semantic/vector retrieval now | Almost certainly right later, and it is a poor first move: it makes retrieval non-deterministic and unexplainable before there is any baseline to compare against. Increment 2 adds retrieval on top of an attributed baseline. |
| Let each caller assemble its own context | Guarantees divergence. The isolation rules and the secret-redaction pass would be reimplemented per caller, and one of those copies would eventually be wrong. |
| Assemble context with a model | Introduces a model call before the model call, non-deterministically, and makes the "why did Monday know this" question unanswerable. |

### Consequences

- Context assembly is unit-testable without a provider, and cross-project leakage is directly assertable.
- Snapshot size is bounded and predictable, which makes provider cost predictable.
- A stale snapshot is possible: the project may change after assembly. Snapshots are timestamped and surfaced in the UI so staleness is visible rather than assumed away.
- Deterministic budgeting will sometimes drop the one fact that mattered. That is a real cost, accepted because the alternative — silently including everything — hides the same failure instead of exposing it.

---

## ADR-017: Project Context Isolation Is Enforced by the Engine, and Adapters Fail Closed

**Date:** 2026-09-02  
**Status:** Accepted  
**Deciders:** Lead Software Engineering

### Context

The AI Workspace reads across sourcingBOT, Cue, WeatherBot, Growth and MondayOS itself, and sends what it reads to an external provider. These are different products with different confidentiality expectations. A context bug is not a display glitch: it puts one project's private material into a prompt about another, in a request that leaves the machine and cannot be recalled.

[ADR-011](#adr-011-the-growth-workspace-is-the-isolation-boundary-for-marketing-state) established this boundary for marketing state. This ADR generalises it to OS-level context assembly.

### Decision

**One snapshot serves exactly one project, and isolation is enforced where context is built rather than where it is displayed.**

1. Every adapter receives a resolved project and its root path, and has no argument that could name a second project.
2. Adapters **fail closed**. An adapter that errors contributes an empty source carrying the error, and the snapshot is still built. It never falls back to unscoped data, and a failure to scope is never a reason to widen scope.
3. **Secrets never enter a snapshot.** Every assembled string passes `core.redaction` before it can reach a prompt or be persisted, and `.env` files, key material and token-shaped values are excluded at the adapter level as well. This is defence in depth: the adapter is meant not to read them, and redaction assumes it did.
4. Conversation storage is project-scoped by directory (ADR-015), so conversation history cannot cross projects either.
5. Cross-project leakage has **explicit tests**, not merely careful code.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| Filter context at the UI layer | The prompt is composed server-side. Filtering at the edge leaves the leak in the request that already left the machine. |
| A shared cross-project context pool with tags | Every retrieval bug becomes a disclosure. Tag-based scoping fails open by default: forget the filter and you get everything. |
| Trust adapters to be correct | Adapters read real filesystems and shell out to git. They will fail in ways not anticipated here; the question is whether failure defaults to empty or to unscoped. |
| Redact only before sending to a provider | Snapshots are persisted and shown in the UI. A secret that reaches storage has already leaked, whether or not a provider saw it. |

### Consequences

- A project with unreadable or missing sources produces a thin snapshot rather than a broad one. That is the intended failure direction.
- Fail-closed adapters can mask a real misconfiguration as "no context". Adapters therefore record the error in the source, so a thin snapshot is diagnosable rather than mysterious.
- Cross-project synthesis — "what did we learn on Cue that applies to sourcingBOT?" — is foreclosed by default. It is a genuine capability loss, accepted as the price of the guarantee, and would need its own ADR and an explicit human-authorised path.

---

## ADR-018: The Responder Seam Is the Extension Point for Model Routing

**Date:** 2026-09-02  
**Status:** Accepted  
**Deciders:** Lead Software Engineering

### Context

The end-state routes work between providers, model tiers, and specialised agents based on what is being asked. Building that router now would mean designing a routing policy before there is a single conversation to route, and the policy would be guesswork.

Building nothing is equally bad: if the workspace calls a provider directly, adding routing later means rewriting the conversation and context layers that should not care.

### Decision

The workspace depends on a **`WorkspaceResponder` protocol**, never on a provider:

```
WorkspaceResponder.respond(WorkspaceRequest) -> WorkspaceReply
```

Increment 1 ships one implementation, `ProviderWorkspaceResponder`, which wraps a single MondayOS `AIProvider` from the existing abstraction ([ADR-004](#adr-004-multi-provider-model-abstraction-provider-independence)). A future `RoutingWorkspaceResponder` implements the same protocol and selects per request, and neither the Conversation model, the Context Engine, nor the service changes.

Two constraints make the seam real rather than decorative:

- **No vendor SDK is imported anywhere in the workspace package.** The provider arrives injected. Which model runs is MondayOS configuration.
- **The request carries what a router would need to decide** — project, message, context snapshot, history — rather than a pre-rendered prompt string. A responder handed only a finished prompt could not route on anything.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| Call `AIProvider` directly from the service | `AIProvider` is one model. Routing is a choice *between* providers, so the seam has to sit above it — otherwise the future router has nowhere to stand. |
| Build the full router now | The routing policy would be invented rather than observed. Routing rules written before any real traffic are the kind of thing that gets deleted. |
| Pass a rendered prompt to the responder | Cheaper, and it destroys the information a router needs. Once context is flattened to a string, "which model suits this request" is unanswerable. |
| Extend `AIProvider` with routing | Conflates two responsibilities: a provider executes a call; a router chooses one. Provider implementations would each need to know about the others. |

### Consequences

- Increment 4 adds routing by adding a class, not by touching conversations or context.
- Tests inject a fake responder and never make a network call.
- Provider identity and model are recorded on each assistant message, so routing decisions become auditable the moment routing exists.
- One indirection is added for a capability not yet present. That is the intended trade: the seam costs a protocol now and saves a rewrite later.
