# MondayOS — Architectural Decision Records

**Last Updated:** 2026-06-27

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
