"""Task manager — create, read, update, and archive tasks."""
from __future__ import annotations

from core.types import EntityId
from tasks.task import ApprovalLevel, Task, TaskPriority, TaskStatus, TaskType


class TaskManager:
    """
    The single interface through which all task mutations flow.

    TaskManager enforces status transition rules, writes task files,
    logs all changes, and emits events to the EventBus on every mutation.
    No code writes task files directly — it goes through TaskManager.

    Phase 1: File-based storage (one .md file per task in tasks/).
    Phase 2: SQLite backend behind the same interface.

    TODO: Accept EventBus as constructor argument for event emission.
    TODO: Auto-generate sequential task IDs (TASK-0001, TASK-0002, ...).
    TODO: Implement file read/write using KnowledgeParser-style frontmatter.
    TODO: Move completed/cancelled tasks to tasks/completed/ on archive().
    TODO: Rebuild tasks/active/index.md on every mutation.
    TODO: Publish TASK_CREATED / TASK_ASSIGNED / etc. to EventBus.
    """

    def create(
        self,
        title: str,
        task_type: TaskType,
        priority: TaskPriority,
        objective: str,
        created_by: str,
        approval_required: ApprovalLevel = ApprovalLevel.HUMAN_REVIEW,
        **kwargs: object,
    ) -> Task:
        """
        Create and persist a new task with status BACKLOG.

        Returns the created Task with its assigned ID.
        Raises TaskValidationError if required fields are empty or invalid.

        TODO: Generate ID, build Task dataclass, write to tasks/active/{id}.md.
        TODO: Publish TASK_CREATED event.
        """
        raise NotImplementedError("TODO: generate ID, create Task, write to tasks/active/")

    def get(self, task_id: EntityId) -> Task:
        """
        Retrieve a task by ID.

        Searches tasks/active/ first, then tasks/completed/.
        Raises TaskNotFoundError if the ID does not exist in either location.
        """
        raise NotImplementedError("TODO: load tasks/active/{id}.md or tasks/completed/{id}.md")

    def update_status(
        self,
        task_id: EntityId,
        new_status: TaskStatus,
        changed_by: str,
        reason: str = "",
    ) -> Task:
        """
        Transition a task to a new status.

        Validates the transition via Task.can_transition_to() before applying.
        Appends a StatusTransition to task.status_history.
        Archives the file if new_status is terminal (COMPLETED or CANCELLED).

        Raises InvalidTransitionError for illegal transitions.
        Raises TaskNotFoundError if the task does not exist.
        """
        raise NotImplementedError("TODO: load task, validate transition, update file, archive if terminal")

    def assign(self, task_id: EntityId, assignee: str, assigned_by: str) -> Task:
        """
        Assign a task to an agent or human. Transitions BACKLOG → ASSIGNED.

        `assignee` format: "human:{name}" or "agent:{agent-id}"
        """
        raise NotImplementedError

    def block(self, task_id: EntityId, reason: str, blocked_by: str) -> Task:
        """Mark IN_PROGRESS → BLOCKED with a descriptive reason and blocking party."""
        raise NotImplementedError

    def append_work_log(self, task_id: EntityId, entry: str, author: str) -> Task:
        """
        Append a dated work log entry to the task.

        Entry is formatted as: `### {date} — {author}\n{entry}`
        """
        raise NotImplementedError

    def list_active(
        self,
        status: TaskStatus | None = None,
        priority: TaskPriority | None = None,
        assigned_to: str | None = None,
        task_type: TaskType | None = None,
    ) -> list[Task]:
        """
        Return active tasks, optionally filtered.

        Reads from tasks/active/. Does not include completed or cancelled tasks.
        """
        raise NotImplementedError("TODO: scan tasks/active/, parse each file, apply filters")

    def archive(self, task_id: EntityId) -> None:
        """
        Move a terminal task file from tasks/active/ to tasks/completed/.

        Raises TaskNotTerminalError if the task is not COMPLETED or CANCELLED.
        Raises TaskNotFoundError if the task does not exist in tasks/active/.
        """
        raise NotImplementedError
