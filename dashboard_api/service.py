"""
DashboardService — the typed bridge between the dashboard and MondayOS.

Every method calls the existing `monday.Monday` public API (the system of
record) and reshapes the result via `serialize`. It adds only what the HTTP
edge needs and MondayOS doesn't already do:

  * input validation → structured 400s,
  * a duplicate-approval guard so a double approve/reject is idempotent
    (MondayOS's `review()` is not idempotent on its own),
  * a monotonic `revision` bumped on every successful write (drives the
    dashboard's cheap change-poll / SSE),
  * an append-only write log.

It never reimplements task/team/approval/knowledge logic, never performs a
gated action (commit/push/merge/deploy/secrets/trading are simply not routed
here), and never returns provider keys.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from monday import Monday

from . import errors, serialize

# (status_code, body). body is the serialized payload on success or an error
# envelope on failure.
Result = tuple[int, Any]

# Dashboard task-status → MondayOS task() action (low-risk transitions only).
_STATUS_ACTION = {
    "active": "start",
    "review": "review",
    "completed": "complete",
}


class DashboardService:
    def __init__(self, monday: Monday, *, provider: str = "fake", write_log: Path | None = None):
        self._m = monday
        self._provider = provider
        self._write_log = write_log
        self.revision = 0

    # ------------------------------------------------------------------ util
    def _bump(self) -> None:
        self.revision += 1

    def _log_write(self, op: str, args: dict[str, Any], ok: bool, message: str = "") -> None:
        if not self._write_log:
            return
        try:
            self._write_log.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "at": datetime.now(tz=timezone.utc).isoformat(),
                "op": op,
                "args": args,
                "ok": ok,
                "message": message,
                "revision": self.revision,
            }
            with self._write_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except OSError:
            pass  # logging must never break a request

    @property
    def provider(self) -> str:
        return self._provider

    def _model(self) -> str:
        # Model comes from config/provider, never a key. "fake" is deterministic.
        return "fake" if self._provider == "fake" else self._provider

    # ------------------------------------------------------------------ reads
    def get_status(self) -> Result:
        s = self._m.status()
        return 200, serialize.system_status(s, self._provider, self._model())

    def list_products(self) -> Result:
        resp = self._m.project("list")
        projects = resp.data.get("projects", []) if resp.success else []
        # Count active tasks once for a coarse open-task figure.
        tasks_resp = self._m.task("list_active")
        open_tasks = tasks_resp.data.get("count", 0) if tasks_resp.success else 0
        return 200, [serialize.product_from_project(p, open_tasks) for p in projects]

    def list_agents(self) -> Result:
        resp = self._m.agent("list")
        agents = resp.data.get("agents", []) if resp.success else []
        # Derive live activity from each role's most recent run.
        hist = self._m.agent("history", limit=100)
        runs = hist.data.get("runs", []) if hist.success else []
        latest: dict[str, dict[str, Any]] = {}
        for r in runs:  # history is newest-first
            latest.setdefault(r.get("role", ""), r)
        out = []
        for a in agents:
            role = a.get("role", "")
            r = latest.get(role)
            activity, task_id = self._activity_for(r)
            out.append(serialize.agent(a, activity, task_id))
        return 200, out

    @staticmethod
    def _activity_for(run: dict[str, Any] | None) -> tuple[str, str | None]:
        if not run:
            return "idle", None
        status = run.get("status", "")
        mapping = {
            "review": "awaiting",
            "awaiting-approval": "awaiting",
            "executed": "executing",
            "running": "executing",
            "blocked": "blocked",
            "failed": "blocked",
            "validation-failed": "blocked",
        }
        activity = mapping.get(status, "idle")
        task_id = None if activity == "idle" else (run.get("task_id") or None)
        return activity, task_id

    def list_tasks(self, status: str | None = None, product: str | None = None) -> Result:
        resp = self._m.task("list_active")
        if not resp.success:
            return 200, []
        rows = [serialize.task(t) for t in resp.data.get("tasks", [])]
        if status:
            rows = [t for t in rows if t["status"] == status]
        if product:
            rows = [t for t in rows if t.get("product") == product]
        return 200, rows

    def get_task(self, task_id: str) -> Result:
        resp = self._m.task("get", task_id=task_id)
        if not resp.success:
            return 404, errors.error(errors.NOT_FOUND, resp.message or f"No task {task_id}.")
        return 200, serialize.task(resp.data)

    def list_team_runs(self, task_id: str | None = None) -> Result:
        resp = self._m.team("history", task_id=task_id, limit=50)
        runs = resp.data.get("runs", []) if resp.success else []
        return 200, [serialize.team_run(r) for r in runs]

    def list_agent_runs(self, team_run_id: str | None = None) -> Result:
        resp = self._m.agent("history", limit=100)
        runs = resp.data.get("runs", []) if resp.success else []
        out = [serialize.agent_run(r) for r in runs]
        if team_run_id:
            out = [r for r in out if r["teamRunId"] == team_run_id]
        return 200, out

    def list_approvals(self) -> Result:
        resp = self._m.team("history", limit=50)
        runs = resp.data.get("runs", []) if resp.success else []
        # Surface awaiting-approval first, plus recently decided ones for context.
        interesting = [
            r for r in runs
            if r.get("status") in ("awaiting-approval", "completed", "changes-requested", "rejected")
        ]
        return 200, [serialize.approval_from_team_run(r) for r in interesting]

    def get_activity(self) -> Result:
        resp = self._m.agent("history", limit=25)
        runs = resp.data.get("runs", []) if resp.success else []
        return 200, [serialize.activity_event(r) for r in runs]

    def search_knowledge(self, query: str) -> Result:
        # SearchResponse always carries `results` (no `success` flag).
        resp = self._m.search(query or "", sources=["knowledge"], limit=25)
        return 200, [serialize.knowledge_item(r) for r in resp.results]

    def publish_history(self) -> Result:
        resp = self._m.publish("history", limit=25)
        hist = resp.data.get("history", []) if resp.success else []
        return 200, [serialize.publish_record(h) for h in hist]

    # ----------------------------------------------------------------- writes
    def create_task(self, body: dict[str, Any]) -> Result:
        title = (body.get("title") or "").strip()
        objective = (body.get("objective") or "").strip()
        if not title or not objective:
            return 400, errors.error(errors.BAD_REQUEST, "Both 'title' and 'objective' are required.")
        resp = self._m.task(
            "create",
            title=title,
            objective=objective,
            task_type=body.get("task_type", "feature"),
            priority=body.get("priority", "P2"),
        )
        self._log_write("create_task", {"title": title}, resp.success, resp.message)
        if not resp.success:
            return 400, errors.error(errors.BAD_REQUEST, resp.message)
        self._bump()
        return 201, serialize.task(resp.data)

    def assign_task(self, task_id: str, body: dict[str, Any]) -> Result:
        assignee = (body.get("assignee") or "").strip()
        if not assignee:
            return 400, errors.error(errors.BAD_REQUEST, "'assignee' is required (e.g. 'role:lead-engineer').")
        resp = self._m.task("assign", task_id=task_id, assignee=assignee)
        self._log_write("assign_task", {"task_id": task_id, "assignee": assignee}, resp.success, resp.message)
        if not resp.success:
            code = errors.NOT_FOUND if "no task" in resp.message.lower() else errors.INVALID_TRANSITION
            return (404 if code == errors.NOT_FOUND else 400), errors.error(code, resp.message)
        self._bump()
        return 200, serialize.task(resp.data)

    def run_team(self, task_id: str, body: dict[str, Any]) -> Result:
        mode = body.get("mode", "review")
        if mode not in ("review", "dry-run"):
            return 400, errors.error(errors.BAD_REQUEST, "mode must be 'review' or 'dry-run'.")
        resp = self._m.team("run", task_id=task_id, provider=self._provider, mode=mode)
        self._log_write("run_team", {"task_id": task_id, "mode": mode}, resp.success, resp.message)
        if not resp.success and not resp.team_run_id:
            code = errors.NOT_FOUND if "no task" in resp.message.lower() or "not found" in resp.message.lower() else errors.UPSTREAM
            return (404 if code == errors.NOT_FOUND else 400), errors.error(code, resp.message)
        self._bump()
        return 200, serialize.team_run(resp.data)

    def set_task_status(self, task_id: str, body: dict[str, Any]) -> Result:
        status = (body.get("status") or "").strip()
        action = _STATUS_ACTION.get(status)
        if not action:
            return 400, errors.error(
                errors.UNSUPPORTED,
                f"Status {status!r} is not settable here. Allowed: {sorted(_STATUS_ACTION)}.",
            )
        resp = self._m.task(action, task_id=task_id)
        self._log_write("set_task_status", {"task_id": task_id, "status": status}, resp.success, resp.message)
        if not resp.success:
            code = errors.NOT_FOUND if "no task" in resp.message.lower() else errors.INVALID_TRANSITION
            return (404 if code == errors.NOT_FOUND else 400), errors.error(code, resp.message)
        self._bump()
        return 200, serialize.task(resp.data)

    # --------------------------------------------------------------- approvals
    def _find_run(self, run_id: str) -> dict[str, Any] | None:
        hist = self._m.agent("history", limit=500)
        if not hist.success:
            return None
        for r in hist.data.get("runs", []):
            if r.get("run_id") == run_id:
                return r
        return None

    def _team_run_for_approval(self, run_id: str) -> dict[str, Any] | None:
        hist = self._m.team("history", limit=100)
        if not hist.success:
            return None
        for r in hist.data.get("runs", []):
            if r.get("approval_run_id") == run_id:
                return r
        return None

    def _decide(self, run_id: str, approve: bool, by: str, note: str) -> Result:
        run = self._find_run(run_id)
        if run is None:
            return 404, errors.error(errors.NOT_FOUND, f"No agent run {run_id}.")

        prior = (run.get("approval") or {}).get("decision")
        want = "approved" if approve else "rejected"
        # Only a recorded human decision ("approved"/"rejected") counts as
        # already-decided; "pending"/"not-required"/"" are pre-review states.
        if prior in ("approved", "rejected"):
            # Duplicate-approval guard — idempotent, never clobbers MondayOS.
            tr = self._team_run_for_approval(run_id)
            payload = serialize.approval_from_team_run(tr) if tr else {
                "id": run_id, "taskId": run.get("task_id", ""), "teamRunId": run.get("execution_id", ""),
                "summary": "", "status": prior, "verdicts": [], "affected": [],
            }
            if prior == want:
                payload["alreadyDecided"] = True
                self._log_write("decide", {"run_id": run_id, "approve": approve}, True, "idempotent (already decided)")
                return 200, payload
            return 409, errors.error(
                errors.ALREADY_DECIDED,
                f"Run {run_id} was already {prior}; cannot {want.rstrip('d')}.",
            )

        # Fresh decision → route through MondayOS's real review/approval logic.
        resp = self._m.agent("review", run_id=run_id, approve=approve, by=by, note=note)
        self._log_write("decide", {"run_id": run_id, "approve": approve, "by": by}, resp.success, resp.message)
        if not resp.success:
            return 400, errors.error(errors.UPSTREAM, resp.message)
        self._bump()
        tr = self._team_run_for_approval(run_id)
        if tr:
            return 200, serialize.approval_from_team_run(tr)
        return 200, {
            "id": run_id, "taskId": resp.task_id, "teamRunId": resp.data.get("execution_id", ""),
            "summary": resp.message, "status": want, "verdicts": [], "affected": [],
        }

    def approve_run(self, run_id: str, body: dict[str, Any]) -> Result:
        return self._decide(run_id, True, body.get("by", "human:dashboard"), body.get("note", ""))

    def reject_run(self, run_id: str, body: dict[str, Any]) -> Result:
        reason = (body.get("reason") or body.get("note") or "").strip()
        return self._decide(run_id, False, body.get("by", "human:dashboard"), reason)
