# Cue Knowledge Ledger Integrity Repair (TASK-0047)

**Date:** 2026-07-21 · **Branch:** `repair/cue-knowledge-ledger` · **Scope:** narrow integrity repair of the knowledge sequence ledger. No Event Planning, no roadmap changes.

Base note: branches on `reconcile/cue-product-state` (PR #11, approved but unmerged), which is the state where `DOC-0006`/`DOC-0012` are tracked.

---

## What was wrong

`knowledge/.sequences.json` held `DEC=1, DOC=5, SPR=2` (plus `PAT=1`, and **no `RES` key**), while the knowledge base on disk had already advanced far beyond those counters. As with the earlier task-ledger defect, the next `monday learn` would have re-minted low IDs and overwritten genuine records.

The referenced-but-untracked Cue records were **present on disk as genuine files** (created 2026-07-08/09 with authentic human provenance), never committed — so this is a *restore into Git*, **not** a reconstruction. Nothing was fabricated.

## Records restored (genuine, untracked → tracked)

All parse and load; provenance preserved verbatim.

| ID | Title | created_at |
|----|-------|-----------|
| DEC-0002 | Cue App — Definition of Done | 2026-07-08 |
| DEC-0003 | Cue App — Scope Guardrails | 2026-07-08 |
| DEC-0004 | Cue App tech stack: React + Vite + TypeScript + Tailwind | 2026-07-09 |
| DEC-0005 | Cue guest counts model RSVPs, never tickets | 2026-07-09 |
| DEC-0006 | Cue event model is the container; ops reference eventId | 2026-07-09 |
| DEC-0007 | Cue Event Detail is the command center | 2026-07-09 |
| DOC-0007 | Cue App Project Charter | 2026-07-08 |
| DOC-0008 | Cue App — Product Vision | 2026-07-08 |
| DOC-0009 | Cue App — Target Users | 2026-07-08 |
| DOC-0010 | Cue App — Competitive Positioning | 2026-07-08 |
| DOC-0011 | Cue App — MVP Scope | 2026-07-08 |
| DOC-0013 | Cue App — Risk Register | 2026-07-08 |
| DOC-0014 | Cue App — Engineering Backlog | 2026-07-08 |
| SPR-0003 | Cue App — Sprint 1 Plan | 2026-07-08 |

(`DEC-0001`, `DOC-0001`–`0006`, `DOC-0012`, `SPR-0001`–`0002` were already tracked.)

## Reserved gaps

**None.** Every referenced record exists on disk as a genuine artifact, so no ID had to be reserved and no timestamps/provenance/decisions/approvals were invented.

## Counters corrected — to the true namespace maximum

| Prefix | Was | Now | Next ID | Basis |
|--------|-----|-----|---------|-------|
| DEC | 1 | **7** | DEC-0008 | max on disk is DEC-0007 (genuine) |
| DOC | 5 | **14** | DOC-0015 | max DOC-0014 |
| SPR | 2 | **3** | SPR-0004 | max SPR-0003 |
| RES | *(absent)* | **99** | RES-0100 | 99 research records existed with **no counter** — a second latent collision |
| PAT | 1 | 1 | PAT-0002 | already correct |

## Deviations from the brief (verified against disk, confirmed with the requester)

1. **DEC counter is 7, not 3.** The brief listed only DEC-0002–0003 for Cue, but **DEC-0004–0007 are genuine, source-referenced Cue decisions** (`projects/cue-app/README.md`, `src/lib/types.ts`, `src/pages/EventDetail.tsx`). Step 5's "at least DEC=3" is satisfied by 7; a literal DEC=3 would have overwritten DEC-0004–0007.
2. **Next decision is DEC-0008, not DEC-0004** (step 7). "next → DEC-0004" is impossible without destroying DEC-0004. Confirmed via the store: with DEC=7 the next `monday learn --type decision` mints **DEC-0008**.
3. **RES counter added (=99).** Surfaced by full-namespace validation (step 5); the brief did not mention it. Fixing it prevents the next research record from colliding at RES-0001.

## Scope notes

- Only the DEC/DOC/SPR Cue-range records and `.sequences.json` (+ this report) are committed. The 99 untracked `RES-*` files and other untracked working-tree work (`retention/`, caches) are **not** part of this repair; only the RES *counter* is corrected to close the collision risk. Committing the RES record files is a separate follow-up.
- No `TASK-0047` task record was created — this repair is scoped to the knowledge ledger, and the brief asked to "commit only this repair."
- `setup_workspace.py --force` was **not** run (known destructive: `overwrite=True`, re-mints IDs).

## Validation

- All **124 knowledge entries parse and load** via `KnowledgeStore`.
- Next IDs confirmed collision-free: DEC-0008, DOC-0015, SPR-0004, RES-0100, PAT-0002.
- Full MondayOS test suite and `monday doctor` — see PR description.
