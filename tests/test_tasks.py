"""Tests for the tasks module."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tasks import (
    ApprovalLevel,
    InvalidTransitionError,
    Task,
    TaskManager,
    TaskNotFoundError,
    TaskParser,
    TaskPriority,
    TaskStatus,
    TaskType,
    TaskValidationError,
)
from tasks.task import StatusTransition, _VALID_TRANSITIONS


def _make_task(status: TaskStatus = TaskStatus.BACKLOG) -> Task:
    now = datetime.now(tz=timezone.utc)
    return Task(
        id="TASK-0001",
        title="Sample task",
        task_type=TaskType.FEATURE,
        status=status,
        priority=TaskPriority.P2,
        created=now,
        updated=now,
        created_by="human:test",
        objective="Do the thing.",
    )


class TestTaskStatusTransitions:
    def test_backlog_can_transition_to_assigned(self) -> None:
        task = _make_task(TaskStatus.BACKLOG)
        assert task.can_transition_to(TaskStatus.ASSIGNED)

    def test_backlog_can_transition_to_cancelled(self) -> None:
        task = _make_task(TaskStatus.BACKLOG)
        assert task.can_transition_to(TaskStatus.CANCELLED)

    def test_backlog_cannot_transition_to_completed(self) -> None:
        task = _make_task(TaskStatus.BACKLOG)
        assert not task.can_transition_to(TaskStatus.COMPLETED)

    def test_in_progress_can_transition_to_blocked(self) -> None:
        task = _make_task(TaskStatus.IN_PROGRESS)
        assert task.can_transition_to(TaskStatus.BLOCKED)

    def test_in_progress_can_transition_to_review(self) -> None:
        task = _make_task(TaskStatus.IN_PROGRESS)
        assert task.can_transition_to(TaskStatus.REVIEW)

    def test_completed_is_terminal(self) -> None:
        task = _make_task(TaskStatus.COMPLETED)
        for status in TaskStatus:
            assert not task.can_transition_to(status)

    def test_cancelled_is_terminal(self) -> None:
        task = _make_task(TaskStatus.CANCELLED)
        for status in TaskStatus:
            assert not task.can_transition_to(status)

    def test_all_statuses_have_transition_entries(self) -> None:
        for status in TaskStatus:
            assert status in _VALID_TRANSITIONS, f"{status} missing from _VALID_TRANSITIONS"


class TestTask:
    def test_backlog_task_is_not_terminal(self) -> None:
        assert not _make_task(TaskStatus.BACKLOG).is_terminal()

    def test_completed_task_is_terminal(self) -> None:
        assert _make_task(TaskStatus.COMPLETED).is_terminal()

    def test_cancelled_task_is_terminal(self) -> None:
        assert _make_task(TaskStatus.CANCELLED).is_terminal()

    def test_blocked_task_is_blocked(self) -> None:
        assert _make_task(TaskStatus.BLOCKED).is_blocked()

    def test_in_progress_task_is_not_blocked(self) -> None:
        assert not _make_task(TaskStatus.IN_PROGRESS).is_blocked()

    def test_default_approval_level_is_human_review(self) -> None:
        assert _make_task().approval_required == ApprovalLevel.HUMAN_REVIEW

    def test_task_has_empty_status_history_by_default(self) -> None:
        assert _make_task().status_history == []

    def test_task_type_values(self) -> None:
        assert TaskType.FEATURE.value == "feature"
        assert TaskType.FIX.value == "fix"

    def test_priority_values(self) -> None:
        assert TaskPriority.P0.value == "P0"
        assert TaskPriority.P3.value == "P3"


class TestStatusTransition:
    def test_transition_records_fields(self) -> None:
        now = datetime.now(tz=timezone.utc)
        t = StatusTransition(
            from_status=TaskStatus.BACKLOG,
            to_status=TaskStatus.ASSIGNED,
            changed_by="agent:brain",
            changed_at=now,
            reason="task picked up by router",
        )
        assert t.from_status == TaskStatus.BACKLOG
        assert t.to_status == TaskStatus.ASSIGNED
        assert t.reason == "task picked up by router"


class TestTaskParser:
    def _make_serialized_task(self) -> tuple[Task, str]:
        parser = TaskParser()
        task = _make_task()
        raw = parser.serialize(task)
        return task, raw

    def test_serialize_produces_frontmatter(self) -> None:
        _, raw = self._make_serialized_task()
        assert raw.startswith("---\n")
        assert "---\n" in raw[4:]

    def test_parse_round_trips_id(self) -> None:
        parser = TaskParser()
        task, raw = self._make_serialized_task()
        restored = parser.parse(raw)
        assert restored.id == task.id

    def test_parse_round_trips_title(self) -> None:
        parser = TaskParser()
        task, raw = self._make_serialized_task()
        restored = parser.parse(raw)
        assert restored.title == task.title

    def test_parse_round_trips_status(self) -> None:
        parser = TaskParser()
        task, raw = self._make_serialized_task()
        restored = parser.parse(raw)
        assert restored.status == task.status

    def test_parse_round_trips_status_history(self) -> None:
        parser = TaskParser()
        now = datetime.now(tz=timezone.utc)
        task = Task(
            id="TASK-0002",
            title="With history",
            task_type=TaskType.FIX,
            status=TaskStatus.ASSIGNED,
            priority=TaskPriority.P1,
            created=now,
            updated=now,
            created_by="human:test",
            objective="Fix it.",
            status_history=[
                StatusTransition(
                    from_status=None,
                    to_status=TaskStatus.BACKLOG,
                    changed_by="human:test",
                    changed_at=now,
                    reason="created",
                ),
                StatusTransition(
                    from_status=TaskStatus.BACKLOG,
                    to_status=TaskStatus.ASSIGNED,
                    changed_by="agent:brain",
                    changed_at=now,
                    reason="picked up",
                ),
            ],
        )
        raw = parser.serialize(task)
        restored = parser.parse(raw)
        assert len(restored.status_history) == 2
        assert restored.status_history[0].from_status is None
        assert restored.status_history[1].from_status == TaskStatus.BACKLOG

    def test_parse_raises_on_missing_required_field(self) -> None:
        from tasks.errors import TaskParseError
        parser = TaskParser()
        raw = "---\ntitle: No ID here\n---\n"
        with pytest.raises(TaskParseError):
            parser.parse(raw)

    def test_parse_raises_on_no_frontmatter(self) -> None:
        from tasks.errors import TaskParseError
        parser = TaskParser()
        with pytest.raises(TaskParseError):
            parser.parse("just plain text with no frontmatter")


class TestTaskManager:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path) -> None:
        self.mgr = TaskManager(tmp_path)
        self.tmp_path = tmp_path

    def _create(self, title: str = "Test task", objective: str = "Do it.") -> Task:
        return self.mgr.create(
            title=title,
            task_type=TaskType.FEATURE,
            priority=TaskPriority.P2,
            objective=objective,
            created_by="human:test",
        )

    def test_create_returns_task(self) -> None:
        task = self._create()
        assert isinstance(task, Task)

    def test_create_assigns_task_id(self) -> None:
        task = self._create()
        assert task.id == "TASK-0001"

    def test_create_returns_backlog_status(self) -> None:
        task = self._create()
        assert task.status == TaskStatus.BACKLOG

    def test_create_writes_file_to_active_dir(self) -> None:
        task = self._create()
        active_file = self.tmp_path / "tasks" / "active" / f"{task.id}.md"
        assert active_file.exists()

    def test_create_sequences_increment(self) -> None:
        t1 = self._create("First")
        t2 = self._create("Second")
        assert t1.id == "TASK-0001"
        assert t2.id == "TASK-0002"

    def test_create_sequences_persist_across_instances(self) -> None:
        self._create("First")
        mgr2 = TaskManager(self.tmp_path)
        t2 = mgr2.create(
            title="Second",
            task_type=TaskType.FIX,
            priority=TaskPriority.P1,
            objective="Different.",
            created_by="human:test",
        )
        assert t2.id == "TASK-0002"

    def test_create_populates_initial_status_history(self) -> None:
        task = self._create()
        assert len(task.status_history) == 1
        assert task.status_history[0].from_status is None
        assert task.status_history[0].to_status == TaskStatus.BACKLOG

    def test_create_raises_on_empty_title(self) -> None:
        with pytest.raises(TaskValidationError):
            self.mgr.create(
                title="",
                task_type=TaskType.FEATURE,
                priority=TaskPriority.P2,
                objective="Some objective.",
                created_by="human:test",
            )

    def test_create_raises_on_empty_objective(self) -> None:
        with pytest.raises(TaskValidationError):
            self.mgr.create(
                title="Valid title",
                task_type=TaskType.FEATURE,
                priority=TaskPriority.P2,
                objective="",
                created_by="human:test",
            )

    def test_get_retrieves_created_task(self) -> None:
        created = self._create()
        retrieved = self.mgr.get(created.id)
        assert retrieved.id == created.id
        assert retrieved.title == created.title

    def test_get_raises_for_unknown_id(self) -> None:
        with pytest.raises(TaskNotFoundError):
            self.mgr.get("TASK-9999")

    def test_update_status_changes_status(self) -> None:
        task = self._create()
        updated = self.mgr.update_status(
            task_id=task.id,
            new_status=TaskStatus.ASSIGNED,
            changed_by="human:test",
            reason="picking up",
        )
        assert updated.status == TaskStatus.ASSIGNED

    def test_update_status_appends_to_history(self) -> None:
        task = self._create()
        updated = self.mgr.update_status(
            task_id=task.id,
            new_status=TaskStatus.ASSIGNED,
            changed_by="human:test",
        )
        assert len(updated.status_history) == 2
        assert updated.status_history[1].from_status == TaskStatus.BACKLOG
        assert updated.status_history[1].to_status == TaskStatus.ASSIGNED

    def test_update_status_rejects_invalid_transition(self) -> None:
        task = self._create()
        with pytest.raises(InvalidTransitionError):
            self.mgr.update_status(
                task_id=task.id,
                new_status=TaskStatus.COMPLETED,
                changed_by="human:test",
            )

    def test_list_active_returns_empty_when_no_tasks(self) -> None:
        assert self.mgr.list_active() == []

    def test_list_active_returns_created_tasks(self) -> None:
        t1 = self._create("Task one")
        t2 = self._create("Task two")
        active = self.mgr.list_active()
        ids = {t.id for t in active}
        assert t1.id in ids
        assert t2.id in ids

    def test_list_active_filters_by_status(self) -> None:
        t1 = self._create("Backlog task")
        t2 = self._create("To be assigned")
        self.mgr.update_status(t2.id, TaskStatus.ASSIGNED, changed_by="human:test")
        assigned = self.mgr.list_active(status=TaskStatus.ASSIGNED)
        assert len(assigned) == 1
        assert assigned[0].id == t2.id

    def test_list_active_filters_by_priority(self) -> None:
        t1 = self._create("P1 task")
        self.mgr.update_status(t1.id, TaskStatus.ASSIGNED, changed_by="human:test")
        p0 = self.mgr.create(
            title="P0 task",
            task_type=TaskType.FIX,
            priority=TaskPriority.P0,
            objective="Fix now.",
            created_by="human:test",
        )
        results = self.mgr.list_active(priority=TaskPriority.P0)
        assert len(results) == 1
        assert results[0].id == p0.id

    def test_completed_task_moves_to_completed_dir(self) -> None:
        task = self._create()
        self.mgr.update_status(task.id, TaskStatus.ASSIGNED, changed_by="human:test")
        self.mgr.update_status(task.id, TaskStatus.IN_PROGRESS, changed_by="human:test")
        self.mgr.update_status(task.id, TaskStatus.COMPLETED, changed_by="human:test")

        active_file = self.tmp_path / "tasks" / "active" / f"{task.id}.md"
        completed_file = self.tmp_path / "tasks" / "completed" / f"{task.id}.md"
        assert not active_file.exists()
        assert completed_file.exists()

    def test_completed_task_not_in_list_active(self) -> None:
        task = self._create()
        self.mgr.update_status(task.id, TaskStatus.ASSIGNED, changed_by="human:test")
        self.mgr.update_status(task.id, TaskStatus.IN_PROGRESS, changed_by="human:test")
        self.mgr.update_status(task.id, TaskStatus.COMPLETED, changed_by="human:test")
        assert self.mgr.list_active() == []

    def test_get_finds_completed_task(self) -> None:
        task = self._create()
        self.mgr.update_status(task.id, TaskStatus.ASSIGNED, changed_by="human:test")
        self.mgr.update_status(task.id, TaskStatus.IN_PROGRESS, changed_by="human:test")
        self.mgr.update_status(task.id, TaskStatus.COMPLETED, changed_by="human:test")
        retrieved = self.mgr.get(task.id)
        assert retrieved.status == TaskStatus.COMPLETED


# ===========================================================================
# TestTaskIdAllocation
# ===========================================================================

class TestTaskIdAllocation:
    """
    Task ID allocation must never reissue an ID that already exists on disk.

    The counter in tasks/.sequences.json only records allocations made through
    this process. Tasks written on another branch or checkout are invisible to
    it, so a lagging counter reissues a live ID — main's counter read 50 while
    TASK-0051 and TASK-0052 existed. Allocation therefore takes
    max(counter, highest_on_disk) + 1.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path) -> None:
        self.mgr = TaskManager(tmp_path)
        self.tmp_path = tmp_path

    def _create(self, mgr: TaskManager | None = None) -> Task:
        return (mgr or self.mgr).create(
            title="Allocation probe",
            task_type=TaskType.FEATURE,
            priority=TaskPriority.P2,
            objective="Probe allocation.",
            created_by="human:test",
        )

    def _write_raw(self, name: str, *, directory: str = "active",
                   content: str = "not valid frontmatter") -> Path:
        d = self.tmp_path / "tasks" / directory
        d.mkdir(parents=True, exist_ok=True)
        path = d / name
        path.write_text(content, encoding="utf-8")
        return path

    def _set_counter(self, value: int) -> None:
        path = self.tmp_path / "tasks" / ".sequences.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"TASK": value}), encoding="utf-8")

    def _reload(self) -> TaskManager:
        return TaskManager(self.tmp_path)

    # --- normal counter path --------------------------------------------

    def test_first_allocation(self) -> None:
        assert self._create().id == "TASK-0001"

    def test_normal_counter_path_increments(self) -> None:
        first = self._create().id
        second = self._create().id
        assert (first, second) == ("TASK-0001", "TASK-0002")

    def test_no_tasks_dir_still_allocates(self) -> None:
        mgr = TaskManager(self.tmp_path / "nonexistent")
        assert self._create(mgr).id == "TASK-0001"

    # --- counter ahead of disk ------------------------------------------

    def test_counter_ahead_of_disk_is_respected(self) -> None:
        """A counter above disk wins — IDs are never reused after archival."""
        self._set_counter(75)
        assert self._create(self._reload()).id == "TASK-0076"

    def test_deleted_task_id_is_not_reissued(self) -> None:
        first = self._create()
        (self.tmp_path / "tasks" / "active" / f"{first.id}.md").unlink()
        assert self._create(self._reload()).id != first.id

    # --- stale counter below highest task id ----------------------------

    def test_stale_counter_below_disk_heals(self) -> None:
        """The real incident: counter 50, TASK-0051 and TASK-0052 on disk."""
        self._write_raw("TASK-0051.md")
        self._write_raw("TASK-0052.md", directory="completed")
        self._set_counter(50)
        assert self._create(self._reload()).id == "TASK-0053"

    def test_missing_counter_heals_from_disk(self) -> None:
        self._write_raw("TASK-0042.md")
        assert self._create(self._reload()).id == "TASK-0043"

    def test_healed_counter_is_persisted(self) -> None:
        self._write_raw("TASK-0051.md")
        self._set_counter(50)
        self._create(self._reload())
        seq = json.loads((self.tmp_path / "tasks" / ".sequences.json").read_text())
        assert seq["TASK"] == 52

    def test_stale_counter_does_not_overwrite_existing_file(self) -> None:
        existing = self._write_raw("TASK-0051.md", content="original content")
        self._set_counter(50)
        self._create(self._reload())
        assert existing.read_text(encoding="utf-8") == "original content"

    # --- active + completed both counted --------------------------------

    def test_completed_directory_is_counted(self) -> None:
        self._write_raw("TASK-0030.md", directory="completed")
        assert self._create(self._reload()).id == "TASK-0031"

    def test_highest_across_both_directories_wins(self) -> None:
        self._write_raw("TASK-0020.md", directory="active")
        self._write_raw("TASK-0060.md", directory="completed")
        assert self._create(self._reload()).id == "TASK-0061"

    def test_archived_task_still_reserves_its_id(self) -> None:
        """A task moved active -> completed must not have its ID reissued."""
        task = self._create()
        self.mgr.update_status(task.id, TaskStatus.ASSIGNED, changed_by="human:test")
        self.mgr.update_status(task.id, TaskStatus.IN_PROGRESS, changed_by="human:test")
        self.mgr.update_status(task.id, TaskStatus.COMPLETED, changed_by="human:test")
        self._set_counter(0)  # counter clobbered after archival
        assert self._create(self._reload()).id != task.id

    # --- malformed files -------------------------------------------------

    def test_malformed_task_still_reserves_its_id(self) -> None:
        """Unparseable body, valid filename — the ID is still taken."""
        self._write_raw("TASK-0007.md", content="{{{ not parseable at all")
        assert self._create(self._reload()).id == "TASK-0008"

    def test_empty_task_file_reserves_its_id(self) -> None:
        self._write_raw("TASK-0011.md", content="")
        assert self._create(self._reload()).id == "TASK-0012"

    def test_non_task_filenames_are_ignored(self) -> None:
        for name in ("README.md", "index.md", "TASK-notanumber.md", "TASK-0009-old.md"):
            self._write_raw(name)
        assert self._create(self._reload()).id == "TASK-0001"

    # --- no duplicate allocation -----------------------------------------

    def test_no_duplicate_allocation_across_many_creates(self) -> None:
        ids = [self._create().id for _ in range(25)]
        assert len(set(ids)) == 25

    def test_no_duplicate_when_counter_reset_between_instances(self) -> None:
        """Simulates a branch switch reverting .sequences.json under live files."""
        ids = [self._create().id for _ in range(3)]
        self._set_counter(0)
        ids.append(self._create(self._reload()).id)
        assert len(set(ids)) == 4
        assert ids[-1] == "TASK-0004"

    # --- monotonicity -----------------------------------------------------

    def test_monotonic_across_manager_instances(self) -> None:
        seen = [int(self._create(self._reload()).id.split("-")[1]) for _ in range(5)]
        assert seen == sorted(seen)
        assert len(set(seen)) == len(seen)

    def test_monotonic_despite_repeated_counter_rollback(self) -> None:
        seen: list[int] = []
        for _ in range(5):
            self._set_counter(0)  # counter clobbered before every create
            seen.append(int(self._create(self._reload()).id.split("-")[1]))
        assert seen == [1, 2, 3, 4, 5]

    # --- id format preserved ----------------------------------------------

    def test_healed_id_keeps_four_digit_format(self) -> None:
        self._write_raw("TASK-0051.md")
        self._set_counter(50)
        assert re.match(r"^TASK-\d{4}$", self._create(self._reload()).id)
