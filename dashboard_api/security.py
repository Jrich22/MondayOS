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
import re
from typing import Any

# Bind address — localhost only by default. Never 0.0.0.0 unless explicitly
# overridden by an operator who understands the exposure.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787

# Env vars whose *values* must never appear in a response.
SECRET_ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "CONFLUENCE_API_TOKEN",
    "CONFLUENCE_EMAIL",
    "CONFLUENCE_BASE_URL",
    "GITHUB_TOKEN",
    "GH_TOKEN",
)

# Token-shaped strings to scrub even if they didn't come from a known env var.
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
]

_REDACTED = "***REDACTED***"


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


def _secret_values() -> list[str]:
    vals = []
    for k in SECRET_ENV_KEYS:
        v = os.environ.get(k)
        if v and len(v) >= 6:
            vals.append(v)
    return vals


def redact_text(text: str) -> str:
    for v in _secret_values():
        if v in text:
            text = text.replace(v, _REDACTED)
    for pat in _SECRET_PATTERNS:
        text = pat.sub(_REDACTED, text)
    return text


def redact(obj: Any) -> Any:
    """Recursively redact any secret-shaped strings from a JSON-able object."""
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        return {k: redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


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
