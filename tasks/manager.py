"""Task manager — create, read, update, and archive tasks."""

from __future__ import annotations

import warnings
from datetime import UTC, datetime
from pathlib import Path

from core.atomic import write_atomic
from core.sequence import IdentityPolicy, Namespace, SequenceAllocator
from core.types import EntityId
from tasks.errors import InvalidTransitionError, TaskNotFoundError, TaskValidationError
from tasks.parser import TaskParser
from tasks.task import ApprovalLevel, StatusTransition, Task, TaskPriority, TaskStatus, TaskType

_TASK_PREFIX = "TASK"
_SEQUENCES_FILENAME = ".sequences.json"

# Files to skip when scanning for task entries
_SKIP_NAMES = frozenset({"index.md", "README.md"})


class TaskManager:
    """
    The single interface through which all task mutations flow.

    Phase 1 uses a Markdown-on-disk backend:
        tasks/active/{TASK-ID}.md    — in-flight tasks
        tasks/completed/{TASK-ID}.md — terminal tasks (COMPLETED or CANCELLED)

    All status transitions are validated against the _VALID_TRANSITIONS graph
    before being applied. Every mutation appends a StatusTransition to the
    task's status_history and rewrites the file atomically.

    Sequence numbers are tracked in tasks/.sequences.json to survive restarts.
    """

    def __init__(self, project_root: Path = Path(".")) -> None:
        self._tasks_dir = project_root / "tasks"
        self._active_dir = self._tasks_dir / "active"
        self._completed_dir = self._tasks_dir / "completed"
        self._sequences_path = self._tasks_dir / _SEQUENCES_FILENAME
        self._parser = TaskParser()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def create(
        self,
        title: str,
        task_type: TaskType,
        priority: TaskPriority,
        objective: str,
        created_by: str,
        approval_required: ApprovalLevel = ApprovalLevel.HUMAN_REVIEW,
        context: str = "",
        acceptance_criteria: list[str] | None = None,
        project: str = "",
    ) -> Task:
        """
        Create and persist a new task with status BACKLOG.

        Returns the created Task with its assigned ID.
        Raises TaskValidationError if title or objective is empty.
        """
        if not title.strip():
            raise TaskValidationError("title cannot be empty", field="title")
        if not objective.strip():
            raise TaskValidationError("objective cannot be empty", field="objective")

        now = datetime.now(tz=UTC)
        task_id = self._next_id()

        initial_transition = StatusTransition(
            from_status=None,
            to_status=TaskStatus.BACKLOG,
            changed_by=created_by,
            changed_at=now,
            reason="created",
        )

        task = Task(
            id=task_id,
            title=title,
            task_type=task_type,
            status=TaskStatus.BACKLOG,
            priority=priority,
            created=now,
            updated=now,
            created_by=created_by,
            objective=objective,
            context=context,
            project=project,
            approval_required=approval_required,
            acceptance_criteria=list(acceptance_criteria or []),
            status_history=[initial_transition],
        )

        self._write(task, directory=self._active_dir)
        return task

    def get(self, task_id: EntityId) -> Task:
        """
        Retrieve a task by ID.

        Searches active/ first, then completed/.
        Raises TaskNotFoundError if the task does not exist in either location.
        """
        for directory in (self._active_dir, self._completed_dir):
            path = directory / f"{task_id}.md"
            if path.exists():
                return self._parser.parse(path.read_text(encoding="utf-8"), source_path=str(path))
        raise TaskNotFoundError(task_id)

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
        Moves the file to completed/ if new_status is terminal.

        Raises InvalidTransitionError for illegal transitions.
        Raises TaskNotFoundError if the task does not exist.
        """
        task = self.get(task_id)

        if not task.can_transition_to(new_status):
            raise InvalidTransitionError(task.status.value, new_status.value)

        now = datetime.now(tz=UTC)
        task.status_history.append(
            StatusTransition(
                from_status=task.status,
                to_status=new_status,
                changed_by=changed_by,
                changed_at=now,
                reason=reason,
            )
        )
        task.status = new_status
        task.updated = now

        if task.is_terminal():
            self._archive(task)
        else:
            self._write(task, directory=self._active_dir)

        return task

    def assign(self, task_id: EntityId, assignee: str, assigned_by: str) -> Task:
        """Assign a task to an agent or human. Transitions BACKLOG → ASSIGNED."""
        task = self.update_status(
            task_id=task_id,
            new_status=TaskStatus.ASSIGNED,
            changed_by=assigned_by,
            reason=f"assigned to {assignee}",
        )
        task.assigned_to = assignee
        task.updated = datetime.now(tz=UTC)
        self._write(task, directory=self._active_dir)
        return task

    def block(self, task_id: EntityId, reason: str, blocked_by: str) -> Task:
        """Mark IN_PROGRESS → BLOCKED with a descriptive reason."""
        task = self.update_status(
            task_id=task_id,
            new_status=TaskStatus.BLOCKED,
            changed_by=blocked_by,
            reason=reason,
        )
        task.blocked_by = reason
        task.updated = datetime.now(tz=UTC)
        self._write(task, directory=self._active_dir)
        return task

    def append_work_log(self, task_id: EntityId, entry: str, author: str) -> Task:
        """Append a dated work log entry to the task."""
        task = self.get(task_id)
        now = datetime.now(tz=UTC)
        date_str = now.strftime("%Y-%m-%d")
        task.work_log.append(f"{date_str} — {author}: {entry}")
        task.updated = now

        directory = self._completed_dir if task.is_terminal() else self._active_dir
        self._write(task, directory=directory)
        return task

    def list_active(
        self,
        status: TaskStatus | None = None,
        priority: TaskPriority | None = None,
        assigned_to: str | None = None,
        task_type: TaskType | None = None,
        project: str | None = None,
    ) -> list[Task]:
        """
        Return active tasks from tasks/active/, optionally filtered.

        Does not include completed or cancelled tasks.

        ``project`` filters on the explicit association only. A task with no
        project is not returned for any project: "unknown" is not "matches
        everything", and guessing here is what the slug-in-title heuristic did.
        Callers that still need legacy behaviour apply their own fallback and can
        say so, which is the point of keeping this exact.
        """
        if not self._active_dir.exists():
            return []

        tasks: list[Task] = []
        for path in sorted(self._active_dir.glob("*.md")):
            if path.name in _SKIP_NAMES:
                continue
            try:
                task = self._parser.parse(path.read_text(encoding="utf-8"), source_path=str(path))
                tasks.append(task)
            except Exception as exc:
                warnings.warn(f"Skipping {path.name}: {exc}", stacklevel=2)

        if status is not None:
            tasks = [t for t in tasks if t.status == status]
        if priority is not None:
            tasks = [t for t in tasks if t.priority == priority]
        if assigned_to is not None:
            tasks = [t for t in tasks if t.assigned_to == assigned_to]
        if task_type is not None:
            tasks = [t for t in tasks if t.task_type == task_type]
        if project is not None:
            tasks = [t for t in tasks if t.project == project]

        return tasks

    def list_completed(self, limit: int = 0, project: str | None = None) -> list[Task]:
        """
        Return terminal tasks from tasks/completed/, most recently updated first.

        The read-only counterpart to list_active(). Parsing lives here rather
        than in callers because this module owns the on-disk task format; a
        caller that walked completed/ itself would be a second parser to keep
        correct.

        Args:
            limit: Maximum tasks to return. 0 (default) returns all.
        """
        if not self._completed_dir.exists():
            return []

        tasks: list[Task] = []
        for path in sorted(self._completed_dir.glob("*.md")):
            if path.name in _SKIP_NAMES:
                continue
            try:
                tasks.append(
                    self._parser.parse(path.read_text(encoding="utf-8"), source_path=str(path))
                )
            except Exception as exc:
                warnings.warn(f"Skipping {path.name}: {exc}", stacklevel=2)

        if project is not None:
            tasks = [t for t in tasks if t.project == project]

        tasks.sort(key=lambda t: (t.updated, t.id), reverse=True)
        return tasks[:limit] if limit > 0 else tasks

    def archive(self, task_id: EntityId) -> None:
        """
        Move a terminal task file from active/ to completed/.

        Raises TaskNotFoundError if not found in active/.
        """
        active_path = self._active_dir / f"{task_id}.md"
        if not active_path.exists():
            raise TaskNotFoundError(task_id)

        task = self._parser.parse(active_path.read_text(encoding="utf-8"))
        self._archive(task)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _next_id(self) -> EntityId:
        """
        Allocate the next task id.

        TRACKED_SEQUENTIAL: task records are committed, so two branches issuing
        TASK-0080 produce an add/add merge conflict on the record path. The
        duplicate remains possible and is guaranteed to surface, which is the
        strongest guarantee sequential ids can offer across disconnected work.
        """
        return EntityId(self._allocator().allocate())

    def _allocator(self) -> SequenceAllocator:
        return SequenceAllocator(
            Namespace(
                prefix=_TASK_PREFIX,
                records=self._tasks_dir,
                counter=self._sequences_path,
                policy=IdentityPolicy.TRACKED_SEQUENTIAL,
            )
        )

    def _write(self, task: Task, *, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{task.id}.md"
        write_atomic(path, self._parser.serialize(task))

    def _archive(self, task: Task) -> None:
        """Move a terminal task from active/ to completed/."""
        self._completed_dir.mkdir(parents=True, exist_ok=True)
        active_path = self._active_dir / f"{task.id}.md"
        completed_path = self._completed_dir / f"{task.id}.md"

        write_atomic(completed_path, self._parser.serialize(task))
        if active_path.exists():
            active_path.unlink()
