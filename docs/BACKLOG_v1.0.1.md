# MondayOS v1.0.1 — deferred backlog

Everything the v1.0 acceptance programme found and deliberately did not fix.
Ordered by observed user impact, not by how interesting the defect is.

Each item states the guarantee it affects. **None of them affects one** — that is
why they are here rather than in v1.0. They are ordered so the work that costs
users answers comes before the work that closes a theoretical hole.

---

## 1. D-12 — resolve dotted `Class.method` citations

**Impact: 3 of 52 hosted turns lost a correct answer (5.8%).** The largest single
cause of refusal in the acceptance run.

The evidence authority resolves symbols but not `Class.method` references, so a
correct citation is reported unverifiable and the answer is refused.

**Care required.** This means loosening a citation check. The multi-handle bypass
(D-6) existed because a citation shape was not resolved strictly enough, so this
work needs its own adversarial battery: a dotted reference whose class exists and
whose method does not must still be refused, as must a method on a class from
another project.

**Status: fixed for parented languages.** `ProjectAuthority.symbol` now resolves
`Owner.member` when — and only when — the index proves the member belongs to that
owner, read from `Symbol.parent`. No index schema change.

**Residual.** Ownership can only be proved where the scanner records a parent:
**56% of MondayOS definitions, and 0% of cue-app's**. TypeScript symbols are
regex-extracted without a parent (see `docs/AI_WORKSPACE.md` §7), so a qualified
TypeScript citation such as `EventStore.save` still resolves to UNKNOWN and still
fails an answer closed. Giving the TypeScript scanner a parent is the remaining
half of D-12, and it is a scanner change rather than an authority one.

---

## 2. D-10 — recognise ranges and bare concept words as score claims

**Impact: none observed.** One occurrence in 52 turns, and the value was
authoritative.

Two shapes escape extraction: `94–95%`, where the first number shares the second's
percent sign, and `Strong evidence (94%)`, where the label omits "strength".

This is the only deferred item that is **fail-open**, which is why it is second
rather than last despite never having caused harm. The range form can appear in
the primary score-reporting position.

**Care required.** Every previous widening of this extractor was correct and
incomplete, and each gap was found by running against a real model rather than by
reasoning about the pattern. Any change here must keep the full D-4 negative
battery green: `36% of the codebase`, `Coverage: 95%`, `Memory utilization: 88%`,
`CPU: 72%`, `File count: 61`.

---

## 3. D-11 — do not inherit a score concept onto a ratio

**Impact: once in 52 turns, with no standalone consequence.**

Under a heading that establishes a concept, `yield a 0.12 ratio` inherits it. The
likely shape of a fix is to exclude values introduced as a named non-score
quantity (ratio, threshold, count) rather than to narrow heading scope, which
would reopen D-9.

---

## 4. D-8 — recognise prose *about* handle syntax

**Impact: one wasted provider call on turns that discuss MondayOS's own plumbing.**

The lowest-value item on this list, recorded for completeness. It may be correct
to never fix it: the cost is one correction, and the alternative is a citation
check that tolerates citation-shaped text it cannot resolve.

---

## 4b. Platform quality — managed products inflate MondayOS's self-measurements

**Impact: MondayOS's own strategic answers report statistics that are 30% not
about MondayOS.**

`projects/cue-app` and `projects/sourcingbot` live inside the MondayOS tree, so
the MondayOS index contains **222 of 751 files (30%) that belong to managed
products**. `project_boundaries` correctly makes them unreachable as evidence —
measured citation leakage is zero — but nothing excludes them from the *counts*.

The visible symptom is the recurring inference in MondayOS's own executive
answers that *"projects concentrates 36% of the codebase in one area, making it
the place where a regression would cost most."* That area is not MondayOS code.
The file counts, area counts and concentration ratios a self-question returns are
all computed over the union.

Not a correctness defect — no wrong evidence is cited, and isolation holds. It is
a measurement-honesty defect in the platform's view of itself, which matters
precisely because self-reasoning is what MondayOS is for.

**Care required.** The boundary set already identifies exactly which roots to
exclude, so the fix is likely to be scoping the statistics rather than the index —
files under a nested project root should stay indexed (they are the products'
own evidence when those projects are queried) but not counted as the parent's.
Changing what the index *contains* would drift the benchmark; changing what the
statistics *count* should not, and that expectation needs checking before the
work starts.

## 5. Measurement — retire the harness's independent score reader as a verdict source

Already done for gate 6, which now reads the product's `score_validation`. The
independent reader remains as an adversarial cross-check, and it earned that
place: it found D-9 and D-10, both on their first live run after being added.

Remaining work is presentational — the readiness page reports audit
disagreements as a gate-6 failure line rather than as a distinct integrity
finding, which reads as a model violation when it is a measurement one.

---

## 6. Gate 4 — obtain a truncation observation

Gate 4 ("no answer reported complete if output was truncated") returned
INCONCLUSIVE in the final run: 45 opportunities, 0 exercised. `stop_reason` is now
recorded correctly on every turn — 50 of 52 came back `end_turn` — and nothing
truncated, so there was nothing to score.

The gate is exercisable and unit-tested in both directions. Closing it in a
hosted run needs a turn whose output is deliberately allowed to hit the token
ceiling. That is a harness scenario, not a product change.

---

## 7. Repository lint drift

`ruff check .` reports 337 findings across `tests/`, `retention/`, `projects/`,
`monday/` and others, none in the packages the v1.0 work touched. They are new
rules (`UP037`, `UP017`) from a ruff upgrade to 0.15.20, not new code. A
mechanical sweep, deliberately not done inside a release review.

---

## 8. `mypy` stub gap

`workspace/store.py` reports missing PyYAML stubs. One line of dependency
configuration, pre-existing.
