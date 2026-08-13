# sourcingBOT — Roadmap

Each increment is independently reviewable and leaves the product working.

---

## Increment 1 — Product Workspace Foundation ✅

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

## Increment 2 — Req Workspace Authoring ✅

**TASK-0056.** Turn the foundation into a usable recruiter workflow.

- Create, edit, save, and reopen requisitions; multiple reqs side by side
- Full job description and hiring-manager intake notes
- Targeting: locations, target/excluded industries, target/excluded companies
- Must-haves and nice-to-haves, keywords, experience guidance, seniority band
- Sourcing goals (candidate/contact targets, free-text framing)
- Autosave with an exact unsaved-changes indicator (ADR-009)
- Completeness ring and a separate sourcing-readiness gate (ADR-008)
- Progressive sections rather than one long form
- 159 tests, strict typecheck, clean production build

Built by extending `Req` and `SourcingBrief` — no competing authoring model
(ADR-007). `Candidate` / `ReqCandidate` untouched.

**Deferred within this increment:** archiving/deleting reqs, duplicating a req
as a template, per-requirement weight editing in the UI (the domain supports
weights; the surface writes sensible defaults), and rich-text job descriptions.

**Carried limitation:** autosave is last-write-wins across browser tabs — see
[SB-1](#sb-1--authoring-autosave-is-last-write-wins), resolved in Increment 5.

## Increment 3 — Supervised Sourcing Sessions ✅ *this increment*

**TASK-0057.** The human-driven workflow, passing through the gate shipped in
Increment 1.

- Start a named supervised session with per-session policy acknowledgement
- Pause / resume / complete, with interruption counts
- Manual candidate capture — the operator records what they reviewed themselves
- Duplicate detection against the persistent pool, with **reuse** offered
- ReqCandidate creation for the active req, with fit rationale and assessments
- Recruiter notes split: durable notes on the person, req-scoped on the evaluation
- Skipped and close-call tracking, session-scoped (ADR-011)
- Live session counts and capture rate; per-req session history
- **Editable requirement weights** and must-have ↔ nice-to-have switching
  (carried forward from Increment 2's known gaps)
- 241 tests, strict typecheck, clean production build

**Still prohibited, and still not implemented:** unattended scraping, scheduled
crawling, rate-limit bypass, automation evasion, bulk export, credential
storage, automated LinkedIn navigation, outreach. These are not later increments
— they are outside the product. See [LINKEDIN_POLICY](LINKEDIN_POLICY.md).

**Deferred within this increment:** Candidate enrichment on reuse (approved as
review-before-apply, ADR-013 / SB-2 — not implemented), editing or deleting a
capture after the fact, promoting a close call into a full capture in one action, session
duration/elapsed-time tracking, and bulk stage changes from the session surface.

## Increment 4 — Sourcing Intelligence

- **SB-2 — Candidate enrichment, review-before-apply (carried from Increment 3).**
  See Carried limitations. Governed by ADR-013.
- **SB-3 — User-created saved views (carried from the Candidate Workspace).**
  See Carried limitations.

The payoff of the persistent-person model.

- Cross-req candidate suggestions: "11 people you already know fit this brief"
- Talent concentration over time, and movement between companies
- Funnel conversion by stage, by req and in aggregate
- Prior-evaluation surfacing when adding someone to a new req

**Requires Increments 2–3** for enough real evaluation history to be meaningful.

---

## Increment 5 — Persistence & Multi-User

- **SB-1 — Concurrent edit safety (carried from Increment 2).** See below.
- Replace `localStorage` with a MondayOS-backed store (rewrite `load`/`persist`)
- Real authentication and per-recruiter attribution
- Migration from local workspaces

**Deferred deliberately.** Local persistence keeps Increments 1–4 free of backend
coupling. The store seam exists so this is a one-file change, not a rewrite.

---

## Carried limitations

Known behaviour that is not acceptable long-term, tracked here rather than left
as a code comment so it has a home and cannot be shipped as permanent by
default.

### SB-3 — Saved views are fixed; recruiters cannot create their own

**Raised by:** Candidate Workspace (TASK-0058) · **Resolve in:** Increment 4 ·
**Severity:** low — a convenience gap, no data or correctness risk

The talent pool ships five fixed saved views: Everyone, Reusable, Scored, Not yet
on a req, and From sourcing. They cover the questions the workspace was designed
around, and they need no persistence, no new entity, and nothing that can go
stale — which is why they were the right call for this increment.

They will not stay sufficient. Recruiters filter by the shape of their own desk:
"my reqs", "Boston staff-level", "everyone I skipped as a close call last
quarter". A fixed list cannot express that, and the requests will arrive quickly
once the workspace is in daily use.

**Deliberately not built yet.** A user-created view is the first thing in
sourcingBOT that must persist per-recruiter, which presupposes the identity and
storage layer Increment 5 introduces. Building it against `localStorage` now
would mean designing it twice, and would strand every saved view on one browser.

**Definition of done:** a recruiter can name, save, edit and delete their own
filter over the talent pool, and it follows them across devices.

### SB-2 — Candidate enrichment is discarded, pending review-before-apply

**Raised by:** Increment 3 (TASK-0057) · **Resolve in:** Increment 4 ·
**Severity:** low — no data loss from the record, but real information is dropped

Reusing an existing Candidate during capture does not update their record
(ADR-012). If the operator learns a new email or a job change while sourcing,
that information is discarded.

Discarding is the correct *default* — a hasty later capture must not clobber
facts established by a more careful earlier one — but it is not the end state.

**Approved product rule (ADR-013): enrichment must be review-before-apply.** New
information produces a **proposed change**, surfaced to the recruiter with the
current value, the proposed value, and its source. The persistent record changes
only on explicit approval. **Silent overwrite of a persistent Candidate fact is
not acceptable in any increment.**

**Definition of done:** an operator capturing a reused Candidate with new
details sees a proposal they can accept or reject, and the pool is never
modified without that decision.

### SB-1 — Authoring autosave is last-write-wins

**Raised by:** Increment 2 (TASK-0056) · **Resolve in:** Increment 5 ·
**Severity:** medium — data loss, but bounded to one recruiter's own tabs

The authoring surface autosaves the whole `Req` and `SourcingBrief` on a
debounce. Two browser tabs open on the same requisition will overwrite each
other: the last save wins and the other tab's edits are lost silently, with no
conflict signal.

Scoped by the current architecture rather than a bug: persistence is
`localStorage` on a single browser profile, so there is no server to arbitrate
and no second user to conflict with. Within one recruiter's own tabs it is still
real data loss.

**Why not fixed now.** A correct fix needs a conflict model — per-field
revisions, or optimistic concurrency against a store that can reject a stale
write. Both presuppose the real backend that Increment 5 introduces. Building a
conflict protocol against `localStorage` would mean designing it twice.

**Interim mitigation:** the save-state indicator (`rev` / `savedRev`, ADR-009)
tells a recruiter when their own edits are pending, so a tab is never silently
believed-saved. It does not detect a competing tab.

**Definition of done:** a second tab editing the same req either merges cleanly
or is told its write was rejected. Silent loss is not acceptable.

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
