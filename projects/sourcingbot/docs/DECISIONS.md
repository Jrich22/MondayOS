# sourcingBOT — Architecture Decision Records

Newest first. Each records the decision, why, and what it costs.

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
