"""
Explicit task -> project association, and the migration onto it.

Increment 1 decided which project a task belonged to by looking for the project
slug in its title or objective. That is a guess, and this module's job is to
replace it with a recorded fact without breaking the tasks written before the
field existed.

The tests that matter here are the ones about *not* guessing: an ambiguous task
must stay unassigned and be reported, and a task that already carries an explicit
project must never be overwritten by a later run.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tasks.manager import TaskManager
from tasks.migration import backfill_projects, infer_project
from tasks.parser import TaskParser
from tasks.task import ApprovalLevel, TaskPriority, TaskType


def _manager(tmp: str) -> TaskManager:
    return TaskManager(Path(tmp))


def _task(manager: TaskManager, title: str, objective: str = "do it", project: str = ""):
    return manager.create(
        title=title,
        task_type=TaskType.FEATURE,
        priority=TaskPriority.P2,
        objective=objective,
        created_by="human:test",
        approval_required=ApprovalLevel.HUMAN_REVIEW,
        project=project,
    )


def _complete(manager: TaskManager, task_id: str) -> None:
    """Walk a task to COMPLETED through the real state machine."""
    from tasks import TaskStatus

    for state in (
        TaskStatus.ASSIGNED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.REVIEW,
        TaskStatus.COMPLETED,
    ):
        manager.update_status(task_id, state, "human:test")


class TestTaskProjectField(unittest.TestCase):
    def test_a_task_can_carry_an_explicit_project(self):
        with TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            task = _task(manager, "Ship it", project="sourcingbot")
            self.assertEqual(manager.get(task.id).project, "sourcingbot")

    def test_the_project_survives_a_serialize_parse_round_trip(self):
        with TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            task = _task(manager, "Ship it", project="cue-app")
            parser = TaskParser()
            self.assertEqual(parser.parse(parser.serialize(task)).project, "cue-app")

    def test_a_legacy_task_without_the_field_still_loads(self):
        """A task that will not load is worse than one whose project is unknown."""
        with TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            task = _task(manager, "Legacy work")
            path = Path(tmp) / "tasks" / "active" / f"{task.id}.md"
            path.write_text(path.read_text().replace("project: ''\n", ""))
            self.assertEqual(manager.get(task.id).project, "")

    def test_listing_by_project_uses_the_explicit_field_only(self):
        with TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            _task(manager, "A", project="alpha")
            _task(manager, "B", project="beta")
            _task(manager, "Mentions alpha in the title")  # no explicit project

            self.assertEqual([t.title for t in manager.list_active(project="alpha")], ["A"])
            self.assertEqual([t.title for t in manager.list_active(project="beta")], ["B"])

    def test_an_unassigned_task_matches_no_named_project(self):
        """ "Unknown" is not "matches everything" — that was the old bug."""
        with TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            _task(manager, "Unowned work")
            self.assertEqual(manager.list_active(project="alpha"), [])

    def test_filtering_on_the_empty_project_returns_the_unassigned(self):
        """
        `project=""` means "tasks with no project", not "all tasks".

        Worth pinning: a caller passing an empty string by accident gets the
        unassigned set rather than everything, which is the safer failure.
        """
        with TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            _task(manager, "Owned", project="alpha")
            unowned = _task(manager, "Unowned work")
            self.assertEqual([t.id for t in manager.list_active(project="")], [unowned.id])

    def test_completed_tasks_filter_by_project_too(self):
        with TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            task = _task(manager, "A", project="alpha")
            _complete(manager, task.id)
            self.assertEqual([t.id for t in manager.list_completed(project="alpha")], [task.id])
            self.assertEqual(manager.list_completed(project="beta"), [])


class TestProjectInference(unittest.TestCase):
    def _t(self, title: str, objective: str = ""):
        from datetime import UTC, datetime

        from tasks.task import Task, TaskStatus

        return Task(
            id="TASK-0001",
            title=title,
            task_type=TaskType.FEATURE,
            status=TaskStatus.BACKLOG,
            priority=TaskPriority.P2,
            created=datetime.now(tz=UTC),
            updated=datetime.now(tz=UTC),
            created_by="human:test",
            objective=objective,
        )

    def test_a_slug_written_as_prose_still_matches(self):
        """`growth-bot` has to match "Growth Bot"."""
        slug, _ = infer_project(self._t("Growth Bot: analytics"), ["growth-bot"])
        self.assertEqual(slug, "growth-bot")

    def test_a_slug_followed_by_punctuation_matches(self):
        """The first matcher normalised the haystack and defeated its own boundary."""
        slug, _ = infer_project(self._t("sourcingBOT Increment 6: MCP server"), ["sourcingbot"])
        self.assertEqual(slug, "sourcingbot")

    def test_a_contained_slug_is_not_a_second_project(self):
        """Text containing "cue-app" means cue-app, not cue."""
        slug, candidates = infer_project(self._t("cue-app onboarding"), ["cue-app", "cue"])
        self.assertEqual(slug, "cue-app")
        self.assertEqual(candidates, ["cue-app"])

    def test_two_genuinely_different_projects_are_ambiguous(self):
        slug, candidates = infer_project(
            self._t("Sync sourcingbot with cue-app"), ["sourcingbot", "cue-app"]
        )
        self.assertEqual(slug, "")
        self.assertEqual(sorted(candidates), ["cue-app", "sourcingbot"])

    def test_a_partial_word_does_not_match(self):
        slug, _ = infer_project(self._t("Refactor the sourcingbots module"), ["sourcingbot"])
        self.assertEqual(slug, "")


class TestMigration(unittest.TestCase):
    def test_a_dry_run_changes_nothing(self):
        with TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            task = _task(manager, "sourcingbot work")
            result = backfill_projects(manager, ["sourcingbot"], dry_run=True)
            self.assertEqual(result.assigned, {task.id: "sourcingbot"})
            self.assertEqual(manager.get(task.id).project, "")

    def test_a_real_run_assigns_the_project(self):
        with TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            task = _task(manager, "sourcingbot work")
            backfill_projects(manager, ["sourcingbot"], dry_run=False)
            self.assertEqual(manager.get(task.id).project, "sourcingbot")

    def test_the_migration_is_idempotent(self):
        with TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            _task(manager, "sourcingbot work")
            backfill_projects(manager, ["sourcingbot"], dry_run=False)
            second = backfill_projects(manager, ["sourcingbot"], dry_run=False)
            self.assertEqual(second.changed, 0)
            self.assertEqual(len(second.already_explicit), 1)

    def test_an_explicit_project_is_never_overwritten(self):
        """A human correction must survive a later migration run."""
        with TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            task = _task(manager, "sourcingbot work", project="cue-app")
            backfill_projects(manager, ["sourcingbot", "cue-app"], dry_run=False)
            self.assertEqual(manager.get(task.id).project, "cue-app")

    def test_ambiguous_tasks_are_reported_not_guessed(self):
        with TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            task = _task(manager, "Sync sourcingbot with cue-app")
            result = backfill_projects(manager, ["sourcingbot", "cue-app"], dry_run=False)
            self.assertEqual(manager.get(task.id).project, "")
            self.assertIn(task.id, result.ambiguous)
            self.assertIn(task.id, result.needs_attention)

    def test_unmatched_tasks_are_reported(self):
        with TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            task = _task(manager, "Generic infrastructure work")
            result = backfill_projects(manager, ["sourcingbot"], dry_run=False)
            self.assertIn(task.id, result.unmatched)
            self.assertEqual(manager.get(task.id).project, "")

    def test_completed_tasks_are_migrated_too(self):
        with TemporaryDirectory() as tmp:
            manager = _manager(tmp)
            task = _task(manager, "sourcingbot work")
            _complete(manager, task.id)
            backfill_projects(manager, ["sourcingbot"], dry_run=False)
            self.assertEqual(manager.get(task.id).project, "sourcingbot")


if __name__ == "__main__":
    unittest.main()
