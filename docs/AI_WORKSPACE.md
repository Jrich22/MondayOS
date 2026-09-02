# AI Workspace

**Version:** 0.1.0  
**Status:** Draft  
**Last Updated:** 2026-09-02  
**Owner:** Lead Software Engineering

---

> **Status note.** Increment 1 is implemented: the Conversation domain, project-scoped
> persistence, the deterministic Context Engine with attributed sources, the responder seam
> backed by the existing MondayOS provider abstraction, the `Monday.workspace()` API, the
> dashboard API routes, and the AI Workspace UI.
>
> **There is no model router yet.** One configured provider answers every request. Streaming is
> not implemented — responses are request/response. Knowledge capture is explicit only: nothing
> is written to knowledge unless a human presses the button, and what is written records that a
> model produced it and a human retained it without verifying it (§6).

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
| 3 | `tasks` | `TaskManager` — active tasks, then recently completed |
| 4 | `knowledge` | `KnowledgeStore` — recent entries scoped to the project |
| 5 | `git` | Branch, working-tree state, recent commits |

Priority is the budget order: source 1 is filled before source 5 is considered. Each source has a
character cap and the snapshot has a total cap; a source that hits its cap is marked
`truncated: true` and says so in the UI. Nothing is silently dropped.

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

## 8. Roadmap

| Increment | Scope | Status |
|---|---|---|
| **1 — Conversational Workspace Foundation** | Conversation domain, project-scoped persistence, deterministic Context Engine, responder seam, `Monday.workspace()`, dashboard routes, AI Workspace UI | **Implemented** |
| **2 — Context Retrieval & Project Bootstrap** | Token/SSE streaming; explicit task→project association; richer project bootstrap; relevance-ranked context retrieval; conversation summarisation for long threads; snapshot reuse and invalidation rules; improved project documentation discovery | Planned |
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
