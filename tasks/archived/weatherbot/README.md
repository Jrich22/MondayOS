# Archived — WeatherBot backlog

TASK-0001 … TASK-0018, archived 2026-08-13.

## Why these are here

These are backlog items for **WeatherBot**, a separate product that lives in its
own repository at `/Users/jrich/AI-Labs/WeatherBot`. They were created
2026-06-28 by `human:weatherbot-product-owner` and reference modules
(`cache.py`, `alerts.py`, the weather API client) that have never existed in
this repository.

They were sitting in `tasks/active/`, so MondayOS counted them as current
delivery risk: `monday doctor` reported "13 high-priority task(s) still in
BACKLOG" and `monday advise` recommended starting TASK-0001 and TASK-0002 ahead
of real Cue App and sourcingBOT work. An agent team reading repository health
saw an inactive external product's backlog as MondayOS's most urgent problem.

Archiving them removes that false signal without touching the work itself.

## What was NOT done

- **Nothing was deleted.** Every file is here, byte-for-byte.
- **No status was changed.** They remain `backlog`, with their full
  `status_history` intact. They were not cancelled — cancelling would assert a
  decision nobody made, and is terminal.
- **Their IDs are still reserved.** `TaskManager._highest_id_on_disk()` scans
  `tasks/` recursively, so archived IDs can never be reissued to a new task.

## Reactivating

Move the files back:

```bash
mv tasks/archived/weatherbot/TASK-00NN.md tasks/active/
```

They return exactly as they were — same status, same history, same priority.
Reactivate the whole product by moving all 18 back.

## Related

- `TASK-0019` (WeatherBot, completed) is in `tasks/completed/` and was left
  there — it is terminal and counts as history, not backlog.
- The duplicate `weatherbot` / `WeatherBot` entries in `config/projects.json`
  are separate registry cruft, not addressed here.
