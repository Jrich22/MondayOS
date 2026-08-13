# sourcingBOT — Architecture Decision Records

Newest first. Each records the decision, why, and what it costs.

---

## ADR-009 — Unsaved-changes is tracked by revision, not timestamp

**Status:** accepted · TASK-0056

**Context.** The draft indicator first compared `lastSavedAt` against
`updatedAt`. Both are ISO timestamps with millisecond resolution, so an edit
made in the same millisecond as a save compared equal and the surface reported
"all changes saved" over a pending edit. A component test caught it.

**Decision.** `Req` carries a monotonic `rev`, incremented by every
`updateReq`, and `savedRev`, stamped by `markSaved`. `hasUnsavedChanges` is
`rev !== savedRev`.

**Rationale.** Whether a recruiter's work is persisted must not depend on clock
resolution. Widening the comparison to `<=` would have inverted the bug —
reporting unsaved forever after a save. Because both fields are persisted, an
unsaved draft is still detectable after a reload, which an in-memory dirty flag
would not survive.

**Consequences.** Two small optional fields on `Req`. `lastSavedAt` is retained
for display ("saved 12s ago") but no longer decides correctness.

---

## ADR-008 — Readiness is derived, and separate from completeness

**Status:** accepted · TASK-0056

**Context.** Increment 2 needs a completeness indicator. The obvious approach is
a stored percentage updated on save, and a single number covering "how done is
this".

**Decision.** `lib/readiness.ts` computes everything on read from the existing
`Req` and `SourcingBrief`. It stores nothing and owns no entity. It reports two
distinct values:

- **completeness** (0–100%) — how much has been authored. A progress signal.
  Nothing blocks on it.
- **readiness** (boolean) — can this req discriminate between candidates? A hard
  gate on opening for sourcing.

Within a section, the items that *block* sourcing are tracked separately from
everything merely missing. A section can be essential without every field in it
being essential: `requirements` gates sourcing because a req with no must-have
filters nobody out, but a missing nice-to-have only costs ranking.

### The approved blocker specification

These six items — and only these — prevent a req being opened for sourcing.
Approved by the product owner 2026-08-13. This list is the specification;
`canOpenForSourcing` implements it. Changing it is a product decision, not a
refactor.

| # | Blocker | Why it gates |
|---|---|---|
| 1 | Requisition code | The req cannot be identified without one |
| 2 | Role title | Nothing to source *for* |
| 3 | Owning team | No accountable owner for the search |
| 4 | Primary location | The search has no geographic anchor |
| 5 | Search headline | No stated definition of who this search is for |
| 6 | At least one must-have | **The load-bearing one.** With no must-have the req filters nobody out, so every candidate trivially matches — worse than having no req at all |

**Explicitly advisory, NOT blocking** (each appears in `suggestions`, never in
`blockers`):

- **Target locations** — approved as advisory 2026-08-13. A recruiter may
  legitimately open a remote or location-agnostic search, and blocking on it
  would force a meaningless value to be entered.
- Nice-to-haves — cost ranking quality, not the ability to source.
- Job description, intake notes, hiring manager, target/excluded industries and
  companies, keywords, experience guidance, sourcing goals.

The distinction throughout: **a blocker is something whose absence makes
sourcing produce a wrong result. Everything else makes it produce a
less-good one.**

**Rationale.** A stored percentage is a third source of truth that can disagree
with the two records it summarises — it goes stale the moment a brief is edited
through any path that forgets to recompute it.

Keeping the two values apart matters more. A req can be 90% complete and unable
to source, or 60% complete and ready. Collapsing them hides exactly the case
that matters. The first implementation derived blockers from section progress,
which silently made every optional field mandatory — a nice-to-have became
required to open a req. Tests caught it; the split above is the fix.

**Consequences.** Readiness is recomputed on every render. It is pure arithmetic
over two small objects, so this is cheaper than the staleness bugs a cached
value would cause.

---

## ADR-007 — Increment 2 extends the existing models; no authoring entity

**Status:** accepted · TASK-0056

**Context.** Authoring adds ~10 fields: full job description, intake notes,
target and excluded industries, experience guidance, sourcing goals, draft
state. A separate `ReqDraft` entity — or an authoring-shaped DTO — would have
kept the "clean" foundation models untouched.

**Decision.** Extend `Req` and `SourcingBrief` in place. Every new field is
optional, so requisitions written by Increment 1 keep loading unchanged. No new
collection was added to `WorkspaceState`; a test asserts the five collections
are unchanged.

Field placement follows ownership rather than convenience:

| Field | Home | Why |
|---|---|---|
| Job description, intake notes, sourcing goals | `Req` | Facts about the *role and the search*, true regardless of how it is searched |
| Target/excluded industries, experience guidance | `SourcingBrief` | Search *targeting*, revised as the search is tuned |
| Must-haves / nice-to-haves | `SourcingBrief.requirements` | Already existed as `required` / `preferred` |

**Alternatives rejected.** A separate draft entity means two records that can
disagree about the same requisition, plus a promotion step where they are
reconciled — the exact fragmentation ADR-002 exists to prevent, applied to reqs
instead of people.

**Consequences.** `Req` and `SourcingBrief` are larger. Accepted: they are the
domain, and the alternative was a synchronisation problem. `Candidate` and
`ReqCandidate` are untouched by this increment, and a test asserts authoring
never writes to them.

---

## ADR-001 — Build inside MondayOS as `projects/sourcingbot/`, mirroring Cue

**Status:** accepted · TASK-0054

**Context.** TASK-0053's generated design output (RES-0106, RES-0107) specified a
separate GitHub repository, PostgreSQL or MongoDB persistence, and a
`products/sourcingbot/` tree importing `@mondayos/core/product`,
`ProductTier`, and `FeatureGate`.

None of those packages exist in this repository. MondayOS has no TypeScript core
library; its one managed product, Cue App, lives at `projects/cue-app/` as a
self-contained Vite app registered in `config/projects.json`. TASK-0054 also
specifies local/mock persistence, contradicting the database recommendation, and
"managed MondayOS product", contradicting the separate repo.

**Decision.** Follow TASK-0054 and the actual Cue convention. Treat RES-0106/0107
as advisory rather than literal. Confirmed with the product owner before any
file was written.

**Consequences.** A MondayOS engineer finds identical tooling across products.
Building `@mondayos/core` first would have been a far larger increment than the
task describes. The cost is that the generated design output and the shipped
architecture diverge — this ADR is the reconciliation record.

---

## ADR-002 — Candidate and ReqCandidate are separate, and the separation is enforced in code

**Status:** accepted · TASK-0054 · the product's defining constraint

**Context.** The obvious model keys a candidate to a requisition. It is simpler
and it is what most systems do — and it fragments one human into N records,
destroying cross-req history and making concentration analytics wrong.

**Decision.** `Candidate` is a persistent person; `ReqCandidate` is one
evaluation holding `candidateId` + `reqId` and nothing that duplicates the
person. Enforce it with `assertNoIdentityDuplication`, which throws if a
Candidate-owned field appears on a ReqCandidate, plus a test asserting the
serialized store contains no person's name inside `reqCandidates`.

**Alternatives rejected.** *Single collapsed record* — the failure mode above.
*Denormalize name onto ReqCandidate for render performance* — the exact erosion
the guard prevents; `joinPipeline` handles it at render time instead.

**Consequences.** Every pipeline surface must join. Worth it: the join is one
tested function, and the model rule survives serialization, refactors, and future
contributors who have not read this file.

---

## ADR-003 — Briefs are versioned; evaluations cite the version they were made against

**Status:** accepted · TASK-0054

**Context.** A recruiter raises the bar mid-search. Existing fit scores were
computed against the old bar. Silently comparing them to the new brief makes
stale numbers look current.

**Decision.** All brief mutations route through `reviseBrief`, which bumps
`version`. `ReqCandidate.briefVersion` records what an evaluation was made
against; `needsReassessment()` flags the gap and the UI surfaces it.

**Consequences.** Evaluations are never silently invalidated *or* silently
trusted — the discrepancy is shown and a human decides. Cost: every mutation must
go through `reviseBrief`, which is why no setter mutates requirements directly.

---

## ADR-004 — The supervision gate ships before the LinkedIn workflow

**Status:** accepted · TASK-0052, TASK-0053, TASK-0054

**Context.** The LinkedIn workflow is deferred, so shipping nothing LinkedIn-
related now was an option.

**Decision.** Ship `lib/linkedin.ts` — the boundary — in this increment.
`startSession` throws without a named operator, a per-session acknowledgement,
and an open req. `PROHIBITED_CAPABILITIES` enumerates what the product refuses to
do, with `supportsCapability()` returning false for each.

**Rationale.** A boundary written after the feature it constrains is negotiated
against delivery pressure. Written first, it is the thing the feature must fit.

**Consequences.** Code exists that nothing calls in production yet. Accepted
deliberately: it is 18 tests of executable policy.

---

## ADR-005 — Install an in-memory `localStorage` for tests

**Status:** accepted · TASK-0054

**Context.** Node 22+ defines its own `localStorage` global that stays
`undefined` without `--localstorage-file`. Under vitest's jsdom environment
`window === globalThis`, so that built-in shadows jsdom's implementation — both
`localStorage` and `window.localStorage` read as undefined. Cue never noticed
because its tests never touch storage; its store simply degrades to in-memory via
a `typeof localStorage === "undefined"` guard, leaving persistence untested.

**Decision.** Add `src/test/setup.ts` with a real in-memory `Storage`, wired via
`setupFiles` in `vite.config.ts`.

**Alternatives rejected.** *Mirror Cue and leave persistence untested* — hides a
whole layer. *Run with `--localstorage-file`* — writes real files during tests
and couples the suite to a Node flag.

**Consequences.** One deliberate config difference from Cue, and the load/persist
round trip is genuinely covered.

---

## ADR-006 — An unmet required requirement caps the fit score at 0

**Status:** accepted · TASK-0054

**Context.** A weighted average across all requirements lets strong preferred
matches mask a missing required one, producing a high score for a disqualified
candidate.

**Decision.** Any required requirement not explicitly met returns 0. `unknown`
counts as unmet for required items — absence of evidence is not evidence — but is
excluded from the preferred pool rather than penalized, so "not asked yet" does
not read as a negative finding.

**Consequences.** Scores are blunt at the low end: a candidate missing one
required item scores 0 regardless of other strengths. That is the intended
signal — the rationale field carries the nuance.
