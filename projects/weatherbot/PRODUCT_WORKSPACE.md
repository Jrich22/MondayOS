# WeatherBot Product Workspace — Index

WeatherBot is the first project managed entirely by MondayOS. Its complete
project-management structure lives as **structured knowledge and tasks** in the
MondayOS stores, created via the public API by
[`setup_workspace.py`](setup_workspace.py). This file is a generated index — the
source of truth is the MondayOS knowledge base and task system.

## Onboard to WeatherBot using MondayOS alone
```bash
monday search weatherbot                 # everything about WeatherBot
monday ask "What is WeatherBot and why does it exist?"
monday task list                          # the live engineering backlog
monday advise                             # what to work on next
monday onboard weatherbot                 # regenerate the onboarding report
```

## Knowledge entries
- Overview & current state — `DOC-0001`
- Project Charter (vision, goals, constraints, metrics) — `DOC-0002`
- Product Roadmap — `DOC-0003`
- Risk Register — `DOC-0004`
- Definition of Done — `DEC-0001`
- Engineering Backlog map — `DOC-0005`
- Sprint Zero — `SPR-0001`
- Implementation pattern (process demo) — `PAT-0001`

## Backlog — epics
- `TASK-0001` [P1] [EPIC] Quality & Test Coverage
- `TASK-0002` [P1] [EPIC] Reliability & Resilience
- `TASK-0003` [P2] [EPIC] Forecast & Alerting
- `TASK-0004` [P2] [EPIC] Observability & Operations
- `TASK-0005` [P2] [EPIC] Distribution & Packaging

Plus 13 features/tasks across the epics (see the Engineering Backlog knowledge entry for the full map with dependencies).

## Sprint Zero
Managed loop verified via workflow execution `06c063f3-1b49-44f9-ba18-2f93899bb443` (task `TASK-0019`).

*Regenerate this workspace with* `python projects/weatherbot/setup_workspace.py --force`.
