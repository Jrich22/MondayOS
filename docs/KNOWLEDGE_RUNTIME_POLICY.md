# Runtime vs. Git Knowledge Policy

**Status:** ADOPTED. Phases 1–3 are implemented and shipped; 4–5 remain open.
**Author:** repository-health pass, 2026-08-13
**Prompted by:** the RES-0100 duplicate-ID collision, then re-prompted — far more
sharply — when the same untracked-generated-knowledge problem was found to be
failing the agent pipeline against itself (see §6).

> This began as a proposal. The open question in §5 — whether human-authored
> `RES-*` research is ever expected — was answered *yes*, so classification is by
> **provenance**, not entry type. That decision is now implemented.

---

## 1. The problem

*(As surveyed when this policy was written. Resolved by §3; kept because the
measurements are the argument.)*

`knowledge/research/` held **104 records. All 104 were machine-generated** — every
one carrying the footer `_Captured by the Execution Orchestrator for task
TASK-XXXX via provider 'X'._` (83 anthropic, 21 openai, spanning 16 tasks).
**Zero were human-curated.**

They have never been tracked in Git, yet the knowledge ledger counts them as
active — 104 of 135 active entries. That gap between "counted" and "committed"
is what produced the RES-0100 collision: `main`'s counter said `RES: 99` while
104 records already existed on disk, so the next allocation reused a live ID.

This is the **second** occurrence of the same bug class. `knowledge/KNOWLEDGE_LEDGER_REPAIR.md`
records the first: *"99 research records existed with no counter — a second latent
collision."* The counter was added then, but the records stayed untracked, so the
counter drifted out of sync again the moment work happened on another branch.

Committing all 104 would only trade one problem for another: an ever-growing Git
diff of model output, where a single execution result can run to hundreds of lines
of generated code that duplicates what already lives in `projects/cue-app/src/`.

There is also a correctness argument. The generated RES-0100 record for sourcingBOT
lists as a project risk: *"Critical repository issues: 6 test failures, 11 uncommitted
changes."* Those six failures were the phantom `lastfailed` entries — never real.
Generated knowledge captured a point-in-time snapshot of a **bug** and preserved it
as durable project truth. Machine output is evidence of what a model said at a
moment, not a durable fact about the system.

## 2. Policy

Classify by **provenance**, not by entry type.

| Belongs in Git | Belongs in the runtime store |
|---|---|
| Source code, tests, configuration | Orchestrator execution results |
| Architecture, decisions (`DEC-*`), docs (`DOC-*`), patterns (`PAT-*`), sprints (`SPR-*`) | Agent run transcripts and verdicts |
| Roadmaps, charters, durable curated research | Provider-specific generated commentary |
| **Sequence counters** (`.sequences.json`) | — |

Three rules follow:

1. **Curated knowledge is committed.** Anything a human authored or approved is
   durable and belongs under version control.
2. **Generated knowledge is preserved, not committed.** It stays readable and
   queryable through MondayOS's store; it does not enter the Git diff by default.
   Nothing is deleted.
3. **ID allocation is authoritative regardless of storage.** The counter must
   account for every entry that exists in either place. This is the rule whose
   absence caused both collisions.

## 3. Migration path

Ordered so each phase is independently verifiable and reversible. **Phase 1 is the
one that matters** — it stops the bug class recurring and is a small, testable change.

**Phase 0 — done.** RES-0100 collision resolved: the newer Invite & RSVP reviewer
entry became RES-0105, counter set to 105. Both records preserved.

**Phase 1 — make ID allocation self-healing.** Today `KnowledgeStore._next_id`
(`knowledge/store.py:228`) trusts the counter alone:
```python
next_seq = self._sequences.get(prefix, 0) + 1
```
Change it to take `max(counter, highest_id_on_disk) + 1`. The counter stays the
fast path; the disk scan is the guard. Had this been in place, the collision could
not have happened regardless of what was or wasn't committed. Ship this first —
it is valuable on its own even if no other phase proceeds.

**Phase 1 — DONE** (PR #19, `f7f393d`). `KnowledgeStore._next_id` now takes
`max(counter, highest_id_on_disk) + 1`; the same guard was added to
`TaskManager._next_id` in `76d2368` after the identical defect surfaced there.

**Phase 2 — mark provenance as a first-class field. DONE** (`fdae657`).

Implemented on the existing `authored_by` field rather than by adding a new
`origin` field. `authored_by` already existed and was *wrong*: orchestrator
captures were stamped `authored_by: human`, which is false and was the reason
nothing downstream could tell curated knowledge from model output. `Monday.learn`
now takes `authored_by`, and `ExecutorEngine._capture_knowledge` passes `"agent"`.
Fixing the lie and adding the signal turned out to be the same change — a new
field would have left the false one in place.

**Phase 3 — split the storage path. DONE** (`fdae657`). `KnowledgeStore.
_type_dir_for()` routes on provenance: generated → `knowledge/runtime/<type>/`
(gitignored), curated → `knowledge/<type>/` (tracked). `remove()` resolves
through the same routing, or generated entries would have been un-deletable.
The loader rglobs `knowledge/`, so loading, search, retrieval and counts are
unchanged — verified by test, not assumed.

**Phase 4 — relocate the existing records. DONE for the 20 on `main`**
(`fdae657`). `knowledge/GENERATED_INDEX.md` is committed as the audit manifest.

**Still open:** the 104 records on `wip/mondayos-v4-retention-and-health` are
committed to that branch's `knowledge/research/`, and `RES-0105` is a generated
record committed to `main` before this convention existed. Neither is urgent —
both are preserved and neither can cause an ID collision now that allocation
self-heals — but the branch's `knowledge/research/` commit should be retired
against this manifest when that branch is next touched.

**Phase 5 — backfill the guard.** Add a `monday doctor` knowledge check that fails
when any counter is below the highest on-disk ID, so the invariant is enforced
continuously rather than repaired after the fact.

## 4. Explicitly rejected

- **Deleting the 104 records.** They are real execution history and the only record
  of several agent runs.
- **Committing all 104 as-is.** Turns model output into permanent Git history and
  guarantees the diff grows without bound.
- **Gitignoring `knowledge/research/` wholesale.** Would silently exclude any future
  human-curated research entry, which legitimately belongs in Git.

## 5. Resolved: is human-authored `RES-*` research expected?

**Yes — decided 2026-08-13.** Curated research had zero members at the time, and
the simpler end state would have been to make `knowledge/research/` wholly
runtime and keep curated long-form knowledge in `DOC-*`. That was rejected
because it forecloses a category: a human-authored research entry legitimately
belongs in version control, and a path-based rule would have silently excluded
it.

So classification is by **provenance, not path or type**. `knowledge/research/`
remains source-controlled and accepts curated entries; only `authored_by != human`
routes to the runtime store. A test asserts that an entry with *absent*
provenance is treated as curated, so a missing field can never silently hide
knowledge from version control.

## 6. Why this stopped being a tidiness concern

The original argument was that committing model output grows the Git diff
without bound. That was true but mild. The sharper problem appeared later: the
untracked generated records were **failing the agent pipeline against itself**.

Every agent run wrote a research record and bumped a sequence counter.
`GitAnalyzer` counted those as "uncommitted changes", `advisor/engine.py`
escalated the count to a HIGH engineering risk, and
`ExecutionPlanner._build_context` injected it into the next run's prompt as
repository state. QA then failed the task for the mess the pipeline had just
made — and because each run added more records, **every rerun made the number
worse.** Two team runs on TASK-0056 (`team-e444918cdb19`, `team-7b1c978e39f9`)
stopped at QA this way, leaving `RES-0123`–`RES-0125` behind as they did so.

Provenance routing (§3) removes the cause. `GitAnalyzer` separating source
dirtiness from runtime state (`fdae657`) removes the amplifier. Both were
needed: routing alone would still have miscounted sequence counters and run
logs, and classification alone would have left generated records accumulating in
the source tree.

The lesson worth keeping: **a system that measures its own repository must not
count its own runtime output as a defect.**
