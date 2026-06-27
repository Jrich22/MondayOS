"""Volatile in-process session memory."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.types import Timestamp
from memory.base import MemoryRecord


class SessionMemory:
    """
    Volatile, in-process memory for a single agent session.

    Session memory is the agent's working context during one execution run.
    It is cleared when the session ends and is never shared across agents.

    This is the only memory tier with a complete implementation in Phase 1
    because it requires no I/O — it lives entirely in process.

    TODO: Add optional debug persistence to memory/session/{session-id}.json.
    TODO: Add checkpoint() / restore() for session continuity on crash.
    TODO: Add max_size eviction (LRU) to prevent unbounded in-process growth.
    TODO: Publish MEMORY_WRITTEN / MEMORY_READ / MEMORY_EXPIRED to EventBus.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._store: dict[str, MemoryRecord] = {}

    # ------------------------------------------------------------------
    # MemoryStore protocol implementation
    # ------------------------------------------------------------------

    def read(self, key: str) -> MemoryRecord | None:
        """Return the record for key, or None if absent or expired."""
        record = self._store.get(key)
        if record is None:
            return None
        if record.is_expired(self._now()):
            return None
        return record

    def write(self, key: str, value: Any, written_by: str, reason: str = "") -> None:
        """Store value under key, incrementing version on overwrite."""
        existing = self._store.get(key)
        version = (existing.version + 1) if existing else 1
        self._store[key] = MemoryRecord(
            key=key,
            value=value,
            written_by=written_by,
            written_at=self._now(),
            version=version,
            reason=reason,
        )

    def expire(self, key: str) -> None:
        """Mark key as expired. Record remains in _store but read() returns None."""
        record = self._store.get(key)
        if record is not None:
            record.mark_expired()

    def invalidate(self, key: str, reason: str) -> None:
        """Invalidate a key with a reason. In session memory, same as expire()."""
        self.expire(key)
        # TODO: log invalidation reason to structured log

    def keys(self) -> list[str]:
        """Return all non-expired keys."""
        now = self._now()
        return [k for k, v in self._store.items() if not v.is_expired(now)]

    # ------------------------------------------------------------------
    # Session-specific operations
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Remove all entries. Called at session end."""
        self._store.clear()

    def snapshot(self) -> dict[str, Any]:
        """Return a plain-dict snapshot of all non-expired values (for debug)."""
        now = self._now()
        return {k: v.value for k, v in self._store.items() if not v.is_expired(now)}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _now(self) -> Timestamp:
        return datetime.now(tz=timezone.utc)
