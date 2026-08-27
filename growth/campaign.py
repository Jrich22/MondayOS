"""
The Campaign - the planning object between a project and its content.

    Project -> Campaign -> Content Items -> Publication -> Performance

A campaign groups content under one objective, one audience, and one conversion
goal, which is what makes "did this work?" a question with an answer. Content
items reference a campaign by id; the campaign holds the reciprocal list.

A campaign belongs to exactly one workspace and cannot be addressed from another
(ADR-011). The workspace slug is stored on the campaign so a misfiled record is
detectable rather than silently adopted by whichever project reads it.

Campaign status is a state machine in the same shape as the content lifecycle:
an adjacency map, validated on every transition, with an audit record appended.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from core.types import EntityId, Timestamp
from growth.errors import InvalidTransitionError


class CampaignStatus(Enum):
    """Lifecycle states for a campaign."""

    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# A campaign that has run cannot go back to draft: content was published under it,
# and rewriting its premise afterwards would make every attached result unreadable.
_VALID_TRANSITIONS: dict[CampaignStatus, set[CampaignStatus]] = {
    CampaignStatus.DRAFT: {CampaignStatus.ACTIVE, CampaignStatus.CANCELLED},
    CampaignStatus.ACTIVE: {
        CampaignStatus.PAUSED,
        CampaignStatus.COMPLETED,
        CampaignStatus.CANCELLED,
    },
    CampaignStatus.PAUSED: {
        CampaignStatus.ACTIVE,
        CampaignStatus.COMPLETED,
        CampaignStatus.CANCELLED,
    },
    CampaignStatus.COMPLETED: set(),
    CampaignStatus.CANCELLED: set(),
}

# States in which a campaign may accept new content. A completed or cancelled
# campaign is a closed book; attaching content to it would silently change a
# result someone has already read.
OPEN_STATES: frozenset[CampaignStatus] = frozenset(
    {CampaignStatus.DRAFT, CampaignStatus.ACTIVE, CampaignStatus.PAUSED}
)


@dataclass
class CampaignTransition:
    """Immutable record of one campaign status change."""

    from_status: CampaignStatus | None
    to_status: CampaignStatus
    changed_by: str
    changed_at: Timestamp
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_status": self.from_status.value if self.from_status else None,
            "to_status": self.to_status.value,
            "changed_by": self.changed_by,
            "changed_at": _fmt(self.changed_at),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CampaignTransition:
        raw_from = data.get("from_status")
        return cls(
            from_status=CampaignStatus(raw_from) if raw_from else None,
            to_status=CampaignStatus(str(data["to_status"])),
            changed_by=str(data.get("changed_by", "")),
            changed_at=_parse(data.get("changed_at")),
            reason=str(data.get("reason", "")),
        )


@dataclass
class Campaign:
    """One campaign in one project's growth workspace."""

    id: EntityId
    project: str
    name: str = ""
    description: str = ""
    objective: str = ""
    target_audience: str = ""
    primary_conversion_goal: str = ""
    start_date: Timestamp | None = None
    end_date: Timestamp | None = None
    status: CampaignStatus = CampaignStatus.DRAFT
    theme: str = ""
    channels: list[str] = field(default_factory=list)
    cta: str = ""
    destination: str = ""
    kpis: list[str] = field(default_factory=list)
    content_item_ids: list[str] = field(default_factory=list)
    experiment_ids: list[str] = field(default_factory=list)
    status_history: list[CampaignTransition] = field(default_factory=list)
    created_at: Timestamp = field(default_factory=lambda: datetime.now(tz=UTC))
    updated_at: Timestamp = field(default_factory=lambda: datetime.now(tz=UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def can_transition_to(self, new_status: CampaignStatus) -> bool:
        """Validate an edge against the _VALID_TRANSITIONS graph in this module."""
        return new_status in _VALID_TRANSITIONS.get(self.status, set())

    def is_terminal(self) -> bool:
        """True when the campaign can never move again."""
        return not _VALID_TRANSITIONS.get(self.status, set())

    def accepts_content(self) -> bool:
        """True while content may still be attached to this campaign."""
        return self.status in OPEN_STATES

    def apply_transition(
        self, new_status: CampaignStatus, changed_by: str, reason: str = ""
    ) -> None:
        """Move to new_status with an audit record. Raises InvalidTransitionError."""
        if not self.can_transition_to(new_status):
            raise InvalidTransitionError(self.status.value, new_status.value)
        now = datetime.now(tz=UTC)
        self.status_history.append(
            CampaignTransition(
                from_status=self.status,
                to_status=new_status,
                changed_by=changed_by,
                changed_at=now,
                reason=reason,
            )
        )
        self.status = new_status
        self.updated_at = now

    def attach_content(self, content_id: str) -> None:
        """Record a content item against this campaign. Idempotent, order-stable."""
        if content_id and content_id not in self.content_item_ids:
            self.content_item_ids.append(content_id)
            self.content_item_ids.sort()
            self.updated_at = datetime.now(tz=UTC)

    def detach_content(self, content_id: str) -> None:
        """Remove a content item reference. Idempotent."""
        if content_id in self.content_item_ids:
            self.content_item_ids.remove(content_id)
            self.updated_at = datetime.now(tz=UTC)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project": self.project,
            "name": self.name,
            "description": self.description,
            "objective": self.objective,
            "target_audience": self.target_audience,
            "primary_conversion_goal": self.primary_conversion_goal,
            "start_date": _fmt(self.start_date) if self.start_date else "",
            "end_date": _fmt(self.end_date) if self.end_date else "",
            "status": self.status.value,
            "theme": self.theme,
            "channels": list(self.channels),
            "cta": self.cta,
            "destination": self.destination,
            "kpis": list(self.kpis),
            "content_item_ids": list(self.content_item_ids),
            "experiment_ids": list(self.experiment_ids),
            "status_history": [t.to_dict() for t in self.status_history],
            "created_at": _fmt(self.created_at),
            "updated_at": _fmt(self.updated_at),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Campaign:
        start = data.get("start_date")
        end = data.get("end_date")
        return cls(
            id=str(data["id"]),
            project=str(data.get("project", "")),
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            objective=str(data.get("objective", "")),
            target_audience=str(data.get("target_audience", "")),
            primary_conversion_goal=str(data.get("primary_conversion_goal", "")),
            start_date=_parse(start) if start else None,
            end_date=_parse(end) if end else None,
            status=CampaignStatus(str(data.get("status", CampaignStatus.DRAFT.value))),
            theme=str(data.get("theme", "")),
            channels=[str(c) for c in (data.get("channels") or [])],
            cta=str(data.get("cta", "")),
            destination=str(data.get("destination", "")),
            kpis=[str(k) for k in (data.get("kpis") or [])],
            content_item_ids=[str(c) for c in (data.get("content_item_ids") or [])],
            experiment_ids=[str(e) for e in (data.get("experiment_ids") or [])],
            status_history=[
                CampaignTransition.from_dict(t) for t in (data.get("status_history") or [])
            ],
            created_at=_parse(data.get("created_at")),
            updated_at=_parse(data.get("updated_at")),
            metadata=dict(data.get("metadata") or {}),
        )


def _parse(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.rstrip("Z")).replace(tzinfo=UTC)
    return datetime.now(tz=UTC)


def _fmt(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
