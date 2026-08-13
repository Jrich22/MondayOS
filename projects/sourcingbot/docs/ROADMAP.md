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

## Increment 2 — Req Workspace Authoring ✅ *this increment*

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
