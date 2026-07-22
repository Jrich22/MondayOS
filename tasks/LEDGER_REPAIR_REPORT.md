# Task Ledger Repair Report

**Date:** 2026-07-21
**Scope:** Repair the MondayOS task-ledger sequence integrity **only**. Broader
Cue product-state reconciliation is explicitly **deferred** (see "Deferred").

---

## What was wrong

`tasks/.sequences.json` held `{"TASK": 19}` while the ledger already contained
task records through **TASK-0045**. Because `TaskManager._next_id()` returns
`counter + 1`, the next task created would have been **TASK-0020**, colliding
with the existing `[EPIC] Event Workspace` record.

The knowledge base shows this had **already happened repeatedly**: with the
counter stuck low, the same low IDs were minted for many different deliverables
over time, each new file overwriting the previous one on disk.

## What was repaired

1. **Sequence counter corrected:** `TASK: 19 → 45`. The next task ID is now
   `TASK-0046`; historical identifiers can no longer be reused.
2. **Genuine ledger files restored into git.** These records existed in the
   working tree (well-formed, with authentic creation/workflow/approval history
   dated 2026-07-08…07-09) but were untracked. They were added as-is, not
   reconstructed:
   - Active: `TASK-0020`–`TASK-0031`
   - Completed: `TASK-0032`–`TASK-0039`, `TASK-0041`

## What was intentionally NOT done (and why)

The original repair brief assumed a corruption state that **the repository did
not match**. Acting on it literally would have destroyed genuine data, so those
steps were declined (confirmed with the requester):

- **Did not migrate "TASK-0020 → TASK-0046."** TASK-0020 is the genuine
  `[EPIC] Event Workspace` record, consistent with
  `projects/cue-app/PRODUCT_WORKSPACE.md` and agent run logs
  (`Epic: TASK-0020 (event_workspace)`). No reconciliation task exists at
  TASK-0020 (nothing in `tasks/` matches "reconcil"). There was nothing to
  migrate.
- **Did not reconstruct TASK-0020–0037.** They already exist as genuine records;
  regenerating them would overwrite real history with invented content.
- **Did not fabricate a reconciliation task** at TASK-0046 (no pre-existing task
  to preserve).

---

## Unresolved historical gaps — for human review

**Root symptom:** while the counter was behind, low task IDs were reused for
multiple unrelated deliverables. The knowledge base still references those IDs
by their *old* meanings, which no longer match the current ledger. These are
**not** auto-repairable without human decisions and are **left untouched**:

### TASK-0020 — reused for at least four different deliverables

| Referenced as (in `knowledge/research/`) | Docs |
|---|---|
| App Shell (Navigation + Layout) | RES-0004 |
| Event Dashboard | RES-0011, RES-0012, RES-0013, RES-0014 |
| Create-Event Flow | RES-0016, RES-0017, RES-0018, RES-0019, RES-0051, RES-0053 |
| Basic Event Detail Page | RES-0021, RES-0022, RES-0023, RES-0024 |
| **Current ledger:** `[EPIC] Event Workspace` | `tasks/active/TASK-0020.md` |

`TASK-0020` is referenced across **~25 research documents** under these
conflicting titles.

### TASK-0021 — reused

| Referenced as | Docs |
|---|---|
| Guest List Model | RES-0026, RES-0027, RES-0028, RES-0029 |
| **Current ledger:** `[EPIC] Guest / Attendee Management` | `tasks/active/TASK-0021.md` |

### Consistent references (no action needed)

- `projects/cue-app/PRODUCT_WORKSPACE.md` and `knowledge/docs/DOC-0014.md` both
  match the current ledger (0020 = Event Workspace epic; 0033 = Create-event
  flow; 0030 = App shell; etc.).
- `tasks/ACTIVE.md`, `tasks/DONE.md`, `tasks/BACKLOG.md` are empty placeholders
  (no stale references).
- `dashboard/src/adapter/demo-data.ts` uses fictional task IDs (incl. a demo
  "TASK-0046") for the demo adapter — **not** ledger references; out of scope.

**Recommended human decisions:**
1. Decide whether the overwritten historical deliverables (Event Dashboard,
   Create-Event Flow, Event Detail Page, Guest List Model) warrant their own
   distinct task IDs, or should be treated as superseded research captured only
   in the knowledge base.
2. If new IDs are assigned, update the affected `RES-*` cross-references
   accordingly. This requires product/eng knowledge and is not safe to automate.

---

## Deferred (not part of this repair)

- **Cue workspace force-regeneration** — not run; its overwrite behavior must be
  inspected first.
- **Broader Cue product-state reconciliation** — not performed.
