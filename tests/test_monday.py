"""
Tests for the Monday public API.

These tests cover only the public surface — what external consumers depend on.
They never import internal modules (brain, tasks, knowledge, etc.) directly.
If a test needs to verify internal behavior, it does so through Monday methods.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from monday import (
    AskResponse,
    LearnResponse,
    Monday,
    MondayConfig,
    ModuleStatus,
    SearchResponse,
    StatusResponse,
    TaskResponse,
)


# ---------------------------------------------------------------------------
# Import contract
# ---------------------------------------------------------------------------

class TestImportContract:
    """The public import surface must remain stable."""

    def test_monday_class_importable(self) -> None:
        assert Monday is not None

    def test_monday_config_importable(self) -> None:
        assert MondayConfig is not None

    def test_ask_response_importable(self) -> None:
        assert AskResponse is not None

    def test_learn_response_importable(self) -> None:
        assert LearnResponse is not None

    def test_search_response_importable(self) -> None:
        assert SearchResponse is not None

    def test_task_response_importable(self) -> None:
        assert TaskResponse is not None

    def test_status_response_importable(self) -> None:
        assert StatusResponse is not None

    def test_module_status_importable(self) -> None:
        assert ModuleStatus is not None


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------

class TestInstantiation:
    def test_monday_instantiates_with_no_arguments(self) -> None:
        monday = Monday()
        assert monday is not None

    def test_monday_instantiates_with_config(self, tmp_path: Path) -> None:
        config = MondayConfig(project_root=tmp_path)
        monday = Monday(config)
        assert monday is not None

    def test_monday_has_version(self) -> None:
        assert Monday.VERSION == "0.1.0"

    def test_monday_repr_contains_version(self) -> None:
        monday = Monday()
        assert "0.1.0" in repr(monday)

    def test_monday_repr_contains_session_id(self) -> None:
        monday = Monday()
        assert monday._session_id in repr(monday)

    def test_two_instances_have_different_session_ids(self) -> None:
        a = Monday()
        b = Monday()
        assert a._session_id != b._session_id

    def test_explicit_session_id_is_used(self) -> None:
        config = MondayConfig(session_id="my-session-123")
        monday = Monday(config)
        assert monday._session_id == "my-session-123"


# ---------------------------------------------------------------------------
# MondayConfig
# ---------------------------------------------------------------------------

class TestMondayConfig:
    def test_default_project_root_is_current_dir(self) -> None:
        config = MondayConfig()
        assert config.project_root == Path(".")

    def test_default_model_tier_is_standard(self) -> None:
        assert MondayConfig().model_tier == "standard"

    def test_default_require_human_approval_is_true(self) -> None:
        assert MondayConfig().require_human_approval is True

    def test_default_log_level_is_info(self) -> None:
        assert MondayConfig().log_level == "INFO"

    def test_default_session_id_is_none(self) -> None:
        assert MondayConfig().session_id is None

    def test_custom_values_are_stored(self, tmp_path: Path) -> None:
        config = MondayConfig(
            project_root=tmp_path,
            model_tier="high",
            require_human_approval=False,
            log_level="DEBUG",
            session_id="abc-123",
        )
        assert config.project_root == tmp_path
        assert config.model_tier == "high"
        assert config.require_human_approval is False
        assert config.log_level == "DEBUG"
        assert config.session_id == "abc-123"


# ---------------------------------------------------------------------------
# status() — substantially implemented; full assertions are valid
# ---------------------------------------------------------------------------

class TestStatus:
    def setup_method(self) -> None:
        self.monday = Monday()

    def test_returns_status_response(self) -> None:
        assert isinstance(self.monday.status(), StatusResponse)

    def test_healthy_is_true(self) -> None:
        assert self.monday.status().healthy is True

    def test_version_matches_class_version(self) -> None:
        assert self.monday.status().version == Monday.VERSION

    def test_session_id_is_non_empty(self) -> None:
        assert self.monday.status().session_id != ""

    def test_session_id_matches_instance(self) -> None:
        assert self.monday.status().session_id == self.monday._session_id

    def test_uptime_is_non_negative(self) -> None:
        assert self.monday.status().uptime_seconds >= 0.0

    def test_uptime_increases_between_calls(self) -> None:
        import time
        s1 = self.monday.status().uptime_seconds
        time.sleep(0.01)
        s2 = self.monday.status().uptime_seconds
        assert s2 > s1

    def test_modules_list_has_six_entries(self) -> None:
        assert len(self.monday.status().modules) == 6

    def test_all_expected_modules_are_present(self) -> None:
        names = {m.name for m in self.monday.status().modules}
        assert names == {"brain", "events", "knowledge", "memory", "search", "tasks"}

    def test_all_modules_are_available(self) -> None:
        for module in self.monday.status().modules:
            assert module.available is True, f"{module.name} is not available"

    def test_all_modules_are_initialized(self) -> None:
        for module in self.monday.status().modules:
            assert module.initialized is True, f"{module.name} is not initialized"

    def test_module_status_has_expected_fields(self) -> None:
        module = self.monday.status().modules[0]
        assert hasattr(module, "name")
        assert hasattr(module, "available")
        assert hasattr(module, "initialized")


# ---------------------------------------------------------------------------
# ask() — typed placeholder; assert return type and field existence
# ---------------------------------------------------------------------------

class TestAsk:
    def setup_method(self) -> None:
        self.monday = Monday()

    def test_returns_ask_response(self) -> None:
        assert isinstance(self.monday.ask("hello"), AskResponse)

    def test_ask_response_has_answer_field(self) -> None:
        r = self.monday.ask("hello")
        assert hasattr(r, "answer")

    def test_ask_response_has_sources_field(self) -> None:
        r = self.monday.ask("hello")
        assert hasattr(r, "sources")
        assert isinstance(r.sources, list)

    def test_ask_response_has_model_used_field(self) -> None:
        r = self.monday.ask("hello")
        assert hasattr(r, "model_used")

    def test_ask_response_has_confidence_field(self) -> None:
        r = self.monday.ask("hello")
        assert hasattr(r, "confidence")
        assert isinstance(r.confidence, float)

    def test_ask_response_has_task_id_field(self) -> None:
        r = self.monday.ask("hello")
        assert hasattr(r, "task_id")

    def test_ask_accepts_context_kwarg(self) -> None:
        r = self.monday.ask("hello", context={"component": "memory"})
        assert isinstance(r, AskResponse)

    def test_ask_with_empty_prompt_returns_response(self) -> None:
        assert isinstance(self.monday.ask(""), AskResponse)

    # TODO: implement and enable these once Brain.execute_task() is wired up
    def test_ask_returns_non_empty_answer(self) -> None:
        pytest.skip("TODO: implement Brain.execute_task() and wire to Monday.ask()")

    def test_ask_populates_sources_from_knowledge(self) -> None:
        pytest.skip("TODO: implement SearchEngine and wire to Monday.ask()")

    def test_ask_populates_model_used(self) -> None:
        pytest.skip("TODO: implement Router and wire to Monday.ask()")


# ---------------------------------------------------------------------------
# learn() — typed placeholder
# ---------------------------------------------------------------------------

class TestLearn:
    def setup_method(self) -> None:
        self.monday = Monday()

    def test_returns_learn_response(self) -> None:
        assert isinstance(self.monday.learn("Some knowledge."), LearnResponse)

    def test_learn_response_has_entry_id_field(self) -> None:
        assert hasattr(self.monday.learn("x"), "entry_id")

    def test_learn_response_has_accepted_field(self) -> None:
        r = self.monday.learn("x")
        assert hasattr(r, "accepted")
        assert isinstance(r.accepted, bool)

    def test_learn_response_has_entry_type_field(self) -> None:
        assert hasattr(self.monday.learn("x"), "entry_type")

    def test_learn_response_has_message_field(self) -> None:
        assert hasattr(self.monday.learn("x"), "message")

    def test_default_entry_type_is_pattern(self) -> None:
        r = self.monday.learn("Some pattern.")
        assert r.entry_type == "pattern"

    def test_explicit_entry_type_is_preserved(self) -> None:
        r = self.monday.learn("Bug: thing breaks.", entry_type="bug")
        assert r.entry_type == "bug"

    def test_learn_accepts_all_optional_args(self) -> None:
        r = self.monday.learn(
            content="Content here.",
            title="My title",
            entry_type="decision",
            tags=["tag1"],
            components=["core"],
        )
        assert isinstance(r, LearnResponse)

    def test_learn_persists_to_knowledge_store(self) -> None:
        pytest.skip("TODO: implement KnowledgeStore.add() and wire to Monday.learn()")

    def test_learn_returns_valid_entry_id(self) -> None:
        pytest.skip("TODO: implement entry ID generation")


# ---------------------------------------------------------------------------
# search() — typed placeholder
# ---------------------------------------------------------------------------

class TestSearch:
    def setup_method(self) -> None:
        self.monday = Monday()

    def test_returns_search_response(self) -> None:
        assert isinstance(self.monday.search("test"), SearchResponse)

    def test_search_response_has_query_field(self) -> None:
        r = self.monday.search("rate limit")
        assert r.query == "rate limit"

    def test_search_response_has_results_field(self) -> None:
        r = self.monday.search("test")
        assert hasattr(r, "results")
        assert isinstance(r.results, list)

    def test_search_response_has_total_found_field(self) -> None:
        r = self.monday.search("test")
        assert hasattr(r, "total_found")
        assert isinstance(r.total_found, int)

    def test_search_response_has_sources_queried_field(self) -> None:
        r = self.monday.search("test")
        assert hasattr(r, "sources_queried")
        assert isinstance(r.sources_queried, list)

    def test_sources_kwarg_is_reflected_in_response(self) -> None:
        r = self.monday.search("test", sources=["knowledge", "tasks"])
        assert r.sources_queried == ["knowledge", "tasks"]

    def test_no_sources_gives_empty_sources_queried(self) -> None:
        r = self.monday.search("test")
        assert r.sources_queried == []

    def test_search_accepts_limit_kwarg(self) -> None:
        r = self.monday.search("test", limit=5)
        assert isinstance(r, SearchResponse)

    def test_search_returns_ranked_results(self) -> None:
        pytest.skip("TODO: implement SearchEngine.search() and wire to Monday.search()")

    def test_search_respects_limit(self) -> None:
        pytest.skip("TODO: implement limit in SearchEngine and Monday.search()")


# ---------------------------------------------------------------------------
# task() — typed placeholder
# ---------------------------------------------------------------------------

class TestTask:
    def setup_method(self) -> None:
        self.monday = Monday()

    def test_returns_task_response(self) -> None:
        assert isinstance(self.monday.task("list"), TaskResponse)

    def test_task_response_has_action_field(self) -> None:
        r = self.monday.task("create")
        assert r.action == "create"

    def test_task_response_has_success_field(self) -> None:
        r = self.monday.task("list")
        assert hasattr(r, "success")
        assert isinstance(r.success, bool)

    def test_task_response_has_task_id_field(self) -> None:
        assert hasattr(self.monday.task("list"), "task_id")

    def test_task_response_has_data_field(self) -> None:
        r = self.monday.task("list")
        assert hasattr(r, "data")
        assert isinstance(r.data, dict)

    def test_task_response_has_message_field(self) -> None:
        assert hasattr(self.monday.task("list"), "message")

    def test_action_is_preserved_in_response(self) -> None:
        for action in ("create", "get", "list", "update", "complete"):
            r = self.monday.task(action)
            assert r.action == action

    def test_explicit_task_id_in_kwargs(self) -> None:
        r = self.monday.task("get", task_id="TASK-0001")
        assert isinstance(r, TaskResponse)

    def test_task_create_creates_task(self) -> None:
        pytest.skip("TODO: implement TaskManager.create() and wire to Monday.task('create')")

    def test_task_list_returns_active_tasks(self) -> None:
        pytest.skip("TODO: implement TaskManager.list_active() and wire to Monday.task('list')")


# ---------------------------------------------------------------------------
# Internal encapsulation
# ---------------------------------------------------------------------------

class TestEncapsulation:
    """Internal subsystems must not be accessible as public attributes."""

    def setup_method(self) -> None:
        self.monday = Monday()

    def test_brain_not_exposed_as_public_attribute(self) -> None:
        assert not hasattr(self.monday, "brain")
        assert not hasattr(self.monday, "_brain")

    def test_bus_not_exposed_as_public_attribute(self) -> None:
        assert not hasattr(self.monday, "bus")
        assert not hasattr(self.monday, "_bus")

    def test_knowledge_not_exposed_as_public_attribute(self) -> None:
        assert not hasattr(self.monday, "knowledge")
        assert not hasattr(self.monday, "_knowledge")

    def test_memory_not_exposed_as_public_attribute(self) -> None:
        assert not hasattr(self.monday, "memory")
        assert not hasattr(self.monday, "_memory")

    def test_search_engine_not_exposed_as_public_attribute(self) -> None:
        assert not hasattr(self.monday, "search_engine")
        assert not hasattr(self.monday, "_search")

    def test_task_manager_not_exposed_as_public_attribute(self) -> None:
        assert not hasattr(self.monday, "task_manager")
        assert not hasattr(self.monday, "_tasks")
