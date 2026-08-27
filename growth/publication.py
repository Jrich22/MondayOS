"""
Publication records, attempt history, and the idempotency key.

A publication record is the durable outcome of publishing one content item. It
lives on the item itself rather than in a side table so that status and evidence
move together: an item marked Published always carries the external id that
proves it, even in a fresh clone.

The idempotency key is derived from the *approved* content version and never from
the clock. Two consequences follow, and both are the point. A retry of the same
approved content reuses the key, so the connector recognises it and reconciles
instead of posting twice. A re-approval after an edit produces a different
fingerprint and therefore a different key, so genuinely new content is genuinely
new to the platform.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from integrations.publishing.connector import PERMANENT_CODES, TRANSIENT_CODES

# Bounded retries. Five attempts over a capped exponential curve is enough to ride
# out a rate limit or a brief outage; beyond that a human should look, because
# something that fails five times is usually not transient after all.
MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 60.0
BACKOFF_CAP_SECONDS = 3600.0
BACKOFF_JITTER_FRACTION = 0.2

# Dispatcher-side refusal codes. These never reach a connector - the gate sequence
# stops first - but they are recorded with the same vocabulary so one failure_code
# field explains every unsuccessful attempt.
GATE_FAILURE_CODES: tuple[str, ...] = (
    "emergency_stop",
    "paused",
    "not_approved",
    "fingerprint_mismatch",
    "account_mismatch",
    "missing_account",
    "not_due",
    "invalid_state",
)


def is_transient(code: str) -> bool:
    """True only for codes the taxonomy marks as worth retrying."""
    return code in TRANSIENT_CODES


def is_permanent(code: str) -> bool:
    """True for connector-permanent codes and every dispatcher gate refusal."""
    return code in PERMANENT_CODES or code in GATE_FAILURE_CODES


def idempotency_key(project: str, content_id: str, approved_fingerprint: str) -> str:
    """
    Stable publication key for one approved version of one content item.

    Deliberately clock-free: including a timestamp would make every retry look
    like a new publication and duplicate the post.
    """
    material = " ".join((project, content_id, approved_fingerprint))
    return f"idem:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:32]}"


def backoff_seconds(attempt: int, jitter: float = 0.0) -> float:
    """
    Delay before attempt ``attempt`` (1-based), exponential and capped.

    ``jitter`` is a caller-supplied fraction in [-1, 1] scaled by
    BACKOFF_JITTER_FRACTION, so the spread is injectable and tests stay
    deterministic rather than depending on a random source.
    """
    if attempt < 1:
        attempt = 1
    raw = BACKOFF_BASE_SECONDS * float(2 ** (attempt - 1))
    capped: float = min(raw, BACKOFF_CAP_SECONDS)
    clamped = max(-1.0, min(1.0, jitter))
    return max(0.0, capped * (1.0 + BACKOFF_JITTER_FRACTION * clamped))


@dataclass
class PublicationAttempt:
    """One recorded attempt to publish, successful or not."""

    attempt: int
    attempted_at: datetime
    success: bool = False
    failure_code: str = ""
    failure_detail: str = ""
    transient: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "attempted_at": _fmt(self.attempted_at),
            "success": self.success,
            "failure_code": self.failure_code,
            "failure_detail": self.failure_detail,
            "transient": self.transient,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PublicationAttempt:
        return cls(
            attempt=int(data.get("attempt", 0)),
            attempted_at=_parse(data.get("attempted_at")),
            success=bool(data.get("success", False)),
            failure_code=str(data.get("failure_code", "")),
            failure_detail=str(data.get("failure_detail", "")),
            transient=bool(data.get("transient", False)),
        )


@dataclass
class PublicationRecord:
    """
    The durable publishing outcome for one content item.

    Never holds a credential. ``failure_detail`` is redacted before it is set, so
    a connector exception carrying a token cannot land here.
    """

    idempotency_key: str = ""
    platform: str = ""
    account_ref: str = ""
    external_id: str = ""
    external_url: str = ""
    attempted_at: datetime | None = None
    published_at: datetime | None = None
    failure_code: str = ""
    failure_detail: str = ""
    retry_count: int = 0
    next_attempt_at: datetime | None = None
    reconciled: bool = False
    attempts: list[PublicationAttempt] = field(default_factory=list)
    connector_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def exhausted(self) -> bool:
        """True when the bounded retry budget is spent."""
        return self.retry_count >= MAX_ATTEMPTS

    def to_dict(self) -> dict[str, Any]:
        return {
            "idempotency_key": self.idempotency_key,
            "platform": self.platform,
            "account_ref": self.account_ref,
            "external_id": self.external_id,
            "external_url": self.external_url,
            "attempted_at": _fmt(self.attempted_at) if self.attempted_at else "",
            "published_at": _fmt(self.published_at) if self.published_at else "",
            "failure_code": self.failure_code,
            "failure_detail": self.failure_detail,
            "retry_count": self.retry_count,
            "next_attempt_at": _fmt(self.next_attempt_at) if self.next_attempt_at else "",
            "reconciled": self.reconciled,
            "attempts": [a.to_dict() for a in self.attempts],
            "connector_metadata": dict(self.connector_metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PublicationRecord:
        return cls(
            idempotency_key=str(data.get("idempotency_key", "")),
            platform=str(data.get("platform", "")),
            account_ref=str(data.get("account_ref", "")),
            external_id=str(data.get("external_id", "")),
            external_url=str(data.get("external_url", "")),
            attempted_at=_parse(data["attempted_at"]) if data.get("attempted_at") else None,
            published_at=_parse(data["published_at"]) if data.get("published_at") else None,
            failure_code=str(data.get("failure_code", "")),
            failure_detail=str(data.get("failure_detail", "")),
            retry_count=int(data.get("retry_count", 0)),
            next_attempt_at=(
                _parse(data["next_attempt_at"]) if data.get("next_attempt_at") else None
            ),
            reconciled=bool(data.get("reconciled", False)),
            attempts=[PublicationAttempt.from_dict(a) for a in (data.get("attempts") or [])],
            connector_metadata=dict(data.get("connector_metadata") or {}),
        )


def _parse(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.rstrip("Z")).replace(tzinfo=UTC)
    return datetime.now(tz=UTC)


def _fmt(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
