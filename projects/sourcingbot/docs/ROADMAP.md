# sourcingBOT — Roadmap

Each increment is independently reviewable and leaves the product working.

---

## Increment 1 — Product Workspace Foundation ✅ *this increment*

**TASK-0054.** Establish the product, its shell, and the domain model everything
later depends on.

- Managed MondayOS product at `projects/sourcingbot/`
- Premium application shell — sidebar, header, persistent oversight strip
- Req Workspace with lifecycle (`draft → open → on-hold → closed`)
- Structured Sourcing Brief with addressable requirements and versioning
- Persistent **Candidate** model
- Separate **ReqCandidate** model, enforced against identity duplication
- Per-req pipeline: stage machine, weighted fit scoring, stage history
- Talent concentration analytics
- Supervised-session boundary (gate only, no workflow)
- localStorage persistence behind a single seam
- 99 tests, strict typecheck, clean production build
- Full documentation set

**Why the model came first.** Every later increment writes through Candidate and
ReqCandidate. Getting the separation wrong would have made each subsequent
increment progressively more expensive to correct.

---

## Increment 2 — Workspace Authoring

Today's data is seeded and read-only in the UI; the domain layer supports
mutation but no surface exposes it.

- Create and edit requisitions
- Author and revise briefs, with the version bump surfaced
- Add people manually; act on the duplicate warning
- Attach an existing person to a req; move stages with reason capture
- Record assessments and recompute fit
- Reassessment prompt when an evaluation trails the brief version

**Deferred to here because** authoring surfaces are cheap to build once the rules
they enforce are settled and tested.

---

## Increment 3 — Supervised LinkedIn Sourcing Workflow

The human-driven workflow, passing through the gate shipped in Increment 1.

- Session start UI with per-session policy acknowledgement
- Manual capture form — the recruiter pastes what they reviewed themselves
- Session history per req, with counts and notes
- Duplicate check against the existing talent pool at capture time

**Explicitly still prohibited:** unattended scraping, scheduled crawling,
rate-limit bypass, automation evasion, bulk export, credential storage. These are
not later increments — they are outside the product. See
[LINKEDIN_POLICY](LINKEDIN_POLICY.md).

---

## Increment 4 — Sourcing Intelligence

The payoff of the persistent-person model.

- Cross-req candidate suggestions: "11 people you already know fit this brief"
- Talent concentration over time, and movement between companies
- Funnel conversion by stage, by req and in aggregate
- Prior-evaluation surfacing when adding someone to a new req

**Requires Increments 2–3** for enough real evaluation history to be meaningful.

---

## Increment 5 — Persistence & Multi-User

- Replace `localStorage` with a MondayOS-backed store (rewrite `load`/`persist`)
- Real authentication and per-recruiter attribution
- Concurrent edit handling
- Migration from local workspaces

**Deferred deliberately.** Local persistence keeps Increments 1–4 free of backend
coupling. The store seam exists so this is a one-file change, not a rewrite.

---

## Not planned

| Not building | Why |
|---|---|
| Full ATS (offers, scheduling, compliance) | sourcingBOT ends where formal application begins |
| Automated outreach sending | Contact is a human decision |
| Autonomous candidate advancement | Stage changes require an accountable person |
| Any prohibited LinkedIn capability | Outside the product's definition, permanently |

## Sequencing rationale

Model → authoring → supervised capture → intelligence → infrastructure.

Each stage depends on the correctness of the one before it, and the data model
is the only decision that becomes *more* expensive to change with every
increment — so it went first, with the supervision boundary alongside it.
