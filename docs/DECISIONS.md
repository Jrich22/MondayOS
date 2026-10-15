# MondayOS — Architectural Decision Records

**Last Updated:** 2026-10-15

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

---

## ADR-019: Reasoning is a deterministic layer between retrieval and generation

**Status:** Accepted
**Date:** 2026-09-03
**Context:** TASK-0080 — reasoning layer

### Context

Retrieval worked. Grounding worked. Hallucinations were rare. And Monday still
behaved like a document search engine: asked what to build next, it reported which
documents it lacked.

The cause was structural, not a prompting failure. Nothing in the system had the
job of *concluding* anything. Retrieval produces documents; a model handed
documents summarises them. There was no component between the two whose output was
a judgement, so no amount of prompt tuning could produce one reliably.

The obvious fix — instruct the model to be more strategic — was rejected. It would
place the reasoning inside the generation step, where it is unobservable,
untestable, and revised every time the model changes.

### Decision

Reasoning becomes a first-class layer, `reasoning/`, running after retrieval and
before generation:

```
question -> retrieve -> understand -> reason -> recommend -> score -> narrate
```

It produces an `Assessment` — typed claims, ranked recommendations, gaps, and
computed confidence — deterministically, from the existing project index,
relationship graph, task store and git history. The model's remaining job is to
**narrate** that assessment.

Three commitments make this safe.

**Every claim is typed.** `FACT`, `INFERENCE` and `RECOMMENDATION` are distinct
kinds, and each carries the rule that produced it. The low hallucination rate came
from an instruction never to infer; removing that instruction to gain a strategic
voice would have cost the trust that made the voice worth having. Typing the claims
is what lets inference happen safely — a labelled conclusion carrying its own
derivation can be rejected by a reader, where an unmarked one must be trusted.

**Confidence is computed, never requested from a model.** A model asked to rate
itself produces a number tracking how confident the prose sounds, not how strong
the evidence is — authority without measurement, failing hardest on a well-written
answer built on nothing. Scores come from evidence: corroboration across
independent source kinds, inference chain length, contradiction, recency. Every
score carries its own reasons, because a percentage nobody can argue with is worse
than no percentage.

**The existing graph is reused, not replaced.** `intelligence.RelationshipGraph`
already models tasks, decisions, files, symbols, tests, commits, PRs and knowledge.
It was built in Increment 3 and used only as a lookup table. Reasoning reads it.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| A stronger system prompt | Puts reasoning where it cannot be tested or asserted, and silently drifts with every model change. The behaviour would be a tendency, not a capability. |
| Ask the model for confidence scores | Produces a plausible number that measures nothing. Indistinguishable on screen from a computed one, which would destroy the only signal telling a reader how much to trust the rest. |
| A second, purpose-built knowledge graph | ADR-017's rule against parallel storage. The graph already models every node kind the reasoning needs; the gap was that nothing reasoned over it. |
| Let reasoning create tasks automatically | A layer that silently files work into a real backlog is one people stop asking questions of. Gaps propose payloads; a human approves the write. |
| Embeddings to relate evidence | Same reason as ADR for the index: retrieval that cannot explain itself cannot be audited, and a similarity-derived edge is a claim the project never made. |

### Consequences

- Strategic answers are assertable in tests. The same repository produces the same
  assessment, so "does Monday reason correctly" is a test rather than a vibe.
- Reasoning survives model changes. Only the narration depends on the provider.
- A failing rule costs its own conclusion, never the answer; a failing engine costs
  the assessment, never the conversation.
- Two registers now exist. Routing is pattern-based and deliberately narrow — a
  lookup answered with strategy is worse than a strategic question answered
  factually.
- Confidence in this repository runs lower than a reader may expect. That is the
  design working: file-count heuristics genuinely are weak evidence, and the score
  says so rather than flattering the analysis.

---

## ADR-020: Initiatives are the unit of product reasoning

**Status:** Accepted
**Date:** 2026-09-03
**Context:** TASK-0081 — initiative intelligence

### Context

ADR-019 gave MondayOS the ability to conclude things. It still concluded them
about the wrong nouns. Every claim it could make was about a file, a task, a
commit or a decision — the artefacts engineers touch — and none of those is what
a product is made of. "How is Billing going?" has no answer at artefact level,
because Billing is not a file.

The gap showed up as advice that was locally correct and strategically useless:
*"workspace/service.py is under-tested"* is true and may matter to nothing anyone
is shipping.

### Decision

Introduce `initiatives/`: a first-class model of business capabilities, assembled
from the work that builds them. Reasoning operates on initiatives first and drills
into implementation only when it changes a decision.

Initiatives are **discovered** from what the project already states — declaration,
task-title prefixes, capability documents, packages — and never from similarity or
clustering. Every membership carries the reason it was assigned, so a wrong
grouping can be corrected rather than merely disbelieved.

Initiatives may also be **declared** in `config/initiatives.json`. This is the only
part of the subsystem that is a system of record, and it exists because discovery
has a hard ceiling: a capability agreed in planning and never started is invisible
to every repository signal. Declaration is what makes reasoning about a *roadmap*
possible rather than reasoning about an inventory.

Executive recommendations additionally split one score into three — **evidence
strength**, **recommendation confidence** and **execution risk** — and carry the
alternatives that lost, with the reason each lost.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| Cluster artefacts by similarity | A grouping nobody can explain is one nobody can correct. The first misfiled capability would be unfixable, and every number computed from it silently wrong. |
| Derive initiatives only, no declaration | Cannot see planned work. A roadmap layer that structurally cannot represent "committed but not started" is not a roadmap layer. |
| Declaration only, no discovery | Accurate and unused. It requires maintenance nobody does, and goes stale exactly when the project moves fastest. |
| Treat every package as an initiative | Produced 57 "initiatives" including `Runbook` and `Task 0051`. A roster that lists everything distinguishes nothing. |
| One blended confidence per recommendation | Hides the case that matters most: strong evidence for a risky change. Averaging reports "rewrite the scheduler" and "add a test" as similar. |
| Compute progress from file counts | A denominator that moves when someone splits a module in two. Reporting maturity signals is less satisfying and more honest. |

### Consequences

- Reasoning answers product questions. "What should we build next" is answered in
  capabilities, with implementation as the drill-down.
- Health leads on blockers rather than percentage, because a blocked capability at
  80% needs attention more than a healthy one at 20%.
- An initiative with no tasks reports no percentage. Some surfaces will want one
  anyway; they should show the maturity signals instead of inventing it.
- Declared initiatives put a small maintenance burden on the user. That is the
  cost of being able to reason about work that has not started.
- Three scores per recommendation is more to read. They disagree often enough that
  collapsing them would lose the signal — which is the reason for the change.

---

## ADR-021: Identity Policy — Sequential for Tracked Records, Hybrid for Runtime

**Status:** Accepted
**Date:** 2026-09-07
**Context:** Foundation hardening — identity allocation

### Context

MondayOS allocated sequential ids from a per-store JSON counter. Five stores each
had their own implementation, and the design rested on an assumption that turned
out to be false: that a counter plus a disk scan makes reuse impossible.

It does not, and the gap is arithmetic rather than a bug. Two branches diverging
from the same counter hold identical state; a deterministic allocator applied to
identical state produces identical output. `flock` cannot see another machine,
and atomic writes make a write durable rather than unique. Both collisions were
reproduced before this work: two clones of one base each allocated `RES-0139`,
and a fresh clone carrying this repository's stale committed counter (135, while
local work had reached 138) allocated `RES-0136`, `RES-0137` and `RES-0138`
straight over live records.

A second fact turned out to matter more than expected. **Git already provides a
collision-detection point, but only for records it tracks.** Two branches each
writing `tasks/active/TASK-0080.md` produce an add/add merge conflict — the
ambiguity surfaces exactly when the two histories first meet. Two branches each
writing an ignored `knowledge/runtime/research/RES-0139.md` never meet at all.

### Decision

Identity policy is determined by **whether a record enters git**, and is declared
explicitly on each namespace as `IdentityPolicy`.

**`TRACKED_SEQUENTIAL`** — `TASK`, `DEC`, `DOC`, `PAT`, `SPR`. Ids stay short and
sortable. Local uniqueness comes from an `flock` around allocation. Cross-branch
duplicates remain possible and are **guaranteed to surface** at merge, which is
the strongest guarantee a sequential id can offer across disconnected work.

**`RUNTIME_HYBRID`** — `RES` (generated), `CONV`, `AGENT`, growth workspaces. The
sequence is kept for readability and ordering; eight base32 characters of
cryptographic randomness supply the uniqueness git cannot. `32**8 = 2**40`, about
1.1 × 10¹² suffixes, so a contested allocation collides with probability ≈ 9 ×
10⁻¹³.

This is **coordination-free probabilistic uniqueness**, not a structural
impossibility. The distinction is deliberate and is stated rather than glossed:
an absolute guarantee requires a coordination point, and that would cost offline
operation, which ADR-002 makes a premise of the system.

The counter is a **monotonic high-water hint**, never authority. Allocation is
`max(counter, highest_on_disk) + 1`, and the counter is never repaired downward.
Disk proves an id is taken; it cannot prove one is free, because deletion is
silent and references outlive records. In this repository `RES` has 34 records
against a high-water of 138 — 104 were deleted, and 32 of those ids are still
cited by tracked documentation.

Allocation holds the lock; the record write happens outside it. A crash between
the two leaves a **gap**. Gaps are free; reuse is not.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| Keep sequential everywhere, heal from disk | Reproduced as failing. Disk is a lower bound and both branches see the same one. |
| Derive the high-water from textual references | Measured: excluding tests, tracked references reach `RES-0125` — below the true 138. And a fixture citing `RES-9999` would burn 9,861 ids. |
| Branch or machine range reservation | Needs a registry of ranges, which is coordination without admitting it. |
| Central allocator service | The only absolute guarantee, and it ends offline operation (ADR-002). |
| UUIDs everywhere | Destroys the ordering that directory listings and high-water scans rely on, and rewrites historical ids. |
| Accept collisions, reconcile later | Already what git does for tracked records. For ignored ones there is no reconciliation point at all. |
| Untrack `knowledge/.sequences.json` | It raises the persisted floor cheaply. Tracking does *not* preserve the true high-water — the file lags by construction — but a stale floor is still better than none. |

### Consequences

- Historical ids are never rewritten. Parsers accept both `RES-0138` and
  `RES-0139-k3f2m8qp`, and a test pins that.
- Next-id behaviour for tracked namespaces is unchanged.
- Two ids in the same namespace can share a sequence number and differ by suffix.
  Anything matching on the sequence alone must expect that.
- `agents` gains disk healing it never had.
- On a merge conflict in a counter file, **take the maximum** for each prefix.
  Both sides are lower bounds; the larger one is the safer floor.
- Runtime ids are longer. That is the price of the uniqueness, and the sequence
  prefix keeps them readable and sortable.

---

## ADR-022: An Open Pull Request Is an Autonomous Review Artifact, Not a Release

**Status:** Accepted

**Date:** 2026-10-03

**Deciders:** Product Owner, MondayOS

### Context

ADR-006 correctly prevents the general agent runtime from taking
production-impacting actions without a human approval. It classified `commit`
and `push` conservatively because that runtime had no structural way to prove
what an agent had changed, what verification ran, or whether the reviewed bytes
were the bytes being published.

MondayOS now needs a fast, unattended implementation loop while retaining a hard
boundary before release. A commit on a unique isolated branch and an open pull
request are inspectable proposals; merging or deploying them is the action that
changes a shared or production environment.

### Decision

The `Monday.build()` controller may create a commit, non-force-push its unique
branch, and open a pull request without a human approval only when all of these
conditions hold:

- the work begins from an exact commit in the configured GitHub repository;
- a coding model can propose text patches but cannot perform controller
  mutations;
- Codex builder and reviewer subprocesses run from a private non-repository
  runtime with model tools disabled, so tracked project configuration and
  repository instructions cannot reconfigure them;
- fixed validation passes in containment with no model-selected commands;
- containment is entered through macOS's system sandbox library before pytest or
  candidate code loads; the candidate worktree is read-only, repository Git
  history and host data are withheld, and network and process inspection are
  denied;
- descendants stay in the controller-owned process group, receive finite
  process/file/CPU limits, and are additionally bounded by aggregate memory and
  private-runtime storage watchdogs;
- candidate pytest configuration is ignored and protected from proposed
  changes; every deliberately excluded containment self-test is named with its
  reason in the validation evidence;
- credential and protected-path checks pass;
- a fresh ChatGPT/Codex reviewer returns a high-confidence pass with no findings;
- the controller passes that reviewer the bounded exact staged diff and the
  reviewer independently verifies its SHA-256 digest before launch;
- the review, validation evidence, staged diff, tree, commit, remote branch, and
  pull request are cryptographically or exactly bound to the same artifact; and
- the controller stops at the open pull request. It cannot merge or deploy.

This is a narrow reclassification of one controller-owned delivery path. It does
not weaken the approval gate for `Monday.execute()`, `Monday.agent()`,
`Monday.team()`, publishing, secrets, destructive operations, live trading,
merge, or deployment.

### Consequences

- Monday can repair and deliver reviewable code while the operator is away.
- ChatGPT is the required independent quality gate; a missing, ambiguous, or
  contradictory review fails closed.
- Every published result is visible in GitHub as source, commit history, checks,
  and a pull request before it can reach a release branch.
- Runtime failures can leave a diagnostic worktree or an interrupted local job,
  but they cannot silently authorize a different artifact.
- Merge and deployment automation require a separate decision and are outside
  this ADR.

---

## ADR-023: Pull-Request Observation Is Exact-Artifact and Aggregate-Only

**Date:** 2026-10-13

**Status:** Accepted

**Deciders:** Product Owner, MondayOS

### Context

ADR-022 makes an open pull request the terminal result of autonomous delivery,
not a release. GitHub can later run checks, accept a human merge, close the PR,
or move its head. MondayOS needs to report those changes and reconcile its local
task without turning observation into a new mutation authority or trusting a
different artifact than the one ChatGPT reviewed.

CI output is also repository-controlled input. Individual check names,
descriptions, annotations, and logs are unnecessary for lifecycle reporting and
would create a new untrusted-text channel into durable records and Telegram.

### Decision

MondayOS provides a separate, GitHub-read-only observation operation for a
successful PR-backed delivery:

```bash
monday build observe DELIVERY-ID [--project NAME] [--json]
```

The observer reconstructs authority from the durable delivery record. Before it
accepts an observation, the selected target must still match the pinned project,
canonical repository directory and filesystem identity, and GitHub origin. The
GitHub response must match the saved repository, base branch, head branch,
pull-request number and URL, same-repository relationship, and reviewed commit
SHA. A head that no longer equals that SHA is a terminal `diverged` result; the
observer never silently follows replacement bytes. An unavailable or
inconsistent response fails closed and does not replace the last verified
lifecycle state.

Check information is reduced to bounded counts and exactly one aggregate state:
`pending`, `passed`, `failed`, or `no-checks`. Individual check names,
descriptions, annotations, and logs are neither exposed nor persisted. The
observer may persist this bounded local record, but it cannot mutate GitHub: it
does not push, edit, close, merge, or deploy.

Open check states, including `failed`, never drive task status. Observation may
repair a distinct initial task-to-`REVIEW` transition only when the durable
delivery explicitly says `review-pending`, the same exact reviewed PR is still
open, and a SHA-256 revision of the complete persisted task record is unchanged
since publication. Legacy records do not acquire this mutation authority. A
newer open delivery remains authoritative over every outcome from an older PR,
including a merge. Otherwise, terminal reconciliation lets an exact reviewed
merge complete an eligible task idempotently, while a closed or SHA-diverged PR
returns a task still in `REVIEW` to `IN_PROGRESS`. Completed, cancelled,
blocked, retargeted, or otherwise human-managed states are not overridden.

Observation is serialized with publication finalization by the repository
workflow lease. Any terminal task transition is then performed under the same
per-task lock used by normal MondayOS and human-triggered mutations, with the
task target revalidated inside that lock.

For private Telegram live builds, the completed request becomes a request-free
tombstone with an immutable delivery, task, project, chat, and PR identity. The
daemon persists the follow-up phase, next poll time, last observation, and last
notified transition, so an in-flight poll is reclaimable after restart. It sends
only meaningful aggregate/lifecycle transitions, stays silent on duplicates,
backs off transient failures without retaining their raw diagnostics, and stops
after a verified terminal state. A watcher also stops after 30 days or a
permanent Telegram destination failure. At most 500 watchers are active; a new
one is visibly declined without affecting its PR when that capacity is full.
`monday telegram --once` drains accepted work and exits; it is not an ongoing
lifecycle monitor.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| Follow a branch or the latest PR for a task | Either can be rebound to bytes that were never validated or reviewed. |
| Persist and forward individual CI results | Names, annotations, and logs are untrusted text and are not needed for a lifecycle decision. |
| Reconcile tasks from check success or failure | Checks can be rerun and do not establish that the PR was merged, closed, or still points to the reviewed commit. |
| Let the observer merge, close, or deploy | That would cross the release boundary established by ADR-022. |
| Use inbound GitHub webhooks for the first implementation | A public receiver, secret management, and replay verification add an unnecessary inbound surface to the outbound-only control plane. |

### Consequences

- The successful `pr-open` delivery result remains immutable while later GitHub
  state is recorded separately against the exact reviewed SHA.
- CLI observation is an explicit one-shot snapshot. Continuous Telegram
  follow-up requires the normal daemon; `--once` does not provide it.
- Notifications are restart-safe and low-noise, and transient GitHub or Telegram
  failures defer observation instead of weakening identity checks.
- Operators receive only bounded lifecycle/check summaries. Detailed CI
  investigation remains in GitHub.
- MondayOS can complete or reopen its local task after verified terminal
  evidence, but merge and deployment remain human/external actions.

---

## ADR-024: Bound Telegram Intake and Keep Queue Controls State-Only

**Date:** 2026-10-14

**Status:** Accepted

**Deciders:** Product Owner, MondayOS

### Context

The Telegram poller can keep accepting work while one long-running model or
build job occupies the single execution worker. An unbounded durable queue can
therefore consume storage and promise more work than one operator-controlled
Mac mini can reasonably process. Operators also need a way to inspect and undo
unstarted work without waiting behind that work or exposing private request
text.

### Decision

MondayOS admits at most 25 pending Telegram jobs. Admission and its Telegram
offset advance remain one atomic state transition. When capacity is exhausted,
the update first becomes a terminal, request-free `queue-full` tombstone without
advancing its offset. That tombstone prevents re-admission while Telegram
redelivery supplies a restart-safe refusal-notice retry. The offset advances
only after that notice succeeds or Telegram reports a permanent destination
failure. Before any retry sends, the saved user and chat destination must still
pass the current allowlists; revoked or malformed routing is terminalized
without contact. The intake loop drains pending refusal notices directly from
durable state before every poll, so recovery does not depend on Telegram still
retaining the original update. Existing state above the cap remains valid and
can drain, but no new work is admitted until capacity is available. Local health
reports queue usage and unresolved refusal notices, and is unhealthy whenever
the inbox cannot accept another job or a refusal remains unresolved.

Every admitted update receives a deterministic `JOB-<update ID>` handle. The
private, allowlisted `/queue` command lists that user's private-chat jobs and
jobs the same user submitted from an allowed group, while hiding every other
user's work. `/cancel JOB-ID` has the same ownership boundary and treats a
foreign or absent handle identically. It may
terminalize only a still-queued job with no side-effect checkpoint. Running,
waiting, retrying, delivered, or otherwise started jobs cannot be interrupted.
Cancellation removes request text, releases capacity, and remains replay-safe
after restart.

These controls execute on the lightweight intake path, not the model/build
FIFO. A control mutation is durable before its Telegram reply. If a retryable
send fails, the update stays unacknowledged; redelivery repeats the idempotent
operation and sends the same safe confirmation. A crash may therefore duplicate
a confirmation, but cannot duplicate execution or reverse cancellation.
Before the worker claims any persisted request, it recomputes its digest and
revalidates the queue key, update identity, resumable phase, message shape, and
origin. Semantic corruption therefore fails closed instead of executing altered
work.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| Leave intake unbounded | One slow worker could accumulate unlimited retained work and request text. |
| Put `/queue` and `/cancel` into the work FIFO | Operators could not regain control while the worker was occupied. |
| Cancel active jobs by terminating their process | Current provider and delivery boundaries do not prove that all external side effects are reversible. |
| Show request excerpts in `/queue` | Telegram handles and phases are enough to operate the queue without redisclosing retained text. |
| Reveal that a foreign job exists | That would leak another origin's activity through guessed update IDs. |

### Consequences

- Intake has a small, explicit storage and backlog bound.
- The operator can see and remove their own unstarted work while a build runs.
- A temporary notification outage cannot silently discard a capacity refusal,
  and monitoring distinguishes runtime liveness from admission readiness.
- Cancellation is deliberately narrower than process interruption; active work
  continues until its existing terminal or recovery path.
- Rejected and cancelled jobs preserve replay identity without preserving the
  request that caused them.

---

## ADR-025: Capacity-Notice Delivery Must Not Gate Telegram Intake

**Date:** 2026-10-14

**Status:** Accepted

**Deciders:** Product Owner, MondayOS

### Context

ADR-024 originally kept the Telegram offset behind a capacity-refused update
until its user notice resolved. That made the notice restart-safe, but it also
made `sendMessage` availability a prerequisite for `getUpdates`: the daemon
retried the refusal before polling and propagated every transient failure. A
full inbox plus a temporary send outage could therefore prevent the operator's
later `/cancel JOB-ID` from entering the control plane, even though that command
was the intended way to free capacity.

### Decision

A capacity refusal is now one atomic checkpoint containing a request-free
terminal tombstone, a bounded notice-outbox entry, and the advanced Telegram
offset. Durable local state—not Telegram redelivery—owns notice recovery.
Transient failures use validated retry metadata and bounded backoff across
restart. Telegram rate limits defer the complete due cohort in one atomic state
write. Notice failure never propagates into polling, and state-only controls are
processed before a newly-created refusal notice is attempted.

The outbox retains at most 500 unresolved refusal notices. Once that bound is
reached, a newer refusal is terminalized and its temporary route is scrubbed;
the rejected request body is never retained. Every retry rechecks current user
and group allowlists and verifies a deterministic binding between the update,
request digest, and minimal route. Permanent failures and revoked, malformed,
or redirected destinations terminalize and scrub the notice without contacting
a different destination. Pre-v7 pending notices have no such binding, so
migration fails them closed, scrubs their legacy route, and advances their held
offset rather than turning mutable legacy fields into authority to message
someone. Local health remains unhealthy while any refusal notice is unresolved.

This supersedes only ADR-024's rule that a capacity-refused update must hold the
Telegram offset behind its notice. The 25-job inbox cap, request-free
tombstones, private ownership boundary, queued-only cancellation, and semantic
request validation remain unchanged.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| Continue relying on Telegram redelivery | A notification outage would still block the very control needed to free capacity. |
| Retry notices in memory after advancing the offset | A process restart could silently lose the refusal notification. |
| Let unresolved notices grow without a bound | A sustained full-inbox flood could turn the liveness fix into local state exhaustion. |
| Drop every failed notice immediately | Temporary outages would become silent permanent notification loss. |

### Consequences

- `/queue`, `/cancel`, and later updates remain reachable during refusal-notice
  outages and rate limits.
- A crash may duplicate the same fixed notice at the remote/local checkpoint
  boundary, but it cannot re-admit the rejected request or execute it.
- Refusal delivery can lag behind intake; health exposes that unresolved state.
- Under extreme sustained refusal volume, the bounded outbox deliberately
  favors control-plane availability over notifying every rejected update.

---

## ADR-026: Patch Builders Receive Only Controller-Selected Context

**Date:** 2026-10-14

**Status:** Accepted

**Deciders:** Product Owner, MondayOS

### Context

The Codex patch builder already selected from a filtered tracked-file manifest
and ran outside the target repository. The preferred Claude Code self-builder
instead launched inside the delivery worktree with read tools. That let its
startup behavior and model browse tracked controller instructions, dependency
manifests, CI workflows, deployment files, or credential-shaped files that the
controller intentionally forbids from model context.

### Decision

Every patch builder uses the same two-stage, controller-owned context boundary.
The first tool-free turn selects from a bounded manifest containing only safe
tracked UTF-8 files. The controller validates those paths, securely re-reads the
unchanged bytes, and supplies only the selected content to a second tool-free
patch turn. Claude and Codex run in fresh temporary directories outside the
target worktree, with a single deadline spanning manifest collection, selection,
context capture, and patch generation. If the temporary runtime overlaps the
target repository or sits anywhere inside a Git repository, the controller
removes it and fails closed before launching the model. The child process's
temporary-file directory is pinned to the same checked runtime. External targets
remain Codex-only; this decision changes the
confidentiality boundary, not builder routing or publication authority.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| Keep Claude read-only inside the worktree | Read-only access still discloses protected files and lets repository configuration influence model startup. |
| Send every non-secret source file in one prompt | It weakens relevance, exceeds deterministic context bounds, and increases disclosure without helping most changes. |
| Remove Claude and always use Codex | Claude remains a useful subscription-authenticated primary builder once it has the same enforced boundary. |

### Consequences

- A builder can reason only over the objective, prior bounded feedback, safe
  manifest names, and the exact controller-selected file contents.
- Claude's default host-derived system prompt is replaced by a fixed
  controller-owned policy, preventing cwd, environment, memory-path, or Git
  status metadata from becoming implicit model context.
- Protected paths, credentials, repository instructions, CI, deployment, and
  dependency configuration never become builder prompt content.
- Selection adds one model turn and shares the build timeout; exhaustion or an
  invalid selection fails or falls back without granting broader access.
- The controller, not either model CLI, remains the sole reader of the target
  worktree and the sole owner of all Git and GitHub mutations.

---

## ADR-027: Completed Live Builds Use a Request-Free Notification Outbox

**Date:** 2026-10-14

**Status:** Accepted

**Deciders:** Product Owner, MondayOS

### Context

Live-build terminal and retry-scheduled messages were sent before the Telegram
source job could be acknowledged. A transient send failure therefore kept a
finished job in the active FIFO, retained its original request text, and could
delay later work. Replaying the source handler merely to recover a notification
also couples messaging availability to build and retry-scheduling idempotency.

### Decision

Terminal live-build results and retry-scheduled messages use a bounded durable
outbox. The worker stages a request-free notice before acknowledging its source
job; normal acknowledgment then terminalizes the job, scrubs its request, and
releases the FIFO regardless of Telegram availability. Only the outbox retries
the message. It cannot rerun a build, allocate another delivery identity, or
create another retry checkpoint.

An active notice contains only a fixed notice kind, safe structured delivery
fields, minimal private destination, bounded retry metadata, and a
deterministic binding to its source update, request digest, origin, task, and
delivery identity. It contains no original request, provider diagnostics,
credentials, or arbitrary rendered error text. Every send revalidates the
binding and the current private destination and allowlists. Malformed,
redirected, revoked, or permanently failed notices become scrubbed terminal
tombstones without contacting a different destination. Legacy records without
the complete binding do not acquire messaging authority during migration.

Delivery is at-least-once. A crash after Telegram accepts a message but before
the local checkpoint may produce the same fixed message twice, but it cannot
duplicate the build or retry scheduling. The outbox has an explicit active
capacity bound; exhaustion fails closed without retaining request text or
blocking source-job terminalization. Local health reports the unresolved count
and remains unhealthy while any delivery notice is pending.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| Keep sending inside the source-job handler | Telegram availability would continue to retain completed requests and block the FIFO. |
| Retry only in memory | A restart could silently lose a terminal or retry-scheduled notification. |
| Retain the source request until delivery succeeds | The notification does not need the objective, and retaining it expands privacy exposure. |
| Allow an unbounded notice list | A sustained Telegram outage could turn recovery into local state exhaustion. |
| Claim exactly-once Telegram delivery | Telegram provides no atomic transaction with the local checkpoint, so that guarantee would be false. |

### Consequences

- Finished source jobs release the FIFO and discard request text independently
  of Telegram availability.
- Notification recovery survives restart without replaying build or scheduling
  side effects.
- Current authorization and deterministic binding prevent persisted route
  changes from redirecting a notice.
- Monitoring distinguishes a healthy worker from an unresolved notification
  backlog.
- Rare duplicate fixed messages remain possible at the remote/local checkpoint
  boundary, and an exhausted or permanently invalid outbox may drop a notice
  to preserve privacy and control-plane availability.

---

## ADR-028: Telegram Health Requires Independent Progress Leases

**Date:** 2026-10-15

**Status:** Accepted

**Deciders:** Product Owner, MondayOS

### Context

The Telegram daemon previously exposed one periodic heartbeat. Its supervisor
thread could continue writing that pulse while the intake thread was stuck in
polling or notification dispatch, or while the sequential worker was stuck in a
model, validation, review, publication, or retry operation. Monitoring could
therefore report a fresh heartbeat for a process that was no longer making
useful progress. Telegram also supplies remote `retry_after` values that must
not be allowed to create unbounded waits or expensive exception rendering.

### Decision

The ephemeral Telegram heartbeat uses a strict versioned schema with three
independent signals: a supervisor pulse, an intake checkpoint, and a worker
checkpoint. The supervisor renews only its own pulse. Each lane records only a
fixed state and fixed operation name plus bounded timestamps; it never records
request text, task titles, provider output, diagnostics, credentials, or other
attacker-controlled strings.

An active intake poll receives at most its configured timeout plus 30 seconds,
capped at 80 seconds; intake dispatch receives 300 seconds. Ordinary worker
operations receive 300 seconds, implementation and validation 35 minutes,
review 45 minutes, and a team stage 65 minutes. The worker may also report a
healthy idle state. Startup, backoff, stopped, overdue, malformed,
future-dated, unknown-version, and legacy records fail closed. The daemon
replaces the ephemeral heartbeat on startup; durable Telegram queue schema and
recovery state are unchanged. One-shot execution records stopped runtime state
and does not create or renew ready daemon leases.

Runtime health requires the worker lock, a fresh supervisor pulse, and both
lanes ready. Backoff remains visibly unhealthy until the loop actually resumes,
even after its scheduled time passes. Telegram rate-limit hints are normalized
before exception rendering and time arithmetic. Invalid hints use a small safe
default and valid hints are capped at 86,400 seconds. Every new owner of the
single-process lock first records stopped runtime state, so setup, identify, and
one-shot work cannot inherit a crashed daemon's still-fresh ready leases.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| Keep one supervisor heartbeat | It measures process/thread survival, not intake or worker progress. |
| Let the supervisor renew every lane | A healthy supervisor could continue masking a hung lane. |
| Derive operations from task or provider messages | Arbitrary content would leak into an operational health file and make validation unbounded. |
| Mark a backoff ready once `resume_at` passes | The underlying loop may still be stuck; only a new checkpoint proves that it resumed. |
| Accept Telegram's delay without a maximum | Hostile or malformed input could suppress work for an unreasonable period or consume resources while rendered. |

### Consequences

- Watchdogs can distinguish supervisor survival, intake progress, and worker
  progress without contacting Telegram.
- Long legitimate phases stay healthy only while their bounded lease is current;
  progress callbacks renew the lease at durable workflow transitions.
- A process can temporarily report unhealthy during startup or an intentional
  retry wait. This is an accurate availability signal rather than a crash claim.
- Upgrades from the old heartbeat format require one normal daemon restart.
- The heartbeat remains ephemeral, strict, bounded, and safe to expose through
  the secret-free health command.

---

## ADR-029: Provider Failures Cross the Runtime as Fixed Diagnostics

**Date:** 2026-10-15

**Status:** Accepted

**Deciders:** Product Owner, MondayOS

### Context

Provider and SDK exceptions can contain remote response bodies, request
fragments, credentials, or model prompts. The orchestrator previously copied
that free-form text into model-call events, execution reports, API responses,
agent and team checkpoints, and ultimately Telegram team replies. Environment
secret pattern replacement is not a sufficient boundary because a private
prompt fragment may be sensitive without resembling a credential.

### Decision

Provider exceptions are reduced at the provider boundary to one fixed code:
authentication failure, rate limiting, unavailability, or generic provider
failure. No exception message or exception class name crosses that boundary.
Readiness results retain only a strict availability boolean, a fixed cause code,
a supported provider label, a fixed model-family label, and the provider's
known credential-variable name. Free-form reasons, response model strings,
custom provider identifiers, and shell installation hints are discarded before
higher layers use them. Fixed readiness codes distinguish a missing SDK,
credential, local service, and local model without exposing vendor prose.

The same boundary applies to the Orchestrator, agent runtime, Workspace
conversation path, and Growth model writer. Model-generation failures inherit
the Growth error boundary, so public API and CLI callers receive the fixed
diagnostic instead of an uncaught provider exception.

Execution reports use a descriptor-bound atomic writer, which replaces complete
JSON records with private file permissions. Each new write safely tightens and
sanitizes older single-link regular reports without following linked report
directories or files. Dashboard JSONL audit writes also use no-follow
regular-file validation, an append lock, and mode 0600. Legacy Agent, Team, and
Workspace records are reduced on read and rewritten only through directories
opened beneath the trusted project root without following project-controlled
links. Agent and team state may copy only an already-reduced report.
Approval, team-identity, task-lease, and Workspace sequence locks use the same
descriptor-bound rule. Lock directories are private, lock leaves must remain
single-link regular files before and after kernel-lock acquisition, and the
Workspace allocator keeps its validated project directory open through counter
read, allocation, and replacement. Workspace record IDs are validated before
all descriptor-relative operations, project discovery ignores linked children,
and unsafe counter or destination leaves fail closed without replacing links.
Telegram team results render an allowlisted status, validated role, fixed
provider label, and the runtime's canonical approval-run handle with fixed
explanatory text; they never append the persisted team message.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| Redact known token patterns from raw exceptions | Prompt fragments and unknown credential formats can remain sensitive without matching a pattern. |
| Keep raw diagnostics only in local logs | Local reports are durable and can be copied into later agent output; they remain part of the confidentiality boundary. |
| Return the exception type but not its message | Provider-defined class names are unnecessary data and fixed semantic codes are more stable for automation. |
| Remove provider failure records entirely | Fixed codes retain enough audit and retry information without retaining provider-controlled prose. |

### Consequences

- Provider failures remain distinguishable for retry and operator diagnosis
  without disclosing provider-controlled text.
- Missing-key guidance can name a fixed, known environment variable selected
  from MondayOS's provider mapping, but SDK-specific prose and
  install commands are no longer propagated through the runtime.
- Exact vendor model IDs are replaced by fixed family labels in cross-layer
  state; vendor consoles remain the place for low-level diagnostics.
- Execution and dashboard audit files containing prompts or generated output
  are private; execution JSON is also crash-safe.
- Workflow and Workspace allocation locks cannot be redirected through linked
  project directories or lock leaves, and are mode 0600 inside mode 0700
  project-controlled directories.
- Atomic migrations and report writes refuse linked destinations, preserving
  both the contents and filesystem metadata of outside files.
- Detailed vendor diagnostics must be investigated at the provider boundary or
  vendor console rather than copied into MondayOS durable state or chat.

---

## ADR-030: PR Observation Must Not Depend on Telegram Delivery

**Date:** 2026-10-15

**Status:** Accepted

**Deciders:** Product Owner, MondayOS

### Context

The first pull-request follow-up loop observed GitHub, rendered a lifecycle
message, sent it to Telegram, and only then checkpointed the observation. It
also checked the current Telegram allowlist before observing. A rate limit could
therefore delay the only GitHub polling clock for up to 24 hours, while a
blocked bot, deleted chat, or revoked destination could stop the watcher
permanently. A crash after the remote send but before the local checkpoint also
forced the combined operation to be retried. Messaging availability was
incorrectly capable of preventing later merge, closure, or divergence evidence
from reaching MondayOS task state.

### Decision

Exact PR observation and Telegram delivery are independent durable lanes. The
observer calls the public `build observe` operation without first authorizing a
Telegram destination. After the delivery, task, project, and PR identities are
revalidated, one atomic local write records the bounded observation, schedules
the next GitHub poll or completes the watcher, and enqueues or supersedes at
most one semantic lifecycle notice.

The lifecycle-notice outbox contains only the immutable source identity,
minimal private route, normalized PR/check state, optional verified merge SHA,
bounded retry metadata, and a deterministic source binding. It contains no
request body, check name, annotation, log, or raw provider, GitHub, or Telegram
diagnostic. An unchanged observation neither duplicates a resolved notice nor
resets an existing notice's backoff. A newer unsent transition replaces the
older notice; a terminal transition therefore supersedes stale open-check
status. Active notices and watchers have explicit capacity bounds, and their
completed source tombstones remain protected from history eviction.

A separate worker lane revalidates the binding and current allowlist before
sending. Telegram rate limits create a durable lane-wide pause that also covers
notices created during the retry window; other retryable failures defer only
their notice.
Revocation and permanent destination failures terminalize and scrub only the
notice; they do not alter the watcher. Verified terminal PR state completes the
watcher even when its notification cannot be delivered. GitHub observation
failures alone control the observer's bounded backoff.
If the watcher's 30-day lifetime expires first, any still-unsent open-state
notice is resolved and scrubbed in the same checkpoint instead of surviving as
stale monitoring status.

Once a durable delivery-recovery run has staged an outcome for a newly opened
PR, its watcher is likewise independent: retry-outcome notification backoff or
permanent failure cannot suppress GitHub observation.

Lifecycle delivery is at-least-once because Telegram and the local state file
cannot share a transaction. A crash after Telegram accepts a message but before
the local acknowledgement may replay the same fixed notice. Once observation
and notice creation have been atomically persisted, restart sends the notice
without re-observing GitHub. A crash before that write may repeat the exact
observer operation; task reconciliation is separately identity-bound and
idempotent.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| Keep one combined poll-and-send operation | Telegram availability would continue to control GitHub truth and task reconciliation. |
| Check destination authorization before observing | Revocation is a messaging decision, not authority to stop exact PR reconciliation. |
| Retry notices only in memory | Restart could silently lose a transition that was already recorded. |
| Queue every observed transition | A prolonged outage could produce stale message bursts and unbounded state; only the newest semantic transition is useful. |
| Claim exactly-once message delivery | Telegram cannot atomically commit with the local checkpoint, so that guarantee would be false. |

### Consequences

- Merge, closure, and reviewed-SHA divergence continue to reconcile tasks when
  Telegram is slow, blocked, deleted, or no longer allowlisted.
- Telegram backoff cannot reduce GitHub polling frequency, and observation
  backoff cannot discard an already-recorded message.
- Operators may rarely receive the same fixed lifecycle message twice after a
  crash at the remote/local acknowledgement boundary.
- Health exposes unresolved lifecycle notices independently from active
  watchers, while permanent route failures resolve instead of hot-looping.
- The normal daemon remains required for continuous observation and outbox
  delivery; one-shot mode neither observes nor delivers lifecycle state, so it
  cannot send a saved status without first reconciling GitHub.

---

## ADR-031: Interrupted Builds Need Proof Before a Successor

**Date:** 2026-10-15

**Status:** Accepted

**Deciders:** Product Owner, MondayOS

### Context

The delivery controller already reconciled an exact identity after a crash, but
an interruption before publication became a permanent result. Telegram could
therefore recover a temporary provider outage automatically while an ordinary
worker or host restart required a second user request. Treating every
`interrupted` label as retryable would be unsafe because a push may have crossed
the remote boundary before the local checkpoint was written.

### Decision

An interrupted delivery authorizes a fresh identity only in the controller's
implementation, validation, review, commit, or final pre-publication phases.
MondayOS revalidates the durable target and controller-owned worktree, pins the
remote identity, and queries the exact unique delivery branch. Only a
well-formed empty remote result permits the interruption to carry the structured
retryable classification. The terminal record also preserves the interruption's
source phase and a SHA-256 proof digest over that phase, the delivery lineage,
task and repository identity, base and branch, controller-owned worktree, and
any local commit. A retained local commit is diagnostic state and is never
reused by the successor; changing a proof-bound field invalidates the record.

If the branch exists, ownership evidence is missing, the remote changes, the
query is malformed or unavailable, or publication may have started, no
successor is authorized. A pre-publication record without sufficient proof
remains permanently terminal for inspection. If publication started or may have
crossed the remote boundary, reconciliation continues only under the original
identity.

Telegram reuses its existing deterministic `-r2` through `-r6` scheduler,
write-ahead attempt checkpoints, capacity bound, and request-free outbox. Each
successor durably identifies the controller-derived immediate predecessor,
retry root, and attempt, which must produce the exact deterministic `ROOT-rN`
delivery ID. The controller independently derives the sole permissible retry
source from durable history, so omitted or caller-substituted lineage cannot
authorize a successor. It re-proves the complete bounded chain under its
repository workflow lock before reserving work, under the task lock, and after
each untrusted publication progress callback immediately adjacent to push and
pull-request creation. A reappearing predecessor branch, any other matching live
pull request regardless of its timestamps, changed task or target identity,
malformed lineage or proof, or unavailable verification stops the successor
before the next irreversible operation. The single 24-hour deadline is saved
before the root build invocation and survives nonterminal reconciliation, so
process downtime cannot create a new recovery window.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| Retry every interrupted record with empty publication fields | A crash can occur between a remote side effect and its local checkpoint. |
| Resume the old model or worktree mutation in place | Provider sessions and partially applied mutations are not replay-safe. |
| Require another Telegram request | It leaves a safely recoverable host crash dependent on human availability. |
| Delete the old worktree before retrying | Retained controller-owned state is useful for diagnosis, and cleanup is a separate destructive lifecycle. |

### Consequences

- A proven pre-publication crash recovers without user action and cannot create
  more than one deterministic successor per attempt.
- A delayed successor cannot rely on stale absence evidence; every predecessor
  remains part of the final publication authorization.
- Delivery creation timestamps cannot suppress a matching live pull request;
  publication state, not record age, controls the gate.
- Push and pull-request uncertainty never authorizes a new identity.
- Remote verification availability is part of the safety proof; an outage may
  leave an interruption terminal for manual inspection.
- Abandoned controller-owned local branches may remain until a separate bounded
  cleanup policy is implemented.

---

## ADR-032: Push Authority Is Bound to the Reviewed Task Revision

**Date:** 2026-10-15

**Status:** Accepted

**Deciders:** Product Owner, MondayOS

### Context

The delivery workflow checked for a completed or cancelled task before push,
then invoked a progress callback while holding the task lock. Task locks are
intentionally reentrant inside the controller process, so that callback could
block, complete, or otherwise mutate the same task before the push. The workflow
did not reread the task or compare its persisted revision after the callback.
The pre-publication check also accepted nonterminal states other than
`in-progress` and did not prove that the title, objective, and acceptance
criteria still matched the content reviewed by ChatGPT.

The old ordering created the local commit before the last callback and recorded
it as the publication commit even when a later task-policy check refused the
push. That permanent publication evidence prevented a corrected fresh delivery,
although the unique branch had never left the machine. Conversely, simply
ignoring every locally prepared commit would allow a malformed record or a
late-arriving remote branch to bypass retry-history safety. Build-start races
could also reopen `blocked` or `review` work, and silently truncating oversized
canonical task content could make the implemented input differ from the task.

### Decision

Task content is one canonical string containing the title, objective, and
acceptance criteria. Monday rejects it rather than truncating it when it exceeds
50,000 characters. Initial admission accepts only `backlog`, `assigned`, or
`in-progress`. Immediately before start and before creating a worktree or
invoking a builder, Monday takes the TaskManager lock, freshly rereads and
revalidates the eligible status, project target, and exact canonical content,
starts the task, and requires a second exact `in-progress` read.

Push authority likewise requires a successful task read whose status is exactly
`in-progress`, whose project target is unchanged, and whose canonical content
exactly equals the reviewed build input. Under the task lock, Monday captures
the complete TaskManager revision only after that check. It invokes the
`pushing` callback, rereads and revalidates the task, and requires the exact same
revision. The actual local commit is deferred until every pre-push progress
callback has completed.

Every new delivery durably sets `deferred_commit_authorization=true` before the
final callbacks; missing legacy fields default false. Monday first persists the
new local SHA as `prepared_commit_sha`, with `commit_sha` still empty. It then
repeats the exact task-content, status, target, and complete-revision checks.
Monday next rederives the required retry predecessor from durable history and
proves the complete chain while the SHA is still prepared. Only success
promotes the SHA into `commit_sha`; that durable checkpoint is immediately
adjacent to the irreversible push, with no callback or controller extension
point in between. If the promotion checkpoint raises, the in-memory record is
restored to the prepared shape before the outer failure checkpoint, because
push was still unreachable.

A marked crash in `publication-ready` authorizes a fresh successor only when the
record remains bound to the exact reviewed artifact, the controller-owned
worktree HEAD equals the base, and the unique branch is absent from the pinned
remote. In `pushing`, empty SHA fields can also mean death inside `commit()`
after HEAD moved but before its return value was checkpointed; owned worktree
and ref consistency plus remote absence are then sufficient because the marker
proves push was unreachable. Once `prepared_commit_sha` exists, HEAD must equal
that SHA. Unmarked legacy or malformed publication-ready/pushing
records that lack the identity required by their original ordering remain
unresolved; the marker and optional prepared SHA are included in new proof
digests without changing legacy proof bytes.

A no-commit policy failure is nonblocking only for the narrow changed-task,
terminal-task, and project-mismatch outcomes. A failure after preparation is
classified structurally: it must retain a full prepared SHA, an empty
publication SHA, pristine push, PR, observation, reconciliation, and
interruption fields, matching full artifact digests, the captured task revision,
and complete base, repository, branch, worktree, and ownership evidence. Monday
freshly proves the remote branch absent when admitting a later build and again
in its final history check. A structurally valid transient prepared failure
remains a deterministic retry source; a nontransient prepared retry child may
retire its safe leaf only after the entire predecessor chain and bindings are
revalidated. Tampered evidence or unavailable remote proof remains conservative.

All other terminal records fail closed when they contain any durable commit,
prepared-commit, push, pull-request, observation, or reconciliation evidence.
The sole settled-publication exception is a successful completed PR record with
a full reviewed artifact and repository/base/branch/commit/PR identity plus an
internally valid terminal lifecycle and check summary from a successful remote
observation. Terminal GitHub truth remains settled when only the later task
reconciliation failed, which is recorded independently. A failed later GitHub
refresh clears the successful-observation proof, so stale terminal fields cannot
authorize a successor; malformed `rejected` or `pr-open` shells likewise cannot
suppress publication evidence.

Remote-absence proof has three outcomes: absent, present/unsafe, or temporarily
indeterminate. Typed transient proof exceptions remain fail-closed for the
current publication, but retain their transient classification. Before a retry
is reserved they create the canonical transient retry child, so the next
deterministic lineage identity remains reachable after recovery. During exact
nonterminal crash recovery they leave the original record unchanged. A false
absence result or nontransient proof failure remains conservative and cannot
authorize publication. Typed transient predecessor-store reads are likewise
preserved before any missing/malformed-record policy wrapper; genuinely absent,
malformed, or nontransient records remain policy failures.

Once an exact approved commit is present on the remote branch, opening or
locating its pull request is recovery of an irreversible transition rather than
a new task authorization. Recovery must therefore continue under the original
delivery identity even if the task or old lineage later changes. Exact-ID lookup
precedes every live task-content redaction and size check. Recovery uses a fixed
generic PR title so live, post-review task content is not exported during reconciliation.
The ordinary first-pass PR may use the task title that passed the final pre-push
authorization.

### Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| Check only for terminal task states | `blocked`, `review`, malformed, and unknown states do not authorize autonomous publication. |
| Trust the task lock without a second check | Reentrant in-process callers can mutate the task from the progress callback. |
| Compare only the displayed update time | Same-second edits and changes outside that field can share the timestamp. |
| Commit before running the final callbacks | A policy refusal would retain publication evidence and unnecessarily strand a safe fresh build. |
| Treat every prepared local commit as harmless | A branch may already exist remotely, or the durable record may no longer describe the reviewed artifact and owned worktree. |
| Truncate oversized canonical task content | The builder and reviewer would act on a specification different from the persisted task. |
| Recheck the task after push and suppress the PR | The branch may already be public; abandoning its exact PR would leave an unresolved irreversible side effect. |
| Read the latest task title during recovery | That title was not part of the authorization that permitted the already-completed push. |

### Consequences

- A status, specification, target, metadata, history, or work-log change before
  push invalidates the captured revision or explicit authorization checks and
  stops publication.
- A concurrent move to a blocked, review, terminal, or unknown state cannot be
  reopened by task start, and oversized task content never reaches a model.
- Task mutation from a reentrant progress callback cannot race the final push.
- A strictly proven unpublished prepared commit or policy-stopped retry child
  does not permanently block a corrected fresh delivery; missing or changed
  proof still blocks it.
- Post-push task changes cannot strand an exact approved remote branch without
  PR reconciliation or leak newly edited task text into the recovered PR title.
- Operators must start a fresh reviewed delivery after a legitimate pre-push
  task edit.
