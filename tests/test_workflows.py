"""Tests for the workflow system (Sprint 1.6)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from workflows import (
    ApprovalDenied,
    StepExecutionError,
    StepStatus,
    StepType,
    WorkflowDefinition,
    WorkflowEngine,
    WorkflowNotFoundError,
    WorkflowStatus,
    WorkflowValidationError,
)
from workflows.engine import _resolve_dict, _resolve_str, _to_list
from workflows.execution import WorkflowExecution


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

AUTO_APPROVE = lambda msg, ctx: True  # noqa: E731
AUTO_REJECT = lambda msg, ctx: False  # noqa: E731


def _make_monday(tmp_path: Path):
    """Create a Monday instance isolated to tmp_path."""
    from monday import Monday, MondayConfig
    return Monday(MondayConfig(project_root=tmp_path))


def _write_yaml(dir_path: Path, filename: str, content: str) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / filename
    path.write_text(content)
    return path


def _defs_dir(tmp_path: Path) -> Path:
    return tmp_path / "workflows" / "definitions"


def _logs_dir(tmp_path: Path) -> Path:
    return tmp_path / "logs" / "workflows"


def _engine(tmp_path: Path, approval_handler=AUTO_APPROVE) -> WorkflowEngine:
    monday = _make_monday(tmp_path)
    return WorkflowEngine(
        monday=monday,
        definitions_dir=_defs_dir(tmp_path),
        logs_dir=_logs_dir(tmp_path),
        approval_handler=approval_handler,
    )


MINIMAL_YAML = """\
name: test-wf
version: "1.0"
description: A minimal test workflow
steps:
  - id: search-step
    type: search
    description: Find anything about foo
    input:
      query: "foo"
      limit: 3
"""

FULL_YAML = """\
name: full-wf
version: "2.0"
description: Workflow with all supported step types
triggers:
  - human-request
inputs:
  topic:
    description: Topic to research
    required: true
  component:
    description: Component context
    required: false
    default: core
steps:
  - id: research
    type: ask
    description: Ask about the topic
    input:
      prompt: "What do we know about {inputs.topic}?"

  - id: search-step
    type: search
    input:
      query: "{inputs.topic}"
      limit: 5

  - id: create-task
    type: task_create
    input:
      title: "Work on {inputs.topic}"
      objective: "Implement {inputs.topic} in {inputs.component}"
      task_type: feature
      priority: P2
      created_by: workflow:test

  - id: approval
    type: human_approval
    message: "Approve work on {inputs.topic} (task {create-task.task_id})?"

  - id: start-task
    type: task_start
    input:
      task_id: "{create-task.task_id}"

  - id: capture
    type: learn
    input:
      title: "Pattern: {inputs.topic}"
      entry_type: pattern
      content: "We learned about {inputs.topic}. Task: {create-task.task_id}"
      tags:
        - test
        - "{inputs.component}"

  - id: done
    type: task_complete
    input:
      task_id: "{create-task.task_id}"
      reason: "Completed. Entry: {capture.entry_id}"
"""


# ---------------------------------------------------------------------------
# TestWorkflowDefinition
# ---------------------------------------------------------------------------

class TestWorkflowDefinition:
    def test_load_minimal_yaml(self, tmp_path):
        path = _write_yaml(_defs_dir(tmp_path), "test-wf.yaml", MINIMAL_YAML)
        wf = WorkflowDefinition.from_yaml(path)
        assert wf.name == "test-wf"
        assert wf.version == "1.0"
        assert wf.description == "A minimal test workflow"
        assert len(wf.steps) == 1
        assert wf.steps[0].id == "search-step"
        assert wf.steps[0].type == StepType.SEARCH

    def test_load_full_yaml(self, tmp_path):
        path = _write_yaml(_defs_dir(tmp_path), "full-wf.yaml", FULL_YAML)
        wf = WorkflowDefinition.from_yaml(path)
        assert wf.name == "full-wf"
        assert wf.version == "2.0"
        assert len(wf.steps) == 7
        assert wf.triggers == ["human-request"]
        assert "topic" in wf.inputs
        assert wf.inputs["topic"].required is True
        assert "component" in wf.inputs
        assert wf.inputs["component"].default == "core"

    def test_all_step_types_loaded(self, tmp_path):
        path = _write_yaml(_defs_dir(tmp_path), "full-wf.yaml", FULL_YAML)
        wf = WorkflowDefinition.from_yaml(path)
        types = [s.type for s in wf.steps]
        assert StepType.ASK in types
        assert StepType.SEARCH in types
        assert StepType.TASK_CREATE in types
        assert StepType.HUMAN_APPROVAL in types
        assert StepType.TASK_START in types
        assert StepType.LEARN in types
        assert StepType.TASK_COMPLETE in types

    def test_file_not_found(self, tmp_path):
        with pytest.raises(WorkflowNotFoundError):
            WorkflowDefinition.from_yaml(tmp_path / "nonexistent.yaml")

    def test_invalid_yaml(self, tmp_path):
        path = _write_yaml(_defs_dir(tmp_path), "bad.yaml", ":\ninvalid: [yaml: here")
        with pytest.raises(WorkflowValidationError, match="Invalid YAML"):
            WorkflowDefinition.from_yaml(path)

    def test_missing_required_field_name(self, tmp_path):
        path = _write_yaml(
            _defs_dir(tmp_path), "bad.yaml",
            "version: '1.0'\nsteps: []\n"
        )
        with pytest.raises(WorkflowValidationError, match="'name'"):
            WorkflowDefinition.from_yaml(path)

    def test_missing_required_field_steps(self, tmp_path):
        path = _write_yaml(
            _defs_dir(tmp_path), "bad.yaml",
            "name: foo\nversion: '1.0'\n"
        )
        with pytest.raises(WorkflowValidationError, match="'steps'"):
            WorkflowDefinition.from_yaml(path)

    def test_unknown_step_type(self, tmp_path):
        yaml_content = (
            "name: bad\nversion: '1.0'\n"
            "steps:\n  - id: s1\n    type: unknown_type\n"
        )
        path = _write_yaml(_defs_dir(tmp_path), "bad.yaml", yaml_content)
        with pytest.raises(WorkflowValidationError, match="unknown_type"):
            WorkflowDefinition.from_yaml(path)

    def test_step_missing_id(self, tmp_path):
        yaml_content = "name: bad\nversion: '1.0'\nsteps:\n  - type: ask\n"
        path = _write_yaml(_defs_dir(tmp_path), "bad.yaml", yaml_content)
        with pytest.raises(WorkflowValidationError, match="'id'"):
            WorkflowDefinition.from_yaml(path)

    def test_step_missing_type(self, tmp_path):
        yaml_content = "name: bad\nversion: '1.0'\nsteps:\n  - id: s1\n"
        path = _write_yaml(_defs_dir(tmp_path), "bad.yaml", yaml_content)
        with pytest.raises(WorkflowValidationError, match="'type'"):
            WorkflowDefinition.from_yaml(path)

    def test_non_mapping_root(self, tmp_path):
        path = _write_yaml(_defs_dir(tmp_path), "bad.yaml", "- just\n- a\n- list\n")
        with pytest.raises(WorkflowValidationError, match="mapping"):
            WorkflowDefinition.from_yaml(path)


# ---------------------------------------------------------------------------
# TestWorkflowExecution
# ---------------------------------------------------------------------------

class TestWorkflowExecution:
    def test_start_sets_initial_state(self):
        ex = WorkflowExecution.start("my-wf", "1.0", {"key": "val"})
        assert ex.workflow_name == "my-wf"
        assert ex.workflow_version == "1.0"
        assert ex.status == WorkflowStatus.RUNNING
        assert ex.started_at != ""
        assert ex.completed_at == ""
        assert ex.inputs == {"key": "val"}

    def test_start_seeds_context_from_inputs(self):
        ex = WorkflowExecution.start("my-wf", "1.0", {"foo": "bar", "baz": "qux"})
        assert ex.context["inputs.foo"] == "bar"
        assert ex.context["inputs.baz"] == "qux"

    def test_to_dict_round_trip(self):
        ex = WorkflowExecution.start("my-wf", "1.0", {"x": "1"})
        ex.status = WorkflowStatus.COMPLETED
        ex.completed_at = "2026-06-28T00:00:00+00:00"
        d = ex.to_dict()
        assert d["workflow_name"] == "my-wf"
        assert d["workflow_version"] == "1.0"
        assert d["status"] == "completed"
        assert d["inputs"] == {"x": "1"}
        assert isinstance(d["steps"], list)

    def test_write_log_creates_json_file(self, tmp_path):
        ex = WorkflowExecution.start("my-wf", "1.0")
        ex.status = WorkflowStatus.COMPLETED
        ex.completed_at = "2026-06-28T00:00:00+00:00"
        log_path = ex.write_log(tmp_path / "logs")
        assert log_path.exists()
        data = json.loads(log_path.read_text())
        assert data["workflow_name"] == "my-wf"
        assert data["workflow_version"] == "1.0"

    def test_write_log_contains_execution_id(self, tmp_path):
        ex = WorkflowExecution.start("my-wf", "1.0")
        ex.status = WorkflowStatus.COMPLETED
        log_path = ex.write_log(tmp_path / "logs")
        data = json.loads(log_path.read_text())
        assert data["execution_id"] == ex.execution_id

    def test_write_log_filename_includes_short_id(self, tmp_path):
        ex = WorkflowExecution.start("my-wf", "1.0")
        ex.status = WorkflowStatus.COMPLETED
        log_path = ex.write_log(tmp_path / "logs")
        assert ex.execution_id[:8] in log_path.name

    def test_write_log_creates_directory(self, tmp_path):
        logs_dir = tmp_path / "deep" / "nested" / "logs"
        ex = WorkflowExecution.start("my-wf", "1.0")
        ex.status = WorkflowStatus.COMPLETED
        ex.write_log(logs_dir)
        assert logs_dir.exists()


# ---------------------------------------------------------------------------
# TestTemplateResolution
# ---------------------------------------------------------------------------

class TestTemplateResolution:
    def test_resolve_str_simple(self):
        assert _resolve_str("Hello {name}", {"name": "world"}) == "Hello world"

    def test_resolve_str_dot_notation(self):
        ctx = {"step1.answer": "found it"}
        assert _resolve_str("Result: {step1.answer}", ctx) == "Result: found it"

    def test_resolve_str_unknown_key_unchanged(self):
        assert _resolve_str("Hello {missing}", {}) == "Hello {missing}"

    def test_resolve_str_multiple_placeholders(self):
        ctx = {"a": "X", "b": "Y"}
        assert _resolve_str("{a} and {b}", ctx) == "X and Y"

    def test_resolve_str_no_placeholders(self):
        assert _resolve_str("plain text", {"a": "b"}) == "plain text"

    def test_resolve_dict_string_values(self):
        ctx = {"key": "value"}
        result = _resolve_dict({"prompt": "Hello {key}"}, ctx)
        assert result["prompt"] == "Hello value"

    def test_resolve_dict_nested(self):
        ctx = {"x": "1"}
        result = _resolve_dict({"outer": {"inner": "val-{x}"}}, ctx)
        assert result["outer"]["inner"] == "val-1"

    def test_resolve_dict_list_values(self):
        ctx = {"tag": "mytag"}
        result = _resolve_dict({"tags": ["fixed", "{tag}"]}, ctx)
        assert result["tags"] == ["fixed", "mytag"]

    def test_resolve_dict_non_string_unchanged(self):
        result = _resolve_dict({"limit": 5, "flag": True}, {})
        assert result["limit"] == 5
        assert result["flag"] is True

    def test_to_list_from_list(self):
        assert _to_list(["a", "b"]) == ["a", "b"]

    def test_to_list_from_str(self):
        assert _to_list("a, b, c") == ["a", "b", "c"]

    def test_to_list_from_none(self):
        assert _to_list(None) == []

    def test_to_list_from_other(self):
        assert _to_list(42) == ["42"]


# ---------------------------------------------------------------------------
# TestWorkflowEngine
# ---------------------------------------------------------------------------

class TestWorkflowEngine:
    def test_list_workflows_empty_dir(self, tmp_path):
        eng = _engine(tmp_path)
        assert eng.list_workflows() == []

    def test_list_workflows_missing_dir(self, tmp_path):
        eng = _engine(tmp_path)
        assert eng.list_workflows() == []

    def test_list_workflows_returns_definitions(self, tmp_path):
        _write_yaml(_defs_dir(tmp_path), "test-wf.yaml", MINIMAL_YAML)
        eng = _engine(tmp_path)
        workflows = eng.list_workflows()
        assert len(workflows) == 1
        assert workflows[0].name == "test-wf"

    def test_list_workflows_skips_malformed(self, tmp_path):
        _write_yaml(_defs_dir(tmp_path), "good.yaml", MINIMAL_YAML)
        _write_yaml(_defs_dir(tmp_path), "bad.yaml", "not: valid: yaml: [")
        eng = _engine(tmp_path)
        workflows = eng.list_workflows()
        assert len(workflows) == 1

    def test_get_workflow_by_filename(self, tmp_path):
        _write_yaml(_defs_dir(tmp_path), "test-wf.yaml", MINIMAL_YAML)
        eng = _engine(tmp_path)
        wf = eng.get_workflow("test-wf")
        assert wf.name == "test-wf"

    def test_get_workflow_not_found(self, tmp_path):
        eng = _engine(tmp_path)
        with pytest.raises(WorkflowNotFoundError):
            eng.get_workflow("nonexistent")

    def test_run_search_step(self, tmp_path):
        _write_yaml(_defs_dir(tmp_path), "test-wf.yaml", MINIMAL_YAML)
        eng = _engine(tmp_path)
        ex = eng.run("test-wf")
        assert ex.status == WorkflowStatus.COMPLETED
        assert len(ex.steps) == 1
        assert ex.steps[0].status == StepStatus.COMPLETED
        assert ex.steps[0].step_type == "search"

    def test_run_sets_workflow_version(self, tmp_path):
        _write_yaml(_defs_dir(tmp_path), "test-wf.yaml", MINIMAL_YAML)
        eng = _engine(tmp_path)
        ex = eng.run("test-wf")
        assert ex.workflow_version == "1.0"

    def test_run_writes_log_file(self, tmp_path):
        _write_yaml(_defs_dir(tmp_path), "test-wf.yaml", MINIMAL_YAML)
        eng = _engine(tmp_path)
        ex = eng.run("test-wf")
        log_files = list(_logs_dir(tmp_path).glob("*.json"))
        assert len(log_files) == 1
        data = json.loads(log_files[0].read_text())
        assert data["execution_id"] == ex.execution_id

    def test_run_context_accumulation(self, tmp_path):
        _write_yaml(_defs_dir(tmp_path), "full-wf.yaml", FULL_YAML)
        eng = _engine(tmp_path)
        ex = eng.run("full-wf", inputs={"topic": "auth", "component": "tasks"})
        assert ex.status == WorkflowStatus.COMPLETED
        # task_id from create-task step should appear in context
        assert "create-task.task_id" in ex.context
        assert ex.context["create-task.task_id"].startswith("TASK-")

    def test_run_inputs_in_context(self, tmp_path):
        _write_yaml(_defs_dir(tmp_path), "full-wf.yaml", FULL_YAML)
        eng = _engine(tmp_path)
        ex = eng.run("full-wf", inputs={"topic": "parser", "component": "core"})
        assert ex.context.get("inputs.topic") == "parser"
        assert ex.context.get("inputs.component") == "core"

    def test_run_input_default_applied(self, tmp_path):
        _write_yaml(_defs_dir(tmp_path), "full-wf.yaml", FULL_YAML)
        eng = _engine(tmp_path)
        ex = eng.run("full-wf", inputs={"topic": "router"})
        assert ex.inputs.get("component") == "core"

    def test_run_approval_approved(self, tmp_path):
        _write_yaml(_defs_dir(tmp_path), "full-wf.yaml", FULL_YAML)
        eng = _engine(tmp_path, approval_handler=AUTO_APPROVE)
        ex = eng.run("full-wf", inputs={"topic": "cache", "component": "memory"})
        assert ex.status == WorkflowStatus.COMPLETED
        approval_step = next(s for s in ex.steps if s.step_id == "approval")
        assert approval_step.status == StepStatus.COMPLETED
        assert approval_step.output.get("approved") is True

    def test_run_approval_rejected_cancels_workflow(self, tmp_path):
        _write_yaml(_defs_dir(tmp_path), "full-wf.yaml", FULL_YAML)
        eng = _engine(tmp_path, approval_handler=AUTO_REJECT)
        ex = eng.run("full-wf", inputs={"topic": "cache", "component": "memory"})
        assert ex.status == WorkflowStatus.CANCELLED
        assert "approval" in ex.error

    def test_run_approval_rejected_step_marked_rejected(self, tmp_path):
        _write_yaml(_defs_dir(tmp_path), "full-wf.yaml", FULL_YAML)
        eng = _engine(tmp_path, approval_handler=AUTO_REJECT)
        ex = eng.run("full-wf", inputs={"topic": "cache", "component": "memory"})
        approval_step = next(s for s in ex.steps if s.step_id == "approval")
        assert approval_step.status == StepStatus.REJECTED

    def test_run_approval_rejected_stops_further_steps(self, tmp_path):
        _write_yaml(_defs_dir(tmp_path), "full-wf.yaml", FULL_YAML)
        eng = _engine(tmp_path, approval_handler=AUTO_REJECT)
        ex = eng.run("full-wf", inputs={"topic": "cache", "component": "memory"})
        # Steps after approval (start-task, capture, done) are not recorded
        step_ids = {s.step_id for s in ex.steps}
        assert "start-task" not in step_ids
        assert "capture" not in step_ids

    def test_run_step_failure_marks_workflow_failed(self, tmp_path):
        bad_yaml = (
            "name: bad-step\nversion: '1.0'\n"
            "steps:\n  - id: broken\n    type: task_complete\n    input:\n      task_id: 'TASK-9999'\n"
        )
        _write_yaml(_defs_dir(tmp_path), "bad-step.yaml", bad_yaml)
        eng = _engine(tmp_path)
        ex = eng.run("bad-step")
        assert ex.status == WorkflowStatus.FAILED
        assert ex.steps[0].status == StepStatus.FAILED
        assert ex.steps[0].error != ""

    def test_run_learn_step_output_in_context(self, tmp_path):
        _write_yaml(_defs_dir(tmp_path), "full-wf.yaml", FULL_YAML)
        eng = _engine(tmp_path)
        ex = eng.run("full-wf", inputs={"topic": "signals", "component": "events"})
        assert ex.status == WorkflowStatus.COMPLETED
        assert "capture.entry_id" in ex.context
        assert ex.context["capture.entry_id"].startswith("PAT-")

    def test_run_log_contains_all_step_ids(self, tmp_path):
        _write_yaml(_defs_dir(tmp_path), "full-wf.yaml", FULL_YAML)
        eng = _engine(tmp_path)
        eng.run("full-wf", inputs={"topic": "testing", "component": "core"})
        log_files = list(_logs_dir(tmp_path).glob("*.json"))
        data = json.loads(log_files[0].read_text())
        step_ids = [s["step_id"] for s in data["steps"]]
        assert "research" in step_ids
        assert "create-task" in step_ids
        assert "approval" in step_ids
        assert "capture" in step_ids
        assert "done" in step_ids

    def test_run_log_contains_version(self, tmp_path):
        _write_yaml(_defs_dir(tmp_path), "full-wf.yaml", FULL_YAML)
        eng = _engine(tmp_path)
        eng.run("full-wf", inputs={"topic": "versioning", "component": "core"})
        log_files = list(_logs_dir(tmp_path).glob("*.json"))
        data = json.loads(log_files[0].read_text())
        assert data["workflow_version"] == "2.0"

    def test_run_not_found(self, tmp_path):
        eng = _engine(tmp_path)
        with pytest.raises(WorkflowNotFoundError):
            eng.run("nonexistent")

    def test_run_per_call_approval_handler_overrides_default(self, tmp_path):
        _write_yaml(_defs_dir(tmp_path), "full-wf.yaml", FULL_YAML)
        # Engine default is reject, but per-call handler approves
        eng = _engine(tmp_path, approval_handler=AUTO_REJECT)
        ex = eng.run(
            "full-wf",
            inputs={"topic": "override", "component": "core"},
            approval_handler=AUTO_APPROVE,
        )
        assert ex.status == WorkflowStatus.COMPLETED

    def test_ask_step_missing_prompt_fails(self, tmp_path):
        bad_yaml = (
            "name: bad-ask\nversion: '1.0'\n"
            "steps:\n  - id: ask-step\n    type: ask\n    input: {}\n"
        )
        _write_yaml(_defs_dir(tmp_path), "bad-ask.yaml", bad_yaml)
        eng = _engine(tmp_path)
        ex = eng.run("bad-ask")
        assert ex.status == WorkflowStatus.FAILED
        assert "ask step requires 'prompt'" in ex.steps[0].error

    def test_search_step_missing_query_fails(self, tmp_path):
        bad_yaml = (
            "name: bad-search\nversion: '1.0'\n"
            "steps:\n  - id: search-step\n    type: search\n    input: {}\n"
        )
        _write_yaml(_defs_dir(tmp_path), "bad-search.yaml", bad_yaml)
        eng = _engine(tmp_path)
        ex = eng.run("bad-search")
        assert ex.status == WorkflowStatus.FAILED


# ---------------------------------------------------------------------------
# TestMonday — workflow() public API
# ---------------------------------------------------------------------------

class TestMondayWorkflow:
    @pytest.fixture(autouse=True)
    def monday(self, tmp_path):
        from monday import Monday, MondayConfig
        defs = tmp_path / "workflows" / "definitions"
        defs.mkdir(parents=True)
        # Copy the real implement_function workflow for end-to-end test
        real_wf = (
            Path(__file__).parent.parent / "workflows" / "definitions" / "implement_function.yaml"
        )
        if real_wf.exists():
            (defs / "implement_function.yaml").write_text(real_wf.read_text())
        # Also write the minimal test workflow
        (defs / "test-wf.yaml").write_text(MINIMAL_YAML)
        self.tmp_path = tmp_path
        self.m = Monday(MondayConfig(project_root=tmp_path))

    def test_workflow_list_returns_workflows(self):
        r = self.m.workflow("list")
        assert r.success is True
        names = [wf["name"] for wf in r.data.get("workflows", [])]
        assert "test-wf" in names

    def test_workflow_list_count_matches(self):
        r = self.m.workflow("list")
        assert r.data["count"] == len(r.data["workflows"])

    def test_workflow_show_returns_steps(self):
        r = self.m.workflow("show", name="test-wf")
        assert r.success is True
        assert r.workflow_name == "test-wf"
        assert len(r.data["steps"]) == 1

    def test_workflow_show_returns_inputs(self):
        r = self.m.workflow("show", name="implement-function")
        if not r.success:
            pytest.skip("implement-function workflow not available")
        assert "function_name" in r.data["inputs"]

    def test_workflow_show_not_found(self):
        r = self.m.workflow("show", name="nonexistent")
        assert r.success is False
        assert "nonexistent" in r.message

    def test_workflow_show_requires_name(self):
        r = self.m.workflow("show")
        assert r.success is False
        assert "name is required" in r.message

    def test_workflow_run_completes(self):
        r = self.m.workflow(
            "run",
            name="test-wf",
            approval_handler=AUTO_APPROVE,
        )
        assert r.success is True
        assert r.status == "completed"
        assert r.execution_id != ""

    def test_workflow_run_not_found(self):
        r = self.m.workflow("run", name="nonexistent")
        assert r.success is False

    def test_workflow_run_requires_name(self):
        r = self.m.workflow("run")
        assert r.success is False
        assert "name is required" in r.message

    def test_workflow_unknown_action(self):
        r = self.m.workflow("invalid")
        assert r.success is False
        assert "invalid" in r.message

    def test_workflow_run_implement_function(self):
        r = self.m.workflow(
            "run",
            name="implement-function",
            inputs={"function_name": "parse_config", "component": "core"},
            approval_handler=AUTO_APPROVE,
        )
        if not r.success and "nonexistent" in r.message.lower():
            pytest.skip("implement-function workflow not available in test env")
        assert r.success is True
        assert r.status == "completed"
        assert r.workflow_name == "implement-function"
        steps = r.data.get("steps", [])
        completed_ids = [s["step_id"] for s in steps if s["status"] == "completed"]
        assert "research" in completed_ids
        assert "create-task" in completed_ids
        assert "approval-gate" in completed_ids
        assert "capture-pattern" in completed_ids
        assert "complete-task" in completed_ids


# ---------------------------------------------------------------------------
# TestCLI — workflow subcommand
# ---------------------------------------------------------------------------

class TestCLIWorkflow:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, capsys):
        defs = tmp_path / "workflows" / "definitions"
        defs.mkdir(parents=True)
        (defs / "test-wf.yaml").write_text(MINIMAL_YAML)
        self.tmp_path = tmp_path
        self.capsys = capsys

    def _run(self, *args: str) -> int:
        from monday.cli import main
        return main(["--project-root", str(self.tmp_path), *args]) or 0

    def test_workflow_list(self):
        rc = self._run("workflow", "list")
        out = self.capsys.readouterr().out
        assert rc == 0
        assert "test-wf" in out

    def test_workflow_list_empty(self, tmp_path):
        from monday.cli import main
        empty_root = tmp_path / "empty-project"
        empty_root.mkdir()
        rc = main(["--project-root", str(empty_root), "workflow", "list"]) or 0
        out = self.capsys.readouterr().out
        assert rc == 0
        assert "No workflows" in out

    def test_workflow_show(self):
        rc = self._run("workflow", "show", "test-wf")
        out = self.capsys.readouterr().out
        assert rc == 0
        assert "test-wf" in out
        assert "search" in out

    def test_workflow_show_not_found(self):
        rc = self._run("workflow", "show", "missing")
        err = self.capsys.readouterr().err
        assert rc == 1
        assert "Error" in err

    def test_workflow_run_yes_flag(self):
        rc = self._run("workflow", "run", "test-wf", "--yes")
        out = self.capsys.readouterr().out
        assert rc == 0
        assert "completed" in out.lower()

    def test_workflow_run_with_var(self):
        # Use test-wf which takes no required inputs; vars are extra context
        rc = self._run("workflow", "run", "test-wf", "--yes", "--var", "extra=value")
        assert rc == 0

    def test_workflow_run_bad_var_format(self):
        rc = self._run("workflow", "run", "test-wf", "--var", "noequals")
        err = self.capsys.readouterr().err
        assert rc == 1
        assert "KEY=VALUE" in err

    def test_workflow_help(self):
        import sys
        with pytest.raises(SystemExit) as exc_info:
            from monday.cli import main
            main(["--project-root", str(self.tmp_path), "workflow", "--help"])
        assert exc_info.value.code == 0
