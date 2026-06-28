"""Task manager — create, read, update, and archive tasks."""
from __future__ import annotations

import json
import warnings
from datetime import datetime, timezone
from pathlib import Path

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
        self._sequences: dict[str, int] = {}
        self._load_sequences()

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

        now = datetime.now(tz=timezone.utc)
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

        now = datetime.now(tz=timezone.utc)
        task.status_history.append(StatusTransition(
            from_status=task.status,
            to_status=new_status,
            changed_by=changed_by,
            changed_at=now,
            reason=reason,
        ))
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
        task.updated = datetime.now(tz=timezone.utc)
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
        task.updated = datetime.now(tz=timezone.utc)
        self._write(task, directory=self._active_dir)
        return task

    def append_work_log(self, task_id: EntityId, entry: str, author: str) -> Task:
        """Append a dated work log entry to the task."""
        task = self.get(task_id)
        now = datetime.now(tz=timezone.utc)
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
    ) -> list[Task]:
        """
        Return active tasks from tasks/active/, optionally filtered.

        Does not include completed or cancelled tasks.
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

        return tasks

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

    def _load_sequences(self) -> None:
        if self._sequences_path.exists():
            try:
                self._sequences = json.loads(
                    self._sequences_path.read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, OSError):
                self._sequences = {}
        else:
            self._sequences = {}

    def _save_sequences(self) -> None:
        self._tasks_dir.mkdir(parents=True, exist_ok=True)
        self._sequences_path.write_text(
            json.dumps(self._sequences, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _next_id(self) -> EntityId:
        next_seq = self._sequences.get(_TASK_PREFIX, 0) + 1
        self._sequences[_TASK_PREFIX] = next_seq
        self._save_sequences()
        return f"{_TASK_PREFIX}-{next_seq:04d}"

    def _write(self, task: Task, *, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{task.id}.md"
        path.write_text(self._parser.serialize(task), encoding="utf-8")

    def _archive(self, task: Task) -> None:
        """Move a terminal task from active/ to completed/."""
        self._completed_dir.mkdir(parents=True, exist_ok=True)
        active_path = self._active_dir / f"{task.id}.md"
        completed_path = self._completed_dir / f"{task.id}.md"

        completed_path.write_text(self._parser.serialize(task), encoding="utf-8")
        if active_path.exists():
            active_path.unlink()
