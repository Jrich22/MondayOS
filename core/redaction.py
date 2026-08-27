"""
Secret redaction — the shared backstop that keeps credential values out of
anything MondayOS persists, logs, or returns.

This lives in ``core`` because more than one layer needs it and none of them
should own it: the dashboard API redacts responses and error bodies, and the
Growth publishing dispatcher redacts connector exceptions before they reach a
stored failure record. Duplicating the patterns would mean two places to update
when a new credential shape appears, and the second one is always the stale one.

Redaction is defence in depth, never the primary control. The primary control is
that credentials are referenced by name and resolved late, so a secret should
never reach these functions in the first place.
"""

from __future__ import annotations

import os
import re
from typing import Any

# Env vars whose *values* must never appear in output.
SECRET_ENV_KEYS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "CONFLUENCE_API_TOKEN",
    "CONFLUENCE_EMAIL",
    "CONFLUENCE_BASE_URL",
    "GITHUB_TOKEN",
    "GH_TOKEN",
)

# Token-shaped strings scrubbed even when they did not come from a known env var.
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
)

REDACTED = "***REDACTED***"

# A value shorter than this is too likely to be a common substring to blind-replace.
_MIN_SECRET_LENGTH = 6


def secret_values(extra_env_keys: tuple[str, ...] = ()) -> list[str]:
    """
    Current environment values that must be scrubbed.

    ``extra_env_keys`` lets a caller add names it knows about at runtime — the
    Growth dispatcher passes the secret names bound in the workspace it is
    publishing for, which this module cannot know statically.
    """
    values: list[str] = []
    for key in tuple(SECRET_ENV_KEYS) + tuple(extra_env_keys):
        value = os.environ.get(key)
        if value and len(value) >= _MIN_SECRET_LENGTH:
            values.append(value)
    return values


def redact_text(text: str, extra_env_keys: tuple[str, ...] = ()) -> str:
    """Replace known secret values and token-shaped substrings in ``text``."""
    for value in secret_values(extra_env_keys):
        if value in text:
            text = text.replace(value, REDACTED)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def redact(obj: Any, extra_env_keys: tuple[str, ...] = ()) -> Any:
    """Recursively redact secret-shaped strings from a JSON-able object."""
    if isinstance(obj, str):
        return redact_text(obj, extra_env_keys)
    if isinstance(obj, dict):
        return {k: redact(v, extra_env_keys) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v, extra_env_keys) for v in obj]
    return obj
