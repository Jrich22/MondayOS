"""Task manager — create, read, update, and archive tasks."""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import stat
import threading
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
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
_TASK_ID_PATTERN = re.compile(r"^TASK-\d{1,64}(?:-[A-Z0-9]{1,12})?$")
_PROCESS_LOCKS_GUARD = threading.Lock()


@dataclass
class _ProcessLockEntry:
    lock: threading.RLock
    users: int = 0
    descriptor: int | None = None
    depth: int = 0


_PROCESS_LOCKS: dict[tuple[int, int, str], _ProcessLockEntry] = {}

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
        self._lock_guard = threading.RLock()
        self._lock_depth = 0
        self._locked_task_id = ""
        self._lock_descriptor: int | None = None

    @contextmanager
    def lock(self, task_id: EntityId) -> Iterator[int]:
        """Serialize one task's state transitions across threads and processes."""
        exact_task_id = str(task_id)
        if not _TASK_ID_PATTERN.fullmatch(exact_task_id):
            raise TaskNotFoundError(exact_task_id)
        active_path = self._active_dir / f"{exact_task_id}.md"
        completed_path = self._completed_dir / f"{exact_task_id}.md"
        if not active_path.exists() and not completed_path.exists():
            raise TaskNotFoundError(exact_task_id)
        self._lock_guard.acquire()
        try:
            if self._lock_depth and self._locked_task_id != exact_task_id:
                raise RuntimeError("nested task locks must target the same task")

            lock_dir = self._tasks_dir / ".locks"
            lock_name = hashlib.sha256(exact_task_id.encode("utf-8")).hexdigest()
            lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory_flags = (
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
            )
            if not hasattr(os, "O_NOFOLLOW"):
                raise OSError("task locks require no-follow filesystem support")
            directory_flags |= os.O_NOFOLLOW
            try:
                directory_descriptor = os.open(lock_dir, directory_flags)
            except OSError as exc:
                raise OSError(
                    "task lock directory must be one controller-owned real directory"
                ) from exc
            try:
                _validate_lock_directory(lock_dir, directory_descriptor)
                directory_metadata = os.fstat(directory_descriptor)
                process_key, process_entry = _acquire_process_task_lock(
                    (directory_metadata.st_dev, directory_metadata.st_ino, lock_name)
                )
                try:
                    descriptor = process_entry.descriptor
                    if descriptor is None:
                        flags = (
                            os.O_RDWR
                            | os.O_CREAT
                            | getattr(os, "O_CLOEXEC", 0)
                            | os.O_NOFOLLOW
                        )
                        try:
                            descriptor = os.open(
                                f"{lock_name}.lock",
                                flags,
                                0o600,
                                dir_fd=directory_descriptor,
                            )
                        except OSError as exc:
                            raise OSError(
                                "task lock must be one controller-owned regular file"
                            ) from exc
                        try:
                            _validate_lock_file(
                                directory_descriptor,
                                f"{lock_name}.lock",
                                descriptor,
                            )
                            fcntl.flock(descriptor, fcntl.LOCK_EX)
                            # Both names are attacker-visible while flock blocks.
                            # Revalidate the pinned directory and file identities
                            # after acquisition before treating the lock as held.
                            _validate_lock_directory(lock_dir, directory_descriptor)
                            _validate_lock_file(
                                directory_descriptor,
                                f"{lock_name}.lock",
                                descriptor,
                            )
                        except BaseException:
                            os.close(descriptor)
                            raise
                        process_entry.descriptor = descriptor
                    else:
                        # A reentrant acquisition, including through another
                        # TaskManager instance, must reuse the same open file
                        # description. A second flock by this thread would wait
                        # on its own first lock forever on some platforms.
                        _validate_lock_file(
                            directory_descriptor,
                            f"{lock_name}.lock",
                            descriptor,
                        )
                    process_entry.depth += 1
                    self._lock_depth += 1
                    if self._lock_depth == 1:
                        self._locked_task_id = exact_task_id
                        self._lock_descriptor = descriptor
                    try:
                        yield descriptor
                    finally:
                        self._lock_depth -= 1
                        if self._lock_depth == 0:
                            self._lock_descriptor = None
                            self._locked_task_id = ""
                        process_entry.depth -= 1
                        if process_entry.depth == 0:
                            held_descriptor = process_entry.descriptor
                            process_entry.descriptor = None
                            if held_descriptor is None:
                                raise RuntimeError("task lock descriptor is unavailable")
                            # Do not issue LOCK_UN: a publication child may have
                            # inherited this same open file description. Closing
                            # our copy keeps its guard locked until the final
                            # inherited descriptor closes.
                            os.close(held_descriptor)
                finally:
                    _release_process_task_lock(process_key, process_entry)
            finally:
                os.close(directory_descriptor)
        finally:
            self._lock_guard.release()

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
        project_delivery_repository: str = "",
        project_source_device: int | None = None,
        project_source_inode: int | None = None,
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
        if not isinstance(project_delivery_repository, str):
            raise TaskValidationError(
                "project_delivery_repository must be a string",
                field="project_delivery_repository",
            )
        for field_name, value in (
            ("project_source_device", project_source_device),
            ("project_source_inode", project_source_inode),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise TaskValidationError(
                    f"{field_name} must be a non-negative integer or null",
                    field=field_name,
                )
        if bool(project_delivery_repository) != (
            project_source_device is not None and project_source_inode is not None
        ):
            raise TaskValidationError(
                "project delivery repository and filesystem identity must be recorded together",
                field="project_delivery_repository",
            )
        if project_delivery_repository and not project:
            raise TaskValidationError(
                "a project delivery target requires an explicit project",
                field="project",
            )

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
            project_delivery_repository=project_delivery_repository,
            project_source_device=project_source_device,
            project_source_inode=project_source_inode,
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
        # Task IDs become filenames.  Reject anything outside the bounded ID
        # grammar before joining paths so traversal, absolute paths, and
        # filesystem-length abuse cannot escape the task stores.
        if not _TASK_ID_PATTERN.fullmatch(str(task_id)):
            raise TaskNotFoundError(str(task_id))
        active_path = self._active_dir / f"{task_id}.md"
        completed_path = self._completed_dir / f"{task_id}.md"

        # The completed copy is the commit record for an archive. A process can
        # stop after that durable write but before removing the active copy; in
        # that split state terminal truth must win. Best-effort cleanup keeps
        # later scans tidy, while a permissions error cannot hide completion.
        if completed_path.exists():
            task = self._parser.parse(
                completed_path.read_text(encoding="utf-8"),
                source_path=str(completed_path),
            )
            if active_path.exists():
                try:
                    active_path.unlink()
                except OSError:
                    pass
            return task
        if active_path.exists():
            return self._parser.parse(
                active_path.read_text(encoding="utf-8"),
                source_path=str(active_path),
            )
        raise TaskNotFoundError(task_id)

    def revision(self, task_id: EntityId) -> str:
        """Return a collision-resistant digest of the complete persisted task.

        Callers that use the digest as an optimistic mutation guard must hold
        ``lock(task_id)`` across both ``get``/``revision`` and the guarded
        action.  Canonical serialization covers status history, work log,
        metadata, targeting, and every other persisted task field, so two
        same-second edits cannot share authority merely because display
        timestamps are second-granularity.
        """
        canonical = self._parser.serialize(self.get(task_id)).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

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
        with self.lock(task_id):
            return self._update_status_locked(task_id, new_status, changed_by, reason)

    def _update_status_locked(
        self,
        task_id: EntityId,
        new_status: TaskStatus,
        changed_by: str,
        reason: str,
    ) -> Task:
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
        with self.lock(task_id):
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
        with self.lock(task_id):
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
        with self.lock(task_id):
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
            # A completed copy means archive commit succeeded even if cleanup
            # of this stale active path was interrupted.
            if (self._completed_dir / path.name).exists():
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
        with self.lock(task_id):
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


def _acquire_process_task_lock(
    key: tuple[int, int, str],
) -> tuple[tuple[int, int, str], _ProcessLockEntry]:
    with _PROCESS_LOCKS_GUARD:
        entry = _PROCESS_LOCKS.get(key)
        if entry is None:
            entry = _ProcessLockEntry(threading.RLock())
            _PROCESS_LOCKS[key] = entry
        entry.users += 1
    try:
        entry.lock.acquire()
    except BaseException:
        with _PROCESS_LOCKS_GUARD:
            entry.users -= 1
            if entry.users == 0 and _PROCESS_LOCKS.get(key) is entry:
                del _PROCESS_LOCKS[key]
        raise
    return key, entry


def _release_process_task_lock(
    key: tuple[int, int, str],
    entry: _ProcessLockEntry,
) -> None:
    entry.lock.release()
    with _PROCESS_LOCKS_GUARD:
        entry.users -= 1
        if entry.users == 0 and _PROCESS_LOCKS.get(key) is entry:
            del _PROCESS_LOCKS[key]


def _validate_lock_directory(path: Path, descriptor: int) -> None:
    opened = os.fstat(descriptor)
    visible = path.lstat()
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(visible.st_mode)
        or stat.S_ISLNK(visible.st_mode)
        or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
        or opened.st_uid != os.geteuid()
        or visible.st_uid != os.geteuid()
        or opened.st_mode & 0o022
        or visible.st_mode & 0o022
    ):
        raise OSError("task lock directory must be one controller-owned real directory")


def _validate_lock_file(
    directory_descriptor: int,
    name: str,
    descriptor: int,
) -> None:
    opened = os.fstat(descriptor)
    visible = os.stat(
        name,
        dir_fd=directory_descriptor,
        follow_symlinks=False,
    )
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(visible.st_mode)
        or opened.st_nlink != 1
        or visible.st_nlink != 1
        or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
        or opened.st_uid != os.geteuid()
        or visible.st_uid != os.geteuid()
        or opened.st_mode & 0o022
        or visible.st_mode & 0o022
    ):
        raise OSError("task lock must be one controller-owned regular file")
