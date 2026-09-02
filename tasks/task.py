"""Task data model, enums, and status transition types."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.types import EntityId, Timestamp


class TaskType(Enum):
    FEATURE = "feature"
    FIX = "fix"
    REFACTOR = "refactor"
    DOCS = "docs"
    RESEARCH = "research"
    OPS = "ops"
    REVIEW = "review"


class TaskStatus(Enum):
    BACKLOG = "backlog"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in-progress"
    BLOCKED = "blocked"
    REVIEW = "review"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskPriority(Enum):
    P0 = "P0"  # system down / data loss — drop everything
    P1 = "P1"  # critical, blocking other work — address this session
    P2 = "P2"  # normal priority
    P3 = "P3"  # low / background


class ApprovalLevel(Enum):
    NONE = "none"                     # autonomous execution
    HUMAN_REVIEW = "human-review"     # human notified after completion
    HUMAN_APPROVAL = "human-approval" # human must approve before execution


# Valid status transitions as an adjacency map.
# A transition not in this map is illegal and will be rejected.
_VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.BACKLOG:      {TaskStatus.ASSIGNED, TaskStatus.CANCELLED},
    TaskStatus.ASSIGNED:     {TaskStatus.IN_PROGRESS, TaskStatus.BACKLOG, TaskStatus.CANCELLED},
    TaskStatus.IN_PROGRESS:  {TaskStatus.BLOCKED, TaskStatus.REVIEW, TaskStatus.COMPLETED, TaskStatus.CANCELLED},
    TaskStatus.BLOCKED:      {TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED},
    TaskStatus.REVIEW:       {TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED, TaskStatus.CANCELLED},
    TaskStatus.COMPLETED:    set(),   # terminal
    TaskStatus.CANCELLED:    set(),   # terminal
}


@dataclass
class StatusTransition:
    """Immutable record of a single task status change. Part of the audit trail."""

    from_status: TaskStatus | None  # None on initial creation
    to_status: TaskStatus
    changed_by: str                 # "human:{name}" or "agent:{id}"
    changed_at: Timestamp
    reason: str = ""


@dataclass
class Task:
    """
    A unit of work in MondayOS.

    Tasks are the atomic record of coordination. Every task preserves its
    full history via status_history. Tasks are never deleted — only archived.

    See docs/TASK_SYSTEM.md for the full schema and lifecycle documentation.
    """

    id: EntityId
    title: str
    task_type: TaskType
    status: TaskStatus
    priority: TaskPriority
    created: Timestamp
    updated: Timestamp
    created_by: str                            # "human:{name}" or "agent:{id}"
    objective: str
    context: str = ""
    # The project this task belongs to, as a registry slug. Empty means the task
    # predates explicit association (or is MondayOS-internal work): consumers
    # must treat "" as unknown rather than as "no project", because a legacy task
    # silently reclassified as unowned would disappear from its project's view.
    project: str = ""
    assigned_to: str | None = None
    parent_task_id: EntityId | None = None
    child_task_ids: list[EntityId] = field(default_factory=list)
    approval_required: ApprovalLevel = ApprovalLevel.HUMAN_REVIEW
    blocked_by: str | None = None              # human-readable blocker description
    knowledge_refs: list[EntityId] = field(default_factory=list)
    commit_refs: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    work_log: list[str] = field(default_factory=list)
    status_history: list[StatusTransition] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_terminal(self) -> bool:
        """True if the task has reached COMPLETED or CANCELLED."""
        return self.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)

    def is_blocked(self) -> bool:
        return self.status == TaskStatus.BLOCKED

    def can_transition_to(self, new_status: TaskStatus) -> bool:
        """
        Validate that moving from self.status → new_status is a legal transition.

        Uses the _VALID_TRANSITIONS graph defined in this module.
        """
        return new_status in _VALID_TRANSITIONS.get(self.status, set())
