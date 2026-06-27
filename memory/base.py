"""Memory base types: MemoryRecord and the MemoryStore Protocol."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from core.types import Timestamp


class MemoryRecord:
    """
    A single value stored in memory, with provenance and versioning metadata.

    Every write creates or increments a MemoryRecord. Reads return the record,
    not just the value, so callers can inspect who wrote it, when, and why.
    """

    def __init__(
        self,
        key: str,
        value: Any,
        written_by: str,
        written_at: Timestamp,
        expires_at: Timestamp | None = None,
        version: int = 1,
        reason: str = "",
    ) -> None:
        self.key = key
        self.value = value
        self.written_by = written_by
        self.written_at = written_at
        self.expires_at = expires_at
        self.version = version
        self.reason = reason
        self._manually_expired = False

    def is_expired(self, now: Timestamp | None = None) -> bool:
        """True if the record has passed its expiry time or was manually expired."""
        if self._manually_expired:
            return True
        if self.expires_at is None:
            return False
        check_time = now or datetime.now(tz=timezone.utc)
        return check_time >= self.expires_at

    def mark_expired(self) -> None:
        """Mark this record as expired without deleting it."""
        self._manually_expired = True

    def __repr__(self) -> str:
        return (
            f"MemoryRecord(key={self.key!r}, version={self.version}, "
            f"written_by={self.written_by!r}, expired={self.is_expired()})"
        )


@runtime_checkable
class MemoryStore(Protocol):
    """
    Protocol (interface) for all memory tier implementations.

    Session, project, and agent memory all implement this interface.
    Callers accept MemoryStore and work with any tier uniformly.

    read() returns None for missing or expired keys — callers never raise
    on a cache miss; they handle the None case.
    """

    def read(self, key: str) -> MemoryRecord | None:
        """Return the record for key, or None if absent or expired."""
        ...

    def write(self, key: str, value: Any, written_by: str, reason: str = "") -> None:
        """Persist a value. Overwrites any existing value for the key."""
        ...

    def expire(self, key: str) -> None:
        """Mark a key expired. The record remains in storage but is hidden from reads."""
        ...

    def invalidate(self, key: str, reason: str) -> None:
        """Explicitly invalidate a record with a logged reason."""
        ...

    def keys(self) -> list[str]:
        """Return all non-expired keys."""
        ...
