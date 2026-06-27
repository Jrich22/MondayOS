"""Tests for the tasks module."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tasks import ApprovalLevel, Task, TaskManager, TaskPriority, TaskStatus, TaskType
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


class TestTaskManager:
    def test_create_not_yet_implemented(self) -> None:
        mgr = TaskManager()
        with pytest.raises(NotImplementedError):
            mgr.create(
                title="Test",
                task_type=TaskType.FEATURE,
                priority=TaskPriority.P2,
                objective="Do thing",
                created_by="human:test",
            )

    def test_get_not_yet_implemented(self) -> None:
        mgr = TaskManager()
        with pytest.raises(NotImplementedError):
            mgr.get("TASK-0001")

    # TODO: The tests below define expected behavior once TaskManager is implemented.

    def test_create_returns_task_with_backlog_status(self) -> None:
        pytest.skip("TODO: implement TaskManager.create()")

    def test_update_status_changes_status(self) -> None:
        pytest.skip("TODO: implement TaskManager.update_status()")

    def test_update_status_rejects_invalid_transition(self) -> None:
        pytest.skip("TODO: implement transition validation in TaskManager.update_status()")

    def test_completed_task_is_archived(self) -> None:
        pytest.skip("TODO: implement TaskManager.archive()")

    def test_list_active_filters_by_status(self) -> None:
        pytest.skip("TODO: implement TaskManager.list_active()")
