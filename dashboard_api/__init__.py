"""
MondayOS dashboard API — a safe, localhost-only HTTP bridge that lets the
dashboard drive real MondayOS actions and approvals.

MondayOS stays the system of record and execution engine: this package only
wraps the `monday.Monday` public API, reshapes results into the dashboard's
typed contract, and enforces the HTTP-edge safety controls (localhost binding,
CORS allowlist, input validation, structured errors, secret redaction, a
duplicate-approval guard, and write logging). It never reimplements task/team/
approval logic and never routes a gated action.
"""
from .service import DashboardService
from .router import route
from .server import build_service, create_server, main

__all__ = ["DashboardService", "route", "build_service", "create_server", "main"]
