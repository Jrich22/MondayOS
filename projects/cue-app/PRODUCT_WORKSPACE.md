# Cue App Product Workspace — Index

Cue App is an AI-native event-operations platform for VC firms, managed
entirely by MondayOS. Its complete product-management structure lives as
**structured knowledge and tasks** in the MondayOS stores, created via the
public API by [`setup_workspace.py`](setup_workspace.py). This file is a
generated index — the source of truth is the MondayOS knowledge base and
task system.

## Explore Cue App using MondayOS alone
```bash
monday search cue-app                     # everything about Cue App
monday ask "What is Cue App and who is it for?"
monday task list                          # the live backlog (epics + Sprint 1)
monday advise                             # what to work on next
```

## Knowledge entries
- Overview & current state — `DOC-0006`
- Project Charter (vision, goals, constraints, metrics) — `DOC-0007`
- Product Vision — `DOC-0008`
- Target Users — `DOC-0009`
- Competitive Positioning — `DOC-0010`
- MVP Scope — `DOC-0011`
- V1 Roadmap — `DOC-0012`
- Risk Register — `DOC-0013`
- Definition of Done — `DEC-0002`
- Scope Guardrails (decision) — `DEC-0003`
- Engineering Backlog map — `DOC-0014`
- Sprint 1 Plan — `SPR-0003`

## Epics
- `TASK-0020` [P1] [EPIC] Event Workspace
- `TASK-0021` [P1] [EPIC] Guest / Attendee Management
- `TASK-0022` [P1] [EPIC] AI Agenda Builder
- `TASK-0023` [P1] [EPIC] Invite & RSVP Flow
- `TASK-0024` [P2] [EPIC] Vendor / Venue Tracker
- `TASK-0025` [P2] [EPIC] Run-of-Show Builder
- `TASK-0026` [P1] [EPIC] Portfolio-Company Tagging
- `TASK-0027` [P2] [EPIC] Post-Event Recap & Insights
- `TASK-0028` [P2] [EPIC] Admin Dashboard
- `TASK-0029` [P1] [EPIC] AI Assistant Layer

## Sprint 1 — MVP foundation tasks
- `TASK-0030` Build the app shell (navigation + layout)
- `TASK-0031` Auth placeholder (no production auth)
- `TASK-0032` Event dashboard (list of events)
- `TASK-0033` Create-event flow
- `TASK-0034` Basic event detail page
- `TASK-0035` Guest list model
- `TASK-0036` AI assistant placeholder
- `TASK-0037` Seed demo data

## Implemented state (reconciled 2026-07-21 · TASK-0046)

Evidence: `projects/cue-app` source + tests, git history, and merged PRs #3–#7.

**Sprint 1 — MVP foundation — SHIPPED ✓** (all merged to `main` in `4560b42`)
- `TASK-0030` App shell — **completed** (was stranded in `review`; corrected)
- `TASK-0031` Auth placeholder — **completed** (was stranded in `review`; corrected)
- `TASK-0032` Event dashboard · `TASK-0033` Create-event flow ·
  `TASK-0034` Event detail · `TASK-0035` Guest list model ·
  `TASK-0036` AI assistant placeholder · `TASK-0037` Seed demo data — **completed**

**Sprint 2 — SHIPPED ✓** (beyond the original Sprint 1 plan)
- `TASK-0038` Multi-Organization Architecture — **completed** (`4560b42`)
- `TASK-0039` Organization & Relationship Intelligence Foundation — **completed**
- `TASK-0040` Roll Call Command Center — **completed** (PR #3)
- `TASK-0041` Event Lifecycle Workspace — **completed** (`4560b42`)
- `TASK-0042` Mission Control Dashboard — **completed** (PR #4)
- `TASK-0043` Communications Center — **completed** (PR #5)
- `TASK-0044` Relationship Intelligence — **completed** (PR #6)
- `TASK-0045` QR & Badge Check-In — **completed** (PR #7)

**Not yet built** (backlog epics): AI Agenda Builder (`TASK-0022`), Invite & RSVP
(`TASK-0023`), Vendor/Venue Tracker (`TASK-0024`), Run-of-Show (`TASK-0025`),
Post-Event Recap (`TASK-0027`).

See [`RECONCILIATION.md`](RECONCILIATION.md) for evidence, reused-ID canonical
mappings, and the next-increment analysis.

---

> ⚠️ **Regeneration caution:** the historical instruction below runs
> `setup_workspace.py --force`. Its overwrite behavior has **not** been verified
> against the now-reconciled ledger; running it could clobber corrected statuses
> and restored records. Inspect the script and prove it is non-destructive before
> using `--force`.

*Regenerate this workspace with* `python projects/cue-app/setup_workspace.py --force`.
