"""End-to-end tests for the artifact-bound delivery state machine."""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from delivery.backends import PatchProposal, ReviewDecision, ToolAvailability
from delivery.github import PullRequestResult
from delivery.types import DeliveryJob
from delivery.validation import MondayValidationRunner, ValidationResult
from delivery.workflow import DeliveryWorkflow


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
    ) -> None:
        self.status = "backlog"
        self.calls: list[str] = []
        self.title = title
        self.objective = objective
        self.acceptance_criteria = acceptance_criteria or [
            "app.py contains the target value"
        ]

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
                    "status": self.status,
                },
            )
        if action == "start":
            self.status = "in-progress"
            return SimpleNamespace(success=True, message="started", data={})
        if action == "review":
            self.status = "review"
            return SimpleNamespace(success=True, message="review", data={})
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

    def run(self, **_: Any) -> list[ValidationResult]:
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

    def availability(self) -> ToolAvailability:
        return ToolAvailability(True, "github", "ready")

    def ensure_pull_request(self, **_: Any) -> PullRequestResult:
        self.calls += 1
        return PullRequestResult(
            True,
            url="https://github.com/owner/project/pull/12",
            number=12,
            message="opened",
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


def test_success_reviews_exact_artifact_pushes_branch_and_opens_pr(tmp_path: Path) -> None:
    root, remote = _repository(tmp_path)
    builder = _Builder([_patch(1, 2)])
    reviewer = _Reviewer([_pass()])
    validator = _Validator([True])
    github = _GitHub()
    workflow, monday = _workflow(
        root, builder=builder, reviewer=reviewer, validator=validator, github=github
    )

    job = workflow.run(task_id="TASK-0001", delivery_id="delivery-success")

    assert job.success is True
    assert job.status == "pr-open"
    assert job.pr_url.endswith("/pull/12")
    assert job.artifact_sha256 == job.reviewed_artifact_sha256
    assert len(job.artifact_sha256) == 64
    assert monday.status == "review"
    assert (root / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert _git(remote, "show", f"{job.branch}:app.py") == "VALUE = 2"
    assert builder.calls == reviewer.calls == validator.calls == github.calls == 1

    same = workflow.run(task_id="TASK-0001", delivery_id="delivery-success")
    assert same.commit_sha == job.commit_sha
    assert builder.calls == 1

    try:
        workflow.run(task_id="TASK-9999", delivery_id="delivery-success")
    except ValueError as exc:
        assert "already bound" in str(exc)
    else:  # pragma: no cover - identity confusion must never be tolerated
        raise AssertionError("a delivery identity was reused for another task")


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
    assert workflow.get(job.delivery_id).status == "pr-open"
    assert _git(remote, "show", f"{job.branch}:app.py") == "VALUE = 2"
    assert github.calls == 1


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
    assert builder.calls == github.calls == 0
    assert monday.calls == ["get"]
    assert not (root / "logs" / "delivery" / "worktrees").exists()


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
    assert job.attempt == 0
    assert job.attempts == []
    assert "claude-code: subscription unavailable" in job.message
    assert "codex-chatgpt: temporary model outage" in job.message
    assert claude.calls == codex.calls == 1
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


def test_all_model_and_validation_text_is_redacted_before_job_persistence(
    tmp_path: Path,
) -> None:
    root, _ = _repository(tmp_path)
    objective_secret = _fixture("sk-", "objectivesecret1234567890")
    summary_secret = _fixture("ghp_", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    validation_secret = _fixture("AKIA", "BBBBBBBBBBBBBBBB")
    review_secret = _fixture("eyJabcde", ".abcde12345", ".signature12345")
    finding_secret = _fixture("12345678", ":", "telegramsecretvalue1234567890")
    recommendation_secret = _fixture("password=", "SuperSecretPassword12345")
    monday = _Monday(objective=f"Set VALUE safely using {objective_secret}")
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
        objective_secret,
        summary_secret,
        validation_secret,
        review_secret,
        finding_secret,
        recommendation_secret,
    ):
        assert secret not in stored
    assert stored.count("[REDACTED]") >= 5
    assert "Raw successful output withheld by the delivery controller." in stored
