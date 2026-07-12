"""
Map MondayOS runtime objects → the dashboard's typed JSON shapes.

The dashboard's TypeScript adapter (`dashboard/src/adapter/types.ts`) is the
contract. These pure functions translate the plain dicts returned by the
`monday` public API into exactly those shapes, so `realAdapter` can consume them
with no client-side massaging. Nothing here calls MondayOS or holds state — it
only reshapes dicts, and it only ever emits whitelisted fields (a structural
guard against leaking internal/secret data).
"""
from __future__ import annotations

from typing import Any

# ---- status ---------------------------------------------------------------


def system_status(status: Any, provider: str, model: str) -> dict[str, Any]:
    return {
        "version": getattr(status, "version", ""),
        "healthy": bool(getattr(status, "healthy", False)),
        "sessionId": getattr(status, "session_id", ""),
        "uptimeSeconds": float(getattr(status, "uptime_seconds", 0.0)),
        "provider": provider,
        "model": model,
    }


# ---- products (MondayOS projects) -----------------------------------------


def product_from_project(entry: dict[str, Any], open_tasks: int = 0) -> dict[str, Any]:
    return {
        "key": entry.get("name", ""),
        "name": entry.get("name", "").replace("-", " ").title() or entry.get("name", ""),
        "summary": entry.get("description", "") or entry.get("source_path", ""),
        "status": "operational",
        "openTasks": open_tasks,
    }


# ---- tasks ----------------------------------------------------------------

_TASK_STATUS = {
    "backlog": "active",
    "assigned": "active",
    "in-progress": "active",
    "blocked": "blocked",
    "review": "review",
    "completed": "completed",
    "cancelled": "completed",
}


def task_status(internal: str) -> str:
    return _TASK_STATUS.get(internal, "active")


def task(t: dict[str, Any]) -> dict[str, Any]:
    assigned = t.get("assigned_to")
    return {
        "id": t.get("id", ""),
        "title": t.get("title", ""),
        "status": task_status(t.get("status", "")),
        "objective": t.get("objective", ""),
        "agent": assigned if assigned else None,
        # MondayOS tasks aren't linked to a product in the public shape.
        "product": t.get("product"),
    }


# ---- runs -----------------------------------------------------------------

_RUN_STATUS = {
    "completed": "completed",
    "review": "awaiting",
    "awaiting-approval": "awaiting",
    "blocked": "blocked",
    "failed": "blocked",
    "validation-failed": "blocked",
    "changes-requested": "blocked",
    "rejected": "blocked",
    "dry-run": "completed",
    "running": "running",
    "executed": "completed",
    "skipped": "pending",
    "unavailable": "blocked",
}

_VERDICT = {"pass": "pass", "needs_changes": "concerns", "block": "fail"}

_ROLE_TITLE = {
    "cpo": "CPO",
    "lead-engineer": "Lead Engineer",
    "qa": "QA",
    "security": "Security",
    "reviewer": "Reviewer",
}


def run_status(internal: str) -> str:
    return _RUN_STATUS.get(internal, "pending")


def role_title(role: str) -> str:
    return _ROLE_TITLE.get(role, role.replace("-", " ").title())


def agent_run_from_stage(stage: dict[str, Any], team_run_id: str) -> dict[str, Any]:
    verdict_raw = stage.get("verdict", "")
    return {
        "id": stage.get("run_id", ""),
        "teamRunId": team_run_id,
        "stage": role_title(stage.get("role", "")),
        "agent": stage.get("agent_name", "") or role_title(stage.get("role", "")),
        "status": run_status(stage.get("status", "")),
        "provider": stage.get("provider_used", "") or None,
        "model": stage.get("provider_model", "") or None,
        "summary": stage.get("summary", "") or None,
        "verdict": _VERDICT.get(verdict_raw) if verdict_raw else None,
        "elapsedMs": None,
    }


def team_run(tr: dict[str, Any]) -> dict[str, Any]:
    trid = tr.get("team_run_id", "")
    stages = [agent_run_from_stage(s, trid) for s in tr.get("stages", [])]
    return {
        "id": trid,
        "taskId": tr.get("task_id", ""),
        "mode": tr.get("mode", "review"),
        "status": run_status(tr.get("status", "")),
        "startedAt": tr.get("created_at", ""),
        "stages": stages,
    }


def agent_run(r: dict[str, Any]) -> dict[str, Any]:
    verdict = (r.get("verdict") or {}).get("verdict") if isinstance(r.get("verdict"), dict) else None
    return {
        "id": r.get("run_id", ""),
        "teamRunId": r.get("execution_id", "") or "",
        "stage": role_title(r.get("role", "")),
        "agent": r.get("agent_name", "") or role_title(r.get("role", "")),
        "status": run_status(r.get("status", "")),
        "provider": r.get("provider_used", "") or None,
        "model": r.get("provider_model", "") or None,
        "summary": ((r.get("verdict") or {}).get("summary") if isinstance(r.get("verdict"), dict) else None) or r.get("message", "") or None,
        "verdict": _VERDICT.get(verdict) if verdict else None,
        "elapsedMs": r.get("duration_ms"),
    }


# ---- approvals (derived from team runs) -----------------------------------

_BLOCKING = {"qa", "security", "reviewer"}
_APPROVAL_STATUS = {
    "awaiting-approval": "open",
    "completed": "approved",
    "changes-requested": "rejected",
    "rejected": "rejected",
}


def approval_from_team_run(tr: dict[str, Any], task_title: str = "") -> dict[str, Any]:
    trid = tr.get("team_run_id", "")
    verdicts = [
        {
            "role": role_title(s.get("role", "")),
            "verdict": _VERDICT.get(s.get("verdict", ""), "concerns"),
            "note": s.get("summary", "") or None,
        }
        for s in tr.get("stages", [])
        if s.get("role") in _BLOCKING
    ]
    return {
        "id": tr.get("approval_run_id", "") or trid,
        "taskId": tr.get("task_id", ""),
        "teamRunId": trid,
        "summary": tr.get("message", "") or (f"Team run for {task_title}" if task_title else "Team run awaiting review"),
        "status": _APPROVAL_STATUS.get(tr.get("status", ""), "open"),
        "verdicts": verdicts,
        "affected": [],
    }


# ---- knowledge ------------------------------------------------------------

_KNOWLEDGE_KIND = {
    "decision": "decision",
    "adr": "decision",
    "pattern": "doc",
    "runbook": "doc",
    "bug": "doc",
    "research": "research",
    "sprint": "sprint",
}


def knowledge_item(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r.get("id", ""),
        "kind": _KNOWLEDGE_KIND.get(r.get("entry_type", ""), "doc"),
        "title": r.get("title", ""),
        "summary": r.get("summary", "") or None,
        "product": None,
    }


# ---- activity (derived from recent agent runs) ----------------------------

_ACTIVITY_KIND = {
    "completed": "completed",
    "review": "awaiting",
    "awaiting-approval": "awaiting",
    "blocked": "blocked",
    "failed": "blocked",
    "validation-failed": "blocked",
    "executed": "executing",
    "running": "executing",
    "skipped": "idle",
    "dry-run": "completed",
}


def activity_event(r: dict[str, Any]) -> dict[str, Any]:
    role = r.get("role", "")
    status = r.get("status", "")
    return {
        "id": r.get("run_id", ""),
        "at": r.get("created_at", ""),
        "agent": role_title(role),
        "message": f"{role_title(role)} {status.replace('-', ' ')} on {r.get('task_id', '')}",
        "kind": _ACTIVITY_KIND.get(status, "executing"),
    }


# ---- publish history ------------------------------------------------------


def publish_record(h: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": h.get("id", "") or h.get("page_id", "") or h.get("doc_id", ""),
        "docId": h.get("doc_id", ""),
        "target": h.get("space_key", "") or h.get("url", "") or "Confluence",
        "at": h.get("at", "") or h.get("published_at", ""),
        "status": h.get("status", "") or h.get("action", "published"),
    }


# ---- agents ---------------------------------------------------------------


def agent(a: dict[str, Any], activity: str, task_id: str | None) -> dict[str, Any]:
    return {
        "id": a.get("id", "") or a.get("role", ""),
        "name": a.get("name", "") or role_title(a.get("role", "")),
        "role": a.get("description", "") or role_title(a.get("role", "")),
        "activity": activity,
        "task": task_id,
    }
