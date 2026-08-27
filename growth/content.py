"""
The Content Item — the unit of approval — and its lifecycle state machine.

The lifecycle runs Draft through Published. Measured and Archived arrive with
measurement (increment 4) and do not exist yet, so an item cannot reach them.

There is exactly one way into Publishing, and it starts at Approved. Nothing skips
approval, and every edge into a publishing state is listed in _VALID_TRANSITIONS
below rather than being decided at a call site.

Approval is never a stored boolean. ``is_approved`` recomputes the fingerprint and
compares, so a hand-edited file cannot claim an approval it does not have.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from core.types import EntityId, Timestamp
from growth.errors import InvalidTransitionError
from growth.fingerprint import compute_fingerprint
from growth.publication import PublicationRecord


class ContentStatus(Enum):
    """Lifecycle states available in this increment. See docs/GROWTH_BOT.md."""

    DRAFT = "draft"
    AI_REVIEW = "ai-review"
    READY_FOR_REVIEW = "ready-for-review"
    CHANGES_REQUESTED = "changes-requested"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


# States from which an item may still be cancelled. Publishing is absent on
# purpose: the request is already with the platform and MondayOS cannot claim it
# did not land. Published is absent because retracting a live post is a deletion,
# which is a different operation with different authority.


# Valid transitions as an adjacency map, mirroring tasks/task.py. A transition not
# in this map is illegal and is rejected.
#
# APPROVED -> READY_FOR_REVIEW is the fingerprint-reset edge (ADR-013): the system
# takes it automatically when an approved item's approved fields change.
_VALID_TRANSITIONS: dict[ContentStatus, set[ContentStatus]] = {
    ContentStatus.DRAFT: {ContentStatus.AI_REVIEW, ContentStatus.CANCELLED},
    ContentStatus.AI_REVIEW: {
        ContentStatus.READY_FOR_REVIEW,
        ContentStatus.DRAFT,
        ContentStatus.CANCELLED,
    },
    ContentStatus.READY_FOR_REVIEW: {
        ContentStatus.APPROVED,
        ContentStatus.CHANGES_REQUESTED,
        ContentStatus.CANCELLED,
    },
    ContentStatus.CHANGES_REQUESTED: {ContentStatus.DRAFT, ContentStatus.CANCELLED},
    ContentStatus.APPROVED: {
        ContentStatus.SCHEDULED,
        ContentStatus.PUBLISHING,
        ContentStatus.READY_FOR_REVIEW,
        ContentStatus.CHANGES_REQUESTED,
        ContentStatus.CANCELLED,
    },
    ContentStatus.SCHEDULED: {
        ContentStatus.PUBLISHING,
        ContentStatus.READY_FOR_REVIEW,
        ContentStatus.CANCELLED,
    },
    # In flight. The only exits are the two the connector can produce; an operator
    # cannot cancel out of here because the platform may already have accepted it.
    ContentStatus.PUBLISHING: {ContentStatus.PUBLISHED, ContentStatus.FAILED},
    # Terminal until measurement lands (increment 4).
    ContentStatus.PUBLISHED: set(),
    ContentStatus.FAILED: {
        ContentStatus.PUBLISHING,
        ContentStatus.READY_FOR_REVIEW,
        ContentStatus.CANCELLED,
    },
    ContentStatus.CANCELLED: set(),
}

# Derived from the graph rather than restated, so the two can never disagree.
CANCELLABLE_STATES = frozenset(
    state for state, targets in _VALID_TRANSITIONS.items() if ContentStatus.CANCELLED in targets
)

# States in which an item is committed to publishing and must not be edited.
PUBLISHING_STATES: frozenset[ContentStatus] = frozenset(
    {ContentStatus.PUBLISHING, ContentStatus.PUBLISHED}
)

# Fields an item must carry before a human can be asked to review it. An item missing
# any of these is not reviewable, and an unreviewable item cannot be approved.
#
# `media` is absent by design: a text-only post is legitimate, and requiring media
# would block it.
REQUIRED_FOR_REVIEW: tuple[str, ...] = (
    "platform",
    "account",
    "copy",
    "cta",
    "destination_url",
    "campaign",
    "expected_goal",
    "expected_audience",
    "scheduled_at",
)


@dataclass
class ContentTransition:
    """Immutable record of one content status change. Part of the audit trail."""

    from_status: ContentStatus | None
    to_status: ContentStatus
    changed_by: str
    changed_at: Timestamp
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_status": self.from_status.value if self.from_status else None,
            "to_status": self.to_status.value,
            "changed_by": self.changed_by,
            "changed_at": _fmt_dt(self.changed_at),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContentTransition:
        raw_from = data.get("from_status")
        return cls(
            from_status=ContentStatus(raw_from) if raw_from else None,
            to_status=ContentStatus(str(data["to_status"])),
            changed_by=str(data.get("changed_by", "")),
            changed_at=_parse_dt(data.get("changed_at")),
            reason=str(data.get("reason", "")),
        )


@dataclass
class ContentItem:
    """
    One publishable artifact bound to one platform, one account, one time.

    The approved fields — project, platform, account, media, copy, cta,
    destination_url, scheduled_at — are the fingerprint (ADR-013). Everything else
    (notes, tags, warnings, campaign metadata) can change without disturbing an
    approval.
    """

    id: EntityId
    project: str
    platform: str = ""
    account: str = ""
    media: list[str] = field(default_factory=list)
    copy: str = ""
    cta: str = ""
    destination_url: str = ""
    scheduled_at: Timestamp | None = None
    # The zone the schedule was authored in, kept for display and audit. It is
    # NOT fingerprinted: approval binds the publication *instant*, which
    # scheduled_at already carries. Re-notating the same instant in another zone
    # publishes at the same moment, so it is not a material change; moving the
    # wall-clock time changes the instant and does reset approval.
    scheduled_timezone: str = ""

    campaign: str = ""
    expected_goal: str = ""
    expected_audience: str = ""

    status: ContentStatus = ContentStatus.DRAFT
    status_history: list[ContentTransition] = field(default_factory=list)

    approved_fingerprint: str = ""
    approved_by: str = ""
    approved_at: Timestamp | None = None

    publication: PublicationRecord | None = None

    warnings: list[str] = field(default_factory=list)
    notes: str = ""
    tags: list[str] = field(default_factory=list)

    created: Timestamp = field(default_factory=lambda: datetime.now(tz=UTC))
    updated: Timestamp = field(default_factory=lambda: datetime.now(tz=UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Approval
    # ------------------------------------------------------------------

    def current_fingerprint(self) -> str:
        """Fingerprint of this item's approved fields as they stand right now."""
        return compute_fingerprint(
            project=self.project,
            platform=self.platform,
            account=self.account,
            media=self.media,
            copy=self.copy,
            cta=self.cta,
            destination_url=self.destination_url,
            scheduled_at=self.scheduled_at,
        )

    def approval_covers_current_content(self) -> bool:
        """
        True when a recorded approval still matches this item's approved fields.

        Distinct from ``is_approved``, which additionally requires the item to be
        sitting in APPROVED. Once an item moves on to Scheduled, Publishing, or
        Failed, the human approval it carries is still the approval of exactly
        this content - so the publishing gate asks this question, and leaves
        "is this state admissible" to its own separate gate.
        """
        return (
            bool(self.approved_fingerprint)
            and self.approved_fingerprint == self.current_fingerprint()
        )

    @property
    def is_approved(self) -> bool:
        """
        True only while a human approval still covers this exact content.

        Computed, never stored: status alone is not evidence, because a file can be
        edited outside the API. Both the status and the fingerprint must agree.
        """
        return self.status is ContentStatus.APPROVED and self.approval_covers_current_content()

    def approval_is_stale(self) -> bool:
        """True when this item is marked Approved but its approved fields have changed."""
        return self.status is ContentStatus.APPROVED and not self.is_approved

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def can_transition_to(self, new_status: ContentStatus) -> bool:
        """Validate an edge against the _VALID_TRANSITIONS graph in this module."""
        return new_status in _VALID_TRANSITIONS.get(self.status, set())

    def is_terminal(self) -> bool:
        """True if the item can never move again."""
        return not _VALID_TRANSITIONS.get(self.status, set())

    def missing_required_fields(self) -> list[str]:
        """Names of REQUIRED_FOR_REVIEW fields this item has not filled in."""
        missing: list[str] = []
        for name in REQUIRED_FOR_REVIEW:
            value = getattr(self, name, None)
            if value is None or (isinstance(value, str) and not value.strip()):
                missing.append(name)
        return missing

    def apply_transition(
        self, new_status: ContentStatus, changed_by: str, reason: str = ""
    ) -> None:
        """Move to new_status, appending an audit record. Raises InvalidTransitionError."""
        if not self.can_transition_to(new_status):
            raise InvalidTransitionError(self.status.value, new_status.value)
        now = datetime.now(tz=UTC)
        self.status_history.append(
            ContentTransition(
                from_status=self.status,
                to_status=new_status,
                changed_by=changed_by,
                changed_at=now,
                reason=reason,
            )
        )
        self.status = new_status
        self.updated = now

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project": self.project,
            "platform": self.platform,
            "account": self.account,
            "media": list(self.media),
            "copy": self.copy,
            "cta": self.cta,
            "destination_url": self.destination_url,
            "scheduled_at": _fmt_dt(self.scheduled_at) if self.scheduled_at else "",
            "scheduled_timezone": self.scheduled_timezone,
            "campaign": self.campaign,
            "expected_goal": self.expected_goal,
            "expected_audience": self.expected_audience,
            "status": self.status.value,
            "status_history": [t.to_dict() for t in self.status_history],
            "approved_fingerprint": self.approved_fingerprint,
            "approved_by": self.approved_by,
            "approved_at": _fmt_dt(self.approved_at) if self.approved_at else "",
            "publication": self.publication.to_dict() if self.publication else None,
            "warnings": list(self.warnings),
            "notes": self.notes,
            "tags": list(self.tags),
            "created": _fmt_dt(self.created),
            "updated": _fmt_dt(self.updated),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContentItem:
        scheduled_raw = data.get("scheduled_at")
        approved_raw = data.get("approved_at")
        return cls(
            id=str(data["id"]),
            project=str(data.get("project", "")),
            platform=str(data.get("platform", "")),
            account=str(data.get("account", "")),
            media=[str(m) for m in (data.get("media") or [])],
            copy=str(data.get("copy", "")),
            cta=str(data.get("cta", "")),
            destination_url=str(data.get("destination_url", "")),
            scheduled_at=_parse_dt(scheduled_raw) if scheduled_raw else None,
            scheduled_timezone=str(data.get("scheduled_timezone", "")),
            campaign=str(data.get("campaign", "")),
            expected_goal=str(data.get("expected_goal", "")),
            expected_audience=str(data.get("expected_audience", "")),
            status=ContentStatus(str(data.get("status", ContentStatus.DRAFT.value))),
            status_history=[
                ContentTransition.from_dict(t) for t in (data.get("status_history") or [])
            ],
            approved_fingerprint=str(data.get("approved_fingerprint", "")),
            approved_by=str(data.get("approved_by", "")),
            approved_at=_parse_dt(approved_raw) if approved_raw else None,
            publication=(
                PublicationRecord.from_dict(data["publication"])
                if data.get("publication")
                else None
            ),
            warnings=[str(w) for w in (data.get("warnings") or [])],
            notes=str(data.get("notes", "")),
            tags=[str(t) for t in (data.get("tags") or [])],
            created=_parse_dt(data.get("created")),
            updated=_parse_dt(data.get("updated")),
            metadata=dict(data.get("metadata") or {}),
        )


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.rstrip("Z")).replace(tzinfo=UTC)
    return datetime.now(tz=UTC)


def _fmt_dt(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
