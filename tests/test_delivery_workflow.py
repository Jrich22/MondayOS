"""End-to-end tests for the artifact-bound delivery state machine."""

from __future__ import annotations

import base64
import errno
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

import tasks.manager as task_manager_module
from delivery.backends import PatchProposal, ReviewDecision, ToolAvailability
from delivery.git import CommandResult, GitCommandError
from delivery.github import PullRequestResult
from delivery.security import ArtifactSecurityError
from delivery.types import DeliveryJob
from delivery.validation import MondayValidationRunner, ValidationResult
from delivery.workflow import DeliveryWorkflow, _objective
from tasks import TaskManager, TaskPriority, TaskStatus, TaskType


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "owner" / "project.git"
    remote.parent.mkdir()
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(remote))
    root = tmp_path / "controller"
    root.mkdir()
    _git(root, "init", "--initial-branch=main")
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / ".gitignore").write_text("logs/\n", encoding="utf-8")
    _git(root, "add", "app.py", ".gitignore")
    _git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        "base",
    )
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "-u", "origin", "main")
    # The production adapter intentionally accepts github.com only. Tests keep
    # the transport local and inject this harmless identity seam.
    return root, remote


class _Monday:
    def __init__(
        self,
        *,
        title: str = "Change the value",
        objective: str = "Set VALUE to the reviewed target.",
        acceptance_criteria: list[str] | None = None,
        project: str = "",
        project_delivery_repository: str = "",
        project_source_device: int | None = None,
        project_source_inode: int | None = None,
    ) -> None:
        self.status = "backlog"
        self.calls: list[str] = []
        self.title = title
        self.objective = objective
        self.acceptance_criteria = acceptance_criteria or [
            "app.py contains the target value"
        ]
        self.project = project
        self.project_delivery_repository = project_delivery_repository
        self.project_source_device = project_source_device
        self.project_source_inode = project_source_inode
        self.updated_sequence = 0
        self.updated = "2026-10-13T00:00:00+00:00"

    def _touch(self) -> None:
        self.updated_sequence += 1
        self.updated = f"2026-10-13T00:00:{self.updated_sequence:02d}+00:00"

    def task(self, action: str, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(action)
        if action == "get":
            return SimpleNamespace(
                success=True,
                message="found",
                data={
                    "id": kwargs["task_id"],
                    "title": self.title,
                    "objective": self.objective,
                    "acceptance_criteria": self.acceptance_criteria,
                    "project": self.project,
                    "project_delivery_repository": self.project_delivery_repository,
                    "project_source_device": self.project_source_device,
                    "project_source_inode": self.project_source_inode,
                    "status": self.status,
                    "updated": self.updated,
                },
            )
        if action == "start":
            self.status = "in-progress"
            self._touch()
            return SimpleNamespace(
                success=True,
                message="started",
                data={"status": self.status, "updated": self.updated},
            )
        if action == "review":
            self.status = "review"
            self._touch()
            return SimpleNamespace(
                success=True,
                message="review",
                data={"status": self.status, "updated": self.updated},
            )
        if action == "complete":
            self.status = "completed"
            self._touch()
            return SimpleNamespace(
                success=True,
                message="completed",
                data={"status": self.status, "updated": self.updated},
            )
        raise AssertionError(action)


class _Builder:
    name = "fake-builder"

    def __init__(
        self,
        patches: list[str],
        *,
        name: str = "fake-builder",
        summary: str = "Changed VALUE",
    ) -> None:
        self.patches = patches
        self.calls = 0
        self.name = name
        self.summary = summary
        self.requests: list[dict[str, Any]] = []

    def availability(self) -> ToolAvailability:
        return ToolAvailability(True, self.name, "ready")

    def propose(self, **kwargs: Any) -> PatchProposal:
        self.requests.append(dict(kwargs))
        patch = self.patches[min(self.calls, len(self.patches) - 1)]
        self.calls += 1
        return PatchProposal(True, self.name, patch=patch, summary=self.summary)


class _FailingBuilder:
    def __init__(self, *, name: str, message: str, raises: bool = True) -> None:
        self.name = name
        self.message = message
        self.raises = raises
        self.calls = 0

    def availability(self) -> ToolAvailability:
        return ToolAvailability(True, self.name, "ready")

    def propose(self, **_: Any) -> PatchProposal:
        self.calls += 1
        if self.raises:
            raise RuntimeError(self.message)
        return PatchProposal(False, self.name, message=self.message)


class _Reviewer:
    name = "fake-chatgpt-reviewer"

    def __init__(self, decisions: list[ReviewDecision], *, mutate: bool = False) -> None:
        self.decisions = decisions
        self.mutate = mutate
        self.calls = 0

    def availability(self) -> ToolAvailability:
        return ToolAvailability(True, self.name, "ready")

    def review(self, **kwargs: Any) -> ReviewDecision:
        if self.mutate:
            (Path(kwargs["worktree"]) / "app.py").write_text("VALUE = 999\n", encoding="utf-8")
        decision = self.decisions[min(self.calls, len(self.decisions) - 1)]
        self.calls += 1
        return decision


class _UnavailableReviewer(_Reviewer):
    def availability(self) -> ToolAvailability:
        return ToolAvailability(False, self.name, "ChatGPT login is unavailable")


class _Validator:
    def __init__(self, outcomes: list[bool], *, output: str | None = None) -> None:
        self.outcomes = outcomes
        self.output = output
        self.calls = 0
        self.requests: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> list[ValidationResult]:
        self.requests.append(dict(kwargs))
        success = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        return [
            ValidationResult(
                name="tests",
                argv=["pytest", "-q"],
                success=success,
                returncode=0 if success else 1,
                output=self.output
                if self.output is not None
                else ("passed" if success else "expected VALUE to change"),
            )
        ]


class _GitHub:
    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[dict[str, Any]] = []

    def availability(self) -> ToolAvailability:
        return ToolAvailability(True, "github", "ready")

    def ensure_pull_request(self, **kwargs: Any) -> PullRequestResult:
        self.calls += 1
        self.requests.append(dict(kwargs))
        return PullRequestResult(
            True,
            url="https://github.com/owner/project/pull/12",
            number=12,
            message="opened",
        )


class _ObserverGitHub(_GitHub):
    def __init__(
        self,
        *,
        lifecycle: str = "open",
        checks: str = "pending",
        success: bool = True,
        merge_commit_sha: str = "",
        message: str = "observed",
        check_counts: dict[str, int] | None = None,
    ) -> None:
        super().__init__()
        self.lifecycle = lifecycle
        self.checks = checks
        self.observation_success = success
        self.merge_commit_sha = merge_commit_sha
        self.observation_message = message
        self.check_counts = check_counts

    def observe_pull_request(self, **kwargs: Any) -> SimpleNamespace:
        self.calls += 1
        self.requests.append(dict(kwargs))
        check_counts = self.check_counts
        if check_counts is None:
            check_counts = {
                "total": 0 if self.checks == "no-checks" else 1,
                "passed": 1 if self.checks == "passed" else 0,
                "failed": 1 if self.checks == "failed" else 0,
                "pending": 1 if self.checks == "pending" else 0,
            }
        return SimpleNamespace(
            success=self.observation_success,
            lifecycle=self.lifecycle,
            checks=self.checks,
            check_counts=check_counts,
            merge_commit_sha=self.merge_commit_sha,
            message=self.observation_message,
        )


def _patch(old: int, new: int) -> str:
    return (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1 +1 @@\n"
        f"-VALUE = {old}\n"
        f"+VALUE = {new}\n"
    )


def _fixture(*parts: str) -> str:
    """Assemble fake credentials without resembling one in the repository."""
    return "".join(parts)


def _failed_add_delete_patch() -> str:
    return (
        "diff --git a/app.py b/app.py\n"
        "deleted file mode 100644\n"
        "--- a/app.py\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-VALUE = 1\n"
        "diff --git a/failed.py b/failed.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/failed.py\n"
        "@@ -0,0 +1 @@\n"
        "+FAILED = True\n"
    )


def _pass(summary: str = "Approved") -> ReviewDecision:
    return ReviewDecision("pass", "high", summary)


def _workflow(
    root: Path,
    *,
    builder: Any,
    reviewer: _Reviewer,
    validator: _Validator,
    github: _GitHub,
    monday: _Monday | None = None,
    max_attempts: int = 3,
) -> tuple[DeliveryWorkflow, _Monday]:
    from delivery.git import GitRepository

    if not (root / "tasks" / "active" / "TASK-0001.md").exists():
        task_manager = TaskManager(root)
        for number in range(1, 11):
            task = task_manager.create(
                title=f"Delivery fixture {number}",
                task_type=TaskType.FEATURE,
                priority=TaskPriority.P2,
                objective="Exercise the delivery workflow.",
                created_by="human:test",
            )
            assert task.id == f"TASK-{number:04d}"
    controller = monday or _Monday()
    repository = GitRepository(
        root,
        trusted_root=root,
        runtime_root=root / "logs" / "delivery",
    )
    # Preserve all real Git behavior while replacing only the github.com URL
    # interpretation; the test transport is a local bare repository.
    repository.github_slug = lambda remote="origin": "owner/project"  # type: ignore[method-assign]
    return (
        DeliveryWorkflow(
            monday=controller,
            project_root=root,
            builders=list(builder) if isinstance(builder, (list, tuple)) else [builder],
            reviewer=reviewer,
            validator=validator,
            github=github,
            repository=repository,
            max_attempts=max_attempts,
        ),
        controller,
    )


def _task_revision(root: Path, task_id: str = "TASK-0001") -> str:
    return TaskManager(root).revision(task_id)


def _persist_delivery(
    workflow: DeliveryWorkflow,
    *,
    delivery_id: str,
    status: str,
    phase: str,
) -> DeliveryJob:
    job = DeliveryJob(
        delivery_id=delivery_id,
        task_id="TASK-0001",
        objective="Set VALUE to the reviewed target.",
        repo_root=str(workflow.project_root),
        status=status,
        phase=phase,
        created_at="2026-10-03T00:00:00+00:00",
        updated_at="2026-10-03T00:00:00+00:00",
    )
    assert workflow.store.reserve(job) is None
    return job


def _persist_owned_stale_delivery(
    workflow: DeliveryWorkflow,
    *,
    delivery_id: str,
    phase: str,
    local_commit: bool = False,
) -> DeliveryJob:
    """Persist a running delivery backed by a real controller-owned worktree."""
    task_response = workflow.monday.task("get", task_id="TASK-0001")
    assert getattr(task_response, "success", False)
    task_data = dict(getattr(task_response, "data", {}) or {})
    base = workflow.repository.resolve_base()
    worktree = workflow.repository.create_worktree("TASK-0001", base)
    commit_sha = ""
    if local_commit:
        (worktree.path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        workflow.repository.stage(worktree, ["app.py"])
        staged_diff = workflow.repository.staged_diff(worktree)
        commit_sha = workflow.repository.commit(
            worktree,
            ["app.py"],
            "TASK-0001: interrupted delivery fixture",
            expected_diff_sha256=hashlib.sha256(
                staged_diff.encode("utf-8")
            ).hexdigest(),
        )
    job = DeliveryJob(
        delivery_id=delivery_id,
        task_id="TASK-0001",
        objective=_objective("TASK-0001", task_data),
        repo_root=str(workflow.repository_root),
        project_name=workflow.project_name,
        expected_repository=workflow.expected_repository,
        root_device=workflow.root_device,
        root_inode=workflow.root_inode,
        status="running",
        phase=phase,
        base_ref=base.ref,
        base_branch=base.branch,
        base_sha=base.sha,
        repository="owner/project",
        branch=worktree.branch,
        worktree=str(worktree.path),
        worktree_owner_token=worktree.owner_token,
        commit_sha=commit_sha,
        created_at="2026-10-15T00:00:00+00:00",
        updated_at="2026-10-15T00:00:00+00:00",
    )
    assert workflow.store.reserve(job) is None
    return job


def _safe_retry_predecessor(
    workflow: DeliveryWorkflow,
    *,
    delivery_id: str,
    predecessor_delivery_id: str = "",
    retry_root_delivery_id: str = "",
    retry_attempt: int = 0,
) -> DeliveryJob:
    """Persist and reconcile one owned, provably unpublished predecessor."""
    stale = _persist_owned_stale_delivery(
        workflow,
        delivery_id=delivery_id,
        phase="reviewing",
    )
    stale.predecessor_delivery_id = predecessor_delivery_id
    stale.retry_root_delivery_id = retry_root_delivery_id
    stale.retry_attempt = retry_attempt
    workflow.store.persist(stale)
    recovered = workflow.run(
        task_id=stale.task_id,
        delivery_id=delivery_id,
        predecessor_delivery_id=predecessor_delivery_id or None,
        retry_root_delivery_id=retry_root_delivery_id or None,
        retry_attempt=retry_attempt,
    )
    assert recovered.status == "interrupted"
    assert recovered.failure_class == "transient"
    return recovered


def _permanent_pushing_interruption(
    workflow: DeliveryWorkflow,
    *,
    delivery_id: str,
) -> DeliveryJob:
    stale = _persist_owned_stale_delivery(
        workflow,
        delivery_id=delivery_id,
        phase="pushing",
        local_commit=True,
    )
    stale.artifact_sha256 = "a" * 64
    stale.reviewed_artifact_sha256 = stale.artifact_sha256
    workflow.store.persist(stale)
    recovered = workflow.run(
        task_id=stale.task_id,
        delivery_id=delivery_id,
    )
    assert recovered.status == "interrupted"
    assert recovered.failure_class == "permanent"
    assert recovered.interrupted_from_phase == "pushing"
    return recovered


def _miss_initial_delivery_lookup(workflow: DeliveryWorkflow) -> Any:
    """Model a contender that checked before another worker reserved the ID."""
    original = workflow._existing_job
    calls = 0

    def lookup(delivery_id: str) -> DeliveryJob | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        return original(delivery_id)

    return mock.patch.object(workflow, "_existing_job", side_effect=lookup)


def _observable_delivery(workflow: DeliveryWorkflow, root: Path) -> DeliveryJob:
    metadata = root.resolve().stat()
    task = workflow.monday.task("get", task_id="TASK-0001")
    assert getattr(task, "success", False)
    task_data = dict(getattr(task, "data", {}) or {})
    job = DeliveryJob(
        delivery_id="delivery-observe-1",
        task_id="TASK-0001",
        objective=_objective("TASK-0001", task_data),
        repo_root=str(root.resolve()),
        status="pr-open",
        phase="completed",
        success=True,
        base_ref="refs/remotes/origin/main",
        base_branch="main",
        base_sha="1" * 40,
        project_name=workflow.project_name,
        expected_repository=workflow.expected_repository,
        repository=workflow.expected_repository or "owner/project",
        branch="codex/task-0001-delivery-observe-1",
        artifact_sha256="2" * 64,
        reviewed_artifact_sha256="2" * 64,
        commit_sha="3" * 40,
        pushed=True,
        pr_number=12,
        pr_url="https://github.com/owner/project/pull/12",
        root_device=metadata.st_dev,
        root_inode=metadata.st_ino,
        created_at="2026-10-13T00:00:00+00:00",
        updated_at="2026-10-13T00:00:00+00:00",
    )
    assert workflow.store.reserve(job) is None
    return job


def test_observe_records_exact_open_pr_and_bounded_check_summary(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    github = _ObserverGitHub(lifecycle="open", checks="pending")
    monday = _Monday()
    monday.status = "review"
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
        monday=monday,
    )
    original = _observable_delivery(workflow, root)

    observed = workflow.observe(original.delivery_id)

    assert observed.observation_success is True
    assert observed.pr_state == "open"
    assert observed.checks_status == "pending"
    assert observed.check_counts == {
        "total": 1,
        "passed": 0,
        "failed": 0,
        "pending": 1,
    }
    assert observed.task_reconciliation == ""
    assert monday.status == "review"
    assert github.requests == [{
        "repo_root": root.resolve(),
        "repository": "owner/project",
        "base": "main",
        "branch": "codex/task-0001-delivery-observe-1",
        "number": 12,
        "url": "https://github.com/owner/project/pull/12",
        "expected_sha": "3" * 40,
    }]


def test_observe_open_pr_repairs_pending_review_without_github_mutation(
    tmp_path: Path,
) -> None:
    class _ObserveOnlyGitHub(_ObserverGitHub):
        def ensure_pull_request(self, **_: Any) -> PullRequestResult:
            raise AssertionError("observation must not publish or update a pull request")

    root, _ = _repository(tmp_path)
    monday = _Monday()
    monday.status = "in-progress"
    builder = _Builder([_patch(1, 9)])
    reviewer = _Reviewer([_pass("must not run")])
    validator = _Validator([True])
    github = _ObserveOnlyGitHub(lifecycle="open", checks="pending")
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
        monday=monday,
    )
    job = _observable_delivery(workflow, root)
    job.review_reconciliation = "review-pending"
    job.review_expected_task_revision = _task_revision(root)
    workflow.store.persist(job)

    observed = workflow.observe(job.delivery_id)

    assert observed.observation_success is True
    assert observed.pr_state == "open"
    assert observed.review_reconciliation == "reviewed"
    assert observed.review_reconciled_at
    assert monday.status == "review"
    assert monday.calls.count("review") == 1
    assert github.calls == 1
    assert builder.calls == reviewer.calls == validator.calls == 0
    durable = workflow.get(job.delivery_id)
    assert durable.review_reconciliation == "reviewed"
    assert durable.review_reconciled_at == observed.review_reconciled_at


def test_observe_verified_merge_completes_task_exactly_once(tmp_path: Path) -> None:
    root, _ = _repository(tmp_path)
    github = _ObserverGitHub(
        lifecycle="merged",
        checks="passed",
        merge_commit_sha="4" * 40,
    )
    monday = _Monday()
    monday.status = "review"
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
        monday=monday,
    )
    job = _observable_delivery(workflow, root)

    first = workflow.observe(job.delivery_id)
    second = workflow.observe(job.delivery_id)

    assert first.pr_state == "merged"
    assert first.merge_commit_sha == "4" * 40
    assert first.task_reconciliation == "completed"
    assert second.task_reconciliation == "already-completed"
    assert monday.status == "completed"
    assert monday.calls.count("complete") == 1


@pytest.mark.parametrize("lifecycle", ["merged", "closed", "diverged"])
@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("title", "A different reviewed outcome"),
        ("objective", "Implement a different specification."),
        ("acceptance_criteria", ["a different result is required"]),
    ],
)
def test_observe_terminal_pr_refuses_changed_task_specification(
    tmp_path: Path,
    lifecycle: str,
    field: str,
    replacement: object,
) -> None:
    root, _ = _repository(tmp_path)
    github = _ObserverGitHub(
        lifecycle=lifecycle,
        checks="passed" if lifecycle == "merged" else "failed",
        merge_commit_sha="4" * 40 if lifecycle == "merged" else "",
    )
    monday = _Monday()
    monday.status = "review"
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
        monday=monday,
    )
    job = _observable_delivery(workflow, root)
    setattr(monday, field, replacement)

    observed = workflow.observe(job.delivery_id)

    assert observed.pr_state == lifecycle
    assert observed.task_reconciliation == "state-changed"
    assert observed.task_reconciled_at
    assert "no task state was changed" in observed.observation_message
    assert monday.status == "review"
    assert monday.calls.count("complete") == 0
    assert monday.calls.count("start") == 0


@pytest.mark.parametrize("lifecycle", ["merged", "closed", "diverged"])
def test_observe_terminal_pr_refuses_legacy_unbound_task_content(
    tmp_path: Path,
    lifecycle: str,
) -> None:
    root, _ = _repository(tmp_path)
    github = _ObserverGitHub(
        lifecycle=lifecycle,
        checks="passed" if lifecycle == "merged" else "failed",
        merge_commit_sha="4" * 40 if lifecycle == "merged" else "",
    )
    monday = _Monday()
    monday.status = "review"
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
        monday=monday,
    )
    job = _observable_delivery(workflow, root)
    job.objective = ""
    workflow.store.persist(job)

    observed = workflow.observe(job.delivery_id)

    assert observed.task_reconciliation == "state-changed"
    assert monday.status == "review"
    assert monday.calls.count("complete") == 0
    assert monday.calls.count("start") == 0


@pytest.mark.parametrize("lifecycle", ["merged", "closed"])
def test_observe_terminal_pr_fails_closed_on_unparseable_task_content(
    tmp_path: Path,
    lifecycle: str,
) -> None:
    root, _ = _repository(tmp_path)
    github = _ObserverGitHub(
        lifecycle=lifecycle,
        checks="passed" if lifecycle == "merged" else "failed",
        merge_commit_sha="4" * 40 if lifecycle == "merged" else "",
    )
    monday = _Monday()
    monday.status = "review"
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
        monday=monday,
    )
    job = _observable_delivery(workflow, root)
    monday.acceptance_criteria = None  # type: ignore[assignment]

    observed = workflow.observe(job.delivery_id)

    assert observed.task_reconciliation == "state-changed"
    assert monday.status == "review"
    assert monday.calls.count("complete") == 0
    assert monday.calls.count("start") == 0


@pytest.mark.parametrize("lifecycle", ["merged", "closed"])
def test_terminal_truth_crash_retires_pending_review_before_task_reconciliation(
    tmp_path: Path,
    lifecycle: str,
) -> None:
    root, _ = _repository(tmp_path)
    github = _ObserverGitHub(
        lifecycle=lifecycle,
        checks="passed" if lifecycle == "merged" else "failed",
        merge_commit_sha="4" * 40 if lifecycle == "merged" else "",
    )
    monday = _Monday()
    monday.status = "review"
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
        monday=monday,
    )
    job = _observable_delivery(workflow, root)
    job.review_reconciliation = "review-pending"
    job.review_expected_task_revision = _task_revision(root)
    workflow.store.persist(job)
    original_persist = workflow.store.persist
    crash_armed = True

    def crash_after_terminal_truth(job_to_persist: DeliveryJob) -> None:
        nonlocal crash_armed
        if (
            crash_armed
            and job_to_persist.pr_state == lifecycle
            and not job_to_persist.task_reconciliation
        ):
            crash_armed = False
            original_persist(job_to_persist)
            raise SystemExit("simulated death after terminal PR truth")
        original_persist(job_to_persist)

    workflow.store.persist = crash_after_terminal_truth  # type: ignore[method-assign]
    with pytest.raises(SystemExit, match="after terminal PR truth"):
        workflow.observe(job.delivery_id)
    workflow.store.persist = original_persist  # type: ignore[method-assign]

    durable = workflow.get(job.delivery_id)
    assert durable.pr_state == lifecycle
    assert durable.review_reconciliation == "review-pending"
    assert monday.calls.count("review") == 0
    assert monday.calls.count("complete") == 0
    assert monday.calls.count("start") == 0

    same_id = workflow.run(task_id=job.task_id, delivery_id=job.delivery_id)

    assert same_id.review_reconciliation == "terminal-unchanged"
    assert monday.status == "review"
    assert monday.calls.count("review") == 0

    first = workflow.observe(job.delivery_id)
    second = workflow.observe(job.delivery_id)

    expected_first = "completed" if lifecycle == "merged" else "reopened"
    expected_second = (
        "already-completed" if lifecycle == "merged" else "already-in-progress"
    )
    assert first.task_reconciliation == expected_first
    assert second.task_reconciliation == expected_second
    assert monday.calls.count("complete") == (1 if lifecycle == "merged" else 0)
    assert monday.calls.count("start") == (1 if lifecycle == "closed" else 0)
    assert monday.calls.count("review") == 0


@pytest.mark.parametrize("lifecycle", ["merged", "closed"])
def test_terminal_task_mutation_crash_replays_without_second_mutation(
    tmp_path: Path,
    lifecycle: str,
) -> None:
    root, _ = _repository(tmp_path)
    github = _ObserverGitHub(
        lifecycle=lifecycle,
        checks="passed" if lifecycle == "merged" else "failed",
        merge_commit_sha="4" * 40 if lifecycle == "merged" else "",
    )
    monday = _Monday()
    monday.status = "review"
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
        monday=monday,
    )
    job = _observable_delivery(workflow, root)
    job.review_reconciliation = "reviewed"
    job.review_reconciled_at = "2026-10-13T00:00:01+00:00"
    workflow.store.persist(job)
    original_persist = workflow.store.persist
    expected_mutation = "completed" if lifecycle == "merged" else "reopened"
    crash_armed = True

    def crash_before_terminal_settlement(job_to_persist: DeliveryJob) -> None:
        nonlocal crash_armed
        if crash_armed and job_to_persist.task_reconciliation == expected_mutation:
            crash_armed = False
            raise SystemExit("simulated death before terminal task settlement")
        original_persist(job_to_persist)

    workflow.store.persist = crash_before_terminal_settlement  # type: ignore[method-assign]
    with pytest.raises(SystemExit, match="before terminal task settlement"):
        workflow.observe(job.delivery_id)
    workflow.store.persist = original_persist  # type: ignore[method-assign]

    assert monday.status == ("completed" if lifecycle == "merged" else "in-progress")
    assert monday.calls.count("complete") == (1 if lifecycle == "merged" else 0)
    assert monday.calls.count("start") == (1 if lifecycle == "closed" else 0)
    durable = workflow.get(job.delivery_id)
    assert durable.pr_state == lifecycle
    assert durable.task_reconciliation == ""

    replayed = workflow.observe(job.delivery_id)

    assert replayed.task_reconciliation == (
        "already-completed" if lifecycle == "merged" else "already-in-progress"
    )
    assert monday.calls.count("complete") == (1 if lifecycle == "merged" else 0)
    assert monday.calls.count("start") == (1 if lifecycle == "closed" else 0)


@pytest.mark.parametrize("lifecycle", ["closed", "diverged"])
def test_observe_terminal_unmerged_pr_returns_task_to_in_progress(
    tmp_path: Path,
    lifecycle: str,
) -> None:
    root, _ = _repository(tmp_path)
    github = _ObserverGitHub(lifecycle=lifecycle, checks="failed")
    monday = _Monday()
    monday.status = "review"
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
        monday=monday,
    )
    job = _observable_delivery(workflow, root)

    observed = workflow.observe(job.delivery_id)

    assert observed.pr_state == lifecycle
    assert observed.task_reconciliation == "reopened"
    assert monday.status == "in-progress"
    assert monday.calls.count("start") == 1


@pytest.mark.parametrize(
    ("lifecycle", "checks", "merge_commit_sha"),
    [
        ("closed", "failed", ""),
        ("merged", "passed", "4" * 40),
    ],
)
def test_observe_old_terminal_pr_does_not_change_task_owned_by_newer_pr(
    tmp_path: Path,
    lifecycle: str,
    checks: str,
    merge_commit_sha: str,
) -> None:
    root, _ = _repository(tmp_path)
    github = _ObserverGitHub(
        lifecycle=lifecycle,
        checks=checks,
        merge_commit_sha=merge_commit_sha,
    )
    monday = _Monday()
    monday.status = "review"
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
        monday=monday,
    )
    old = _observable_delivery(workflow, root)
    newer = DeliveryJob.from_dict(old.to_dict(include_private=True))
    newer.delivery_id = "delivery-observe-newer"
    newer.created_at = "2026-10-13T00:01:00+00:00"
    newer.branch = "codex/task-0001-delivery-observe-newer"
    newer.pr_number = 13
    newer.pr_url = "https://github.com/owner/project/pull/13"
    assert workflow.store.reserve(newer) is None

    observed = workflow.observe(old.delivery_id)

    assert observed.task_reconciliation == "superseded"
    assert monday.status == "review"
    assert monday.calls.count("start") == 0
    assert monday.calls.count("complete") == 0


def test_observe_outage_is_retryable_without_overwriting_last_known_state(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    github = _ObserverGitHub(success=False, message="temporary outage")
    monday = _Monday()
    monday.status = "review"
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
        monday=monday,
    )
    job = _observable_delivery(workflow, root)
    job.pr_state = "open"
    job.checks_status = "passed"
    workflow.store.persist(job)

    observed = workflow.observe(job.delivery_id)

    assert observed.observation_success is False
    assert observed.pr_state == "open"
    assert observed.checks_status == "passed"
    assert "temporary outage" in observed.observation_message
    assert monday.status == "review"


def test_observe_rejects_a_non_pr_delivery_before_github(tmp_path: Path) -> None:
    root, _ = _repository(tmp_path)
    github = _ObserverGitHub()
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
    )
    job = _persist_delivery(
        workflow,
        delivery_id="delivery-not-published",
        status="failed",
        phase="failed",
    )
    metadata = root.resolve().stat()
    job.root_device = metadata.st_dev
    job.root_inode = metadata.st_ino
    workflow.store.persist(job)

    with pytest.raises(ValueError, match="PR-backed"):
        workflow.observe(job.delivery_id)

    assert github.requests == []


def test_observe_returns_retryable_snapshot_while_publication_lease_is_held(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    github = _ObserverGitHub(lifecycle="open", checks="pending")
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
    )
    workflow.lease_timeout = 0.01
    job = _observable_delivery(workflow, root)
    job.observation_success = True
    job.observation_message = "prior durable observation"
    job.pr_state = "open"
    job.checks_status = "passed"
    job.check_counts = {"total": 1, "passed": 1, "failed": 0, "pending": 0}
    workflow.store.persist(job)
    lease_held = threading.Event()
    release_lease = threading.Event()

    def hold_publication_lease() -> None:
        with workflow._workflow_lease():
            lease_held.set()
            assert release_lease.wait(5)

    holder = threading.Thread(target=hold_publication_lease)
    holder.start()
    assert lease_held.wait(5)
    try:
        busy = workflow.observe(job.delivery_id)
    finally:
        release_lease.set()
        holder.join(5)

    assert not holder.is_alive()
    assert busy.observation_success is False
    assert "still finalizing" in busy.observation_message
    assert github.calls == 0
    durable = workflow.get(job.delivery_id)
    assert durable.observation_success is True
    assert durable.observation_message == "prior durable observation"

    observed = workflow.observe(job.delivery_id)
    assert observed.observation_success is True
    assert observed.checks_status == "pending"
    assert github.calls == 1


@pytest.mark.parametrize(
    "check_counts",
    [
        {"total": 1, "passed": 0, "failed": 1},
        {"total": 2, "passed": 0, "failed": 1, "pending": 0},
        {"total": 501, "passed": 0, "failed": 501, "pending": 0},
        {"total": 1, "passed": 0, "failed": 0, "pending": 1},
        {"total": 1, "passed": 0, "failed": 1, "pending": 0, "name": 1},
    ],
)
def test_observe_rejects_malformed_check_aggregate_without_losing_prior_state(
    tmp_path: Path,
    check_counts: dict[str, int],
) -> None:
    root, _ = _repository(tmp_path)
    github = _ObserverGitHub(
        lifecycle="closed",
        checks="failed",
        check_counts=check_counts,
    )
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
    )
    job = _observable_delivery(workflow, root)
    job.pr_state = "open"
    job.checks_status = "passed"
    job.check_counts = {"total": 1, "passed": 1, "failed": 0, "pending": 0}
    workflow.store.persist(job)

    observed = workflow.observe(job.delivery_id)

    assert observed.observation_success is False
    assert "inconsistent" in observed.observation_message
    assert observed.pr_state == "open"
    assert observed.checks_status == "passed"
    assert observed.check_counts == {
        "total": 1,
        "passed": 1,
        "failed": 0,
        "pending": 0,
    }


def test_observe_reopened_pr_clears_stale_terminal_reconciliation(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    github = _ObserverGitHub(lifecycle="closed", checks="failed")
    monday = _Monday()
    monday.status = "review"
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
        monday=monday,
    )
    job = _observable_delivery(workflow, root)

    closed = workflow.observe(job.delivery_id)
    assert closed.task_reconciliation == "reopened"
    assert closed.task_reconciled_at
    assert monday.status == "in-progress"

    github.lifecycle = "open"
    github.checks = "passed"
    reopened = workflow.observe(job.delivery_id)

    assert reopened.pr_state == "open"
    assert reopened.task_reconciliation == ""
    assert reopened.task_reconciled_at == ""
    assert monday.status == "in-progress"
    assert monday.calls.count("start") == 1


@pytest.mark.parametrize("lifecycle", ["closed", "merged"])
def test_observe_terminal_pr_does_not_override_human_blocked_task(
    tmp_path: Path,
    lifecycle: str,
) -> None:
    root, _ = _repository(tmp_path)
    github = _ObserverGitHub(
        lifecycle=lifecycle,
        checks="failed" if lifecycle == "closed" else "passed",
        merge_commit_sha="4" * 40 if lifecycle == "merged" else "",
    )
    monday = _Monday()
    monday.status = "blocked"
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
        monday=monday,
    )
    job = _observable_delivery(workflow, root)

    observed = workflow.observe(job.delivery_id)

    assert observed.observation_success is True
    assert observed.task_reconciliation == "state-unchanged"
    assert monday.status == "blocked"
    assert "start" not in monday.calls
    assert "complete" not in monday.calls


def test_observe_terminal_pr_refuses_retargeted_task(tmp_path: Path) -> None:
    root, _ = _repository(tmp_path)
    github = _ObserverGitHub(lifecycle="closed", checks="failed")
    monday = _Monday(project="other-project")
    monday.status = "review"
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
        monday=monday,
    )
    job = _observable_delivery(workflow, root)

    observed = workflow.observe(job.delivery_id)

    assert observed.observation_success is True
    assert observed.task_reconciliation == "target-changed"
    assert monday.status == "review"
    assert "start" not in monday.calls


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("artifact_sha256", ""),
        ("reviewed_artifact_sha256", ""),
        ("base_sha", ""),
        ("pushed", False),
        ("phase", "pull-request"),
    ],
)
def test_observe_rejects_incomplete_reviewed_artifact_binding(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    root, _ = _repository(tmp_path)
    github = _ObserverGitHub()
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
    )
    job = _observable_delivery(workflow, root)
    setattr(job, field, value)
    workflow.store.persist(job)

    with pytest.raises(ValueError, match="PR-backed|exact reviewed"):
        workflow.observe(job.delivery_id)

    assert github.calls == 0


def test_observe_task_reconciliation_respects_real_task_lock(
    tmp_path: Path,
) -> None:
    from monday import Monday, MondayConfig

    root, _ = _repository(tmp_path)
    github_called = threading.Event()

    class _SignalingGitHub(_ObserverGitHub):
        def observe_pull_request(self, **kwargs: Any) -> SimpleNamespace:
            result = super().observe_pull_request(**kwargs)
            github_called.set()
            return result

    monday = Monday(MondayConfig(project_root=root))
    github = _SignalingGitHub(lifecycle="closed", checks="failed")
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
        monday=monday,  # type: ignore[arg-type]
    )
    assert monday.task("start", task_id="TASK-0001").success
    assert monday.task(
        "review",
        task_id="TASK-0001",
        changed_by="human:test",
        reason="PR opened",
    ).success
    job = _observable_delivery(workflow, root)
    observed: list[DeliveryJob] = []
    errors: list[BaseException] = []

    def run_observer() -> None:
        try:
            observed.append(workflow.observe(job.delivery_id))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    manager = TaskManager(root)
    with manager.lock("TASK-0001"):
        observer = threading.Thread(target=run_observer)
        observer.start()
        assert github_called.wait(5)
        assert monday.task("start", task_id="TASK-0001").success
        manager.block("TASK-0001", "human hold", "human:test")

    observer.join(5)
    assert not observer.is_alive()
    assert errors == []
    assert observed[0].task_reconciliation == "state-unchanged"
    assert manager.get("TASK-0001").status.value == "blocked"


def test_default_validator_is_bound_to_configured_project_root(tmp_path: Path) -> None:
    workflow = DeliveryWorkflow(
        monday=_Monday(),
        project_root=tmp_path,
        reviewer=_Reviewer([_pass()]),
        github=_GitHub(),
        repository=object(),
    )

    assert isinstance(workflow.validator, MondayValidationRunner)
    assert workflow.validator._source_root == tmp_path.resolve()


def test_external_target_must_not_overlap_controller_directory(tmp_path: Path) -> None:
    controller = tmp_path / "mondayos"
    controller.mkdir()
    nested = controller / "customer-project"
    nested.mkdir()

    for target in (controller, nested, tmp_path):
        try:
            DeliveryWorkflow(
                monday=_Monday(project="customer-project"),
                project_root=controller,
                repository_root=target,
                project_name="customer-project",
                expected_repository="owner/customer-project",
                reviewer=_Reviewer([_pass()]),
                github=_GitHub(),
                repository=object(),
            )
        except ValueError as exc:
            assert "must be separate" in str(exc)
        else:  # pragma: no cover - controller/target overlap breaks isolation
            raise AssertionError(f"overlapping target was accepted: {target}")


def test_external_project_uses_controller_state_and_delivers_to_target(
    tmp_path: Path,
) -> None:
    from delivery.git import GitRepository

    target, remote = _repository(tmp_path)
    controller_root = tmp_path / "mondayos"
    controller_root.mkdir()
    task = TaskManager(controller_root).create(
        title="External delivery fixture",
        task_type=TaskType.FEATURE,
        priority=TaskPriority.P2,
        objective="Exercise external project delivery.",
        created_by="human:test",
    )
    target_stat = target.stat()
    monday = _Monday(
        project="weatherbot",
        project_delivery_repository="owner/project",
        project_source_device=target_stat.st_dev,
        project_source_inode=target_stat.st_ino,
    )
    validator = _Validator([True])
    github = _GitHub()
    repository = GitRepository(
        target,
        trusted_root=target,
        runtime_root=controller_root / "logs" / "delivery",
    )
    repository.github_slug = lambda remote="origin": "owner/project"  # type: ignore[method-assign]
    workflow = DeliveryWorkflow(
        monday=monday,
        project_root=controller_root,
        repository_root=target,
        project_name="weatherbot",
        expected_repository="owner/project",
        expected_root_identity=(target_stat.st_dev, target_stat.st_ino),
        builders=[_Builder([_patch(1, 2)], name="codex-chatgpt")],
        reviewer=_Reviewer([_pass()]),
        validator=validator,
        github=github,
        repository=repository,
    )

    job = workflow.run(
        task_id=task.id,
        delivery_id="delivery-external-success",
    )

    assert job.success is True
    assert job.project_name == "weatherbot"
    assert job.expected_repository == "owner/project"
    assert job.repo_root == str(target)
    assert workflow.store.root == controller_root / "logs" / "delivery"
    assert (workflow.store.records / "delivery-external-success.json").is_file()
    assert not (target / "logs" / "delivery").exists()
    assert (target / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert _git(remote, "show", f"{job.branch}:app.py") == "VALUE = 2"
    assert validator.requests[0]["source_root"] == controller_root
    assert validator.requests[0]["repository_root"] == target
    assert validator.requests[0]["mondayos_tests"] is False
    assert github.requests[0]["repo_root"] == target
    assert github.requests[0]["repository"] == "owner/project"


def test_existing_task_cannot_follow_removed_and_reregistered_project_name(
    tmp_path: Path,
) -> None:
    from monday import Monday, MondayConfig

    controller = tmp_path / "mondayos"
    original = tmp_path / "original"
    replacement = tmp_path / "replacement"
    controller.mkdir()
    original.mkdir()
    replacement.mkdir()
    monday = Monday(MondayConfig(project_root=controller))
    assert monday.project(
        "register",
        name="weatherbot",
        path=str(original),
        delivery_repository="owner/original",
    ).success
    task = monday.task(
        "create",
        title="Change the value",
        objective="Set VALUE to the reviewed target.",
        project="weatherbot",
    )
    assert task.success
    assert task.data["project_delivery_repository"] == "owner/original"

    assert monday.project("remove", name="weatherbot").success
    assert monday.project(
        "register",
        name="weatherbot",
        path=str(replacement),
        delivery_repository="owner/replacement",
    ).success
    replacement_stat = replacement.stat()
    builder = _Builder([_patch(1, 2)])
    workflow = DeliveryWorkflow(
        monday=monday,
        project_root=controller,
        repository_root=replacement,
        project_name="weatherbot",
        expected_repository="owner/replacement",
        expected_root_identity=(replacement_stat.st_dev, replacement_stat.st_ino),
        builders=[builder],
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
        repository=object(),
    )

    job = workflow.run(
        task_id=str(task.task_id),
        delivery_id="delivery-retargeted-task",
    )

    assert job.success is False
    assert job.failure_code == "project-mismatch"
    assert "different GitHub repository" in job.message
    assert builder.calls == 0


def test_delivery_identity_cannot_be_rebound_to_another_external_project(
    tmp_path: Path,
) -> None:
    controller = tmp_path / "mondayos"
    first = tmp_path / "first"
    second = tmp_path / "second"
    controller.mkdir()
    first.mkdir()
    second.mkdir()
    first_stat = first.stat()
    second_stat = second.stat()
    first_workflow = DeliveryWorkflow(
        monday=_Monday(project="first"),
        project_root=controller,
        repository_root=first,
        project_name="first",
        expected_repository="owner/first",
        expected_root_identity=(first_stat.st_dev, first_stat.st_ino),
        builders=[_Builder([_patch(1, 2)])],
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
        repository=object(),
    )
    reserved = DeliveryJob(
        delivery_id="delivery-bound-project",
        task_id="TASK-EXT-0002",
        objective="safe objective",
        repo_root=str(first),
        project_name="first",
        expected_repository="owner/first",
        root_device=first_stat.st_dev,
        root_inode=first_stat.st_ino,
        created_at="2026-10-12T00:00:00+00:00",
        updated_at="2026-10-12T00:00:00+00:00",
    )
    assert first_workflow.store.reserve(reserved) is None
    second_workflow = DeliveryWorkflow(
        monday=_Monday(project="second"),
        project_root=controller,
        repository_root=second,
        project_name="second",
        expected_repository="owner/second",
        expected_root_identity=(second_stat.st_dev, second_stat.st_ino),
        builders=[_Builder([_patch(1, 2)])],
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
        repository=object(),
    )

    try:
        second_workflow.run(
            task_id="TASK-EXT-0002",
            delivery_id="delivery-bound-project",
        )
    except ValueError as exc:
        assert "already bound" in str(exc)
    else:  # pragma: no cover - a durable identity may never change target
        raise AssertionError("delivery identity was rebound")


def test_success_reviews_exact_artifact_pushes_branch_and_opens_pr(tmp_path: Path) -> None:
    root, remote = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    reviewer = _Reviewer([_pass()])
    validator = _Validator([True])
    github = _GitHub()
    workflow, monday = _workflow(
        root, builder=builder, reviewer=reviewer, validator=validator, github=github
    )
    push_requests: list[dict[str, Any]] = []
    progress_phases: list[str] = []
    original_push = workflow.repository.push

    def recording_push(*args: Any, **kwargs: Any) -> str:
        push_requests.append(dict(kwargs))
        return original_push(*args, **kwargs)

    workflow.repository.push = recording_push

    job = workflow.run(
        task_id="TASK-0001",
        delivery_id="delivery-success",
        progress_callback=lambda current: progress_phases.append(current["phase"]),
    )

    assert job.success is True
    assert job.status == "pr-open"
    assert job.pr_url.endswith("/pull/12")
    assert job.artifact_sha256 == job.reviewed_artifact_sha256
    assert len(job.artifact_sha256) == 64
    assert job.review_reconciliation == "reviewed"
    assert job.review_reconciled_at
    assert job.task_reconciliation == ""
    assert monday.status == "review"
    assert (root / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert _git(remote, "show", f"{job.branch}:app.py") == "VALUE = 2"
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 1
    guard_fds = github.requests[0]["inherited_guard_fds"]
    assert isinstance(guard_fds, tuple) and len(guard_fds) == 2
    assert guard_fds[0] != guard_fds[1]
    assert push_requests[0]["inherited_guard_fds"] == guard_fds
    assert "pushing" in progress_phases
    assert "pull-request" in progress_phases
    assert progress_phases.index("pushing") < progress_phases.index("pull-request")
    for descriptor in guard_fds:
        with pytest.raises(OSError):
            os.fstat(descriptor)

    same = workflow.run(task_id="TASK-0001", delivery_id="delivery-success")
    assert same.commit_sha == job.commit_sha
    assert builder.calls == 1

    try:
        workflow.run(task_id="TASK-9999", delivery_id="delivery-success")
    except ValueError as exc:
        assert "already bound" in str(exc)
    else:  # pragma: no cover - identity confusion must never be tolerated
        raise AssertionError("a delivery identity was reused for another task")


@pytest.mark.skipif(os.name != "posix", reason="requires descriptor inheritance")
def test_workflow_lease_graceful_unwind_preserves_inherited_child_guard(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
    )
    ready_read, ready_write = os.pipe()
    release_read, release_write = os.pipe()
    child: subprocess.Popen[bytes] | None = None
    try:
        with workflow._workflow_lease() as descriptor:
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    (
                        "import os,sys; "
                        "os.fstat(int(sys.argv[1])); "
                        "os.write(int(sys.argv[2]), b'1'); "
                        "os.read(int(sys.argv[3]), 1)"
                    ),
                    str(descriptor),
                    str(ready_write),
                    str(release_read),
                ],
                pass_fds=(descriptor, ready_write, release_read),
            )
            os.close(ready_write)
            ready_write = -1
            os.close(release_read)
            release_read = -1
            assert os.read(ready_read, 1) == b"1"

        workflow.lease_timeout = 0.01
        with pytest.raises(TimeoutError, match="another build"):
            with workflow._workflow_lease():
                pass

        os.write(release_write, b"1")
        assert child.wait(timeout=2) == 0
        with workflow._workflow_lease():
            pass
    finally:
        for pipe_descriptor in (
            ready_read,
            ready_write,
            release_read,
            release_write,
        ):
            if pipe_descriptor >= 0:
                os.close(pipe_descriptor)
        if child is not None and child.poll() is None:
            child.kill()
            child.wait(timeout=2)


def test_controller_death_after_push_is_reconciled_without_a_second_push(
    tmp_path: Path,
) -> None:
    root, remote = _repository(tmp_path)
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
    )
    push_calls = 0
    original_push = workflow.repository.push

    def recording_push(*args: Any, **kwargs: Any) -> str:
        nonlocal push_calls
        push_calls += 1
        return original_push(*args, **kwargs)

    workflow.repository.push = recording_push
    original_persist = workflow.store.persist
    crash_armed = True

    def crash_before_pushed_persistence(job: DeliveryJob) -> None:
        nonlocal crash_armed
        if crash_armed and job.phase == "pushing" and job.pushed:
            crash_armed = False
            raise SystemExit("simulated controller death after push")
        original_persist(job)

    workflow.store.persist = crash_before_pushed_persistence  # type: ignore[method-assign]
    with pytest.raises(SystemExit, match="after push"):
        workflow.run(task_id="TASK-0001", delivery_id="delivery-crash-after-push")
    workflow.store.persist = original_persist  # type: ignore[method-assign]

    durable = workflow.get("delivery-crash-after-push")
    assert durable.phase == "pushing"
    assert durable.pushed is False
    assert _git(remote, "show", f"{durable.branch}:app.py") == "VALUE = 2"

    recovered = workflow.run(
        task_id="TASK-0001",
        delivery_id="delivery-crash-after-push",
    )

    assert recovered.success is True
    assert recovered.status == "pr-open"
    assert push_calls == 1
    assert github.calls == 1


def test_published_retry_recovery_ignores_later_predecessor_publication(
    tmp_path: Path,
) -> None:
    root, remote = _repository(tmp_path)
    github = _GitHub()
    workflow, monday = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
    )
    predecessor = _safe_retry_predecessor(
        workflow,
        delivery_id="delivery-recovery-lineage-root",
    )
    push_calls = 0
    original_push = workflow.repository.push

    def recording_push(*args: Any, **kwargs: Any) -> str:
        nonlocal push_calls
        push_calls += 1
        return original_push(*args, **kwargs)

    workflow.repository.push = recording_push
    original_persist = workflow.store.persist
    crash_armed = True

    def crash_before_pushed_persistence(job: DeliveryJob) -> None:
        nonlocal crash_armed
        if crash_armed and job.phase == "pushing" and job.pushed:
            crash_armed = False
            raise SystemExit("simulated retry controller death after push")
        original_persist(job)

    workflow.store.persist = crash_before_pushed_persistence  # type: ignore[method-assign]
    successor_id = f"{predecessor.delivery_id}-r2"
    with pytest.raises(SystemExit, match="retry controller death after push"):
        workflow.run(
            task_id=predecessor.task_id,
            delivery_id=successor_id,
            predecessor_delivery_id=predecessor.delivery_id,
            retry_root_delivery_id=predecessor.delivery_id,
            retry_attempt=2,
        )
    workflow.store.persist = original_persist  # type: ignore[method-assign]
    durable = workflow.get(successor_id)

    assert durable.phase == "pushing"
    assert durable.pushed is False
    assert _git(remote, "show", f"{durable.branch}:app.py") == "VALUE = 2"
    monday.title = "Edited only after the retry branch was pushed"
    monday._touch()
    predecessor_published = False

    def publish_predecessor_during_recovery(snapshot: dict[str, Any]) -> None:
        nonlocal predecessor_published
        if snapshot["phase"] == "pull-request" and not predecessor_published:
            predecessor_published = True
            _git(
                root,
                "push",
                "origin",
                f"{predecessor.base_sha}:refs/heads/{predecessor.branch}",
            )

    recovered = workflow.run(
        task_id=predecessor.task_id,
        delivery_id=successor_id,
        predecessor_delivery_id=predecessor.delivery_id,
        retry_root_delivery_id=predecessor.delivery_id,
        retry_attempt=2,
        progress_callback=publish_predecessor_during_recovery,
    )

    assert predecessor_published is True
    assert recovered.success is True
    assert recovered.status == "pr-open"
    assert recovered.pushed is True
    assert recovered.objective == durable.objective
    assert push_calls == 1
    assert github.calls == 1
    assert _git(remote, "rev-parse", f"refs/heads/{predecessor.branch}") == (
        predecessor.base_sha
    )


def test_controller_death_before_deferred_commit_is_proven_safe_for_successor(
    tmp_path: Path,
) -> None:
    root, remote = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    reviewer = _Reviewer([_pass()])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
    )

    def crash_before_push(event: dict[str, Any]) -> None:
        if event.get("phase") == "pushing":
            raise SystemExit("simulated controller death before push")

    with pytest.raises(SystemExit, match="before push"):
        workflow.run(
            task_id="TASK-0001",
            delivery_id="delivery-crash-before-push",
            progress_callback=crash_before_push,
        )

    durable = workflow.get("delivery-crash-before-push")
    assert durable.phase == "pushing"
    assert durable.deferred_commit_authorization is True
    assert durable.prepared_commit_sha == ""
    assert durable.commit_sha == ""
    assert durable.pushed is False
    assert _git(
        remote,
        "for-each-ref",
        "--format=%(refname)",
        f"refs/heads/{durable.branch}",
    ) == ""
    calls = (builder.calls, reviewer.calls, validator.calls, github.calls)

    recovered = workflow.run(
        task_id="TASK-0001",
        delivery_id="delivery-crash-before-push",
    )

    assert recovered.status == "interrupted"
    assert recovered.failure_code == "interrupted-run"
    assert recovered.failure_class == "transient"
    assert recovered.interruption_proof_sha256
    assert (builder.calls, reviewer.calls, validator.calls, github.calls) == calls


def test_controller_death_after_pr_creation_is_reconciled_exactly(
    tmp_path: Path,
) -> None:
    root, remote = _repository(tmp_path)
    github = _GitHub()
    workflow, monday = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
    )
    push_calls = 0
    original_push = workflow.repository.push

    def recording_push(*args: Any, **kwargs: Any) -> str:
        nonlocal push_calls
        push_calls += 1
        return original_push(*args, **kwargs)

    workflow.repository.push = recording_push
    original_persist = workflow.store.persist
    crash_armed = True

    def crash_before_pr_persistence(job: DeliveryJob) -> None:
        nonlocal crash_armed
        if crash_armed and job.status == "pr-open":
            crash_armed = False
            raise SystemExit("simulated controller death after PR creation")
        original_persist(job)

    workflow.store.persist = crash_before_pr_persistence  # type: ignore[method-assign]
    with pytest.raises(SystemExit, match="after PR creation"):
        workflow.run(task_id="TASK-0001", delivery_id="delivery-crash-after-pr")
    workflow.store.persist = original_persist  # type: ignore[method-assign]

    durable = workflow.get("delivery-crash-after-pr")
    assert durable.phase == "pull-request"
    assert durable.pushed is True
    assert durable.pr_url == ""
    assert github.calls == 1
    assert _git(remote, "show", f"{durable.branch}:app.py") == "VALUE = 2"

    original_finish = workflow._finish_published

    def finish_while_repository_lease_is_held(*args: Any, **kwargs: Any) -> DeliveryJob:
        lock_paths = list((workflow.store.root / "workflow-locks").glob("*.lock"))
        assert len(lock_paths) == 1
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import fcntl,os,sys; "
                    "fd=os.open(sys.argv[1], os.O_RDWR); "
                    "\ntry: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)"
                    "\nexcept BlockingIOError: raise SystemExit(0)"
                    "\nraise SystemExit(1)"
                ),
                str(lock_paths[0]),
            ],
            check=False,
        )
        assert probe.returncode == 0
        return original_finish(*args, **kwargs)

    workflow._finish_published = finish_while_repository_lease_is_held  # type: ignore[method-assign]

    recovery_phases: list[str] = []
    recovered = workflow.run(
        task_id="TASK-0001",
        delivery_id="delivery-crash-after-pr",
        progress_callback=lambda current: recovery_phases.append(current["phase"]),
    )

    assert recovered.success is True
    assert recovered.pr_url.endswith("/pull/12")
    assert recovered.review_reconciliation == "reviewed"
    assert recovered.review_reconciled_at
    assert monday.status == "review"
    assert monday.calls.count("review") == 1
    assert "pull-request" in recovery_phases
    assert push_calls == 1
    # The second idempotent ensure call represents the exact PR lookup that a
    # production GhCliClient performs before it ever attempts creation.
    assert github.calls == 2


def test_unverified_pr_failure_stays_recoverable_and_retries_exact_identity(
    tmp_path: Path,
) -> None:
    class _UnavailablePr(_GitHub):
        def ensure_pull_request(self, **kwargs: Any) -> PullRequestResult:
            self.calls += 1
            self.requests.append(dict(kwargs))
            return PullRequestResult(False, message="temporary GitHub query failure")

    root, remote = _repository(tmp_path)
    unavailable = _UnavailablePr()
    workflow, monday = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=unavailable,
    )

    pending = workflow.run(
        task_id="TASK-0001",
        delivery_id="delivery-pr-temporarily-unverified",
    )

    assert pending.status == "publication-pending"
    assert pending.success is False
    assert pending.terminal is False
    assert pending.pushed is True
    assert "Retry this exact delivery identity" in pending.message
    assert _git(remote, "show", f"{pending.branch}:app.py") == "VALUE = 2"

    recovered_github = _GitHub()
    workflow.github = recovered_github
    monday.title = "A later title that was never reviewed"
    monday._touch()
    recovered = workflow.run(
        task_id="TASK-0001",
        delivery_id="delivery-pr-temporarily-unverified",
    )

    assert recovered.success is True
    assert recovered.status == "pr-open"
    assert unavailable.calls == 1
    assert recovered_github.calls == 1
    assert recovered_github.requests[0]["title"] == "TASK-0001: Autonomous build"


@pytest.mark.parametrize(
    "later_objective",
    [
        "x" * 50_001,
        "A later credential-like edit sk-abcdefghijklmnopqrst",
    ],
    ids=["oversized", "credential-like"],
)
def test_exact_publication_recovery_precedes_live_task_content_validation(
    tmp_path: Path,
    later_objective: str,
) -> None:
    class _UnavailablePr(_GitHub):
        def ensure_pull_request(self, **kwargs: Any) -> PullRequestResult:
            self.calls += 1
            self.requests.append(dict(kwargs))
            return PullRequestResult(False, message="temporary GitHub query failure")

    root, remote = _repository(tmp_path)
    workflow, monday = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_UnavailablePr(),
    )
    pending = workflow.run(
        task_id="TASK-0001",
        delivery_id=f"delivery-recovery-after-{len(later_objective)}-edit",
    )
    assert pending.status == "publication-pending"
    assert _git(remote, "show", f"{pending.branch}:app.py") == "VALUE = 2"

    monday.objective = later_objective
    monday._touch()
    recovered_github = _GitHub()
    workflow.github = recovered_github
    recovered = workflow.run(
        task_id="TASK-0001",
        delivery_id=pending.delivery_id,
    )

    assert recovered.success is True
    assert recovered.status == "pr-open"
    assert recovered_github.calls == 1
    assert recovered_github.requests[0]["title"] == "TASK-0001: Autonomous build"


def test_failure_is_persisted_before_repository_lease_is_released(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
    )
    contender, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 3)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
    )
    contender.lease_timeout = 0.01

    def fail_during_run(*_args: Any, **_kwargs: Any) -> DeliveryJob:
        raise RuntimeError("deterministic graceful failure")

    workflow._run_locked = fail_during_run  # type: ignore[method-assign]
    original_fail = workflow._fail
    concurrent_snapshots: list[DeliveryJob] = []

    def persist_failure_while_contended(
        job: DeliveryJob,
        code: str,
        message: str,
    ) -> DeliveryJob:
        concurrent_snapshots.append(
            contender.run(
                task_id="TASK-0001",
                delivery_id="delivery-failure-linearized",
            )
        )
        return original_fail(job, code, message)

    workflow._fail = persist_failure_while_contended  # type: ignore[method-assign]
    failed = workflow.run(
        task_id="TASK-0001",
        delivery_id="delivery-failure-linearized",
    )

    assert [snapshot.status for snapshot in concurrent_snapshots] == ["running"]
    assert failed.status == "failed"
    assert failed.message == "deterministic graceful failure"
    assert workflow.get(failed.delivery_id).status == "failed"


def test_new_delivery_id_cannot_bypass_an_unresolved_delivery(tmp_path: Path) -> None:
    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
    )
    _persist_delivery(
        workflow,
        delivery_id="delivery-unresolved-original",
        status="publication-pending",
        phase="pushing",
    )

    blocked = workflow.run(
        task_id="TASK-0001",
        delivery_id="delivery-unresolved-replacement",
    )

    assert blocked.status == "failed"
    assert blocked.failure_code == "unresolved-delivery"
    assert "delivery-unresolved-original" in blocked.message
    assert builder.calls == 0


def test_task_store_failure_after_pr_keeps_published_delivery_successful(
    tmp_path: Path,
) -> None:
    class _ReviewWriteFails(_Monday):
        def task(self, action: str, **kwargs: Any) -> SimpleNamespace:
            if action == "review":
                raise OSError("task store unavailable")
            return super().task(action, **kwargs)

    root, remote = _repository(tmp_path)
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
        monday=_ReviewWriteFails(),
    )

    job = workflow.run(
        task_id="TASK-0001",
        delivery_id="delivery-task-review-write-fails",
    )

    assert job.success is True
    assert job.status == "pr-open"
    assert job.failure_code == ""
    assert job.pr_url.endswith("/pull/12")
    assert "task status could not be moved to REVIEW" in job.message
    assert job.review_reconciliation == "review-pending"
    assert job.review_reconciled_at == ""
    assert job.task_reconciliation == ""
    durable = workflow.get(job.delivery_id)
    assert durable.status == "pr-open"
    assert durable.review_reconciliation == "review-pending"
    assert durable.review_reconciled_at == ""
    assert _git(remote, "show", f"{job.branch}:app.py") == "VALUE = 2"
    assert github.calls == 1


@pytest.mark.parametrize(
    "write_before_crash",
    [False, True],
    ids=["before-settlement-write", "after-settlement-write"],
)
def test_controller_death_during_review_settlement_recovers_at_most_once(
    tmp_path: Path,
    write_before_crash: bool,
) -> None:
    root, _ = _repository(tmp_path)
    monday = _Monday()
    original, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
        monday=monday,
    )
    original_persist = original.store.persist
    crash_armed = True

    def crash_around_review_settlement(job: DeliveryJob) -> None:
        nonlocal crash_armed
        if crash_armed and job.review_reconciliation == "reviewed":
            crash_armed = False
            if write_before_crash:
                original_persist(job)
            raise SystemExit("simulated controller death during REVIEW settlement")
        original_persist(job)

    original.store.persist = crash_around_review_settlement  # type: ignore[method-assign]
    delivery_id = (
        "delivery-review-settlement-after-write"
        if write_before_crash
        else "delivery-review-settlement-before-write"
    )
    with pytest.raises(SystemExit, match="during REVIEW settlement"):
        original.run(task_id="TASK-0001", delivery_id=delivery_id)
    original.store.persist = original_persist  # type: ignore[method-assign]

    durable = original.get(delivery_id)
    assert durable.status == "pr-open"
    assert durable.review_reconciliation == (
        "reviewed" if write_before_crash else "review-pending"
    )
    assert monday.status == "review"
    assert monday.calls.count("review") == 1

    builder = _Builder([_patch(1, 9)])
    reviewer = _Reviewer([_pass("must not run")])
    validator = _Validator([True])
    github = _GitHub()
    restarted, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
        monday=monday,
    )

    repaired = restarted.run(task_id="TASK-0001", delivery_id=delivery_id)

    assert repaired.status == "pr-open"
    assert repaired.review_reconciliation == "reviewed"
    assert repaired.review_reconciled_at
    assert monday.calls.count("review") == 1
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0
    assert restarted.get(delivery_id).review_reconciliation == "reviewed"


def test_terminal_pr_retry_after_restart_repairs_review_without_republishing(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    monday = _Monday()
    monday.status = "in-progress"
    original_builder = _Builder([_patch(1, 2)])
    original_reviewer = _Reviewer([_pass()])
    original_validator = _Validator([True])
    original_github = _GitHub()
    original, _ = _workflow(
        root,
        builder=original_builder,
        reviewer=original_reviewer,
        validator=original_validator,
        github=original_github,
        monday=monday,
    )
    job = _observable_delivery(original, root)
    job.review_reconciliation = "review-pending"
    job.review_expected_task_revision = _task_revision(root)
    job.message = (
        "ChatGPT approved the exact artifact and PR #12 is open; "
        "task status could not be moved to REVIEW; "
        "the local worktree was retained for cleanup"
    )
    original.store.persist(job)

    retry_builder = _Builder([_patch(1, 9)])
    retry_reviewer = _Reviewer([_pass("must not run")])
    retry_validator = _Validator([True])
    retry_github = _GitHub()
    restarted, _ = _workflow(
        root,
        builder=retry_builder,
        reviewer=retry_reviewer,
        validator=retry_validator,
        github=retry_github,
        monday=monday,
    )

    repaired = restarted.run(
        task_id=job.task_id,
        delivery_id=job.delivery_id,
    )

    assert repaired.status == "pr-open"
    assert repaired.review_reconciliation == "reviewed"
    assert repaired.review_reconciled_at
    assert repaired.task_reconciliation == ""
    assert "task status could not be moved to REVIEW" not in repaired.message
    assert "the local worktree was retained for cleanup" in repaired.message
    assert monday.status == "review"
    assert monday.calls.count("review") == 1
    assert retry_builder.calls == 0
    assert retry_reviewer.calls == 0
    assert retry_validator.calls == 0
    assert retry_github.calls == 0
    assert restarted.get(job.delivery_id).review_reconciliation == "reviewed"

    reviewed_at = repaired.review_reconciled_at
    replayed = restarted.run(task_id=job.task_id, delivery_id=job.delivery_id)

    assert replayed.review_reconciliation == "reviewed"
    assert replayed.review_reconciled_at == reviewed_at
    assert monday.calls.count("review") == 1
    assert retry_builder.calls == retry_reviewer.calls == retry_validator.calls == 0
    assert retry_github.calls == 0


def test_pending_review_repair_accepts_already_review_without_second_transition(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    monday = _Monday()
    monday.status = "review"
    builder = _Builder([_patch(1, 9)])
    reviewer = _Reviewer([_pass("must not run")])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
        monday=monday,
    )
    job = _observable_delivery(workflow, root)
    job.review_reconciliation = "review-pending"
    job.review_expected_task_revision = _task_revision(root)
    workflow.store.persist(job)

    settled = workflow.run(task_id=job.task_id, delivery_id=job.delivery_id)

    assert settled.review_reconciliation == "reviewed"
    assert settled.review_reconciled_at
    assert monday.status == "review"
    assert monday.calls.count("review") == 0
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0

    reconciled_at = settled.review_reconciled_at
    replayed = workflow.run(task_id=job.task_id, delivery_id=job.delivery_id)

    assert replayed.review_reconciliation == "reviewed"
    assert replayed.review_reconciled_at == reconciled_at
    assert monday.calls.count("review") == 0


@pytest.mark.parametrize("lifecycle", ["merged", "closed", "diverged"])
def test_terminal_pr_observation_wins_over_pending_initial_review(
    tmp_path: Path,
    lifecycle: str,
) -> None:
    root, _ = _repository(tmp_path)
    monday = _Monday()
    monday.status = "in-progress"
    github = _ObserverGitHub(
        lifecycle=lifecycle,
        checks="passed" if lifecycle == "merged" else "failed",
        merge_commit_sha="4" * 40 if lifecycle == "merged" else "",
    )
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 9)]),
        reviewer=_Reviewer([_pass("must not run")]),
        validator=_Validator([True]),
        github=github,
        monday=monday,
    )
    job = _observable_delivery(workflow, root)
    job.review_reconciliation = "review-pending"
    job.review_expected_task_revision = _task_revision(root)
    workflow.store.persist(job)

    observed = workflow.observe(job.delivery_id)

    assert observed.pr_state == lifecycle
    assert observed.task_reconciliation == (
        "completed" if lifecycle == "merged" else "already-in-progress"
    )
    assert observed.review_reconciliation == "terminal-unchanged"
    assert observed.review_reconciled_at
    assert monday.status == ("completed" if lifecycle == "merged" else "in-progress")
    assert monday.calls.count("review") == 0
    assert monday.calls.count("complete") == (1 if lifecycle == "merged" else 0)

    replayed = workflow.run(task_id=job.task_id, delivery_id=job.delivery_id)

    assert replayed.review_reconciliation == "terminal-unchanged"
    assert monday.status == ("completed" if lifecycle == "merged" else "in-progress")
    assert monday.calls.count("review") == 0
    assert monday.calls.count("complete") == (1 if lifecycle == "merged" else 0)


@pytest.mark.parametrize("raw_reconciliation", ["", "invalid-legacy-state"])
def test_legacy_or_invalid_pr_open_record_does_not_authorize_review_transition(
    tmp_path: Path,
    raw_reconciliation: str,
) -> None:
    root, _ = _repository(tmp_path)
    monday = _Monday()
    monday.status = "in-progress"
    builder = _Builder([_patch(1, 9)])
    reviewer = _Reviewer([_pass("must not run")])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
        monday=monday,
    )
    job = _observable_delivery(workflow, root)
    payload = job.to_dict(include_private=True)
    payload["review_reconciliation"] = raw_reconciliation
    payload["review_expected_task_revision"] = _task_revision(root)
    record_path = workflow.store.records / f"{job.delivery_id}.json"
    record_path.write_text(json.dumps(payload), encoding="utf-8")

    assert workflow.get(job.delivery_id).review_reconciliation == ""

    unchanged = workflow.run(task_id=job.task_id, delivery_id=job.delivery_id)

    assert unchanged.review_reconciliation == ""
    assert monday.status == "in-progress"
    assert monday.calls.count("review") == 0
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0


def test_pending_review_repair_refuses_changed_task_revision(tmp_path: Path) -> None:
    root, _ = _repository(tmp_path)
    monday = _Monday()
    monday.status = "in-progress"
    builder = _Builder([_patch(1, 9)])
    reviewer = _Reviewer([_pass("must not run")])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
        monday=monday,
    )
    job = _observable_delivery(workflow, root)
    task_manager = TaskManager(root)
    expected_revision = task_manager.revision(job.task_id)
    unchanged_updated_at = task_manager.get(job.task_id).updated
    job.review_reconciliation = "review-pending"
    job.review_expected_task_revision = expected_revision
    workflow.store.persist(job)
    with mock.patch.object(task_manager_module, "datetime") as clock:
        clock.now.return_value = unchanged_updated_at
        task_manager.append_work_log(
            job.task_id,
            "A human changed the task after publication.",
            "human:test",
        )

    assert task_manager.get(job.task_id).updated == unchanged_updated_at
    assert task_manager.revision(job.task_id) != expected_revision

    settled = workflow.run(task_id=job.task_id, delivery_id=job.delivery_id)

    assert monday.status == "in-progress"
    assert monday.calls.count("review") == 0
    assert settled.review_reconciliation == "state-changed"
    assert settled.review_reconciled_at
    assert settled.review_expected_task_revision == expected_revision
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0


def test_newer_open_delivery_supersedes_old_pending_review(tmp_path: Path) -> None:
    root, _ = _repository(tmp_path)
    monday = _Monday()
    monday.status = "in-progress"
    builder = _Builder([_patch(1, 9)])
    reviewer = _Reviewer([_pass("must not run")])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
        monday=monday,
    )
    old = _observable_delivery(workflow, root)
    old.review_reconciliation = "review-pending"
    old.review_expected_task_revision = _task_revision(root)
    workflow.store.persist(old)
    newer = DeliveryJob.from_dict(old.to_dict(include_private=True))
    newer.delivery_id = "delivery-observe-newer"
    newer.created_at = "2026-10-13T00:01:00+00:00"
    newer.updated_at = newer.created_at
    newer.branch = "codex/task-0001-delivery-observe-newer"
    newer.repository = "OWNER/PROJECT"
    newer.pr_number = 13
    newer.pr_url = "https://github.com/owner/project/pull/13"
    assert workflow.store.reserve(newer) is None

    settled = workflow.run(task_id=old.task_id, delivery_id=old.delivery_id)

    assert settled.review_reconciliation == "superseded"
    assert settled.review_reconciled_at
    assert monday.status == "in-progress"
    assert monday.calls.count("review") == 0
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0
    assert workflow.get(newer.delivery_id).review_reconciliation == "review-pending"


@pytest.mark.parametrize(
    ("task_status", "expected_reconciliation"),
    [
        ("completed", "terminal-unchanged"),
        ("cancelled", "terminal-unchanged"),
        ("blocked", "state-unchanged"),
    ],
)
def test_pending_review_repair_never_overrides_human_managed_task_state(
    tmp_path: Path,
    task_status: str,
    expected_reconciliation: str,
) -> None:
    root, _ = _repository(tmp_path)
    monday = _Monday()
    monday.status = task_status
    builder = _Builder([_patch(1, 9)])
    reviewer = _Reviewer([_pass("must not run")])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
        monday=monday,
    )
    job = _observable_delivery(workflow, root)
    job.review_reconciliation = "review-pending"
    workflow.store.persist(job)

    settled = workflow.run(task_id=job.task_id, delivery_id=job.delivery_id)

    assert monday.status == task_status
    assert monday.calls.count("review") == 0
    assert settled.review_reconciliation == expected_reconciliation
    assert settled.review_reconciled_at
    assert settled.task_reconciliation == ""
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0


def test_pending_review_repair_refuses_a_retargeted_task(tmp_path: Path) -> None:
    root, _ = _repository(tmp_path)
    monday = _Monday()
    monday.status = "in-progress"
    builder = _Builder([_patch(1, 9)])
    reviewer = _Reviewer([_pass("must not run")])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
        monday=monday,
    )
    job = _observable_delivery(workflow, root)
    job.review_reconciliation = "review-pending"
    workflow.store.persist(job)
    monday.project = "another-project"

    settled = workflow.run(task_id=job.task_id, delivery_id=job.delivery_id)

    assert monday.status == "in-progress"
    assert monday.calls.count("review") == 0
    assert settled.review_reconciliation == "target-changed"
    assert settled.review_reconciled_at
    assert settled.task_reconciliation == ""
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0


def test_task_becoming_terminal_after_review_stops_before_push_or_pr(
    tmp_path: Path,
) -> None:
    class _TerminalAfterCommit:
        def __init__(self, repository: Any, monday: _Monday) -> None:
            self._repository = repository
            self._monday = monday

        def __getattr__(self, name: str) -> Any:
            return getattr(self._repository, name)

        def commit(self, *args: Any, **kwargs: Any) -> str:
            commit = self._repository.commit(*args, **kwargs)
            self._monday.status = "completed"
            return commit

    root, remote = _repository(tmp_path)
    github = _GitHub()
    workflow, monday = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
    )
    workflow.repository = _TerminalAfterCommit(workflow.repository, monday)

    job = workflow.run(
        task_id="TASK-0001",
        delivery_id="delivery-terminal-before-publish",
    )

    assert job.success is False
    assert job.status == "failed"
    assert job.failure_code == "terminal-task"
    assert job.commit_sha == ""
    assert re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", job.prepared_commit_sha)
    assert job.pushed is False
    assert github.calls == 0
    assert _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/codex") == ""


def test_stale_nonterminal_delivery_is_interrupted_under_free_lease(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    reviewer = _Reviewer([_pass()])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
    )
    _persist_delivery(
        workflow,
        delivery_id="delivery-stale-worker",
        status="running",
        phase="validating",
    )

    recovered = workflow.run(
        task_id="TASK-0001",
        delivery_id="delivery-stale-worker",
    )

    assert recovered.status == "interrupted"
    assert recovered.phase == "interrupted"
    assert recovered.failure_code == "interrupted-run"
    assert "new build request" in recovered.message
    assert "new delivery identity" in recovered.message
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0
    assert workflow.get("delivery-stale-worker").status == "interrupted"


@pytest.mark.parametrize(
    ("phase", "local_commit"),
    [
        ("implementing", False),
        ("validating", False),
        ("reviewing", False),
        ("committing", False),
        ("committing", True),
        ("publication-ready", True),
    ],
)
def test_owned_stale_prepublication_delivery_is_retryable_only_after_remote_absence(
    tmp_path: Path,
    phase: str,
    local_commit: bool,
) -> None:
    root, remote = _repository(tmp_path)
    builder = _Builder([_patch(1, 9)])
    reviewer = _Reviewer([_pass("must not run")])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
    )
    delivery_id = f"delivery-stale-{phase}-{int(local_commit)}"
    stale = _persist_owned_stale_delivery(
        workflow,
        delivery_id=delivery_id,
        phase=phase,
        local_commit=local_commit,
    )
    prior_commit = stale.commit_sha
    assert _git(
        remote,
        "for-each-ref",
        "--format=%(refname)",
        f"refs/heads/{stale.branch}",
    ) == ""

    recovered = workflow.run(task_id=stale.task_id, delivery_id=delivery_id)

    assert recovered.status == "interrupted"
    assert recovered.phase == "interrupted"
    assert recovered.failure_code == "interrupted-run"
    assert recovered.failure_class == "transient"
    assert recovered.commit_sha == prior_commit
    assert "not published" in recovered.message
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0


def test_successor_fails_closed_if_predecessor_branch_appears_before_run(
    tmp_path: Path,
) -> None:
    root, remote = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    reviewer = _Reviewer([_pass()])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
    )
    predecessor = _safe_retry_predecessor(
        workflow,
        delivery_id="delivery-chain-root-before-run",
    )
    _git(
        root,
        "push",
        "origin",
        f"{predecessor.base_sha}:refs/heads/{predecessor.branch}",
    )

    with mock.patch.object(
        workflow.repository,
        "push",
        wraps=workflow.repository.push,
    ) as push:
        successor = workflow.run(
            task_id=predecessor.task_id,
            delivery_id=f"{predecessor.delivery_id}-r2",
            predecessor_delivery_id=predecessor.delivery_id,
            retry_root_delivery_id=predecessor.delivery_id,
            retry_attempt=2,
        )

    assert successor.status == "failed"
    assert successor.success is False
    assert successor.failure_class == "policy"
    assert successor.failure_code == "artifact-security"
    assert successor.predecessor_delivery_id == predecessor.delivery_id
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0
    push.assert_not_called()
    assert _git(remote, "rev-parse", f"refs/heads/{predecessor.branch}") == (
        predecessor.base_sha
    )


def test_final_prepublication_chain_recheck_blocks_successor_push(
    tmp_path: Path,
) -> None:
    root, remote = _repository(tmp_path)

    class PublishingPredecessorBuilder(_Builder):
        predecessor: DeliveryJob

        def propose(self, **kwargs: Any) -> PatchProposal:
            _git(
                root,
                "push",
                "origin",
                f"{self.predecessor.base_sha}:refs/heads/"
                f"{self.predecessor.branch}",
            )
            return super().propose(**kwargs)

    builder = PublishingPredecessorBuilder([_patch(1, 2)])
    reviewer = _Reviewer([_pass()])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
    )
    predecessor = _safe_retry_predecessor(
        workflow,
        delivery_id="delivery-chain-root-during-build",
    )
    builder.predecessor = predecessor

    with mock.patch.object(
        workflow.repository,
        "push",
        wraps=workflow.repository.push,
    ) as push:
        successor = workflow.run(
            task_id=predecessor.task_id,
            delivery_id=f"{predecessor.delivery_id}-r2",
            predecessor_delivery_id=predecessor.delivery_id,
            retry_root_delivery_id=predecessor.delivery_id,
            retry_attempt=2,
        )

    assert successor.status == "failed"
    assert successor.phase == "failed"
    assert successor.success is False
    assert successor.pushed is False
    assert successor.failure_class == "policy"
    assert successor.failure_code == "artifact-security"
    assert builder.calls == reviewer.calls == validator.calls == 1
    assert github.calls == 0
    push.assert_not_called()
    assert _git(remote, "rev-parse", f"refs/heads/{predecessor.branch}") == (
        predecessor.base_sha
    )
    assert _git(
        remote,
        "for-each-ref",
        "--format=%(refname)",
        f"refs/heads/{successor.branch}",
    ) == ""


def test_r3_checks_full_chain_when_root_branch_reappears(tmp_path: Path) -> None:
    root, remote = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    reviewer = _Reviewer([_pass()])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
    )
    root_predecessor = _safe_retry_predecessor(
        workflow,
        delivery_id="delivery-chain-root-r3",
    )
    retry_two = _safe_retry_predecessor(
        workflow,
        delivery_id="delivery-chain-root-r3-r2",
        predecessor_delivery_id=root_predecessor.delivery_id,
        retry_root_delivery_id=root_predecessor.delivery_id,
        retry_attempt=2,
    )
    _git(
        root,
        "push",
        "origin",
        f"{root_predecessor.base_sha}:refs/heads/{root_predecessor.branch}",
    )
    assert _git(
        remote,
        "for-each-ref",
        "--format=%(refname)",
        f"refs/heads/{retry_two.branch}",
    ) == ""

    retry_three = workflow.run(
        task_id=root_predecessor.task_id,
        delivery_id="delivery-chain-root-r3-r3",
        predecessor_delivery_id=retry_two.delivery_id,
        retry_root_delivery_id=root_predecessor.delivery_id,
        retry_attempt=3,
    )

    assert retry_three.status == "failed"
    assert retry_three.failure_class == "policy"
    assert retry_three.predecessor_delivery_id == retry_two.delivery_id
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_id", "TASK-0002"),
        ("expected_repository", "owner/other-project"),
    ],
)
def test_successor_fails_closed_on_predecessor_binding_tamper(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    reviewer = _Reviewer([_pass()])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
    )
    field_slug = field.replace("_", "-")
    predecessor = _safe_retry_predecessor(
        workflow,
        delivery_id=f"delivery-chain-tampered-{field_slug}",
    )
    setattr(predecessor, field, value)
    workflow.store.persist(predecessor)

    successor = workflow.run(
        task_id="TASK-0001",
        delivery_id=f"{predecessor.delivery_id}-r2",
        predecessor_delivery_id=predecessor.delivery_id,
        retry_root_delivery_id=predecessor.delivery_id,
        retry_attempt=2,
    )

    assert successor.status == "failed"
    assert successor.success is False
    assert successor.failure_class == "policy"
    assert "changed task or repository identity" in successor.message
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "Build an unrelated capability"),
        ("objective", "Replace the previously accepted objective."),
        ("acceptance_criteria", ["A different outcome is accepted"]),
    ],
)
def test_successor_fails_closed_when_task_specification_changed(
    tmp_path: Path,
    field: str,
    value: str | list[str],
) -> None:
    root, remote = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    reviewer = _Reviewer([_pass()])
    validator = _Validator([True])
    github = _GitHub()
    workflow, monday = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
    )
    field_slug = field.replace("_", "-")
    predecessor = _safe_retry_predecessor(
        workflow,
        delivery_id=f"delivery-chain-content-{field_slug}",
    )
    setattr(monday, field, value)
    monday._touch()

    with mock.patch.object(
        workflow.repository,
        "push",
        wraps=workflow.repository.push,
    ) as push:
        successor = workflow.run(
            task_id=predecessor.task_id,
            delivery_id=f"{predecessor.delivery_id}-r2",
            predecessor_delivery_id=predecessor.delivery_id,
            retry_root_delivery_id=predecessor.delivery_id,
            retry_attempt=2,
        )

    assert successor.status == "failed"
    assert successor.success is False
    assert successor.failure_class == "policy"
    assert successor.failure_code == "task-changed"
    assert "changed task specification" in successor.message
    assert successor.objective != predecessor.objective
    assert workflow.get(successor.delivery_id).to_dict() == successor.to_dict()
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0
    push.assert_not_called()
    assert _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads") == (
        "refs/heads/main"
    )


def test_successor_fails_closed_on_empty_legacy_predecessor_specification(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    reviewer = _Reviewer([_pass()])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
    )
    predecessor = _safe_retry_predecessor(
        workflow,
        delivery_id="delivery-chain-empty-objective",
    )
    predecessor.objective = ""
    workflow.store.persist(predecessor)

    with mock.patch.object(
        workflow.repository,
        "push",
        wraps=workflow.repository.push,
    ) as push:
        successor = workflow.run(
            task_id=predecessor.task_id,
            delivery_id=f"{predecessor.delivery_id}-r2",
            predecessor_delivery_id=predecessor.delivery_id,
            retry_root_delivery_id=predecessor.delivery_id,
            retry_attempt=2,
        )

    assert successor.status == "failed"
    assert successor.success is False
    assert successor.failure_class == "policy"
    assert successor.failure_code == "task-changed"
    assert "changed task specification" in successor.message
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0
    push.assert_not_called()


def test_r3_fails_closed_when_root_predecessor_specification_changed(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    reviewer = _Reviewer([_pass()])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
    )
    root_predecessor = _safe_retry_predecessor(
        workflow,
        delivery_id="delivery-chain-content-root",
    )
    retry_two = _safe_retry_predecessor(
        workflow,
        delivery_id=f"{root_predecessor.delivery_id}-r2",
        predecessor_delivery_id=root_predecessor.delivery_id,
        retry_root_delivery_id=root_predecessor.delivery_id,
        retry_attempt=2,
    )
    assert retry_two.objective == root_predecessor.objective
    root_predecessor.objective = "tampered root specification"
    workflow.store.persist(root_predecessor)

    with mock.patch.object(
        workflow.repository,
        "push",
        wraps=workflow.repository.push,
    ) as push:
        retry_three = workflow.run(
            task_id=root_predecessor.task_id,
            delivery_id=f"{root_predecessor.delivery_id}-r3",
            predecessor_delivery_id=retry_two.delivery_id,
            retry_root_delivery_id=root_predecessor.delivery_id,
            retry_attempt=3,
        )

    assert retry_three.status == "failed"
    assert retry_three.success is False
    assert retry_three.failure_class == "policy"
    assert retry_three.failure_code == "task-changed"
    assert "changed task specification" in retry_three.message
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0
    push.assert_not_called()


def test_atomic_task_changed_retry_reservation_survives_crash_and_allows_fresh_request(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    reviewer = _Reviewer([_pass()])
    validator = _Validator([True])
    github = _GitHub()
    workflow, monday = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
    )
    predecessor = _safe_retry_predecessor(
        workflow,
        delivery_id="delivery-chain-retired-content-root",
    )
    monday.objective = "A newly requested specification."
    monday._touch()
    refused_id = f"{predecessor.delivery_id}-r2"
    original_reserve = workflow.store.reserve

    def crash_after_terminal_reservation(job: DeliveryJob) -> DeliveryJob | None:
        existing = original_reserve(job)
        if job.delivery_id == refused_id:
            assert existing is None
            assert job.status == "failed"
            assert job.failure_code == "task-changed"
            raise SystemExit("simulated crash after terminal reservation")
        return existing

    workflow.store.reserve = crash_after_terminal_reservation  # type: ignore[method-assign]
    with pytest.raises(SystemExit, match="after terminal reservation"):
        workflow.run(
            task_id=predecessor.task_id,
            delivery_id=refused_id,
            predecessor_delivery_id=predecessor.delivery_id,
            retry_root_delivery_id=predecessor.delivery_id,
            retry_attempt=2,
        )
    workflow.store.reserve = original_reserve  # type: ignore[method-assign]

    refused = workflow.get(refused_id)

    assert refused.status == "failed"
    assert refused.phase == "failed"
    assert refused.failure_code == "task-changed"
    assert refused.failure_class == "policy"
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0

    replayed = workflow.run(
        task_id=predecessor.task_id,
        delivery_id=refused_id,
        predecessor_delivery_id=predecessor.delivery_id,
        retry_root_delivery_id=predecessor.delivery_id,
        retry_attempt=2,
    )

    assert replayed.to_dict() == refused.to_dict()
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0

    fresh = workflow.run(
        task_id=predecessor.task_id,
        delivery_id="delivery-fresh-after-content-change",
    )

    assert fresh.success is True
    assert fresh.status == "pr-open"
    assert fresh.predecessor_delivery_id == ""
    assert fresh.objective == refused.objective
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 1


def test_valid_predecessor_allows_deterministic_successor_and_survives_reload(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    reviewer = _Reviewer([_pass()])
    validator = _Validator([True])
    github = _GitHub()
    workflow, monday = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
    )
    predecessor = _safe_retry_predecessor(
        workflow,
        delivery_id="delivery-chain-valid-root",
    )
    successor_id = f"{predecessor.delivery_id}-r2"

    successor = workflow.run(
        task_id=predecessor.task_id,
        delivery_id=successor_id,
        predecessor_delivery_id=predecessor.delivery_id,
        retry_root_delivery_id=predecessor.delivery_id,
        retry_attempt=2,
    )

    assert successor.success is True
    assert successor.status == "pr-open"
    assert successor.predecessor_delivery_id == predecessor.delivery_id
    assert successor.objective == predecessor.objective
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 1

    replay_builder = _Builder([_patch(1, 9)])
    replay_reviewer = _Reviewer([_pass("must not run")])
    replay_validator = _Validator([True])
    replay_github = _GitHub()
    restarted, _ = _workflow(
        root,
        builder=replay_builder,
        reviewer=replay_reviewer,
        validator=replay_validator,
        github=replay_github,
        monday=monday,
    )
    reloaded = restarted.get(successor_id)
    replayed = restarted.run(
        task_id=predecessor.task_id,
        delivery_id=successor_id,
        predecessor_delivery_id=predecessor.delivery_id,
        retry_root_delivery_id=predecessor.delivery_id,
        retry_attempt=2,
    )

    assert reloaded.predecessor_delivery_id == predecessor.delivery_id
    assert replayed.to_dict() == reloaded.to_dict()
    assert replay_builder.calls == 0
    assert replay_reviewer.calls == replay_validator.calls == replay_github.calls == 0


@pytest.mark.parametrize("lineage", ["omitted", "wrong"])
def test_safe_predecessor_requires_exact_successor_lineage_before_builder(
    tmp_path: Path,
    lineage: str,
) -> None:
    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    reviewer = _Reviewer([_pass()])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
    )
    predecessor = _safe_retry_predecessor(
        workflow,
        delivery_id="delivery-adversarial-lineage-root",
    )
    lineage_kwargs: dict[str, object] = {}
    if lineage == "wrong":
        lineage_kwargs = {
            "predecessor_delivery_id": "delivery-adversarial-wrong",
            "retry_root_delivery_id": predecessor.delivery_id,
            "retry_attempt": 2,
        }

    try:
        successor = workflow.run(
            task_id=predecessor.task_id,
            delivery_id=f"{predecessor.delivery_id}-r2",
            **lineage_kwargs,
        )
    except (ArtifactSecurityError, ValueError):
        successor = None

    if successor is not None:
        assert successor.status == "failed"
        assert successor.success is False
        assert successor.failure_class == "policy"
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0


def test_unsafe_retry_child_cannot_consume_safe_root_lineage(
    tmp_path: Path,
) -> None:
    root, remote = _repository(tmp_path)

    class PublishingRootBuilder(_Builder):
        predecessor: DeliveryJob

        def propose(self, **kwargs: Any) -> PatchProposal:
            _git(
                root,
                "push",
                "origin",
                f"{self.predecessor.base_sha}:refs/heads/"
                f"{self.predecessor.branch}",
            )
            return super().propose(**kwargs)

    builder = PublishingRootBuilder([_patch(1, 2)])
    reviewer = _Reviewer([_pass()])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
    )
    predecessor = _safe_retry_predecessor(
        workflow,
        delivery_id="delivery-adversarial-consumed-root",
    )
    builder.predecessor = predecessor

    unsafe_child = DeliveryJob.from_dict(
        predecessor.to_dict(include_private=True)
    )
    unsafe_child.delivery_id = f"{predecessor.delivery_id}-r2"
    unsafe_child.predecessor_delivery_id = predecessor.delivery_id
    unsafe_child.retry_root_delivery_id = predecessor.delivery_id
    unsafe_child.retry_attempt = 2
    unsafe_child.status = "failed"
    unsafe_child.phase = "failed"
    unsafe_child.success = False
    unsafe_child.base_ref = ""
    unsafe_child.base_branch = ""
    unsafe_child.base_sha = ""
    unsafe_child.repository = ""
    unsafe_child.branch = ""
    unsafe_child.worktree = ""
    unsafe_child.worktree_owner_token = ""
    unsafe_child.commit_sha = ""
    unsafe_child.failure_code = "builder-unavailable"
    unsafe_child.failure_class = "permanent"
    unsafe_child.interrupted_from_phase = ""
    unsafe_child.interruption_proof_sha256 = ""
    assert workflow.store.reserve(unsafe_child) is None

    with mock.patch.object(
        workflow.repository,
        "push",
        wraps=workflow.repository.push,
    ) as successor_push:
        fresh = workflow.run(
            task_id=predecessor.task_id,
            delivery_id="delivery-adversarial-unbound-after-unsafe-child",
        )

    assert fresh.status == "failed"
    assert fresh.success is False
    assert fresh.failure_class == "policy"
    assert fresh.failure_code == "artifact-security"
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0
    successor_push.assert_not_called()
    assert _git(
        remote,
        "for-each-ref",
        "--format=%(refname)",
        f"refs/heads/{predecessor.branch}",
    ) == ""


def test_fresh_identity_cannot_bypass_permanent_pushing_interruption(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    reviewer = _Reviewer([_pass()])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
    )
    predecessor = _permanent_pushing_interruption(
        workflow,
        delivery_id="delivery-adversarial-pushing-root",
    )

    successor = workflow.run(
        task_id=predecessor.task_id,
        delivery_id=f"{predecessor.delivery_id}-r2",
    )

    assert successor.status == "failed"
    assert successor.success is False
    assert successor.failure_class == "policy"
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0


def test_pushing_progress_callback_cannot_publish_predecessor_before_successor_push(
    tmp_path: Path,
) -> None:
    root, remote = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    reviewer = _Reviewer([_pass()])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
    )
    predecessor = _safe_retry_predecessor(
        workflow,
        delivery_id="delivery-adversarial-pushing-callback-root",
    )
    callback_published = False

    def publish_predecessor(snapshot: dict[str, Any]) -> None:
        nonlocal callback_published
        if snapshot["phase"] == "pushing" and not callback_published:
            callback_published = True
            _git(
                root,
                "push",
                "origin",
                f"{predecessor.base_sha}:refs/heads/{predecessor.branch}",
            )

    with mock.patch.object(
        workflow.repository,
        "push",
        wraps=workflow.repository.push,
    ) as successor_push:
        successor = workflow.run(
            task_id=predecessor.task_id,
            delivery_id=f"{predecessor.delivery_id}-r2",
            predecessor_delivery_id=predecessor.delivery_id,
            retry_root_delivery_id=predecessor.delivery_id,
            retry_attempt=2,
            progress_callback=publish_predecessor,
        )

    assert callback_published is True
    assert successor.success is False
    assert successor.pushed is False
    assert github.calls == 0
    successor_push.assert_not_called()
    assert _git(remote, "rev-parse", f"refs/heads/{predecessor.branch}") == (
        predecessor.base_sha
    )


def test_pull_request_progress_callback_cannot_strand_pushed_successor(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    reviewer = _Reviewer([_pass()])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
    )
    predecessor = _safe_retry_predecessor(
        workflow,
        delivery_id="delivery-adversarial-pr-callback-root",
    )
    callback_opened_predecessor = False

    def open_predecessor(snapshot: dict[str, Any]) -> None:
        nonlocal callback_opened_predecessor
        if snapshot["phase"] == "pull-request" and not callback_opened_predecessor:
            callback_opened_predecessor = True
            predecessor.status = "pr-open"
            predecessor.phase = "completed"
            predecessor.success = True
            predecessor.pushed = True
            predecessor.commit_sha = predecessor.base_sha
            predecessor.pr_number = 91
            predecessor.pr_url = "https://github.com/owner/project/pull/91"
            predecessor.pr_state = "open"
            predecessor.checks_status = "pending"
            predecessor.failure_code = ""
            predecessor.failure_class = ""
            workflow.store.persist(predecessor)

    successor = workflow.run(
        task_id=predecessor.task_id,
        delivery_id=f"{predecessor.delivery_id}-r2",
        predecessor_delivery_id=predecessor.delivery_id,
        retry_root_delivery_id=predecessor.delivery_id,
        retry_attempt=2,
        progress_callback=open_predecessor,
    )

    assert callback_opened_predecessor is True
    assert successor.success is True
    assert successor.pushed is True
    assert successor.status == "pr-open"
    assert github.calls == 1


@pytest.mark.parametrize("tamper", ["truncate", "rebind"])
def test_r3_rejects_tampered_middle_lineage_link(
    tmp_path: Path,
    tamper: str,
) -> None:
    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    reviewer = _Reviewer([_pass()])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
    )
    root_predecessor = _safe_retry_predecessor(
        workflow,
        delivery_id="delivery-adversarial-middle-root",
    )
    retry_two = _safe_retry_predecessor(
        workflow,
        delivery_id=f"{root_predecessor.delivery_id}-r2",
        predecessor_delivery_id=root_predecessor.delivery_id,
        retry_root_delivery_id=root_predecessor.delivery_id,
        retry_attempt=2,
    )
    if tamper == "truncate":
        retry_two.predecessor_delivery_id = ""
    else:
        alternate = DeliveryJob.from_dict(
            root_predecessor.to_dict(include_private=True)
        )
        alternate.delivery_id = "delivery-adversarial-alternate-root"
        alternate.status = "failed"
        alternate.phase = "failed"
        alternate.success = False
        alternate.base_ref = ""
        alternate.base_branch = ""
        alternate.base_sha = ""
        alternate.repository = ""
        alternate.branch = ""
        alternate.worktree = ""
        alternate.worktree_owner_token = ""
        alternate.commit_sha = ""
        alternate.failure_code = "provider-timeout"
        alternate.failure_class = "transient"
        alternate.predecessor_delivery_id = ""
        alternate.retry_root_delivery_id = ""
        alternate.retry_attempt = 0
        alternate.interrupted_from_phase = ""
        alternate.interruption_proof_sha256 = ""
        assert workflow.store.reserve(alternate) is None
        retry_two.predecessor_delivery_id = alternate.delivery_id
    workflow.store.persist(retry_two)

    retry_three = workflow.run(
        task_id=root_predecessor.task_id,
        delivery_id=f"{root_predecessor.delivery_id}-r3",
        predecessor_delivery_id=retry_two.delivery_id,
        retry_root_delivery_id=root_predecessor.delivery_id,
        retry_attempt=3,
    )

    assert retry_three.status == "failed"
    assert retry_three.success is False
    assert retry_three.failure_class == "policy"
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0


def test_transient_flip_cannot_authorize_permanent_pushing_interruption(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    reviewer = _Reviewer([_pass()])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
    )
    predecessor = _permanent_pushing_interruption(
        workflow,
        delivery_id="delivery-adversarial-transient-flip-root",
    )
    predecessor.failure_class = "transient"
    workflow.store.persist(predecessor)

    successor = workflow.run(
        task_id=predecessor.task_id,
        delivery_id=f"{predecessor.delivery_id}-r2",
        predecessor_delivery_id=predecessor.delivery_id,
        retry_root_delivery_id=predecessor.delivery_id,
        retry_attempt=2,
    )

    assert successor.status == "failed"
    assert successor.success is False
    assert successor.failure_class == "policy"
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0


def test_older_timestamp_cannot_hide_any_matching_live_pull_request(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    reviewer = _Reviewer([_pass()])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
    )
    predecessor = _safe_retry_predecessor(
        workflow,
        delivery_id="delivery-adversarial-timestamp-root",
    )
    hidden_open_pr = DeliveryJob.from_dict(
        predecessor.to_dict(include_private=True)
    )
    hidden_open_pr.delivery_id = "delivery-adversarial-hidden-open-pr"
    hidden_open_pr.status = "pr-open"
    hidden_open_pr.phase = "completed"
    hidden_open_pr.success = True
    hidden_open_pr.pushed = True
    hidden_open_pr.commit_sha = predecessor.base_sha
    hidden_open_pr.pr_number = 92
    hidden_open_pr.pr_url = "https://github.com/owner/project/pull/92"
    hidden_open_pr.pr_state = "open"
    hidden_open_pr.checks_status = "pending"
    hidden_open_pr.failure_code = ""
    hidden_open_pr.failure_class = ""
    hidden_open_pr.predecessor_delivery_id = ""
    hidden_open_pr.retry_root_delivery_id = ""
    hidden_open_pr.retry_attempt = 0
    hidden_open_pr.interrupted_from_phase = ""
    hidden_open_pr.interruption_proof_sha256 = ""
    hidden_open_pr.created_at = "2000-01-01T00:00:00+00:00"
    hidden_open_pr.updated_at = hidden_open_pr.created_at
    assert hidden_open_pr.created_at < predecessor.created_at
    assert workflow.store.reserve(hidden_open_pr) is None

    successor = workflow.run(
        task_id=predecessor.task_id,
        delivery_id=f"{predecessor.delivery_id}-r2",
        predecessor_delivery_id=predecessor.delivery_id,
        retry_root_delivery_id=predecessor.delivery_id,
        retry_attempt=2,
    )

    assert successor.status == "failed"
    assert successor.success is False
    assert successor.failure_class == "policy"
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0


def test_same_id_reservation_race_uses_prepublication_absence_proof(
    tmp_path: Path,
) -> None:
    root, remote = _repository(tmp_path)
    builder = _Builder([_patch(1, 9)])
    reviewer = _Reviewer([_pass("must not run")])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
    )
    stale = _persist_owned_stale_delivery(
        workflow,
        delivery_id="delivery-raced-prepublication",
        phase="reviewing",
    )
    assert _git(
        remote,
        "for-each-ref",
        "--format=%(refname)",
        f"refs/heads/{stale.branch}",
    ) == ""

    with _miss_initial_delivery_lookup(workflow):
        recovered = workflow.run(
            task_id=stale.task_id,
            delivery_id=stale.delivery_id,
        )

    assert recovered.status == "interrupted"
    assert recovered.phase == "interrupted"
    assert recovered.failure_code == "interrupted-run"
    assert recovered.failure_class == "transient"
    assert "not published" in recovered.message
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0


def test_same_id_reservation_race_reconciles_exact_remote_publication(
    tmp_path: Path,
) -> None:
    root, remote = _repository(tmp_path)
    builder = _Builder([_patch(1, 9)])
    reviewer = _Reviewer([_pass("must not run")])
    validator = _Validator([True])
    github = _GitHub()
    monday = _Monday()
    monday.status = "in-progress"
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
        monday=monday,
    )
    stale = _persist_owned_stale_delivery(
        workflow,
        delivery_id="delivery-raced-publication",
        phase="pushing",
        local_commit=True,
    )
    stale.artifact_sha256 = "a" * 64
    stale.reviewed_artifact_sha256 = stale.artifact_sha256
    stale.review_expected_task_revision = _task_revision(root)
    workflow.store.persist(stale)
    _git(
        root,
        "push",
        "origin",
        f"{stale.commit_sha}:refs/heads/{stale.branch}",
    )
    assert _git(remote, "rev-parse", f"refs/heads/{stale.branch}") == stale.commit_sha

    with (
        _miss_initial_delivery_lookup(workflow),
        mock.patch.object(
            workflow.repository,
            "push",
            side_effect=AssertionError("publication reconciliation must not push twice"),
        ),
    ):
        recovered = workflow.run(
            task_id=stale.task_id,
            delivery_id=stale.delivery_id,
        )

    assert recovered.status == "pr-open"
    assert recovered.phase == "completed"
    assert recovered.success is True
    assert recovered.pushed is True
    assert recovered.commit_sha == stale.commit_sha
    assert github.calls == 1
    assert monday.status == "review"
    assert builder.calls == reviewer.calls == validator.calls == 0


@pytest.mark.parametrize(
    ("phase", "local_commit"),
    [
        ("reviewing", False),
        ("publication-ready", True),
    ],
)
def test_owned_stale_delivery_with_remote_branch_stays_permanent(
    tmp_path: Path,
    phase: str,
    local_commit: bool,
) -> None:
    root, remote = _repository(tmp_path)
    builder = _Builder([_patch(1, 9)])
    reviewer = _Reviewer([_pass("must not run")])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
    )
    delivery_id = f"delivery-remote-{phase}"
    stale = _persist_owned_stale_delivery(
        workflow,
        delivery_id=delivery_id,
        phase=phase,
        local_commit=local_commit,
    )
    published_sha = stale.commit_sha or stale.base_sha
    _git(
        root,
        "push",
        "origin",
        f"{published_sha}:refs/heads/{stale.branch}",
    )
    assert _git(remote, "rev-parse", f"refs/heads/{stale.branch}") == published_sha

    recovered = workflow.run(task_id=stale.task_id, delivery_id=delivery_id)

    assert recovered.status == "interrupted"
    assert recovered.failure_code == "interrupted-run"
    assert recovered.failure_class == "permanent"
    assert "new build request" in recovered.message
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0


@pytest.mark.parametrize("ownership_failure", ["missing", "tampered"])
def test_owned_stale_delivery_with_unproven_ownership_stays_permanent(
    tmp_path: Path,
    ownership_failure: str,
) -> None:
    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 9)])
    reviewer = _Reviewer([_pass("must not run")])
    validator = _Validator([True])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=validator,
        github=github,
    )
    delivery_id = f"delivery-owner-{ownership_failure}"
    stale = _persist_owned_stale_delivery(
        workflow,
        delivery_id=delivery_id,
        phase="validating",
    )
    owner_marker = (
        workflow.store.root
        / "owners"
        / f"{Path(stale.worktree).name}.owner.json"
    )
    if ownership_failure == "missing":
        owner_marker.unlink()
    else:
        marker = json.loads(owner_marker.read_text(encoding="utf-8"))
        marker["owner_token"] = "0" * 64
        owner_marker.write_text(
            json.dumps(marker, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    recovered = workflow.run(task_id=stale.task_id, delivery_id=delivery_id)

    assert recovered.status == "interrupted"
    assert recovered.failure_code == "interrupted-run"
    assert recovered.failure_class == "permanent"
    assert "new build request" in recovered.message
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 0


def test_stale_reviewing_delivery_with_changed_local_head_stays_permanent(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 9)]),
        reviewer=_Reviewer([_pass("must not run")]),
        validator=_Validator([True]),
        github=_GitHub(),
    )
    stale = _persist_owned_stale_delivery(
        workflow,
        delivery_id="delivery-changed-head",
        phase="reviewing",
    )
    worktree = workflow.repository.recover_worktree(
        path=Path(stale.worktree),
        branch=stale.branch,
        base_sha=stale.base_sha,
        owner_token=stale.worktree_owner_token,
    )
    (worktree.path / "app.py").write_text("VALUE = 7\n", encoding="utf-8")
    workflow.repository.stage(worktree, ["app.py"])
    staged_diff = workflow.repository.staged_diff(worktree)
    workflow.repository.commit(
        worktree,
        ["app.py"],
        "Unexpected local commit",
        expected_diff_sha256=hashlib.sha256(
            staged_diff.encode("utf-8")
        ).hexdigest(),
    )

    recovered = workflow.run(
        task_id=stale.task_id,
        delivery_id=stale.delivery_id,
    )

    assert recovered.status == "interrupted"
    assert recovered.failure_class == "permanent"
    assert "new build request" in recovered.message


def test_live_nonterminal_delivery_is_returned_when_lease_is_busy(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
    )
    workflow.lease_timeout = 0.01
    _persist_delivery(
        workflow,
        delivery_id="delivery-live-worker",
        status="running",
        phase="implementing",
    )

    with workflow._workflow_lease():
        snapshot = workflow.run(
            task_id="TASK-0001",
            delivery_id="delivery-live-worker",
        )

    assert snapshot.status == "running"
    assert snapshot.phase == "implementing"
    assert snapshot.failure_code == ""
    assert workflow.get("delivery-live-worker").status == "running"


def test_terminal_delivery_is_returned_without_recovery_mutation(tmp_path: Path) -> None:
    root, _ = _repository(tmp_path)
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
    )
    original = _persist_delivery(
        workflow,
        delivery_id="delivery-already-terminal",
        status="failed",
        phase="failed",
    )
    original.failure_code = "reviewer-unavailable"
    original.message = "Already settled"
    workflow.store.persist(original)

    same = workflow.run(
        task_id="TASK-0001",
        delivery_id="delivery-already-terminal",
    )

    assert same.status == "failed"
    assert same.failure_code == "reviewer-unavailable"
    assert same.message == "Already settled"


def test_failed_add_delete_attempt_is_replaced_from_base_by_codex_repair(
    tmp_path: Path,
) -> None:
    root, remote = _repository(tmp_path)
    builder = _Builder(
        [_failed_add_delete_patch(), _patch(1, 3)],
        name="codex-chatgpt",
    )
    reviewer = _Reviewer([_pass("Second version approved")])
    validator = _Validator([False, True])
    github = _GitHub()
    workflow, _ = _workflow(
        root, builder=builder, reviewer=reviewer, validator=validator, github=github
    )

    job = workflow.run(task_id="TASK-0002", delivery_id="delivery-validation-repair")

    assert job.success is True
    assert [attempt["status"] for attempt in job.attempts] == [
        "validation-failed",
        "approved",
    ]
    assert reviewer.calls == 1
    assert _git(remote, "show", f"{job.branch}:app.py") == "VALUE = 3"
    assert "failed.py" not in _git(remote, "ls-tree", "--name-only", job.branch).splitlines()


def test_failed_validation_output_never_reaches_repair_model_or_record(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    encoded_source = base64.b64encode(
        b"proprietary source copied by a candidate test"
    ).decode("ascii")
    builder = _Builder([_patch(1, 2), _patch(1, 3)])
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([False, True], output=encoded_source),
        github=_GitHub(),
    )

    job = workflow.run(
        task_id="TASK-0002",
        delivery_id="delivery-validation-output-withheld",
    )
    stored = (
        workflow.store.records / "delivery-validation-output-withheld.json"
    ).read_text(encoding="utf-8")
    repair_feedback = str(builder.requests[1]["feedback"])

    assert job.success is True
    assert encoded_source not in repair_feedback
    assert encoded_source not in stored
    assert "tests failed (exit 1)" in repair_feedback
    assert "Raw failed output withheld by the delivery controller." in stored


def test_reviewer_requested_change_runs_full_validation_and_review_again(
    tmp_path: Path,
) -> None:
    root, remote = _repository(tmp_path)
    builder = _Builder([_patch(1, 2), _patch(1, 3)])
    reviewer = _Reviewer(
        [
            ReviewDecision(
                "needs_changes",
                "high",
                "Use the correct target",
                findings=["VALUE should be 3"],
            ),
            _pass(),
        ]
    )
    validator = _Validator([True, True])
    github = _GitHub()
    workflow, _ = _workflow(
        root, builder=builder, reviewer=reviewer, validator=validator, github=github
    )

    job = workflow.run(task_id="TASK-0003", delivery_id="delivery-review-repair")

    assert job.success is True
    assert [attempt["status"] for attempt in job.attempts] == ["needs-changes", "approved"]
    assert reviewer.calls == validator.calls == builder.calls == 2
    assert _git(remote, "show", f"{job.branch}:app.py") == "VALUE = 3"


def test_reviewer_block_is_a_non_retryable_policy_outcome(tmp_path: Path) -> None:
    root, remote = _repository(tmp_path)
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer(
            [ReviewDecision("block", "high", "The proposed change violates policy")]
        ),
        validator=_Validator([True]),
        github=github,
    )

    job = workflow.run(task_id="TASK-0003", delivery_id="delivery-review-policy-block")

    assert job.status == "rejected"
    assert job.failure_code == "review-blocked"
    assert job.failure_class == "policy"
    assert job.retry_after_seconds == 0
    assert github.calls == 0
    assert _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/codex") == ""


def test_unavailable_reviewer_fails_before_worktree_or_builder(tmp_path: Path) -> None:
    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    reviewer = _UnavailableReviewer([_pass()])
    github = _GitHub()
    workflow, monday = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=_Validator([True]),
        github=github,
    )

    job = workflow.run(task_id="TASK-0004", delivery_id="delivery-no-reviewer")

    assert job.success is False
    assert job.failure_code == "reviewer-unavailable"
    assert job.failure_class == "permanent"
    assert job.retry_after_seconds == 0
    assert builder.calls == github.calls == 0
    assert monday.calls == ["get"]
    assert not (root / "logs" / "delivery" / "worktrees").exists()


def test_structured_temporary_reviewer_outage_is_durable_and_idempotent(
    tmp_path: Path,
) -> None:
    class _TemporarilyUnavailableReviewer(_Reviewer):
        def availability(self) -> ToolAvailability:
            return ToolAvailability(
                False,
                self.name,
                "review service is unavailable",
                failure_class="transient",
                retry_after_seconds=45,
            )

    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    reviewer = _TemporarilyUnavailableReviewer([_pass()])
    github = _GitHub()
    workflow, monday = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=_Validator([True]),
        github=github,
    )

    job = workflow.run(
        task_id="TASK-0004",
        delivery_id="delivery-temporary-reviewer-outage",
    )

    assert job.status == "failed"
    assert job.failure_code == "reviewer-unavailable"
    assert job.failure_class == "transient"
    assert job.retry_after_seconds == 45
    assert workflow.get(job.delivery_id).to_dict() == job.to_dict()

    same = workflow.run(task_id="TASK-0004", delivery_id=job.delivery_id)
    assert same.failure_class == "transient"
    assert same.retry_after_seconds == 45
    assert builder.calls == github.calls == 0
    # Exact-ID terminal replay is resolved from its durable binding before any
    # second live task read.
    assert monday.calls == ["get"]


def test_typed_github_availability_timeout_is_transient(tmp_path: Path) -> None:
    class _TimedOutGitHub(_GitHub):
        def availability(self) -> ToolAvailability:
            raise subprocess.TimeoutExpired("gh auth status", 20)

    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    github = _TimedOutGitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
    )

    job = workflow.run(
        task_id="TASK-0004",
        delivery_id="delivery-github-availability-timeout",
    )

    assert job.status == "failed"
    assert job.failure_code == "github-unavailable"
    assert job.failure_class == "transient"
    assert job.retry_after_seconds == 0
    assert job.commit_sha == ""
    assert job.pushed is False
    assert job.pr_url == ""
    assert builder.calls == github.calls == 0


def test_typed_reviewer_runtime_timeout_is_transient_before_commit(
    tmp_path: Path,
) -> None:
    class _TimedOutReviewer(_Reviewer):
        def review(self, **_: Any) -> ReviewDecision:
            self.calls += 1
            raise subprocess.TimeoutExpired(self.name, 30)

    root, remote = _repository(tmp_path)
    reviewer = _TimedOutReviewer([_pass()])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=reviewer,
        validator=_Validator([True]),
        github=github,
    )

    job = workflow.run(
        task_id="TASK-0004",
        delivery_id="delivery-reviewer-runtime-timeout",
    )

    assert job.status == "failed"
    assert job.failure_code == "runtime"
    assert job.failure_class == "transient"
    assert job.retry_after_seconds == 0
    assert job.commit_sha == ""
    assert job.pushed is False
    assert job.pr_url == ""
    assert reviewer.calls == 2
    assert github.calls == 0
    assert _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/codex") == ""


def test_invalid_repository_identity_fails_preflight_before_builder_or_fetch(
    tmp_path: Path,
) -> None:
    from delivery.git import GitValidationError

    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    github = _GitHub()
    workflow, monday = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
    )

    def _invalid_origin() -> str:
        raise GitValidationError("origin is not a supported github.com repository URL")

    workflow.repository.github_slug = _invalid_origin  # type: ignore[method-assign]
    workflow.repository.resolve_base = (  # type: ignore[method-assign]
        lambda: (_ for _ in ()).throw(AssertionError("preflight must not fetch"))
    )

    capabilities = workflow.capabilities()
    job = workflow.run(task_id="TASK-0004", delivery_id="delivery-invalid-origin")

    assert capabilities["ready"] is False
    assert capabilities["repository"]["available"] is False
    assert capabilities["validation"]["available"] is True
    assert job.failure_code == "repository-unavailable"
    assert job.failure_class == "policy"
    assert job.retry_after_seconds == 0
    assert builder.calls == github.calls == 0
    assert monday.calls == ["get"]
    assert not (root / "logs" / "delivery" / "worktrees").exists()


def test_post_review_drift_invalidates_approval_and_never_pushes(tmp_path: Path) -> None:
    root, remote = _repository(tmp_path)
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()], mutate=True),
        validator=_Validator([True]),
        github=github,
    )

    job = workflow.run(task_id="TASK-0005", delivery_id="delivery-drift")

    assert job.success is False
    assert "unstaged" in job.message
    assert github.calls == 0
    assert _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/codex") == ""


def test_repair_budget_exhaustion_never_pushes(tmp_path: Path) -> None:
    root, remote = _repository(tmp_path)
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2), _patch(1, 3)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([False, False]),
        github=github,
        max_attempts=2,
    )

    job = workflow.run(task_id="TASK-0006", delivery_id="delivery-exhausted")

    assert job.success is False
    assert job.failure_code == "repair-exhausted"
    assert job.failure_class == "permanent"
    assert job.retry_after_seconds == 0
    assert len(job.attempts) == 2
    assert github.calls == 0
    assert _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/codex") == ""
    retained = Path(job.worktree)
    assert retained.exists()
    assert (retained / "app.py").read_text(encoding="utf-8") == "VALUE = 3\n"
    assert "+VALUE = 3" in _git(retained, "diff", "--cached")


def test_builder_provider_failure_falls_back_without_consuming_patch_attempt(
    tmp_path: Path,
) -> None:
    root, remote = _repository(tmp_path)
    provider_secret = _fixture("sk-", "providerfailuresecret1234567890")
    claude = _FailingBuilder(
        name="claude-code",
        message=f"rate limited with {provider_secret}",
    )
    codex = _Builder([_patch(1, 2)], name="codex-chatgpt")
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=[claude, codex],
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
        max_attempts=1,
    )

    job = workflow.run(task_id="TASK-0007", delivery_id="delivery-provider-failover")

    assert job.success is True
    assert job.attempt == 1
    assert len(job.attempts) == 1
    assert job.attempts[0]["number"] == 1
    assert job.attempts[0]["builder"] == "codex-chatgpt"
    assert job.attempts[0]["provider_failures"] == [
        {"builder": "claude-code", "message": "rate limited with [REDACTED]"}
    ]
    assert provider_secret not in str(job.to_dict())
    assert claude.calls == codex.calls == github.calls == 1
    assert _git(remote, "show", f"{job.branch}:app.py") == "VALUE = 2"


def test_all_builder_provider_failures_stop_before_any_patch_or_push(
    tmp_path: Path,
) -> None:
    root, remote = _repository(tmp_path)
    claude = _FailingBuilder(name="claude-code", message="subscription unavailable")
    codex = _FailingBuilder(
        name="codex-chatgpt",
        message="temporary model outage",
        raises=False,
    )
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=[claude, codex],
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
        max_attempts=1,
    )

    job = workflow.run(task_id="TASK-0008", delivery_id="delivery-all-providers-failed")

    assert job.success is False
    assert job.failure_code == "builders-failed"
    # The word "temporary" in provider-controlled prose is not a retry signal.
    assert job.failure_class == "permanent"
    assert job.retry_after_seconds == 0
    assert job.attempt == 0
    assert job.attempts == []
    assert "claude-code: subscription unavailable" in job.message
    assert "codex-chatgpt: temporary model outage" in job.message
    assert claude.calls == codex.calls == 1
    assert github.calls == 0
    assert _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/codex") == ""


def test_typed_builder_timeouts_are_classified_without_reading_provider_prose(
    tmp_path: Path,
) -> None:
    class _TimedOutBuilder(_FailingBuilder):
        def propose(self, **_: Any) -> PatchProposal:
            self.calls += 1
            raise subprocess.TimeoutExpired(self.name, 30)

    root, remote = _repository(tmp_path)
    builder = _TimedOutBuilder(name="codex-chatgpt", message="ignored")
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
        max_attempts=1,
    )

    job = workflow.run(
        task_id="TASK-0008",
        delivery_id="delivery-builder-timeout",
    )

    assert job.status == "failed"
    assert job.failure_code == "builders-failed"
    assert job.failure_class == "transient"
    assert job.retry_after_seconds == 0
    assert job.attempt == 0
    assert job.commit_sha == ""
    assert job.pushed is False
    assert job.pr_url == ""
    assert workflow.get(job.delivery_id).failure_class == "transient"
    assert builder.calls == 1
    assert github.calls == 0
    assert _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/codex") == ""


def test_builder_policy_error_dominates_a_wrapping_timeout(tmp_path: Path) -> None:
    class _PolicyWrappedBuilder(_FailingBuilder):
        def propose(self, **_: Any) -> PatchProposal:
            self.calls += 1
            try:
                raise ArtifactSecurityError("unsafe artifact")
            except ArtifactSecurityError as exc:
                raise TimeoutError("typed wrapper") from exc

    root, remote = _repository(tmp_path)
    builder = _PolicyWrappedBuilder(name="codex-chatgpt", message="ignored")
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
        max_attempts=1,
    )

    job = workflow.run(
        task_id="TASK-0008",
        delivery_id="delivery-builder-policy-chain",
    )

    assert job.status == "failed"
    assert job.failure_code == "builders-failed"
    assert job.failure_class == "policy"
    assert job.retry_after_seconds == 0
    assert job.commit_sha == ""
    assert job.pushed is False
    assert job.pr_url == ""
    assert builder.calls == 1
    assert github.calls == 0
    assert _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/codex") == ""


def test_unsafe_proposal_is_a_bounded_patch_attempt_and_can_be_repaired(
    tmp_path: Path,
) -> None:
    root, remote = _repository(tmp_path)
    unsafe_patch = (
        "diff --git a/../outside.py b/../outside.py\n"
        "--- a/../outside.py\n"
        "+++ b/../outside.py\n"
        "@@ -0,0 +1 @@\n"
        "+unsafe = True\n"
    )
    builder = _Builder([unsafe_patch, _patch(1, 2)])
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
        max_attempts=2,
    )

    job = workflow.run(task_id="TASK-0009", delivery_id="delivery-unsafe-repair")

    assert job.success is True
    assert job.attempt == 2
    assert [attempt["status"] for attempt in job.attempts] == [
        "patch-invalid",
        "approved",
    ]
    assert "unsafe patch path" in job.attempts[0]["message"]
    assert builder.calls == 2
    assert _git(remote, "show", f"{job.branch}:app.py") == "VALUE = 2"


def test_secret_like_task_input_is_rejected_before_persistence_or_model_call(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    objective_secret = _fixture("sk-", "objectivesecret1234567890")
    builder = _Builder([_patch(1, 2)])
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
        monday=_Monday(objective=f"Set VALUE safely using {objective_secret}"),
    )

    try:
        workflow.run(task_id="TASK-0010", delivery_id="delivery-secret-objective")
    except ValueError as exc:
        assert "credential-like material" in str(exc)
    else:  # pragma: no cover - task secrets must never reach a model or record
        raise AssertionError("secret-like task input was accepted")

    assert builder.calls == 0
    assert not (workflow.store.records / "delivery-secret-objective.json").exists()


def test_all_model_and_validation_output_is_redacted_before_job_persistence(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    summary_secret = _fixture("ghp_", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    validation_secret = _fixture("AKIA", "BBBBBBBBBBBBBBBB")
    review_secret = _fixture("eyJabcde", ".abcde12345", ".signature12345")
    finding_secret = _fixture("12345678", ":", "telegramsecretvalue1234567890")
    recommendation_secret = _fixture("password=", "SuperSecretPassword12345")
    monday = _Monday()
    builder = _Builder([_patch(1, 2)], summary=f"used {summary_secret}")
    reviewer = _Reviewer(
        [
            ReviewDecision(
                "block",
                "high",
                f"review detected {review_secret}",
                findings=[f"remove {finding_secret}"],
                recommendations=[f"rotate {recommendation_secret}"],
            )
        ]
    )
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=reviewer,
        validator=_Validator([True], output=f"test log {validation_secret}"),
        github=_GitHub(),
        monday=monday,
    )

    job = workflow.run(task_id="TASK-0010", delivery_id="delivery-redacted-record")
    stored = (workflow.store.records / "delivery-redacted-record.json").read_text(
        encoding="utf-8"
    )

    assert job.status == "rejected"
    for secret in (
        summary_secret,
        validation_secret,
        review_secret,
        finding_secret,
        recommendation_secret,
    ):
        assert secret not in stored
    assert stored.count("[REDACTED]") >= 5
    assert "Raw successful output withheld by the delivery controller." in stored


@pytest.mark.parametrize("task_status", ["blocked", "review"])
def test_publication_ready_rejects_task_that_is_no_longer_in_progress(
    tmp_path: Path,
    task_status: str,
) -> None:
    root, remote = _repository(tmp_path)
    github = _GitHub()
    workflow, monday = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
    )
    state_changed = False

    def change_task_state(snapshot: dict[str, Any]) -> None:
        nonlocal state_changed
        if snapshot["phase"] == "publication-ready" and not state_changed:
            state_changed = True
            monday.status = task_status
            monday._touch()

    job = workflow.run(
        task_id="TASK-0001",
        delivery_id=f"delivery-publication-ready-{task_status}",
        progress_callback=change_task_state,
    )

    assert state_changed is True
    assert job.success is False
    assert job.status == "failed"
    assert job.failure_code == "task-changed"
    assert job.failure_class == "policy"
    assert job.commit_sha == ""
    assert job.pushed is False
    assert github.calls == 0
    assert _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/codex") == ""


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("title", "A different task title"),
        ("objective", "A different reviewed objective."),
        ("acceptance_criteria", ["A different acceptance criterion"]),
    ],
)
def test_publication_ready_rejects_changed_reviewed_task_specification(
    tmp_path: Path,
    field: str,
    changed_value: object,
) -> None:
    root, remote = _repository(tmp_path)
    github = _GitHub()
    workflow, monday = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
    )
    spec_changed = False

    def change_task_specification(snapshot: dict[str, Any]) -> None:
        nonlocal spec_changed
        if snapshot["phase"] == "publication-ready" and not spec_changed:
            spec_changed = True
            setattr(monday, field, changed_value)
            monday._touch()

    job = workflow.run(
        task_id="TASK-0001",
        delivery_id=f"delivery-publication-ready-changed-{field.replace('_', '-')}",
        progress_callback=change_task_specification,
    )

    assert spec_changed is True
    assert job.success is False
    assert job.status == "failed"
    assert job.failure_code == "task-changed"
    assert job.failure_class == "policy"
    assert job.commit_sha == ""
    assert job.pushed is False
    assert github.calls == 0
    assert _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/codex") == ""


@pytest.mark.parametrize(
    ("mutation", "expected_status", "expected_failure_code", "expected_failure_class"),
    [
        ("block", TaskStatus.BLOCKED, "task-changed", "policy"),
        ("complete", TaskStatus.COMPLETED, "terminal-task", "policy"),
        ("work-log", TaskStatus.IN_PROGRESS, "task-changed", "policy"),
    ],
)
def test_pushing_callback_cannot_publish_after_reentrant_task_mutation(
    tmp_path: Path,
    mutation: str,
    expected_status: TaskStatus,
    expected_failure_code: str,
    expected_failure_class: str,
) -> None:
    root, remote = _repository(tmp_path)
    github = _GitHub()
    workflow, monday = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
    )
    task_manager = TaskManager(root)
    task_manager.assign("TASK-0001", "agent:test", "human:test")
    task_manager.update_status(
        "TASK-0001",
        TaskStatus.IN_PROGRESS,
        "agent:test",
        "prepare publication race fixture",
    )
    task_changed = False

    def mutate_real_task(snapshot: dict[str, Any]) -> None:
        nonlocal task_changed
        if snapshot["phase"] != "pushing" or task_changed:
            return
        task_changed = True
        if mutation == "block":
            task_manager.block("TASK-0001", "human intervened", "human:test")
            monday.status = "blocked"
            monday._touch()
        elif mutation == "complete":
            task_manager.update_status(
                "TASK-0001",
                TaskStatus.COMPLETED,
                "human:test",
                "human completed task",
            )
            monday.status = "completed"
            monday._touch()
        else:
            task_manager.append_work_log(
                "TASK-0001",
                "human changed the task while publication was pending",
                "human:test",
            )

    job = workflow.run(
        task_id="TASK-0001",
        delivery_id=f"delivery-pushing-task-{mutation}",
        progress_callback=mutate_real_task,
    )

    assert task_changed is True
    assert task_manager.get("TASK-0001").status == expected_status
    assert job.success is False
    assert job.status == "failed"
    assert job.failure_code == expected_failure_code
    assert job.failure_class == expected_failure_class
    assert job.commit_sha == ""
    assert job.pushed is False
    assert github.calls == 0
    assert _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/codex") == ""


def test_unchanged_in_progress_task_still_publishes_after_final_authorization(
    tmp_path: Path,
) -> None:
    root, remote = _repository(tmp_path)
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
    )
    observed_phases: list[str] = []

    job = workflow.run(
        task_id="TASK-0001",
        delivery_id="delivery-final-task-authorization-control",
        progress_callback=lambda snapshot: observed_phases.append(snapshot["phase"]),
    )

    assert "publication-ready" in observed_phases
    assert "pushing" in observed_phases
    assert job.success is True
    assert job.status == "pr-open"
    assert job.pushed is True
    assert github.calls == 1
    assert _git(remote, "show", f"{job.branch}:app.py") == "VALUE = 2"


@pytest.mark.parametrize("task_status", ["blocked", "review", "unknown"])
def test_ineligible_task_admission_never_starts_or_builds(
    tmp_path: Path,
    task_status: str,
) -> None:
    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    monday = _Monday()
    monday.status = task_status
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
        monday=monday,
    )

    with mock.patch.object(
        workflow.repository,
        "create_worktree",
        wraps=workflow.repository.create_worktree,
    ) as create_worktree:
        job = workflow.run(
            task_id="TASK-0001",
            delivery_id=f"delivery-ineligible-{task_status}",
        )

    assert job.status == "failed"
    assert job.failure_code == "task-changed"
    assert job.failure_class == "policy"
    assert "start" not in monday.calls
    assert builder.calls == 0
    create_worktree.assert_not_called()


def test_assigned_task_remains_eligible_for_build(tmp_path: Path) -> None:
    root, _ = _repository(tmp_path)
    monday = _Monday()
    monday.status = "assigned"
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
        monday=monday,
    )

    job = workflow.run(
        task_id="TASK-0001",
        delivery_id="delivery-assigned-admission",
    )

    assert job.success is True
    assert job.status == "pr-open"
    assert "start" in monday.calls


@pytest.mark.parametrize("task_status", ["blocked", "review"])
def test_task_change_between_admission_and_start_never_reopens_task(
    tmp_path: Path,
    task_status: str,
) -> None:
    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    workflow, monday = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
    )

    def intervene(snapshot: dict[str, Any]) -> None:
        if snapshot["phase"] == "preparing":
            monday.status = task_status
            monday._touch()

    with mock.patch.object(
        workflow.repository,
        "create_worktree",
        wraps=workflow.repository.create_worktree,
    ) as create_worktree:
        job = workflow.run(
            task_id="TASK-0001",
            delivery_id=f"delivery-before-start-{task_status}",
            progress_callback=intervene,
        )

    assert job.status == "failed"
    assert job.failure_code == "task-changed"
    assert job.failure_class == "policy"
    assert monday.status == task_status
    assert "start" not in monday.calls
    assert builder.calls == 0
    create_worktree.assert_not_called()


def test_post_commit_task_refusal_keeps_audit_sha_and_allows_fresh_build(
    tmp_path: Path,
) -> None:
    class _BlockAfterCommit:
        def __init__(self, repository: Any, monday: _Monday) -> None:
            self._repository = repository
            self._monday = monday

        def __getattr__(self, name: str) -> Any:
            return getattr(self._repository, name)

        def commit(self, *args: Any, **kwargs: Any) -> str:
            commit = self._repository.commit(*args, **kwargs)
            self._monday.status = "blocked"
            self._monday._touch()
            return commit

    root, remote = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    github = _GitHub()
    workflow, monday = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
    )
    repository = workflow.repository
    workflow.repository = _BlockAfterCommit(repository, monday)

    refused = workflow.run(
        task_id="TASK-0001",
        delivery_id="delivery-post-commit-task-refusal",
    )

    assert refused.status == "failed"
    assert refused.failure_code == "task-changed"
    assert refused.failure_class == "policy"
    assert refused.commit_sha == ""
    assert re.fullmatch(
        r"[0-9a-f]{40}(?:[0-9a-f]{24})?",
        refused.prepared_commit_sha,
    )
    assert github.calls == 0
    from telegram_bot.service import _safe_delivery_retry_result

    retry_snapshot = refused.to_dict()
    retry_snapshot["publication_uncertain"] = False
    assert _safe_delivery_retry_result(retry_snapshot) is False

    workflow.repository = repository
    monday.status = "in-progress"
    monday._touch()
    recovered = workflow.run(
        task_id="TASK-0001",
        delivery_id="delivery-after-post-commit-task-refusal",
    )

    assert recovered.success is True
    assert recovered.status == "pr-open"
    assert github.calls == 1
    assert _git(remote, "show", f"{recovered.branch}:app.py") == "VALUE = 2"


@pytest.mark.parametrize("publish_prepared_branch", [False, True])
def test_post_commit_task_lookup_failure_uses_structural_absence_proof(
    tmp_path: Path,
    publish_prepared_branch: bool,
) -> None:
    class _UnavailableMonday(_Monday):
        unavailable = False

        def task(self, action: str, **kwargs: Any) -> SimpleNamespace:
            if action == "get" and self.unavailable:
                self.calls.append(action)
                return SimpleNamespace(success=False, message="task store unavailable", data={})
            return super().task(action, **kwargs)

    class _DisableTaskReadsAfterCommit:
        def __init__(self, repository: Any, monday: _UnavailableMonday) -> None:
            self._repository = repository
            self._monday = monday

        def __getattr__(self, name: str) -> Any:
            return getattr(self._repository, name)

        def commit(self, *args: Any, **kwargs: Any) -> str:
            commit = self._repository.commit(*args, **kwargs)
            self._monday.unavailable = True
            return commit

    root, remote = _repository(tmp_path)
    monday = _UnavailableMonday()
    builder = _Builder([_patch(1, 2)])
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
        monday=monday,
    )
    repository = workflow.repository
    workflow.repository = _DisableTaskReadsAfterCommit(repository, monday)
    refused = workflow.run(
        task_id="TASK-0001",
        delivery_id=(
            "delivery-post-commit-lookup-published"
            if publish_prepared_branch
            else "delivery-post-commit-lookup-restored"
        ),
    )

    assert refused.status == "failed"
    assert refused.failure_code == "task-not-found"
    assert refused.commit_sha == ""
    assert re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", refused.prepared_commit_sha)
    if publish_prepared_branch:
        _git(
            root,
            "push",
            "origin",
            f"{refused.prepared_commit_sha}:refs/heads/{refused.branch}",
        )
    workflow.repository = repository
    monday.unavailable = False
    calls = builder.calls

    fresh = workflow.run(
        task_id="TASK-0001",
        delivery_id=(
            "delivery-after-published-lookup-failure"
            if publish_prepared_branch
            else "delivery-after-restored-lookup-failure"
        ),
    )

    if publish_prepared_branch:
        assert fresh.status == "failed"
        assert fresh.failure_code == "artifact-security"
        assert builder.calls == calls
        assert _git(remote, "rev-parse", f"refs/heads/{refused.branch}") == (
            refused.prepared_commit_sha
        )
    else:
        assert fresh.success is True
        assert fresh.status == "pr-open"
        assert builder.calls == calls + 1


def test_transient_prepared_failure_remains_retry_lineage_leaf(tmp_path: Path) -> None:
    class _TimeoutMonday(_Monday):
        unavailable = False

        def task(self, action: str, **kwargs: Any) -> SimpleNamespace:
            if action == "get" and self.unavailable:
                raise TimeoutError("task store timeout")
            return super().task(action, **kwargs)

    class _TimeoutAfterCommit:
        def __init__(self, repository: Any, monday: _TimeoutMonday) -> None:
            self._repository = repository
            self._monday = monday

        def __getattr__(self, name: str) -> Any:
            return getattr(self._repository, name)

        def commit(self, *args: Any, **kwargs: Any) -> str:
            commit = self._repository.commit(*args, **kwargs)
            self._monday.unavailable = True
            return commit

    root, _ = _repository(tmp_path)
    monday = _TimeoutMonday()
    builder = _Builder([_patch(1, 2)])
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
        monday=monday,
    )
    repository = workflow.repository
    workflow.repository = _TimeoutAfterCommit(repository, monday)
    predecessor = workflow.run(
        task_id="TASK-0001",
        delivery_id="delivery-transient-prepared-root",
    )

    assert predecessor.status == "failed"
    assert predecessor.failure_class == "transient"
    assert predecessor.commit_sha == ""
    assert predecessor.prepared_commit_sha
    workflow.repository = repository
    monday.unavailable = False
    retry = workflow.run(
        task_id=predecessor.task_id,
        delivery_id=f"{predecessor.delivery_id}-r2",
        predecessor_delivery_id=predecessor.delivery_id,
        retry_root_delivery_id=predecessor.delivery_id,
        retry_attempt=2,
    )

    assert retry.success is True
    assert retry.status == "pr-open"


@pytest.mark.parametrize("publish_prepared_branch", [False, True])
def test_nontransient_prepared_retry_child_retires_only_unpublished_lineage(
    tmp_path: Path,
    publish_prepared_branch: bool,
) -> None:
    class _UnavailableMonday(_Monday):
        unavailable = False

        def task(self, action: str, **kwargs: Any) -> SimpleNamespace:
            if action == "get" and self.unavailable:
                self.calls.append(action)
                return SimpleNamespace(success=False, message="task store unavailable", data={})
            return super().task(action, **kwargs)

    class _DisableTaskReadsAfterCommit:
        def __init__(self, repository: Any, monday: _UnavailableMonday) -> None:
            self._repository = repository
            self._monday = monday

        def __getattr__(self, name: str) -> Any:
            return getattr(self._repository, name)

        def commit(self, *args: Any, **kwargs: Any) -> str:
            commit = self._repository.commit(*args, **kwargs)
            self._monday.unavailable = True
            return commit

    root, remote = _repository(tmp_path)
    monday = _UnavailableMonday()
    builder = _Builder([_patch(1, 2)])
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
        monday=monday,
    )
    predecessor = _safe_retry_predecessor(
        workflow,
        delivery_id="delivery-prepared-child-lineage-root",
    )
    repository = workflow.repository
    workflow.repository = _DisableTaskReadsAfterCommit(repository, monday)
    child = workflow.run(
        task_id=predecessor.task_id,
        delivery_id=f"{predecessor.delivery_id}-r2",
        predecessor_delivery_id=predecessor.delivery_id,
        retry_root_delivery_id=predecessor.delivery_id,
        retry_attempt=2,
    )

    assert child.status == "failed"
    assert child.failure_code == "task-not-found"
    assert child.failure_class == "permanent"
    assert child.prepared_commit_sha
    if publish_prepared_branch:
        _git(
            root,
            "push",
            "origin",
            f"{child.prepared_commit_sha}:refs/heads/{child.branch}",
        )
    workflow.repository = repository
    monday.unavailable = False
    calls = builder.calls
    fresh = workflow.run(
        task_id=predecessor.task_id,
        delivery_id=(
            "delivery-after-published-prepared-child"
            if publish_prepared_branch
            else "delivery-after-unpublished-prepared-child"
        ),
    )

    if publish_prepared_branch:
        assert fresh.status == "failed"
        assert fresh.failure_code == "artifact-security"
        assert builder.calls == calls
        assert _git(remote, "rev-parse", f"refs/heads/{child.branch}") == (
            child.prepared_commit_sha
        )
    else:
        assert fresh.success is True
        assert fresh.status == "pr-open"
        assert builder.calls == calls + 1


@pytest.mark.parametrize(
    ("field", "value"),
    [("review_reconciliation", "reviewed"), ("status", "rejected")],
)
def test_tampered_prepared_policy_failure_blocks_a_fresh_build(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    class _BlockAfterCommit:
        def __init__(self, repository: Any, monday: _Monday) -> None:
            self._repository = repository
            self._monday = monday

        def __getattr__(self, name: str) -> Any:
            return getattr(self._repository, name)

        def commit(self, *args: Any, **kwargs: Any) -> str:
            commit = self._repository.commit(*args, **kwargs)
            self._monday.status = "blocked"
            self._monday._touch()
            return commit

    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    workflow, monday = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
    )
    repository = workflow.repository
    workflow.repository = _BlockAfterCommit(repository, monday)
    refused = workflow.run(
        task_id="TASK-0001",
        delivery_id=f"delivery-tampered-prepared-{field.replace('_', '-')}",
    )
    setattr(refused, field, value)
    workflow.store.persist(refused)
    workflow.repository = repository
    monday.status = "in-progress"
    monday._touch()
    calls = builder.calls

    blocked = workflow.run(
        task_id="TASK-0001",
        delivery_id=f"delivery-after-tampered-prepared-{field.replace('_', '-')}",
    )

    assert blocked.status == "failed"
    assert blocked.failure_code == "unresolved-delivery"
    assert builder.calls == calls


def test_prepared_status_tamper_during_pushing_blocks_before_successor_push(
    tmp_path: Path,
) -> None:
    class _BlockAfterCommit:
        def __init__(self, repository: Any, monday: _Monday) -> None:
            self._repository = repository
            self._monday = monday

        def __getattr__(self, name: str) -> Any:
            return getattr(self._repository, name)

        def commit(self, *args: Any, **kwargs: Any) -> str:
            commit = self._repository.commit(*args, **kwargs)
            self._monday.status = "blocked"
            self._monday._touch()
            return commit

    root, remote = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    github = _GitHub()
    workflow, monday = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
    )
    repository = workflow.repository
    workflow.repository = _BlockAfterCommit(repository, monday)
    refused = workflow.run(
        task_id="TASK-0001",
        delivery_id="delivery-prepared-status-race-root",
    )
    workflow.repository = repository
    monday.status = "in-progress"
    monday._touch()
    tampered = False

    def tamper_old_status(snapshot: dict[str, Any]) -> None:
        nonlocal tampered
        if snapshot["phase"] == "pushing" and not tampered:
            tampered = True
            refused.status = "rejected"
            workflow.store.persist(refused)

    with mock.patch.object(
        workflow.repository,
        "push",
        wraps=workflow.repository.push,
    ) as successor_push:
        successor = workflow.run(
            task_id="TASK-0001",
            delivery_id="delivery-after-prepared-status-race",
            progress_callback=tamper_old_status,
        )

    assert tampered is True
    assert successor.status == "failed"
    assert successor.failure_code == "artifact-security"
    assert successor.failure_class == "policy"
    assert successor.commit_sha == ""
    assert successor.prepared_commit_sha
    assert successor.pushed is False
    assert github.calls == 0
    successor_push.assert_not_called()
    assert _git(
        remote,
        "for-each-ref",
        "--format=%(refname)",
        f"refs/heads/{successor.branch}",
    ) == ""


def test_published_prepared_policy_branch_blocks_a_fresh_build(
    tmp_path: Path,
) -> None:
    class _BlockAfterCommit:
        def __init__(self, repository: Any, monday: _Monday) -> None:
            self._repository = repository
            self._monday = monday

        def __getattr__(self, name: str) -> Any:
            return getattr(self._repository, name)

        def commit(self, *args: Any, **kwargs: Any) -> str:
            commit = self._repository.commit(*args, **kwargs)
            self._monday.status = "blocked"
            self._monday._touch()
            return commit

    root, remote = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    workflow, monday = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
    )
    repository = workflow.repository
    workflow.repository = _BlockAfterCommit(repository, monday)
    refused = workflow.run(
        task_id="TASK-0001",
        delivery_id="delivery-published-prepared-policy-refusal",
    )
    _git(
        root,
        "push",
        "origin",
        f"{refused.prepared_commit_sha}:refs/heads/{refused.branch}",
    )
    workflow.repository = repository
    monday.status = "in-progress"
    monday._touch()
    calls = builder.calls

    blocked = workflow.run(
        task_id="TASK-0001",
        delivery_id="delivery-after-published-prepared-policy-refusal",
    )

    assert blocked.status == "failed"
    assert blocked.failure_code == "artifact-security"
    assert builder.calls == calls
    assert _git(remote, "rev-parse", f"refs/heads/{refused.branch}") == (
        refused.prepared_commit_sha
    )


def test_task_policy_retry_child_retires_safe_lineage_for_fresh_build(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    workflow, monday = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
    )
    predecessor = _safe_retry_predecessor(
        workflow,
        delivery_id="delivery-task-policy-lineage-root",
    )
    monday.status = "blocked"
    monday._touch()
    policy_child = workflow.run(
        task_id="TASK-0001",
        delivery_id=f"{predecessor.delivery_id}-r2",
        predecessor_delivery_id=predecessor.delivery_id,
        retry_root_delivery_id=predecessor.delivery_id,
        retry_attempt=2,
    )

    assert policy_child.status == "failed"
    assert policy_child.failure_code == "task-changed"
    assert policy_child.failure_class == "policy"
    assert builder.calls == 0

    monday.status = "in-progress"
    monday._touch()
    fresh = workflow.run(
        task_id="TASK-0001",
        delivery_id="delivery-after-task-policy-lineage",
    )

    assert fresh.success is True
    assert fresh.status == "pr-open"
    assert builder.calls == 1


def test_legacy_unmarked_policy_retry_child_can_retire_safe_lineage(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
    )
    predecessor = _safe_retry_predecessor(
        workflow,
        delivery_id="delivery-legacy-policy-lineage-root",
    )
    legacy_child = DeliveryJob(
        delivery_id=f"{predecessor.delivery_id}-r2",
        task_id=predecessor.task_id,
        objective=predecessor.objective,
        repo_root=predecessor.repo_root,
        project_name=predecessor.project_name,
        expected_repository=predecessor.expected_repository,
        root_device=predecessor.root_device,
        root_inode=predecessor.root_inode,
        status="failed",
        phase="failed",
        failure_code="task-changed",
        failure_class="policy",
        predecessor_delivery_id=predecessor.delivery_id,
        retry_root_delivery_id=predecessor.delivery_id,
        retry_attempt=2,
        created_at="2026-10-15T00:01:00+00:00",
        updated_at="2026-10-15T00:01:00+00:00",
    )
    assert legacy_child.deferred_commit_authorization is False
    assert workflow.store.reserve(legacy_child) is None

    fresh = workflow.run(
        task_id=predecessor.task_id,
        delivery_id="delivery-after-legacy-policy-lineage",
    )

    assert fresh.success is True
    assert fresh.status == "pr-open"
    assert builder.calls == 1


def test_closed_policy_retry_lineage_is_revalidated_after_pushing_callback(
    tmp_path: Path,
) -> None:
    root, remote = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    github = _GitHub()
    workflow, monday = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
    )
    predecessor = _safe_retry_predecessor(
        workflow,
        delivery_id="delivery-closed-policy-lineage-root",
    )
    monday.status = "blocked"
    monday._touch()
    policy_child = workflow.run(
        task_id="TASK-0001",
        delivery_id=f"{predecessor.delivery_id}-r2",
        predecessor_delivery_id=predecessor.delivery_id,
        retry_root_delivery_id=predecessor.delivery_id,
        retry_attempt=2,
    )
    assert policy_child.failure_code == "task-changed"
    monday.status = "in-progress"
    monday._touch()
    ancestor_published = False

    def publish_retired_ancestor(snapshot: dict[str, Any]) -> None:
        nonlocal ancestor_published
        if snapshot["phase"] == "pushing" and not ancestor_published:
            ancestor_published = True
            _git(
                root,
                "push",
                "origin",
                f"{predecessor.base_sha}:refs/heads/{predecessor.branch}",
            )

    with mock.patch.object(
        workflow.repository,
        "push",
        wraps=workflow.repository.push,
    ) as successor_push:
        successor = workflow.run(
            task_id="TASK-0001",
            delivery_id="delivery-after-closed-policy-lineage-race",
            progress_callback=publish_retired_ancestor,
        )

    assert ancestor_published is True
    assert successor.success is False
    assert successor.pushed is False
    assert successor.status == "failed"
    assert successor.failure_code == "artifact-security"
    assert successor.failure_class == "policy"
    assert successor.commit_sha == ""
    assert re.fullmatch(
        r"[0-9a-f]{40}(?:[0-9a-f]{24})?",
        successor.prepared_commit_sha,
    )
    assert github.calls == 0
    successor_push.assert_not_called()
    assert _git(remote, "rev-parse", f"refs/heads/{predecessor.branch}") == (
        predecessor.base_sha
    )


def test_prepared_commit_crash_is_proven_safe_for_retry(tmp_path: Path) -> None:
    root, remote = _repository(tmp_path)
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
    )
    original_persist = workflow.store.persist
    crash_armed = True

    def crash_after_prepared_checkpoint(job: DeliveryJob) -> None:
        nonlocal crash_armed
        original_persist(job)
        if (
            crash_armed
            and job.phase == "pushing"
            and job.prepared_commit_sha
            and not job.commit_sha
        ):
            crash_armed = False
            raise SystemExit("simulated crash after prepared commit")

    workflow.store.persist = crash_after_prepared_checkpoint  # type: ignore[method-assign]
    with pytest.raises(SystemExit, match="prepared commit"):
        workflow.run(
            task_id="TASK-0001",
            delivery_id="delivery-crash-after-prepared-commit",
        )
    workflow.store.persist = original_persist  # type: ignore[method-assign]
    durable = workflow.get("delivery-crash-after-prepared-commit")

    assert durable.phase == "pushing"
    assert durable.commit_sha == ""
    assert re.fullmatch(
        r"[0-9a-f]{40}(?:[0-9a-f]{24})?",
        durable.prepared_commit_sha,
    )
    assert _git(
        remote,
        "for-each-ref",
        "--format=%(refname)",
        f"refs/heads/{durable.branch}",
    ) == ""

    recovered = workflow.run(
        task_id="TASK-0001",
        delivery_id=durable.delivery_id,
    )

    assert recovered.status == "interrupted"
    assert recovered.failure_class == "transient"
    assert recovered.interrupted_from_phase == "pushing"
    assert recovered.interruption_proof_sha256
    assert recovered.prepared_commit_sha == durable.prepared_commit_sha


def test_publication_ready_crash_uses_explicit_deferred_commit_proof(
    tmp_path: Path,
) -> None:
    root, remote = _repository(tmp_path)
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
    )

    def crash_before_final_task_lock(snapshot: dict[str, Any]) -> None:
        if snapshot["phase"] == "publication-ready":
            raise SystemExit("simulated crash at publication-ready")

    with pytest.raises(SystemExit, match="publication-ready"):
        workflow.run(
            task_id="TASK-0001",
            delivery_id="delivery-publication-ready-precommit-crash",
            progress_callback=crash_before_final_task_lock,
        )
    durable = workflow.get("delivery-publication-ready-precommit-crash")

    assert durable.phase == "publication-ready"
    assert durable.deferred_commit_authorization is True
    assert durable.prepared_commit_sha == durable.commit_sha == ""
    assert _git(
        remote,
        "for-each-ref",
        "--format=%(refname)",
        f"refs/heads/{durable.branch}",
    ) == ""

    recovered = workflow.run(task_id=durable.task_id, delivery_id=durable.delivery_id)

    assert recovered.status == "interrupted"
    assert recovered.failure_class == "transient"
    assert recovered.interrupted_from_phase == "publication-ready"
    assert recovered.interruption_proof_sha256


def test_commit_internal_crash_is_proven_safe_before_sha_checkpoint(
    tmp_path: Path,
) -> None:
    class _CrashAfterCommit:
        def __init__(self, repository: Any) -> None:
            self._repository = repository

        def __getattr__(self, name: str) -> Any:
            return getattr(self._repository, name)

        def commit(self, *args: Any, **kwargs: Any) -> str:
            self._repository.commit(*args, **kwargs)
            raise SystemExit("simulated crash inside commit checkpoint window")

    root, remote = _repository(tmp_path)
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
    )
    repository = workflow.repository
    workflow.repository = _CrashAfterCommit(repository)

    with pytest.raises(SystemExit, match="commit checkpoint window"):
        workflow.run(
            task_id="TASK-0001",
            delivery_id="delivery-commit-internal-crash",
        )
    workflow.repository = repository
    durable = workflow.get("delivery-commit-internal-crash")

    assert durable.phase == "pushing"
    assert durable.deferred_commit_authorization is True
    assert durable.prepared_commit_sha == durable.commit_sha == ""
    assert _git(Path(durable.worktree), "rev-parse", "HEAD") != durable.base_sha
    assert _git(
        remote,
        "for-each-ref",
        "--format=%(refname)",
        f"refs/heads/{durable.branch}",
    ) == ""

    recovered = workflow.run(task_id=durable.task_id, delivery_id=durable.delivery_id)

    assert recovered.status == "interrupted"
    assert recovered.failure_class == "transient"
    assert recovered.interruption_proof_sha256


@pytest.mark.parametrize("phase", ["publication-ready", "pushing"])
def test_legacy_unmarked_precommit_publication_shape_stays_conservative(
    tmp_path: Path,
    phase: str,
) -> None:
    root, _ = _repository(tmp_path)
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
    )
    stale = _persist_owned_stale_delivery(
        workflow,
        delivery_id=f"delivery-legacy-unmarked-{phase}",
        phase=phase,
    )
    stale.artifact_sha256 = "a" * 64
    stale.reviewed_artifact_sha256 = stale.artifact_sha256
    assert stale.deferred_commit_authorization is False
    workflow.store.persist(stale)

    recovered = workflow.run(task_id=stale.task_id, delivery_id=stale.delivery_id)

    assert recovered.status == "interrupted"
    assert recovered.failure_class == "permanent"
    assert recovered.interruption_proof_sha256 == ""


def test_legacy_uncheckpointed_committing_head_preserves_remote_absence_proof(
    tmp_path: Path,
) -> None:
    root, remote = _repository(tmp_path)
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
    )
    stale = _persist_owned_stale_delivery(
        workflow,
        delivery_id="delivery-legacy-uncheckpointed-commit",
        phase="committing",
        local_commit=True,
    )
    local_head = stale.commit_sha
    stale.commit_sha = ""
    assert stale.deferred_commit_authorization is False
    workflow.store.persist(stale)
    assert _git(remote, "for-each-ref", "--format=%(refname)", stale.branch) == ""

    recovered = workflow.run(task_id=stale.task_id, delivery_id=stale.delivery_id)

    assert recovered.status == "interrupted"
    assert recovered.failure_class == "transient"
    assert recovered.interruption_proof_sha256
    assert _git(Path(stale.worktree), "rev-parse", "HEAD") == local_head


def test_published_prepared_branch_keeps_crash_recovery_conservative(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    workflow, _ = _workflow(
        root,
        builder=_Builder([_patch(1, 2)]),
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
    )
    original_persist = workflow.store.persist
    crash_armed = True

    def crash_after_prepared_checkpoint(job: DeliveryJob) -> None:
        nonlocal crash_armed
        original_persist(job)
        if crash_armed and job.prepared_commit_sha and not job.commit_sha:
            crash_armed = False
            raise SystemExit("simulated crash after prepared commit")

    workflow.store.persist = crash_after_prepared_checkpoint  # type: ignore[method-assign]
    with pytest.raises(SystemExit, match="prepared commit"):
        workflow.run(
            task_id="TASK-0001",
            delivery_id="delivery-published-prepared-commit",
        )
    workflow.store.persist = original_persist  # type: ignore[method-assign]
    durable = workflow.get("delivery-published-prepared-commit")
    _git(
        root,
        "push",
        "origin",
        f"{durable.prepared_commit_sha}:refs/heads/{durable.branch}",
    )

    recovered = workflow.run(
        task_id="TASK-0001",
        delivery_id=durable.delivery_id,
    )

    assert recovered.status == "interrupted"
    assert recovered.failure_class == "permanent"
    assert recovered.interruption_proof_sha256 == ""


@pytest.mark.parametrize(
    ("status", "success", "evidence_field", "evidence_value"),
    [
        ("rejected", False, "commit_sha", "3" * 40),
        ("pr-open", False, "commit_sha", "4" * 40),
        ("rejected", False, "task_reconciliation", "reopened"),
    ],
)
def test_malformed_terminal_publication_evidence_blocks_a_fresh_build(
    tmp_path: Path,
    status: str,
    success: bool,
    evidence_field: str,
    evidence_value: str,
) -> None:
    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
    )
    metadata = root.resolve().stat()
    malformed = DeliveryJob(
        delivery_id=(
            f"delivery-malformed-evidence-{status}-{evidence_field.replace('_', '-')}"
        ),
        task_id="TASK-0001",
        objective="Set VALUE to the reviewed target.",
        repo_root=str(root.resolve()),
        project_name=workflow.project_name,
        expected_repository=workflow.expected_repository,
        root_device=metadata.st_dev,
        root_inode=metadata.st_ino,
        status=status,
        phase="completed" if status == "pr-open" else "review-blocked",
        success=success,
        failure_code="review-blocked",
        failure_class="policy",
        created_at="2026-10-15T00:00:00+00:00",
        updated_at="2026-10-15T00:00:00+00:00",
    )
    setattr(malformed, evidence_field, evidence_value)
    assert workflow.store.reserve(malformed) is None

    blocked = workflow.run(
        task_id=malformed.task_id,
        delivery_id=f"delivery-after-{malformed.delivery_id.removeprefix('delivery-')}",
    )

    assert blocked.status == "failed"
    assert blocked.failure_code == "unresolved-delivery"
    assert builder.calls == 0


@pytest.mark.parametrize("task_outage", ["response", "exception"])
def test_verified_terminal_pull_request_with_task_outage_is_settled_history(
    tmp_path: Path,
    task_outage: str,
) -> None:
    class _TaskOutageMonday(_Monday):
        unavailable = False

        def task(self, action: str, **kwargs: Any) -> SimpleNamespace:
            if action == "get" and self.unavailable:
                self.calls.append(action)
                if task_outage == "exception":
                    raise OSError("task store unavailable")
                return SimpleNamespace(
                    success=False,
                    message="task store unavailable",
                    data={},
                )
            return super().task(action, **kwargs)

    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    github = _ObserverGitHub(lifecycle="closed", checks="failed")
    monday = _TaskOutageMonday()
    monday.status = "review"
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
        monday=monday,
    )
    prior = _observable_delivery(workflow, root)
    monday.unavailable = True
    settled = workflow.observe(prior.delivery_id)

    assert settled.pr_state == "closed"
    assert settled.observation_success is True
    assert settled.task_reconciliation == "failed"
    monday.unavailable = False
    monday.status = "in-progress"
    monday._touch()
    fresh = workflow.run(
        task_id=prior.task_id,
        delivery_id="delivery-after-verified-closed-pr",
    )

    assert fresh.success is True
    assert fresh.status == "pr-open"
    assert builder.calls == 1


def test_failed_remote_refresh_cannot_settle_stale_terminal_pr_state(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    github = _ObserverGitHub(lifecycle="closed", checks="failed")
    monday = _Monday()
    monday.status = "review"
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
        monday=monday,
    )
    prior = _observable_delivery(workflow, root)
    closed = workflow.observe(prior.delivery_id)

    assert closed.pr_state == "closed"
    assert closed.observation_success is True
    assert monday.status == "in-progress"

    github.lifecycle = "open"
    github.checks = "pending"
    github.observation_success = False
    github.observation_message = "GitHub state is temporarily unavailable"
    uncertain = workflow.observe(prior.delivery_id)

    assert uncertain.pr_state == "closed"
    assert uncertain.observation_success is False
    blocked = workflow.run(
        task_id=prior.task_id,
        delivery_id="delivery-after-uncertain-reopened-pr",
    )

    assert blocked.status == "failed"
    assert blocked.failure_code == "unresolved-delivery"
    assert builder.calls == 0


def test_promotion_checkpoint_failure_restores_prepared_state_before_failure(
    tmp_path: Path,
) -> None:
    root, remote = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
    )
    original_persist = workflow.store.persist
    failure_armed = True

    def fail_after_promotion_checkpoint(job: DeliveryJob) -> None:
        nonlocal failure_armed
        original_persist(job)
        if (
            failure_armed
            and job.phase == "pushing"
            and job.commit_sha
            and not job.prepared_commit_sha
            and not job.pushed
        ):
            failure_armed = False
            raise OSError(errno.EIO, "simulated promotion checkpoint failure")

    workflow.store.persist = fail_after_promotion_checkpoint  # type: ignore[method-assign]
    with mock.patch.object(
        workflow.repository,
        "push",
        wraps=workflow.repository.push,
    ) as push:
        failed = workflow.run(
            task_id="TASK-0001",
            delivery_id="delivery-promotion-checkpoint-failure",
        )
    workflow.store.persist = original_persist  # type: ignore[method-assign]

    assert failure_armed is False
    assert failed.status == "failed"
    assert failed.commit_sha == ""
    assert re.fullmatch(
        r"[0-9a-f]{40}(?:[0-9a-f]{24})?",
        failed.prepared_commit_sha,
    )
    assert failed.pushed is False
    assert github.calls == 0
    push.assert_not_called()
    assert _git(
        remote,
        "for-each-ref",
        "--format=%(refname)",
        f"refs/heads/{failed.branch}",
    ) == ""
    durable = workflow.get(failed.delivery_id)
    assert durable.commit_sha == ""
    assert durable.prepared_commit_sha == failed.prepared_commit_sha

    fresh = workflow.run(
        task_id=failed.task_id,
        delivery_id="delivery-after-promotion-checkpoint-failure",
    )

    assert fresh.success is True
    assert fresh.status == "pr-open"


def test_transient_predecessor_proof_outage_advances_retry_lineage(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
    )
    predecessor = _safe_retry_predecessor(
        workflow,
        delivery_id="delivery-proof-outage-lineage-root",
    )
    retry_two_id = f"{predecessor.delivery_id}-r2"
    proof_timeout = GitCommandError(
        CommandResult(
            argv=("git", "ls-remote", "origin"),
            returncode=-9,
            stdout="",
            stderr="",
            timed_out=True,
        )
    )

    with mock.patch.object(
        workflow.repository,
        "remote_branch_is_absent",
        side_effect=proof_timeout,
    ):
        retry_two = workflow.run(
            task_id=predecessor.task_id,
            delivery_id=retry_two_id,
            predecessor_delivery_id=predecessor.delivery_id,
            retry_root_delivery_id=predecessor.delivery_id,
            retry_attempt=2,
        )

    assert retry_two.status == "failed"
    assert retry_two.failure_code == "git-command"
    assert retry_two.failure_class == "transient"
    assert retry_two.predecessor_delivery_id == predecessor.delivery_id
    assert retry_two.retry_root_delivery_id == predecessor.delivery_id
    assert retry_two.retry_attempt == 2
    assert builder.calls == github.calls == 0

    retry_three = workflow.run(
        task_id=predecessor.task_id,
        delivery_id=f"{predecessor.delivery_id}-r3",
        predecessor_delivery_id=retry_two.delivery_id,
        retry_root_delivery_id=predecessor.delivery_id,
        retry_attempt=3,
    )

    assert retry_three.success is True
    assert retry_three.status == "pr-open"
    assert builder.calls == github.calls == 1


def test_transient_stale_recovery_proof_outage_preserves_exact_identity(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
    )
    stale = _persist_owned_stale_delivery(
        workflow,
        delivery_id="delivery-stale-proof-outage",
        phase="reviewing",
    )
    proof_timeout = GitCommandError(
        CommandResult(
            argv=("git", "ls-remote", "origin"),
            returncode=-9,
            stdout="",
            stderr="",
            timed_out=True,
        )
    )

    with mock.patch.object(
        workflow.repository,
        "remote_branch_is_absent",
        side_effect=proof_timeout,
    ):
        with pytest.raises(GitCommandError, match="timed out"):
            workflow.run(task_id=stale.task_id, delivery_id=stale.delivery_id)

    durable = workflow.get(stale.delivery_id)
    assert durable.status == "running"
    assert durable.phase == "reviewing"

    recovered = workflow.run(task_id=stale.task_id, delivery_id=stale.delivery_id)

    assert recovered.status == "interrupted"
    assert recovered.failure_class == "transient"
    successor = workflow.run(
        task_id=stale.task_id,
        delivery_id=f"{stale.delivery_id}-r2",
        predecessor_delivery_id=stale.delivery_id,
        retry_root_delivery_id=stale.delivery_id,
        retry_attempt=2,
    )
    assert successor.success is True
    assert successor.status == "pr-open"
    assert builder.calls == 1


def test_non_timeout_predecessor_proof_failure_remains_policy(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    github = _GitHub()
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=github,
    )
    predecessor = _safe_retry_predecessor(
        workflow,
        delivery_id="delivery-proof-error-lineage-root",
    )
    proof_failure = GitCommandError(
        CommandResult(
            argv=("git", "ls-remote", "origin"),
            returncode=128,
            stdout="",
            stderr="remote rejected the request",
        )
    )

    with mock.patch.object(
        workflow.repository,
        "remote_branch_is_absent",
        side_effect=proof_failure,
    ):
        retry_two = workflow.run(
            task_id=predecessor.task_id,
            delivery_id=f"{predecessor.delivery_id}-r2",
            predecessor_delivery_id=predecessor.delivery_id,
            retry_root_delivery_id=predecessor.delivery_id,
            retry_attempt=2,
        )

    assert retry_two.status == "failed"
    assert retry_two.failure_code == "artifact-security"
    assert retry_two.failure_class == "policy"
    assert retry_two.pushed is False
    assert builder.calls == github.calls == 0


@pytest.mark.parametrize(
    "failed_predecessor_read",
    [2, 4],
    ids=["asserted-binding-read", "full-chain-read"],
)
def test_transient_predecessor_store_outage_advances_retry_lineage(
    tmp_path: Path,
    failed_predecessor_read: int,
) -> None:
    root, _ = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    workflow, _ = _workflow(
        root,
        builder=builder,
        reviewer=_Reviewer([_pass()]),
        validator=_Validator([True]),
        github=_GitHub(),
    )
    predecessor = _safe_retry_predecessor(
        workflow,
        delivery_id=f"delivery-store-outage-root-{failed_predecessor_read}",
    )
    original_get = workflow.store.get
    predecessor_reads = 0

    def flaky_get(delivery_id: str) -> DeliveryJob:
        nonlocal predecessor_reads
        if delivery_id == predecessor.delivery_id:
            predecessor_reads += 1
            if predecessor_reads == failed_predecessor_read:
                cause = OSError(errno.EAGAIN, "delivery store temporarily unavailable")
                raise ValueError("delivery record could not be read") from cause
        return original_get(delivery_id)

    retry_two_id = f"{predecessor.delivery_id}-r2"
    with mock.patch.object(workflow.store, "get", side_effect=flaky_get):
        retry_two = workflow.run(
            task_id=predecessor.task_id,
            delivery_id=retry_two_id,
            predecessor_delivery_id=predecessor.delivery_id,
            retry_root_delivery_id=predecessor.delivery_id,
            retry_attempt=2,
        )

    assert predecessor_reads == failed_predecessor_read
    assert retry_two.status == "failed"
    assert retry_two.failure_class == "transient"
    assert builder.calls == 0

    retry_three = workflow.run(
        task_id=predecessor.task_id,
        delivery_id=f"{predecessor.delivery_id}-r3",
        predecessor_delivery_id=retry_two.delivery_id,
        retry_root_delivery_id=predecessor.delivery_id,
        retry_attempt=3,
    )

    assert retry_three.success is True
    assert retry_three.status == "pr-open"
    assert builder.calls == 1
