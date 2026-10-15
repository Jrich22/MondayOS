"""End-to-end tests for the artifact-bound delivery state machine."""

from __future__ import annotations

import base64
import json
import os
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
from delivery.github import PullRequestResult
from delivery.security import ArtifactSecurityError
from delivery.types import DeliveryJob
from delivery.validation import MondayValidationRunner, ValidationResult
from delivery.workflow import DeliveryWorkflow
from tasks import TaskManager, TaskPriority, TaskType


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


def _observable_delivery(workflow: DeliveryWorkflow, root: Path) -> DeliveryJob:
    metadata = root.resolve().stat()
    job = DeliveryJob(
        delivery_id="delivery-observe-1",
        task_id="TASK-0001",
        objective="Set VALUE to the reviewed target.",
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
    workflow, _ = _workflow(
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
    recovered = workflow.run(
        task_id="TASK-0001",
        delivery_id="delivery-pr-temporarily-unverified",
    )

    assert recovered.success is True
    assert recovered.status == "pr-open"
    assert unavailable.calls == 1
    assert recovered_github.calls == 1


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
    assert monday.calls == ["get", "get"]


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
