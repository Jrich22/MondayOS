# Cue App — Product-State Reconciliation (TASK-0046)

**Date:** 2026-07-21 · **Branch:** `reconcile/cue-product-state` · **Type:** project-state reconciliation (not a product feature)

Reconciles MondayOS roadmap / tasks / knowledge / docs against the **actually
implemented** Cue App, using GitHub + `projects/cue-app` source as authoritative
evidence. No product features were built; no completed modules were redesigned.

---

## 1. Confirmed implemented capabilities

The **seven requested capability areas plus Multi-Organization Architecture**
(eight rows below) are implemented and merged to `main`, evidenced by source
modules, tests (231 passing), and merged PRs. Multi-Org was delivered alongside
the requested set and is included here for completeness.

| Capability | Source (under `projects/cue-app/src`) | Task | Evidence |
|---|---|---|---|
| Event workspace foundation | `components/shell`, `create`, `dashboard`, `detail`; `lib/create,detail,data` | 0030,0032,0033,0034,0037 | commit `4560b42` |
| Guest / attendee model | `components/detail/guests`; `lib/guests*` | 0035 | commit `4560b42` |
| Multi-organization architecture | `lib/branding,classification` | 0038 | commit `4560b42` |
| Roll Call Command Center | `components/rollcall`; `lib/rollcall*` | 0040 | **PR #3** (`772dbf53`) |
| Mission Control | `components/mission`; `lib/mission` | 0042 | **PR #4** (`b5424583`) |
| Communications Center | `components/comms`; `lib/comms*` | 0043 | **PR #5** (`ad1a18ff`) |
| Relationship Intelligence | `components/people`; `lib/person-graph,person-timeline,person-ai` | 0039,0044 | **PR #6** (`9fcd3441`) |
| QR & Badge Check-In | `components/checkin`; `lib/qr,badge,checkin` | 0045 | **PR #7** (`b17b3b0f`) |

## 2. Records corrected

| Record | Change | Basis |
|---|---|---|
| `TASK-0030` App shell | `review → completed` (moved to `completed/`) | Merged to main in `4560b42`; foundational to all shipped work; siblings completed 2026-07-09 |
| `TASK-0031` Auth placeholder | `review → completed` | `lib/session.ts` merged in `4560b42` (mock user, no prod auth per DEC-0003) |
| `TASK-0040` Roll Call | Fixed corrupt status token `in_progress → in-progress` | `TaskStatus` enum canonical value; record was previously unparseable by the strict parser |
| `commit_refs` on 0030–0045 | Populated with commit/PR evidence (were all empty `[]`) | git history + merged PRs |
| `DOC-0006` Overview & current state | "Greenfield / no code exists" → reconciled shipped state | Contradicted by merged source |
| `DOC-0012` V1 Roadmap | Sprint 1 marked SHIPPED; Sprint 2 out-of-order note added | git history |
| `PRODUCT_WORKSPACE.md` | Added reconciled implemented-state section + regen caution | this reconciliation |
| `TASK-0046` | Created (reconciliation task; counter 45 → 46) | this work |

Status changes use actor `reconciliation:TASK-0046` and preserve prior
`status_history` and chronology.

## 3. Canonical mappings for reused historical IDs

While the sequence counter was stuck (repaired in PR #10), low IDs were re-minted
for different deliverables, so historical `RES-*` research references `TASK-0020`
/`TASK-0021` under old meanings. **The `RES-*` files are intentionally left
untouched.** These are the canonical current IDs:

| Historical reference (in `RES-*`) | Canonical task |
|---|---|
| App Shell | **TASK-0030** |
| Event Dashboard | **TASK-0032** |
| Create-Event Flow | **TASK-0033** |
| Event Detail | **TASK-0034** |
| Guest List Model | **TASK-0035** |

Reference distribution (from the ledger-repair report): `TASK-0020` appears in
~25 `RES-*` docs under the first four meanings above; `TASK-0021` in RES-0026–0029
as Guest List Model. Rewriting that history requires human product judgment and
is **not** done here.

## 4. Active roadmap — next-increment analysis (conflicting evidence)

**"Event Planning as the next increment" is only partially supported — flagged,
not confirmed.**

- The documented roadmap (`DOC-0012`) orders **M1 Guests & Invites → M2 AI
  Planning → M3 Execution → M4 Learn**, with the rationale "guests before AI."
- Reality: Sprint 2 shipped guest/relationship (M1-ish) **and** execution (M3-ish:
  Roll Call, Mission Control, Comms, QR/Badge) **out of order**.
- The AI-planning prerequisite (real event + guest model) **is** satisfied.
- **But** the explicit M1 item **Invite & RSVP Flow (TASK-0023)** and all of
  **M2 AI Planning** (AI Agenda Builder `TASK-0022`, Run-of-Show `TASK-0025`) are
  still backlog epics.

**Conclusion:** Event Planning (AI Agenda/Run-of-Show, M2) is a *reasonable* next
increment because its foundation exists, but it **conflicts with the documented
value-chain order**, which places Invite & RSVP (M1) first. **Product impact:**
choosing Event Planning next skips in-app invites/RSVP; choosing M1 first defers
the differentiated AI wedge. This is a product-owner decision — recorded here,
not resolved. (Per task scope, Event Planning was **not** implemented.)

## 5. Preserved historical ambiguities / could-not-confirm

- **`RES-*` reused-ID history** — preserved verbatim; canonical mapping added
  above instead of rewriting.
- **TASK-0039 (Org & Relationship Intelligence Foundation) and TASK-0041 (Event
  Lifecycle Workspace)** — functionality landed bundled in `4560b42` (foundation)
  and evolved through later PRs; a single clean per-task commit cannot be
  attributed. Linked to `4560b42` with this caveat.
- Sprint 1 (0030–0037) all landed in the single commit `4560b42` (Sprint 1 MVP
  bundled with the first Sprint 2 commit); per-task commits are not separable.

## 6. Follow-ups (out of scope here)

1. **MondayOS-wide version drift** — not investigated in this Cue reconciliation
   (not required for it); flagged as a separate follow-up.
2. **`RES-*` cleanup** — human decision on whether overwritten historical
   deliverables get their own new IDs (starting `TASK-0047`) or stand as
   knowledge-only research.
3. **Doctor TESTS false positive** — `[TESTS] 6 test(s) failed` is a phantom
   stale-`.pytest_cache` entry for removed `test_*_not_yet_implemented` node IDs
   (0 definitions today); all live suites pass. Clear with `pytest --cache-clear`.
4. **`setup_workspace.py --force`** — uses `overwrite=True` and re-mints task IDs;
   **not run**. Must be proven non-destructive against the reconciled ledger first.

## 7. Validation

- **MondayOS Python suite:** 1082 passed, 12 skipped, 5 subtests passed.
- **Cue App tests:** 231 passed (22 files).
- **Cue App typecheck:** clean (`tsc -b --noEmit`, 0 errors).
- **Cue App production build:** success (137 modules).
- **All 46 task records parse** under the strict `TaskManager` parser.
- **`monday doctor`:** only critical is the phantom stale-cache TESTS entry above;
  29 active tasks (was 30; −2 completed +1 created), consistent with the ledger.

> Note: the Python total (1082, not the 978 baseline) includes an **unrelated,
> uncommitted `retention/` package** present in the working tree. It is
> deliberately **excluded** from this branch and commit.
