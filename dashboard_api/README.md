# MondayOS Dashboard API

A **safe, localhost-only HTTP bridge** that lets the Mission Control dashboard
drive real MondayOS actions and approvals. MondayOS stays the system of record
and execution engine — this package only wraps the `monday.Monday` public API,
reshapes results into the dashboard's typed contract, and enforces the
HTTP-edge safety controls. It never reimplements task/team/approval logic and
never routes a gated action.

## Run

```bash
# From the repo root (so MondayOS packages import):
python -m dashboard_api
# → http://127.0.0.1:8787  (provider=fake, deterministic + offline)
```

Environment:

| Var | Default | Meaning |
| --- | --- | --- |
| `MONDAYOS_API_HOST` | `127.0.0.1` | Bind address — **localhost only** unless deliberately overridden |
| `MONDAYOS_API_PORT` | `8787` | Port |
| `MONDAYOS_ROOT` | `.` | Workspace root (tasks, logs, knowledge live here) |
| `MONDAYOS_DASHBOARD_PROVIDER` | `fake` | Team/agent provider (`fake` needs no API keys) |
| `DASHBOARD_ORIGIN` | `localhost:5273,4173` | Comma-separated CORS allowlist |

Then run the dashboard in LIVE mode by setting `VITE_MONDAYOS_API` (see
`dashboard/.env.example`). With no API reachable, the dashboard stays on the
offline demo adapter.

## Endpoints

**Reads:** `GET /health` · `/revision` · `/status` · `/products` · `/agents` ·
`/tasks` · `/tasks/{id}` · `/agent-runs` · `/team-runs` · `/approvals` ·
`/activity` · `/knowledge/search?query=` · `/publish/history` ·
`/pull-requests` (empty — MondayOS doesn't manage PRs).

**Writes:** `POST /tasks` · `/tasks/{id}/assign` · `/tasks/{id}/team-run` ·
`/tasks/{id}/status` · `/agent-runs/{id}/approve` · `/agent-runs/{id}/reject`.

**Streaming:** `GET /events` (Server-Sent Events: heartbeat + `revision` change
signal; the client refetches on change and polls `/revision` as a fallback).

Gated actions (commit / push / merge / deploy / secrets / live trading) are
**not routed** here in Phase 2.

## Safety controls

- **Localhost bind** by default (`127.0.0.1`); never `0.0.0.0` implicitly.
- **CORS allowlist** — a present `Origin` must be on the allowlist or the
  request is refused (`403 forbidden-origin`).
- **Input validation** → structured `400`s.
- **Structured errors** — every non-2xx is `{"error":{"code","message"}}`; no
  stack traces cross the boundary (a handler crash becomes a redacted `500`).
- **Secret redaction** — provider keys / token-shaped strings are scrubbed from
  every response, defence-in-depth on top of whitelisted serialization.
- **ApprovalGate preserved** — approvals go through `monday.agent("review", …)`;
  the API adds only a duplicate-approval guard (idempotent re-approve, `409` on
  a conflicting decision).
- **Write logging** — every write appends to `logs/dashboard_api.jsonl`.

## Layout

```
service.py     # DashboardService — wraps Monday, validation, dup-guard, revision
serialize.py   # MondayOS dicts → dashboard TS shapes (whitelisted fields only)
router.py      # pure (method, path, query, origin, body) → (status, headers, body)
server.py      # stdlib ThreadingHTTPServer shell + SSE; python -m dashboard_api
security.py    # CORS allowlist, secret redaction, bind defaults
errors.py      # structured error codes + envelope
```

Tested by `tests/test_dashboard_api.py` (route/service + live-socket + full
create→run→approve end-to-end, all with `provider=fake`).
