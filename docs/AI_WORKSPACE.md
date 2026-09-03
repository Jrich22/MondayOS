# AI Workspace

**Version:** 0.1.0  
**Status:** Draft  
**Last Updated:** 2026-09-02  
**Owner:** Lead Software Engineering

---

> **Status note.** Increments 1, 2 and 3 are implemented: the Conversation domain, project-scoped
> persistence, the deterministic Context Engine with attributed sources, the responder seam
> backed by the existing MondayOS provider abstraction, the `Monday.workspace()` API, the
> dashboard API routes, and the AI Workspace UI.
>
> Increment 2 adds real streaming, explicit task→project association, relevance-ranked context
> with per-item attribution, conservative snapshot reuse, Continue Working, conversation search,
> slash commands, response actions, and a live activity feed.
>
> Increment 3 adds deep project intelligence: a deterministic index of every
> source file, document, ADR, test and config; a symbol index; a relationship
> graph linking tasks, decisions, code, tests, commits and PRs; and a question
> engine that retrieves evidence and cites it navigably.
>
> **There is still no model router.** One configured provider answers every request, behind the
> unchanged `WorkspaceResponder` seam. Knowledge capture remains explicit only, and what is
> written records that a model produced it and a human retained it without verifying it (§6).

---

## 1. Why This Exists

Work currently spreads across ChatGPT, Claude, GitHub, project folders and documentation, and
the operator is the only thing holding it together. Each tool holds a fragment: the reasoning
lives in a chat that will be closed, the state lives in a repository the chat cannot see, and the
decisions live in a document neither one reads.

The AI Workspace makes MondayOS the surface where that work happens, so that conversation,
project state and durable knowledge are the same system rather than three that must be manually
reconciled.

The end-state is narrow and concrete:

> Open MondayOS. Select a project. MondayOS already knows that project's architecture, tasks,
> ADRs, documentation, git state, knowledge and previous conversations. Talk to it
> conversationally. MondayOS decides which capability handles the work. What matters is retained
> in MondayOS rather than in one chat session.

## 2. What It Is Not

The AI Workspace is an **orchestration surface over existing MondayOS capabilities**. It is not a
new memory system, and it does not own domain state.

| It reuses | It does not build |
|---|---|
| `knowledge/` — the knowledge store and MKS contract | A second knowledge store |
| `tasks/` — TaskManager and the real state machine | A parallel task model |
| `brain/providers/` — the `AIProvider` abstraction | Any vendor SDK call |
| `monday/project.py` — the project registry | A second project concept |
| `dashboard_api/` — routing, CORS, redaction, revision | A separate HTTP server |
| `core/redaction.py` — secret redaction | Bespoke secret handling |

The single genuinely new thing is the **Conversation**, because MondayOS had no first-class
representation of a durable dialogue.

## 3. Architecture

```
                    ┌─────────────────────────────────────┐
                    │   Dashboard — AI Workspace UI       │
                    │   projects · conversation · context │
                    └──────────────┬──────────────────────┘
                                   │  dashboard_api routes
                    ┌──────────────▼──────────────────────┐
                    │   Monday.workspace(action, ...)     │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │        WorkspaceService             │
                    └───┬───────────┬──────────────┬──────┘
                        │           │              │
        ┌───────────────▼──┐  ┌─────▼─────────┐  ┌─▼──────────────────┐
        │ ConversationStore│  │ ContextEngine │  │ WorkspaceResponder │
        │  project-scoped  │  │ deterministic │  │   (routing seam)   │
        │      files       │  │  attributed   │  └─┬──────────────────┘
        └──────────────────┘  └─────┬─────────┘    │
                                    │              │ ProviderWorkspaceResponder
              ┌─────────────────────┼───────┐      │
              │        context adapters     │      ▼
        ┌─────▼─────┬────────┬──────┬───────┴┐   AIProvider
        │ identity  │ tasks  │ know │  git   │   (existing abstraction)
        │ (registry)│        │ledge │  docs  │
        └───────────┴────────┴──────┴────────┘
```

Three properties define the design, each with its own ADR:

- **Conversations are files MondayOS owns** ([ADR-015](DECISIONS.md#adr-015-conversations-are-project-scoped-files-and-mondayos-owns-the-record)). The browser caches; it is never the record.
- **Context is an attributed, budgeted, deterministic assembly** ([ADR-016](DECISIONS.md#adr-016-the-context-snapshot-is-an-attributed-budgeted-assembly)). Every snapshot can answer "why did Monday know this?"
- **Isolation is enforced in the engine, and adapters fail closed** ([ADR-017](DECISIONS.md#adr-017-project-context-isolation-is-enforced-by-the-engine-and-adapters-fail-closed)).
- **The responder is the routing seam** ([ADR-018](DECISIONS.md#adr-018-the-responder-seam-is-the-extension-point-for-model-routing)), so increment 4 adds a class rather than a rewrite.

## 4. Domain Model

### Conversation

Stored at `workspace/conversations/{project}/CONV-NNNN.md` — Markdown with YAML frontmatter,
per ADR-003. The project is a **path segment**, so scoping is structural rather than a filter
applied at read time.

| Field | Meaning |
|---|---|
| `id` | `CONV-NNNN`, allocated from that project's own sequence counter |
| `project` | Resolved project slug; a conversation belongs to exactly one |
| `title` | Human-editable; defaults to a slug of the first user message |
| `status` | `active` \| `archived` |
| `created_at` / `updated_at` | ISO-8601 UTC |
| `active_snapshot_id` | The most recent context snapshot answered against |
| `messages` | Ordered; see below |
| `artifact_refs` | References only in increment 1 — nothing is created or edited |
| `task_refs` | MondayOS task ids this conversation concerns |

### Message

| Field | Meaning |
|---|---|
| `id` | `MSG-NNNN`, unique within the conversation |
| `role` | `user` \| `assistant` \| `event` |
| `content` | The visible text, and only the visible text |
| `created_at` | ISO-8601 UTC |
| `provider` / `model` | Recorded for assistant messages; provenance, never branched on |
| `snapshot_id` | The context this message was answered against |
| `tokens_used` | Reported by the provider when known |
| `error` | Set when generation failed; the turn is preserved, not swallowed |

**Hidden reasoning is never persisted.** Provider-private chain-of-thought is not requested, not
stored, and not returned by the API.

### Artifact reference

Increment 1 defines the vocabulary and stores references. It does not create or edit artifacts.
Kinds: `document`, `file`, `task`, `pull-request`, `image`, `report`, `other`.

## 5. Context Engine

`ContextEngine.build(project) -> ContextSnapshot` assembles from existing subsystems through
narrow adapters. It calls no model and is fully deterministic.

| Priority | Source | Reads |
|---|---|---|
| 1 | `identity` | Project registry — name, description, path |
| 2 | `docs` | Project `docs/`: ADR titles and statuses, documentation filenames |
| 3 | `tasks` | `TaskManager` — active tasks (explicit project), then recently completed |
| 4 | `knowledge` | `KnowledgeStore` — recent entries scoped to the project |
| 5 | `git` | Branch, working-tree state, recent commits |

Priority is the budget order: source 1 is filled before source 5 is considered. Each source has a
character cap and the snapshot has a total cap; a source that hits its cap is marked
`truncated: true` and says so in the UI. Nothing is silently dropped.

**Relevance ranking (increment 2).** Within each category, items are ranked against the current
request and **every item records why it survived**. The reason vocabulary is closed and
enumerable, so the panel can aggregate it and a human can reason about it:

| Reason | Meaning |
|---|---|
| `active-task` | An in-progress or in-review task — relevant to almost any question |
| `architecture-priority` | Documentation and ADRs, which outrank incidental matches |
| `keyword-match` | The item shares terms with the request |
| `recent` | Included by recency where the request does not distinguish |
| `baseline` | No signal either way; kept in arrival order |

Ranking is deterministic keyword overlap plus structural signals already in the data. No vector
store, no embeddings, no second index. An empty request reorders nothing — no query is no
evidence, and inventing an order from no evidence is worse than the order the data arrived in.

**Snapshot reuse (increment 2).** A snapshot is reused only when a fingerprint of what it was
built from — git HEAD, working-tree state, task statuses, knowledge ids, docs mtimes — is
unchanged *and* the request ranks equivalently. Everything else rebuilds. Stale context produces a
confident answer about a state that no longer exists, and nothing in the transcript reveals that,
so every uncertainty here resolves to rebuilding.

## 5a. Streaming

Responses stream token-by-token through a provider-neutral seam. `AIProvider.stream()` yields
`ProviderChunk`s; a provider that has not implemented it falls back to yielding the whole answer
as one chunk and reports `supports_streaming = False`, so the difference is visible rather than
animated over.

Three lifecycle properties are enforced and tested:

- **The user message is persisted before the provider is called.** A failure or a stop never loses
  what the human said.
- **Stopping persists the partial.** Closing the stream — the Stop button aborting the request —
  raises `GeneratorExit` inside the service, whose `finally` writes whatever text arrived as an
  assistant message marked `incomplete`. The UI renders it as explicitly partial with a retry.
- **An incomplete answer is never presented as a finished one.** `Message.incomplete` is
  persisted, not inferred at read time.

## 5b. Task → project association

`Task.project` is an explicit registry slug. The Context Engine prefers it absolutely: a task
naming a *different* project is excluded outright, and the increment-1 substring heuristic now
applies only to tasks with no project at all.

`tasks/migration.py` backfills the field. It never guesses (an ambiguous task stays unassigned and
is reported), never overwrites an explicit value, and reports every task it skipped so the
remainder can be assigned by hand. On this repository it assigned 19 tasks, left 4 ambiguous and
37 unmatched, and a second run changed nothing.

Every assembled string passes `core.redaction` before it can be persisted or sent.

## 6. Knowledge Capture

Capture is **explicit only**. Nothing is written to knowledge unless a human presses the
button on a specific assistant message — a conversation contains as much exploration as
conclusion, and writing all of it would fill the store with things nobody decided.

The entry is written through the existing `KnowledgeStore`; no field was added to the CKO to
support it. Two properties matter:

**The type does not overclaim.** MKS `RESEARCH` means an investigation was conducted — it
mandates a question, a methodology and findings (MKS 9.10). A model answering in a chat did
none of those, so filing its output as research would assert rigour that never happened.
Captured messages are `DOCUMENTATION` (MKS 9.9), a structured reference record, with its
mandatory `content_type` and `scope` supplied. Entries get `DOC-` ids.

**Two parties did two different things, and both are recorded.** The model produced the
content; a human decided to keep it. Recording only one would misattribute.

| Signal | Where it lives | Value |
|---|---|---|
| Model produced the content | `authored_by` | `agent` |
| Which model | `metadata.produced_by`, `.provider`, `.model` | e.g. `model:anthropic/claude-…` |
| A human chose to save it | `metadata.saved_by` | `human` |
| When | `metadata.saved_at` | ISO-8601 UTC |
| Originating project | `components`, `tags`, `metadata.project` | project slug |
| Originating conversation | `components`, `metadata.conversation_id` | `CONV-NNNN` |
| Originating message | `metadata.message_id` | `MSG-NNNN` |
| Context it was grounded in | `metadata.context_snapshot_id` | `CTX-…` |
| Nothing verified it | `confidence` = 0.5, `metadata.verification` | stated explicitly |

`confidence` is below 1.0 deliberately. MKS treats it as how much weight a consumer should
give an entry, and agent-authored content self-reports. A human electing to retain an answer
is not the same as a human checking it, and an entry claiming 1.0 would say otherwise.

The capture is also recorded as an `event` message in the transcript, so knowledge that came
from a conversation is traceable from both ends.

**Known limitation for increment 2.** `DOCUMENTATION` is the most accurate *existing* MKS
type, but it is not a perfect fit: MKS has no type for "model output a human elected to
retain without verification". Introducing one would widen the knowledge architecture, which
this increment deliberately does not do. If captured entries become numerous enough that
`DOCUMENTATION` stops discriminating usefully, the right fix is a new MKS type with its own
ADR — not more metadata on this one.

## 7. Security and Isolation

Enforced, and directly tested:

- A snapshot serves exactly one project. Adapters take a resolved project and have no argument
  that could name a second.
- Adapters **fail closed**: an adapter that errors contributes an empty source carrying the error.
  A failure to scope is never a reason to widen scope.
- Conversation storage is project-scoped by directory; history cannot cross projects.
- `.env` files, key material and token-shaped values are excluded at the adapter level, and
  `core.redaction` runs over everything as defence in depth.
- The provider receives only the current project's snapshot and the current conversation.

## 7a. Project Intelligence

Increments 1 and 2 gave Monday *context*: identity, documentation filenames, ADR
titles, task rows, git state. That is enough to say what is happening and not
enough to say **why** or **where**. Increment 3 reads the project itself.

`intelligence/` is an OS-level service beside `knowledge/` and `tasks/`, not a
workspace feature. Two commitments shape all of it:

**Deterministic.** No embeddings, no vector database, no model in the indexing
or retrieval path. Identical project state produces an identical index and
identical answers. Retrieval that cannot be explained cannot be trusted, and an
answer that cannot name its source is indistinguishable from one that was
invented.

**Derived.** The index is a cache, never a system of record. Tasks live in the
TaskManager, knowledge in the KnowledgeStore, history in git. Every fact in the
index is recoverable by re-reading the project; deleting it costs a rebuild.
It is gitignored.

### What is indexed

| Stage | What it produces |
|---|---|
| `scanner` | Files classified by **role** — source, test, documentation, decision, config, prompt |
| `symbols` | Definitions: classes, functions, methods, interfaces, protocols, dataclasses, enums, constants, types |
| `index` | Per-file record + an inverted **term index** + a **symbol index** |
| `graph` | Task → ADR → Code → Test → Commit → PR |
| `questions` | Eight intents, each with its own retrieval strategy |
| `evidence` | Navigable citations and the "Based on" line |

Classification is by role rather than extension because `DECISIONS.md` is a
decision record before it is documentation, and "why did we decide" and "where is
this documented" want different sources.

Terms come from real content, not filenames. Identifiers are split on both
`snake_case` and `camelCase` *and* kept whole, so `render_context` is findable as
`render`, `context`, and `render_context`.

### The relationship graph

Every edge derives from something the project wrote down, and carries the literal
reason:

    Task     → ADR      a task's text names ADR-017
    Code     → ADR      a docstring names ADR-017
    Test     → Code     naming convention (`test_workspace.py` → `workspace/`)
    Commit   → File     `git log --name-only`
    Commit   → Task     a commit message names TASK-0073
    Task     → Commit   the task's own recorded `commit_refs`
    PR       → Commit   a merge commit says "Merge pull request #39"

Nothing is inferred by similarity. An edge produced by "these look related" is a
claim the project never made, and it would be indistinguishable in the output
from one the project did make.

**ADR ids are namespaced by the decision log that defines them.** MondayOS and
sourcingBOT both define an ADR-017; merging them would cite the wrong decision,
which is exactly what the evidence trail exists to prevent. A reference resolves
to the nearest log — a sourcingBOT file naming ADR-017 links to sourcingBOT's.

### Questions and evidence

Eight intents: current work, why-decision, where-implemented, everything-about,
why-blocked, what-changed, where-documented, and a general fallback. Each has its
own retrieval strategy, because a single similarity search would return the same
sources for "why did we decide X" and "where is X implemented" and be wrong for
at least one.

Every citation is navigable — `workspace/context/engine.py lines 42-201`,
`ADR-015`, `TASK-0073`, `PR #39`, a commit sha. `workspace/service.py` is a hint;
a line range is checkable, and a citation nobody can follow is one nobody
verifies.

### How it reaches a conversation

As a **context source**, not a second route into the prompt. Everything the
Context Engine assembles is budgeted, attributed and redacted in one place
(ADR-016, ADR-017); a separate path would be a second place those rules have to
hold. Retrieval runs *before* the model, so the evidence is fixed before any
generation and the same citations are shown to the operator.

The source ranks second, below identity: an answer about the wrong project is
worse than one with thin evidence.

### Subject carry-over

A conversation records what it is about. Twenty minutes into discussing the
ContextEngine, "find every place **it** is used" means the ContextEngine. The
pronoun is the signal — an earlier version relied on a verb stoplist, where one
missing word turned a follow-up into a search for that word. An explicit new
subject always wins, so carry-over never overrides what was actually asked.

### Performance

Measured on this repository — 725 files, 5.1 MB:

| | |
|---|---|
| Index, cold | **924 ms** (5,688 symbols, 17,267 terms) |
| Index, warm | **99 ms** (725 reused, 0 reparsed) |
| Graph build | **41 ms** (995 nodes, 1,628 edges) |
| Question | **1.5 ms** mean |
| Cache | 3.4 MB |

Staleness is per file by size and mtime, so editing one file reparses one file.

### Known limitations

Stated rather than hidden, and each is a deliberate boundary:

- **TypeScript symbols are regex, not parsed.** Finds `export class/interface/type/enum/function/const`; silently misses exotic forms. A real TS parser is a dependency this project does not have, and the failure direction is *absent from the index* rather than *wrong in it*.
- **Test→code links use naming convention**, not import analysis. It is the convention this project follows and each edge is checkable by reading two filenames, but it misses tests that do not follow it.
- **Task→commit quality depends on references.** Only a minority of commits name a task, so the graph leans on `Task.commit_refs`.
- **Retrieval produces grounded evidence; the provider writes the prose.** The finding is assembled deterministically and bounds what a model can say, but answer quality still depends on the configured provider.
- **The credential filter is conservative.** A legitimate file named `secret.py` is skipped. A missing file costs a gap in retrieval; an indexed credential costs a credential.

## 8. Roadmap

| Increment | Scope | Status |
|---|---|---|
| **1 — Conversational Workspace Foundation** | Conversation domain, project-scoped persistence, deterministic Context Engine, responder seam, `Monday.workspace()`, dashboard routes, AI Workspace UI | **Implemented** |
| **2 — Interactive Monday** | Real streaming with stop; explicit task→project association and migration; relevance-ranked context with per-item attribution; snapshot reuse and invalidation; Continue Working and return briefing; conversation search; slash commands; response actions; live activity | **Implemented** |
| **3 — Deep Project Intelligence** | Deterministic project indexer; symbol index; Task→ADR→Code→Test→Commit→PR graph; eight-intent question engine; navigable evidence; Context Engine integration; conversational subject carry-over | **Implemented** |
| **3 — Artifacts, Tasks, GitHub & Knowledge** | Create and edit artifacts inline, task creation from conversation, PR/issue context, automatic knowledge extraction proposals | Planned |
| **4 — Model Router** | `RoutingWorkspaceResponder`: route by task shape, cost and capability across providers and local models | Planned |
| **5 — Execution Engine / Agent Delegation** | Delegate conversation turns to agent teams and the orchestrator, with the existing approval gates | Planned |
| **6 — Mission Control & Daily Briefing** | Cross-project briefing, notification centre, standing questions | Planned |
| **7 — Workspace Polish** | Desktop-quality interaction: keyboard model, search, command surface, density work | Planned |

Increment 2 is deliberately retrieval rather than a memory rewrite: the attributed baseline from
increment 1 is what makes retrieval quality measurable.

## 9. Deliberately Not Built

Not in increment 1, and not partially stubbed: multi-model routing, provider benchmarking, agent
teams, computer/browser control, autonomous PR creation or coding, daily briefing, notification
centre, automatic conversation summarisation, automatic knowledge extraction, vector memory,
Slack/email integration, file editor, terminal, PR viewer, artifact creation.

Streaming is not implemented. The dashboard API is request/response, and adding SSE for token
streaming would have widened increment 1 materially. It is the first item of increment 2.
