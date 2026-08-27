"""
The provider-neutral publishing interface.

A connector executes an already-approved instruction. It makes no content
decisions: it does not rewrite copy, choose an account, pick a time, or decide
whether publishing is permitted. Every one of those judgements happens upstream
in Growth review and in the dispatcher's gate sequence. A connector that needed
to reason would be an agent, and publishing is the one path in MondayOS that must
stay deterministic.

Nothing in this module imports the growth package: a connector receives a plain
:class:`PublishRequest` and knows nothing about workspaces, fingerprints, or
lifecycle states.

The failure taxonomy is the contract that drives retries. A connector must raise
:class:`TransientPublishError` only for conditions that a later identical attempt
could genuinely resolve, and :class:`PermanentPublishError` for everything else.
Misclassifying a permanent failure as transient is how a system ends up retrying
an invalid credential a hundred times.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# ---------------------------------------------------------------------------
# Failure codes
# ---------------------------------------------------------------------------

# Conditions where an identical later attempt may succeed.
TRANSIENT_CODES: tuple[str, ...] = (
    "timeout",
    "network",
    "rate_limited",
    "server_error",
    "unavailable",
)

# Conditions where retrying changes nothing. Retrying these burns quota, and for
# a rejected-content case it repeatedly re-submits material a platform already
# refused.
PERMANENT_CODES: tuple[str, ...] = (
    "invalid_credentials",
    "permission_denied",
    "media_rejected",
    "invalid_content",
    "account_mismatch",
    "policy_rejected",
    "unsupported_platform",
)


class PublishError(Exception):
    """Base class for connector failures."""

    code: str = "unknown"

    def __init__(self, message: str, code: str = "") -> None:
        self.code = code or self.code
        super().__init__(message)


class TransientPublishError(PublishError):
    """A failure a later identical attempt could resolve."""

    code = "unavailable"


class PermanentPublishError(PublishError):
    """A failure that retrying cannot resolve."""

    code = "invalid_content"


# ---------------------------------------------------------------------------
# Request / outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublishRequest:
    """
    One approved publication instruction.

    Frozen on purpose: a connector must not be able to alter what it was asked to
    publish. The payload here is the exact approved content — the dispatcher has
    already verified it against the approval fingerprint, and nothing downstream
    may normalize, trim, or repair it.

    Attributes:
        idempotency_key: Stable key derived upstream from the approved content
                         version. The connector uses it to recognise a request it
                         has already fulfilled.
        platform:        Target platform slug.
        account_id:      Platform account this publishes as.
        copy:            Post copy, verbatim.
        cta:             Call to action, verbatim.
        media:           Media references, order significant.
        destination_url: Destination URL, verbatim.
        scheduled_at:    Intended publication instant (UTC-aware).
        credential:      Resolved secret value. Never stored, never logged, never
                         echoed back in an outcome.
    """

    idempotency_key: str
    platform: str
    account_id: str
    copy: str = ""
    cta: str = ""
    media: tuple[str, ...] = ()
    destination_url: str = ""
    scheduled_at: datetime | None = None
    credential: str = ""

    def redacted(self) -> dict[str, Any]:
        """A representation safe to log or persist — the credential is omitted."""
        return {
            "idempotency_key": self.idempotency_key,
            "platform": self.platform,
            "account_id": self.account_id,
            "media_count": len(self.media),
            "destination_url": self.destination_url,
            "scheduled_at": self.scheduled_at.isoformat() if self.scheduled_at else "",
        }


@dataclass
class PublishOutcome:
    """
    What a connector reports back.

    ``already_published`` distinguishes a fresh publication from a reconciliation:
    the connector recognised the idempotency key and is reporting the existing
    post rather than creating a second one.
    """

    success: bool
    external_id: str = ""
    external_url: str = ""
    already_published: bool = False
    failure_code: str = ""
    failure_detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "external_id": self.external_id,
            "external_url": self.external_url,
            "already_published": self.already_published,
            "failure_code": self.failure_code,
            "failure_detail": self.failure_detail,
            "metadata": dict(self.metadata),
        }


# ---------------------------------------------------------------------------
# The interface
# ---------------------------------------------------------------------------


class PublishingConnector(ABC):
    """
    Minimal interface the Growth dispatcher depends on.

    Implementations are platform adapters. They must not perform retries or
    backoff of their own — retry policy is the dispatcher's, so that the attempt
    history, the bounded attempt count, and the audit trail all live in one place.
    """

    #: Platform slug this connector serves.
    platform: str = ""

    @abstractmethod
    def validate(self, request: PublishRequest) -> PublishOutcome:
        """
        Check a request against platform constraints without publishing.

        Returns an unsuccessful outcome carrying a permanent failure code when the
        content could never be accepted (over length, unsupported media). Never
        mutates the request to make it fit.
        """

    @abstractmethod
    def publish(self, request: PublishRequest) -> PublishOutcome:
        """
        Publish immediately, or reconcile if the idempotency key already landed.

        Raises TransientPublishError / PermanentPublishError for failures the
        connector cannot express as an outcome.
        """

    @abstractmethod
    def status(self, idempotency_key: str) -> PublishOutcome | None:
        """Return the outcome for a previously accepted key, or None if unknown."""

    def schedule(self, request: PublishRequest) -> PublishOutcome:
        """
        Hand a future-dated request to the platform's own scheduler.

        The default refuses: most platforms have no scheduling API, and silently
        publishing immediately when a caller asked to schedule would be the worst
        possible interpretation. MondayOS schedules locally instead — the
        dispatcher holds the item and publishes when the time arrives.
        """
        raise PermanentPublishError(
            f"{self.platform or type(self).__name__} does not support platform-side "
            "scheduling; MondayOS schedules locally and publishes at the due time.",
            code="unsupported_platform",
        )

    def cancel(self, idempotency_key: str) -> bool:
        """
        Cancel a platform-side scheduled publication if supported.

        Returns False by default. A connector with no scheduling API has nothing
        to cancel; cancelling a *locally* scheduled item never reaches a connector.
        """
        return False
