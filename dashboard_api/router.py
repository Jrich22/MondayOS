"""
Pure request routing for the dashboard API.

`route()` maps (method, path, query, origin, body) → (status, headers, body).
It has no sockets and no I/O of its own, so the whole routing table — including
CORS decisions, input validation, structured errors, and secret redaction — is
unit-testable by calling this one function. `server.py` is a thin socket shell
around it.
"""
from __future__ import annotations

from typing import Any

from . import errors, security
from .service import DashboardService


def _match(parts: list[str], pattern: list[str]) -> dict[str, str] | None:
    """Match path segments against a pattern with {param} placeholders."""
    if len(parts) != len(pattern):
        return None
    params: dict[str, str] = {}
    for seg, pat in zip(parts, pattern):
        if pat.startswith("{") and pat.endswith("}"):
            params[pat[1:-1]] = seg
        elif seg != pat:
            return None
    return params


def route(
    service: DashboardService,
    method: str,
    path: str,
    query: dict[str, str] | None = None,
    origin: str | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, str], Any]:
    query = query or {}
    body = body or {}
    headers = security.cors_headers(origin)

    # CORS: a *present* Origin must be on the allowlist for any method.
    if not security.is_allowed_origin(origin):
        return 403, headers, errors.error(errors.FORBIDDEN_ORIGIN, "Origin not allowed.")

    if method == "OPTIONS":
        return 204, headers, None

    parts = [p for p in path.strip("/").split("/") if p != ""]

    try:
        status, payload = _dispatch(service, method, parts, query, body)
    except Exception as exc:  # noqa: BLE001 — the edge must never leak a raw crash
        # A single failing endpoint must not reset the connection (which the
        # client reads as a degraded link); return a redacted structured 500.
        status, payload = 500, errors.error(errors.UPSTREAM, security.redact_text(str(exc)) or "internal error")
    return status, headers, security.redact(payload)


def _dispatch(
    service: DashboardService,
    method: str,
    parts: list[str],
    query: dict[str, str],
    body: dict[str, Any],
) -> tuple[int, Any]:
    # ---- health / revision (probe + change poll) ----
    if method == "GET" and parts == ["health"]:
        return 200, {"ok": True, "mode": "live", "revision": service.revision}
    if method == "GET" and parts == ["revision"]:
        return 200, {"revision": service.revision}

    # ---- reads ----
    if method == "GET":
        if parts == ["status"]:
            return service.get_status()
        if parts == ["products"]:
            return service.list_products()
        if parts == ["agents"]:
            return service.list_agents()
        if parts == ["tasks"]:
            return service.list_tasks(query.get("status"), query.get("product"))
        if len(parts) == 2 and parts[0] == "tasks":
            return service.get_task(parts[1])
        if parts == ["agent-runs"]:
            return service.list_agent_runs(query.get("teamRunId"))
        if parts == ["team-runs"]:
            return service.list_team_runs(query.get("taskId"))
        if parts == ["approvals"]:
            return service.list_approvals()
        if parts == ["activity"]:
            return service.get_activity()
        if parts == ["knowledge", "search"]:
            return service.search_knowledge(query.get("query", query.get("q", "")))
        if parts == ["publish", "history"]:
            return service.publish_history()
        if parts == ["pull-requests"]:
            # MondayOS does not manage PRs (Phase 3). Honest empty list.
            return 200, []
        return 404, errors.error(errors.NOT_FOUND, "Unknown endpoint.")

    # ---- writes ----
    if method == "POST":
        if parts == ["tasks"]:
            return service.create_task(body)
        if (p := _match(parts, ["tasks", "{id}", "assign"])) is not None:
            return service.assign_task(p["id"], body)
        if (p := _match(parts, ["tasks", "{id}", "team-run"])) is not None:
            return service.run_team(p["id"], body)
        if (p := _match(parts, ["tasks", "{id}", "status"])) is not None:
            return service.set_task_status(p["id"], body)
        if (p := _match(parts, ["agent-runs", "{id}", "approve"])) is not None:
            return service.approve_run(p["id"], body)
        if (p := _match(parts, ["agent-runs", "{id}", "reject"])) is not None:
            return service.reject_run(p["id"], body)
        return 404, errors.error(errors.NOT_FOUND, "Unknown endpoint.")

    return 405, errors.error(errors.BAD_REQUEST, f"Method {method} not allowed.")
