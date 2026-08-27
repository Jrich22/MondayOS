"""
Append-only audit trail for Growth publishing.

Every lifecycle transition and every connector attempt is written here before the
operation is reported as complete. The trail answers, for any published post,
who caused it, on behalf of which project, against which account, and what the
platform said back.

Records are append-only JSONL under ``logs/growth/<slug>/audit.jsonl``, matching
the treatment of ``logs/agents/`` and ``logs/publish/``: runtime state, not
version-controlled source. Partitioning by slug keeps one project's trail out of
another's file.

Every record passes through the shared redaction layer on the way in. That is a
backstop, not the control - a credential should never reach this module - but an
audit trail is exactly the file people paste into tickets, so it gets the belt
as well as the braces.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.redaction import redact

_AUDIT_FILENAME = "audit.jsonl"

# What happened. Kept as a small closed vocabulary so the trail is greppable.
EVENT_TRANSITION = "transition"
EVENT_ATTEMPT = "attempt"
EVENT_REFUSED = "refused"
EVENT_RECONCILED = "reconciled"
EVENT_PAUSE = "pause"


@dataclass
class AuditRecord:
    """One auditable thing that happened to one content item."""

    event: str
    project: str
    content_id: str
    actor: str
    at: str = ""
    platform: str = ""
    account_ref: str = ""
    old_status: str = ""
    new_status: str = ""
    reason: str = ""
    attempt: int = 0
    result: str = ""
    failure_code: str = ""
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "at": self.at or _now(),
            "project": self.project,
            "content_id": self.content_id,
            "actor": self.actor,
            "platform": self.platform,
            "account_ref": self.account_ref,
            "old_status": self.old_status,
            "new_status": self.new_status,
            "reason": self.reason,
            "attempt": self.attempt,
            "result": self.result,
            "failure_code": self.failure_code,
            "detail": self.detail,
            "extra": dict(self.extra),
        }


class AuditTrail:
    """Append-only audit log for one project's growth activity."""

    def __init__(
        self, project_root: Path, slug: str, extra_secret_env_keys: tuple[str, ...] = ()
    ) -> None:
        self._path = Path(project_root) / "logs" / "growth" / slug / _AUDIT_FILENAME
        self._slug = slug
        # Secret names bound in this workspace, so their values are scrubbed even
        # though core.redaction cannot know them statically.
        self._extra_keys = tuple(extra_secret_env_keys)

    @property
    def path(self) -> Path:
        return self._path

    def record(self, record: AuditRecord) -> dict[str, Any]:
        """Append one redacted record and return what was written."""
        redacted = redact(record.to_dict(), self._extra_keys)
        payload: dict[str, Any] = redacted if isinstance(redacted, dict) else record.to_dict()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
        return payload

    def read(self, content_id: str = "", limit: int = 0) -> list[dict[str, Any]]:
        """
        Records for this project, oldest first, optionally filtered by item.

        A malformed line is skipped rather than aborting the read: a corrupt tail
        must not make the rest of the trail unreadable.
        """
        if not self._path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
        if content_id:
            rows = [r for r in rows if r.get("content_id") == content_id]
        if limit:
            rows = rows[-limit:]
        return rows


def _now() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
