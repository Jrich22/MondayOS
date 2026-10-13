"""Focused tests for the additive Monday.build API and ``monday build`` CLI."""
from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path
from types import ModuleType
from unittest import mock

from monday import BuildResponse, Monday, MondayConfig
from monday.cli import main


class _Job:
    def __init__(self, **overrides: object) -> None:
        self.payload: dict[str, object] = {
            "delivery_id": "delivery-test-1",
            "task_id": "TASK-0001",
            "status": "pr-open",
            "phase": "pull-request",
            "success": True,
            "branch": "codex/task-0001-delivery-test-1",
            "commit_sha": "abc123",
            "pr_url": "https://github.com/acme/mondayos/pull/7",
            "changed_files": ["monday/api.py"],
            "attempts": [{"number": 1, "status": "passed"}],
            "pr_state": "open",
            "checks_status": "pending",
            "merge_commit_sha": "",
            "pr_observed_at": "2026-10-13T00:00:00+00:00",
            "observation_success": True,
            "observation_message": "The pull request is open; checks are pending.",
            "task_reconciliation": "",
            "message": "Pull request opened.",
        }
        self.payload.update(overrides)

    def to_dict(self) -> dict[str, object]:
        return dict(self.payload)

    @property
    def project_name(self) -> str:
        """Mirror the real DeliveryJob attribute used for identity assertions."""
        return str(self.payload.get("project_name", ""))


class _Workflow:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.job = _Job()

    def run(self, **kwargs: object) -> _Job:
        self.calls.append(("run", kwargs))
        return self.job

    def get(self, delivery_id: str) -> _Job:
        self.calls.append(("get", {"delivery_id": delivery_id}))
        return self.job

    def observe(self, delivery_id: str) -> _Job:
        self.calls.append(("observe", {"delivery_id": delivery_id}))
        return self.job

    def history(self, **kwargs: object) -> list[object]:
        self.calls.append(("history", kwargs))
        return [self.job, {**self.job.to_dict(), "delivery_id": "delivery-test-2"}]

    def capabilities(self) -> dict[str, object]:
        self.calls.append(("capabilities", {}))
        return {"ready": True, "builder": {"available": True, "name": "codex"}}


def _monday(tmp_path: Path) -> Monday:
    return Monday(MondayConfig(project_root=tmp_path))


def _call_build(
    monday: Monday,
    workflow: _Workflow,
    action: str,
    **kwargs: object,
) -> BuildResponse:
    with mock.patch("delivery.workflow.DeliveryWorkflow", return_value=workflow):
        return monday.build(action, **kwargs)


def test_build_response_is_part_of_the_public_package() -> None:
    response = BuildResponse(action="capabilities", success=True)

    assert response.action == "capabilities"
    assert response.changed_files == []
    assert response.attempts == []
    assert response.project_name == ""
    assert response.repository == ""
    assert response.repo_root == ""
    assert response.pr_state == "unobserved"
    assert response.checks_status == "unobserved"
    assert response.merge_commit_sha == ""
    assert response.pr_observed_at == ""
    assert response.task_reconciliation == ""


def test_build_run_passes_identity_and_callbacks_without_invoking_real_tools(
    tmp_path: Path,
) -> None:
    workflow = _Workflow()
    progress = object()
    checkpoint = object()

    response = _call_build(
        _monday(tmp_path),
        workflow,
        "run",
        task_id="TASK-0001",
        delivery_id="delivery-test-1",
        progress_callback=progress,
        checkpoint_callback=checkpoint,
    )

    assert response.success is True
    assert response.delivery_id == "delivery-test-1"
    assert response.status == "pr-open"
    assert response.pr_url.endswith("/pull/7")
    assert response.changed_files == ["monday/api.py"]
    assert response.attempts == [{"number": 1, "status": "passed"}]
    assert workflow.calls == [(
        "run",
        {
            "task_id": "TASK-0001",
            "delivery_id": "delivery-test-1",
            "progress_callback": progress,
            "checkpoint_callback": checkpoint,
        },
    )]


def test_build_run_uses_job_outcome_as_response_success(tmp_path: Path) -> None:
    workflow = _Workflow()
    workflow.job = _Job(success=False, status="failed", phase="preflight", message="No reviewer")

    response = _call_build(_monday(tmp_path), workflow, "run", task_id="TASK-0001")

    assert response.success is False
    assert response.status == "failed"
    assert response.message == "No reviewer"


def test_build_get_reports_successful_lookup_even_for_failed_job(tmp_path: Path) -> None:
    workflow = _Workflow()
    workflow.job = _Job(success=False, status="failed")

    response = _call_build(
        _monday(tmp_path), workflow, "get", delivery_id="delivery-test-1"
    )

    assert response.success is True
    assert response.status == "failed"
    assert workflow.calls == [("get", {"delivery_id": "delivery-test-1"})]


def test_build_observe_surfaces_pr_lifecycle_and_observation_outcome(
    tmp_path: Path,
) -> None:
    workflow = _Workflow()

    response = _call_build(
        _monday(tmp_path), workflow, "observe", delivery_id="delivery-test-1"
    )

    assert response.success is True
    assert response.pr_state == "open"
    assert response.checks_status == "pending"
    assert response.pr_observed_at == "2026-10-13T00:00:00+00:00"
    assert response.message == "The pull request is open; checks are pending."
    assert workflow.calls == [("observe", {"delivery_id": "delivery-test-1"})]

    workflow.job = _Job(
        observation_success=False,
        observation_message="GitHub observation is temporarily unavailable.",
    )
    unavailable = _call_build(
        _monday(tmp_path), workflow, "observe", delivery_id="delivery-test-1"
    )
    assert unavailable.success is False
    assert "unavailable" in unavailable.message


def test_build_history_normalizes_objects_and_dicts(tmp_path: Path) -> None:
    workflow = _Workflow()

    response = _call_build(
        _monday(tmp_path), workflow, "history", task_id="TASK-0001", limit=5
    )

    assert response.success is True
    assert response.data["count"] == 2
    assert [job["delivery_id"] for job in response.data["jobs"]] == [
        "delivery-test-1",
        "delivery-test-2",
    ]
    assert workflow.calls == [("history", {"task_id": "TASK-0001", "limit": 5})]


def test_build_capabilities_are_returned_unchanged(tmp_path: Path) -> None:
    workflow = _Workflow()

    response = _call_build(_monday(tmp_path), workflow, "capabilities")

    assert response.success is True
    assert response.data["ready"] is True
    assert response.data["builder"]["name"] == "codex"


def test_build_constructs_delivery_workflow_lazily_without_policy_injection(
    tmp_path: Path,
) -> None:
    constructed: dict[str, object] = {}
    workflow = _Workflow()

    class DeliveryWorkflow:
        def __new__(cls, **kwargs: object) -> _Workflow:
            constructed.update(kwargs)
            return workflow

    fake_module = ModuleType("delivery.workflow")
    fake_module.DeliveryWorkflow = DeliveryWorkflow  # type: ignore[attr-defined]

    with mock.patch.dict(sys.modules, {"delivery.workflow": fake_module}):
        response = _monday(tmp_path).build("capabilities")

    assert response.success is True
    assert constructed == {
        "project_root": tmp_path,
        "monday": mock.ANY,
    }
    assert isinstance(constructed["monday"], Monday)


def test_build_project_selector_pins_registered_target_in_workflow_constructor(
    tmp_path: Path,
) -> None:
    controller = tmp_path / "controller"
    target = tmp_path / "weather-portal"
    target.mkdir(parents=True)
    monday = _monday(controller)
    registration = monday.project(
        "register",
        name="Weather Portal",
        path=str(target),
        delivery_repository="Acme/Weather-Portal",
    )
    assert registration.success is True

    workflow = _Workflow()
    constructor = mock.Mock(return_value=workflow)
    with mock.patch("delivery.workflow.DeliveryWorkflow", constructor):
        response = monday.build(
            "run",
            task_id="TASK-0001",
            project="Weather Portal",
        )

    target_stat = target.stat()
    assert response.success is True
    constructor.assert_called_once_with(
        project_root=controller,
        monday=monday,
        repository_root=target.resolve(),
        project_name="weather-portal",
        expected_repository="acme/weather-portal",
        expected_root_identity=(target_stat.st_dev, target_stat.st_ino),
    )
    assert workflow.calls == [(
        "run",
        {
            "task_id": "TASK-0001",
            "delivery_id": None,
            "progress_callback": None,
            "checkpoint_callback": None,
        },
    )]


def test_build_observe_reconstructs_the_registered_external_target(
    tmp_path: Path,
) -> None:
    controller = tmp_path / "controller"
    target = tmp_path / "weather-portal"
    target.mkdir(parents=True)
    monday = _monday(controller)
    assert monday.project(
        "register",
        name="Weather Portal",
        path=str(target),
        delivery_repository="Acme/Weather-Portal",
    ).success
    workflow = _Workflow()
    workflow.job = _Job(project_name="weather-portal")
    constructor = mock.Mock(return_value=workflow)

    with mock.patch("delivery.workflow.DeliveryWorkflow", constructor):
        response = monday.build(
            "observe",
            delivery_id="delivery-test-1",
            project="Weather Portal",
        )

    target_stat = target.stat()
    assert response.success is True
    constructor.assert_called_once_with(
        project_root=controller,
        monday=monday,
        repository_root=target.resolve(),
        project_name="weather-portal",
        expected_repository="acme/weather-portal",
        expected_root_identity=(target_stat.st_dev, target_stat.st_ino),
    )
    assert workflow.calls == [("observe", {"delivery_id": "delivery-test-1"})]


def test_build_refuses_analysis_only_project_before_constructing_workflow(
    tmp_path: Path,
) -> None:
    controller = tmp_path / "controller"
    target = tmp_path / "analysis-only"
    target.mkdir(parents=True)
    monday = _monday(controller)
    assert monday.project("register", name="analysis", path=str(target)).success

    with mock.patch("delivery.workflow.DeliveryWorkflow") as constructor:
        response = monday.build("run", task_id="TASK-0001", project="analysis")

    assert response.success is False
    assert "analysis only" in response.message
    constructor.assert_not_called()


def test_build_missing_project_is_a_typed_failure_before_workflow_creation(
    tmp_path: Path,
) -> None:
    with mock.patch("delivery.workflow.DeliveryWorkflow") as constructor:
        response = _monday(tmp_path).build(
            "run", task_id="TASK-0001", project="not-registered"
        )

    assert response.success is False
    assert "not registered" in response.message
    constructor.assert_not_called()


def test_build_refuses_project_whose_registered_directory_vanished(
    tmp_path: Path,
) -> None:
    controller = tmp_path / "controller"
    target = tmp_path / "vanished"
    target.mkdir(parents=True)
    monday = _monday(controller)
    assert monday.project(
        "register",
        name="vanished",
        path=str(target),
        delivery_repository="acme/vanished",
    ).success
    target.rmdir()

    with mock.patch("delivery.workflow.DeliveryWorkflow") as constructor:
        response = monday.build("capabilities", project="vanished")

    assert response.success is False
    assert "no longer exists" in response.message
    constructor.assert_not_called()


def test_build_history_canonicalizes_project_filter_without_retargeting_workflow(
    tmp_path: Path,
) -> None:
    workflow = _Workflow()

    response = _call_build(
        _monday(tmp_path),
        workflow,
        "history",
        project="Weather Portal",
        task_id="TASK-0001",
        limit=4,
    )

    assert response.success is True
    assert workflow.calls == [(
        "history",
        {
            "task_id": "TASK-0001",
            "limit": 4,
            "project_name": "weather-portal",
        },
    )]


def test_build_self_project_selector_maps_to_controller_records(tmp_path: Path) -> None:
    workflow = _Workflow()

    history = _call_build(
        _monday(tmp_path),
        workflow,
        "history",
        project="MondayOS",
    )
    lookup = _call_build(
        _monday(tmp_path),
        workflow,
        "get",
        delivery_id="delivery-test-1",
        project="mondayos",
    )

    assert history.success is True
    assert lookup.success is True
    assert workflow.calls == [
        ("history", {"task_id": None, "limit": 20, "project_name": ""}),
        ("get", {"delivery_id": "delivery-test-1"}),
    ]


def test_build_get_project_selector_is_a_durable_identity_assertion(
    tmp_path: Path,
) -> None:
    workflow = _Workflow()
    workflow.job = _Job(project_name="weather-portal")

    matching = _call_build(
        _monday(tmp_path),
        workflow,
        "get",
        delivery_id="delivery-test-1",
        project="Weather Portal",
    )
    mismatched = _call_build(
        _monday(tmp_path),
        workflow,
        "get",
        delivery_id="delivery-test-1",
        project="another-project",
    )

    assert matching.success is True
    assert matching.project_name == "weather-portal"
    assert mismatched.success is False
    assert "does not belong" in mismatched.message


def test_build_response_surfaces_target_identity_metadata(tmp_path: Path) -> None:
    workflow = _Workflow()
    workflow.job = _Job(
        project_name="weather-portal",
        repository="acme/weather-portal",
        repo_root="/srv/repos/weather-portal",
    )

    response = _call_build(_monday(tmp_path), workflow, "run", task_id="TASK-0001")

    assert response.project_name == "weather-portal"
    assert response.repository == "acme/weather-portal"
    assert response.repo_root == "/srv/repos/weather-portal"


def test_task_creation_binds_registered_project_to_canonical_identity(
    tmp_path: Path,
) -> None:
    controller = tmp_path / "controller"
    target = tmp_path / "target"
    target.mkdir(parents=True)
    monday = _monday(controller)
    assert monday.project(
        "register", name="Weather Portal", path=str(target)
    ).success

    response = monday.task(
        "create",
        title="Add forecast",
        objective="Show a seven-day forecast.",
        project="Weather Portal",
    )

    assert response.success is True
    assert response.data["project"] == "weather-portal"


def test_task_creation_accepts_a_registered_unicode_analysis_project(
    tmp_path: Path,
) -> None:
    controller = tmp_path / "controller"
    target = tmp_path / "target"
    target.mkdir(parents=True)
    monday = _monday(controller)
    assert monday.project("register", name="Café", path=str(target)).success

    response = monday.task(
        "create",
        title="Analyze feedback",
        objective="Summarize the feedback.",
        project="CAFÉ",
    )

    assert response.success is True
    assert response.data["project"] == "café"


def test_task_creation_rejects_unknown_project_without_persisting_task(
    tmp_path: Path,
) -> None:
    monday = _monday(tmp_path)

    response = monday.task(
        "create",
        title="Add forecast",
        objective="Show a seven-day forecast.",
        project="not-registered",
    )

    assert response.success is False
    assert "Invalid task project" in response.message
    assert "not registered" in response.message
    assert monday.task("list").data["count"] == 0


def test_build_unknown_action_and_malformed_results_fail_as_responses(tmp_path: Path) -> None:
    unknown = _call_build(_monday(tmp_path), _Workflow(), "merge")
    assert unknown.success is False
    assert "Valid actions" in unknown.message

    workflow = _Workflow()
    workflow.job = object()  # type: ignore[assignment]
    malformed = _call_build(_monday(tmp_path), workflow, "run", task_id="TASK-0001")
    assert malformed.success is False
    assert "unsupported result" in malformed.message


def test_build_constructor_runtime_failure_is_a_typed_response(tmp_path: Path) -> None:
    with mock.patch(
        "delivery.workflow.DeliveryWorkflow",
        side_effect=RuntimeError("Git is unavailable"),
    ):
        response = _monday(tmp_path).build("capabilities")

    assert response.success is False
    assert response.action == "capabilities"
    assert response.message == "Git is unavailable"


def _cli(*argv: str) -> tuple[int, str]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        code = main(list(argv))
    return code, output.getvalue()


def _response(*, success: bool = True, status: str = "pr-open") -> BuildResponse:
    job = _Job(success=success, status=status).to_dict()
    return BuildResponse(
        action="run",
        success=success,
        message=str(job["message"]),
        delivery_id=str(job["delivery_id"]),
        task_id=str(job["task_id"]),
        status=status,
        phase=str(job["phase"]),
        branch=str(job["branch"]),
        commit_sha=str(job["commit_sha"]),
        pr_url=str(job["pr_url"]),
        changed_files=list(job["changed_files"]),  # type: ignore[arg-type]
        attempts=list(job["attempts"]),  # type: ignore[arg-type]
        data=job,
    )


def test_build_cli_run_forwards_reserved_id_and_prints_json(tmp_path: Path) -> None:
    with mock.patch.object(Monday, "build", return_value=_response()) as build:
        code, output = _cli(
            "--project-root", str(tmp_path), "build", "run", "TASK-0001",
            "--id", "delivery-test-1", "--json",
        )

    assert code == 0
    assert json.loads(output)["pr_url"].endswith("/pull/7")
    build.assert_called_once_with(
        "run", task_id="TASK-0001", delivery_id="delivery-test-1"
    )


def test_build_cli_forwards_explicit_project_to_every_selector_aware_action(
    tmp_path: Path,
) -> None:
    run = _response()
    get = _response()
    get.action = "get"
    observe = _response()
    observe.action = "observe"
    observe.pr_state = "open"
    observe.checks_status = "pending"
    history = BuildResponse(action="history", success=True, data={"jobs": [], "count": 0})
    capabilities = BuildResponse(
        action="capabilities", success=True, data={"ready": True}
    )

    with mock.patch.object(
        Monday,
        "build",
        side_effect=[run, get, observe, history, capabilities],
    ) as build:
        assert _cli(
            "--project-root",
            str(tmp_path),
            "build",
            "run",
            "TASK-0001",
            "--project",
            "weather-portal",
        )[0] == 0
        assert _cli(
            "--project-root",
            str(tmp_path),
            "build",
            "get",
            "delivery-test-1",
            "--project",
            "weather-portal",
        )[0] == 0
        assert _cli(
            "--project-root",
            str(tmp_path),
            "build",
            "observe",
            "delivery-test-1",
            "--project",
            "weather-portal",
        )[0] == 0
        assert _cli(
            "--project-root",
            str(tmp_path),
            "build",
            "history",
            "--project",
            "weather-portal",
        )[0] == 0
        assert _cli(
            "--project-root",
            str(tmp_path),
            "build",
            "capabilities",
            "--project",
            "weather-portal",
        )[0] == 0

    assert build.call_args_list == [
        mock.call(
            "run",
            task_id="TASK-0001",
            delivery_id=None,
            project="weather-portal",
        ),
        mock.call(
            "get",
            delivery_id="delivery-test-1",
            project="weather-portal",
        ),
        mock.call(
            "observe",
            delivery_id="delivery-test-1",
            project="weather-portal",
        ),
        mock.call(
            "history",
            task_id=None,
            limit=20,
            project="weather-portal",
        ),
        mock.call("capabilities", project="weather-portal"),
    ]


def test_project_and_task_cli_persist_delivery_enrollment_and_binding(
    tmp_path: Path,
) -> None:
    controller = tmp_path / "controller"
    target = tmp_path / "weather-portal"
    target.mkdir(parents=True)

    register_code, register_output = _cli(
        "--project-root",
        str(controller),
        "project",
        "register",
        "Weather Portal",
        str(target),
        "--delivery-repository",
        "Acme/Weather-Portal",
    )
    task_code, _ = _cli(
        "--project-root",
        str(controller),
        "task",
        "create",
        "--title",
        "Add forecast",
        "--objective",
        "Show a seven-day forecast.",
        "--project",
        "Weather Portal",
    )

    monday = _monday(controller)
    registered = monday.project("get", name="Weather Portal")
    tasks = monday.task("list")
    assert register_code == 0
    assert "acme/weather-portal" in register_output
    assert registered.data["delivery_repository"] == "acme/weather-portal"
    assert task_code == 0
    assert tasks.data["count"] == 1
    assert tasks.data["tasks"][0]["project"] == "weather-portal"
    assert (
        tasks.data["tasks"][0]["project_delivery_repository"]
        == "acme/weather-portal"
    )
    target_stat = target.stat()
    assert tasks.data["tasks"][0]["project_source_device"] == target_stat.st_dev
    assert tasks.data["tasks"][0]["project_source_inode"] == target_stat.st_ino


def test_build_cli_get_renders_pull_request(tmp_path: Path) -> None:
    response = _response()
    response.action = "get"
    with mock.patch.object(Monday, "build", return_value=response) as build:
        code, output = _cli(
            "--project-root", str(tmp_path), "build", "get", "delivery-test-1"
        )

    assert code == 0
    assert "BUILD — delivery-test-1" in output
    assert "https://github.com/acme/mondayos/pull/7" in output
    build.assert_called_once_with("get", delivery_id="delivery-test-1")


def test_build_cli_get_json_separates_lookup_success_from_failed_job(
    tmp_path: Path,
) -> None:
    response = _response(success=False, status="failed")
    response.action = "get"
    response.success = True
    response.data["success"] = False
    with mock.patch.object(Monday, "build", return_value=response):
        code, output = _cli(
            "--project-root", str(tmp_path), "build", "get", "delivery-test-1", "--json"
        )

    payload = json.loads(output)
    assert code == 0
    assert payload["action"] == "get"
    assert payload["success"] is True
    assert payload["status"] == "failed"
    assert payload["data"]["success"] is False


def test_build_cli_history_and_capabilities(tmp_path: Path) -> None:
    history = BuildResponse(
        action="history",
        success=True,
        data={"jobs": [_Job().to_dict()], "count": 1},
    )
    capabilities = BuildResponse(
        action="capabilities",
        success=True,
        data={"ready": True, "reviewer": {"available": True}},
    )
    with mock.patch.object(Monday, "build", side_effect=[history, capabilities]) as build:
        history_code, history_output = _cli(
            "--project-root", str(tmp_path), "build", "history",
            "--task", "TASK-0001", "--limit", "3",
        )
        capability_code, capability_output = _cli(
            "--project-root", str(tmp_path), "build", "capabilities"
        )

    assert history_code == 0
    assert "Builds (1)" in history_output
    assert "delivery-test-1" in history_output
    assert capability_code == 0
    assert "Build capabilities" in capability_output
    assert "reviewer" in capability_output
    assert build.call_args_list == [
        mock.call("history", task_id="TASK-0001", limit=3),
        mock.call("capabilities"),
    ]


def test_build_cli_history_and_capabilities_json_keep_response_envelope(
    tmp_path: Path,
) -> None:
    history = BuildResponse(
        action="history",
        success=True,
        message="1 build(s)",
        data={"jobs": [_Job().to_dict()], "count": 1},
    )
    capabilities = BuildResponse(
        action="capabilities",
        success=True,
        message="Build runtime capabilities reported.",
        data={"ready": False, "reviewer": {"available": True}},
    )
    with mock.patch.object(Monday, "build", side_effect=[history, capabilities]):
        history_code, history_output = _cli(
            "--project-root", str(tmp_path), "build", "history", "--json"
        )
        capability_code, capability_output = _cli(
            "--project-root", str(tmp_path), "build", "capabilities", "--json"
        )

    history_payload = json.loads(history_output)
    capabilities_payload = json.loads(capability_output)
    assert history_code == 0
    assert history_payload["action"] == "history"
    assert history_payload["success"] is True
    assert history_payload["data"]["count"] == 1
    assert capability_code == 1
    assert capabilities_payload["action"] == "capabilities"
    assert capabilities_payload["success"] is True
    assert capabilities_payload["data"]["ready"] is False


def test_build_cli_surfaces_preflight_failure_and_nonzero_exit(tmp_path: Path) -> None:
    failure = BuildResponse(
        action="run",
        success=False,
        message="ChatGPT reviewer is unavailable.",
    )
    with mock.patch.object(Monday, "build", return_value=failure):
        code, output = _cli(
            "--project-root", str(tmp_path), "build", "run", "TASK-0001"
        )

    assert code == 1
    assert "ChatGPT reviewer is unavailable" in output
