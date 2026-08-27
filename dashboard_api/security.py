"""
Safety controls for the local dashboard API.

The API is a privileged bridge to the MondayOS runtime, so it is locked down:
bound to localhost only, an explicit CORS allowlist for the dashboard origin,
and a redaction pass that guarantees no provider key / secret env value ever
leaves the process — even inside an error message. None of this trusts the
serializers; redaction is a defence-in-depth backstop.
"""
from __future__ import annotations

import os

from core import redaction as _core_redaction

# Bind address — localhost only by default. Never 0.0.0.0 unless explicitly
# overridden by an operator who understands the exposure.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787

# Redaction moved to core.redaction so the Growth publishing dispatcher can share
# it (one place to update when a new credential shape appears). Re-exported here
# because this module is the API edge's security surface and callers use
# `security.redact_text(...)` / `security.redact(...)`.
SECRET_ENV_KEYS = _core_redaction.SECRET_ENV_KEYS
redact_text = _core_redaction.redact_text
redact = _core_redaction.redact


def allowed_origins() -> set[str]:
    """The dashboard origins permitted by CORS. Override with DASHBOARD_ORIGIN
    (comma-separated). Defaults to the local Vite dev + preview ports."""
    env = os.environ.get("DASHBOARD_ORIGIN", "").strip()
    if env:
        return {o.strip() for o in env.split(",") if o.strip()}
    return {
        "http://localhost:5273",
        "http://127.0.0.1:5273",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    }


def is_allowed_origin(origin: str | None) -> bool:
    # No Origin header (curl, same-origin, server-to-server) is allowed; a
    # *present* Origin must be on the allowlist.
    return origin is None or origin in allowed_origins()


def cors_headers(origin: str | None) -> dict[str, str]:
    """CORS headers for an allowed origin (empty if not allowed / no origin)."""
    if origin and origin in allowed_origins():
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Max-Age": "600",
            "Vary": "Origin",
        }
    return {}
