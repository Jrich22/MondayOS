"""Tests for the brain module."""
from __future__ import annotations

from pathlib import Path

import pytest

from brain import Brain, BrainConfig, ModelTier, Router, RoutingDecision


class TestBrainConfig:
    def test_default_model_tier_is_standard(self, tmp_path: Path) -> None:
        config = BrainConfig(
            project_root=tmp_path,
            knowledge_dir=tmp_path / "knowledge",
            memory_dir=tmp_path / "memory",
            tasks_dir=tmp_path / "tasks",
            logs_dir=tmp_path / "logs",
        )
        assert config.default_model_tier == "standard"

    def test_human_approval_defaults_to_true(self, tmp_path: Path) -> None:
        config = BrainConfig(
            project_root=tmp_path,
            knowledge_dir=tmp_path / "knowledge",
            memory_dir=tmp_path / "memory",
            tasks_dir=tmp_path / "tasks",
            logs_dir=tmp_path / "logs",
        )
        assert config.require_human_approval is True

    def test_from_project_root_builds_standard_paths(self, tmp_path: Path) -> None:
        config = BrainConfig.from_project_root(tmp_path)
        assert config.knowledge_dir == tmp_path / "knowledge"
        assert config.memory_dir == tmp_path / "memory"
        assert config.tasks_dir == tmp_path / "tasks"
        assert config.logs_dir == tmp_path / "logs"

    def test_from_project_root_preserves_root(self, tmp_path: Path) -> None:
        config = BrainConfig.from_project_root(tmp_path)
        assert config.project_root == tmp_path


class TestBrain:
    def _make_brain(self, tmp_path: Path) -> Brain:
        return Brain(BrainConfig.from_project_root(tmp_path))

    def test_brain_instantiates_without_error(self, tmp_path: Path) -> None:
        brain = self._make_brain(tmp_path)
        assert brain.config.project_root == tmp_path

    def test_execute_task_not_yet_implemented(self, tmp_path: Path) -> None:
        brain = self._make_brain(tmp_path)
        with pytest.raises(NotImplementedError):
            brain.execute_task("TASK-0001")

    def test_create_task_not_yet_implemented(self, tmp_path: Path) -> None:
        brain = self._make_brain(tmp_path)
        with pytest.raises(NotImplementedError):
            brain.create_task(title="Test", objective="Do thing")

    def test_query_knowledge_not_yet_implemented(self, tmp_path: Path) -> None:
        brain = self._make_brain(tmp_path)
        with pytest.raises(NotImplementedError):
            brain.query_knowledge("rate limit")

    # TODO: The tests below define expected behavior for the full Brain implementation.

    def test_execute_task_runs_full_lifecycle(self, tmp_path: Path) -> None:
        pytest.skip("TODO: implement Brain.execute_task()")

    def test_create_task_returns_task_id(self, tmp_path: Path) -> None:
        pytest.skip("TODO: implement Brain.create_task()")

    def test_approval_gate_blocks_execution_until_approved(self, tmp_path: Path) -> None:
        pytest.skip("TODO: implement approval gate enforcement in Brain")

    def test_knowledge_is_queried_before_task_execution(self, tmp_path: Path) -> None:
        pytest.skip("TODO: verify SearchEngine.search() is called before model routing")


class TestModelTier:
    def test_all_four_tiers_exist(self) -> None:
        assert ModelTier.HIGH
        assert ModelTier.STANDARD
        assert ModelTier.FAST
        assert ModelTier.LOCAL

    def test_tier_values(self) -> None:
        assert ModelTier.HIGH.value == "high"
        assert ModelTier.LOCAL.value == "local"


class TestRouter:
    def test_route_not_yet_implemented(self) -> None:
        from datetime import datetime, timezone
        from tasks.task import Task, TaskStatus
        router = Router()
        task = Task(
            id="TASK-0001",
            title="Test",
            task_type=pytest.importorskip("tasks").TaskType.FEATURE,
            status=TaskStatus.ASSIGNED,
            priority=pytest.importorskip("tasks").TaskPriority.P2,
            created=datetime.now(tz=timezone.utc),
            updated=datetime.now(tz=timezone.utc),
            created_by="human:test",
            objective="Do thing",
        )
        with pytest.raises(NotImplementedError):
            router.route(task)

    # TODO: The tests below define expected routing behavior.

    def test_routing_decision_always_has_reasoning(self) -> None:
        pytest.skip("TODO: implement Router.route() with mandatory reasoning")

    def test_privacy_sensitive_task_routes_to_local_tier(self) -> None:
        pytest.skip("TODO: implement privacy_sensitive routing flag")

    def test_fix_task_defaults_to_standard_tier(self) -> None:
        pytest.skip("TODO: implement type-based routing heuristics")
