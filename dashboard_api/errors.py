"""Structured error codes + envelope for the dashboard API.

Every non-2xx response carries `{"error": {"code", "message"}}` — a stable,
typed shape the dashboard's `realAdapter` parses. Messages are human-readable
and safe (no stack traces, no secrets); codes are machine-stable.
"""
from __future__ import annotations

from typing import Any

# Stable machine codes.
BAD_REQUEST = "bad-request"
NOT_FOUND = "not-found"
INVALID_TRANSITION = "invalid-transition"
ALREADY_DECIDED = "already-decided"
FORBIDDEN_ORIGIN = "forbidden-origin"
GATED = "gated"
UPSTREAM = "upstream-error"
UNSUPPORTED = "unsupported"


def error(code: str, message: str) -> dict[str, Any]:
    """Build the error envelope body."""
    return {"error": {"code": code, "message": message}}
