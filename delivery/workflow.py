"""Durable, artifact-bound autonomous build and pull-request workflow."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sys
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from delivery.backends import (
    ArtifactReviewer,
    ClaudeCodePatchBuilder,
    CodexArtifactReviewer,
    CodexPatchBuilder,
    PatchBuilder,
    ReviewDecision,
    ToolAvailability,
    _controller_env,
    _redact,
    _run_process,
)
from delivery.git import ChangedPath, GitRepository, GitValidationError, Worktree
from delivery.github import GhCliClient
from delivery.security import (
    ArtifactSecurityError,
    artifact_digest,
    proposed_paths,
    scan_staged_diff,
    verification_digest,
)
from delivery.store import DeliveryStore
from delivery.types import DeliveryAttempt, DeliveryJob
from delivery.validation import MondayValidationRunner, ValidationResult

ProgressCallback = Callable[[dict[str, Any]], None]
CheckpointCallback = Callable[[dict[str, Any]], None]


class _WorkflowLeaseTimeoutError(TimeoutError):
    """Another live delivery still owns this repository's workflow lease."""


class _BuilderProviderError(RuntimeError):
    """A builder was reachable but did not return a usable patch proposal."""


class DeliveryWorkflow:
    """Turn one MondayOS task into one independently reviewed GitHub PR.

    This initial slice deliberately targets only the configured MondayOS
    checkout.  It cannot merge, deploy, install dependencies, or execute a
    model-selected command.  Builders return text patches; the controller owns
    every filesystem, validation, Git, and GitHub mutation.
    """

    def __init__(
        self,
        *,
        monday: Any,
        project_root: Path,
        builders: Sequence[PatchBuilder] | None = None,
        reviewer: ArtifactReviewer | None = None,
        validator: Any | None = None,
        github: Any | None = None,
        repository: Any | None = None,
        max_attempts: int = 3,
        builder_timeout: float = 1_800,
        reviewer_timeout: float = 1_200,
        lease_timeout: float = 30,
    ) -> None:
        if not 1 <= max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")
        self.monday = monday
        self.project_root = Path(project_root).resolve()
        self.store = DeliveryStore(self.project_root)
        self.builders = list(builders or (ClaudeCodePatchBuilder(), CodexPatchBuilder()))
        self.reviewer = reviewer or CodexArtifactReviewer()
        self.validator = validator or MondayValidationRunner(source_root=self.project_root)
        self.github = github or GhCliClient()
        self.repository = repository or GitRepository(
            self.project_root,
            trusted_root=self.project_root,
            runtime_root=self.store.root,
        )
        self.max_attempts = max_attempts
        self.builder_timeout = builder_timeout
        self.reviewer_timeout = reviewer_timeout
        self.lease_timeout = lease_timeout

    def capabilities(self) -> dict[str, Any]:
        """Return live, read-only readiness for builders, reviewer, and GitHub."""
        builder_rows = [self._availability(builder) for builder in self.builders]
        reviewer = self._availability(self.reviewer)
        github = self._availability(self.github)
        validation = self._validation_readiness()
        repository = self._repository_readiness()
        return {
            "ready": any(row["available"] for row in builder_rows)
            and reviewer["available"]
            and github["available"]
            and validation["available"]
            and repository["available"],
            "builders": builder_rows,
            "reviewer": reviewer,
            "github": github,
            "validation": validation,
            "repository": repository,
            "max_attempts": self.max_attempts,
            "delivery": "pull-request-only",
            "merge_enabled": False,
            "deploy_enabled": False,
        }

    def get(self, delivery_id: str) -> DeliveryJob:
        return self.store.get(delivery_id)

    def history(self, *, task_id: str | None = None, limit: int = 20) -> list[DeliveryJob]:
        return self.store.history(task_id=task_id, limit=limit)

    def run(
        self,
        *,
        task_id: str,
        delivery_id: str | None = None,
        progress_callback: ProgressCallback | None = None,
        checkpoint_callback: CheckpointCallback | None = None,
    ) -> DeliveryJob:
        """Run or idempotently retrieve one delivery identity."""
        delivery_id = delivery_id or f"delivery-{uuid.uuid4().hex[:16]}"
        task_response = self.monday.task("get", task_id=task_id)
        task_data = dict(getattr(task_response, "data", {}) or {})
        objective = _objective(task_id, task_data)
        now = _now()
        job = DeliveryJob(
            delivery_id=delivery_id,
            task_id=task_id,
            objective=objective,
            repo_root=str(self.project_root),
            max_attempts=self.max_attempts,
            created_at=now,
            updated_at=now,
        )
        existing = self._existing_job(delivery_id)
        if existing is not None:
            self._require_task_binding(existing, task_id)
            if existing.terminal:
                return existing
            return self._reconcile_interrupted(
                existing,
                progress_callback=progress_callback,
            )

        reservation_owned = False
        try:
            with self._workflow_lease():
                # Reserve only after owning the repository-wide lease.  This
                # closes the gap where a duplicate caller could otherwise see
                # a fresh record before its live worker acquired the lease and
                # incorrectly classify that live worker as crashed.
                existing = self.store.reserve(job)
                if existing is not None:
                    self._require_task_binding(existing, task_id)
                    if existing.terminal:
                        return existing
                    return self._mark_interrupted(existing, progress_callback)
                reservation_owned = True

                if checkpoint_callback is not None:
                    # The reservation is durable before an external controller is
                    # asked to checkpoint it.  Failure stops before model or Git work.
                    checkpoint_callback(job.to_dict())
                if not getattr(task_response, "success", False):
                    return self._fail(
                        job,
                        "task-not-found",
                        getattr(task_response, "message", ""),
                    )
                if task_data.get("status") in {"completed", "cancelled"}:
                    return self._fail(
                        job,
                        "terminal-task",
                        "Completed or cancelled tasks cannot run",
                    )

                self._update(
                    job,
                    status="running",
                    phase="preflight",
                    message="Checking build tools",
                )
                self._notify(job, progress_callback)
                repository_ready = self._repository_readiness()
                if not repository_ready["available"]:
                    return self._fail(
                        job,
                        "repository-unavailable",
                        repository_ready["reason"],
                    )
                validation_ready = self._validation_readiness()
                if not validation_ready["available"]:
                    return self._fail(
                        job,
                        "validation-unavailable",
                        validation_ready["reason"],
                    )
                builders, builder_reasons = self._select_builders()
                if not builders:
                    detail = "; ".join(builder_reasons) or "No builders are configured"
                    return self._fail(
                        job,
                        "builder-unavailable",
                        f"No authenticated coding builder is available. {detail}",
                    )
                reviewer_ready = self._availability(self.reviewer)
                if not reviewer_ready["available"]:
                    return self._fail(
                        job,
                        "reviewer-unavailable",
                        reviewer_ready["reason"],
                    )
                github_ready = self._availability(self.github)
                if not github_ready["available"]:
                    return self._fail(job, "github-unavailable", github_ready["reason"])
                job.builder = _safe_text(builders[0].name, 200)
                self.store.persist(job)
                return self._run_locked(job, task_data, builders, progress_callback)
        except _WorkflowLeaseTimeoutError as exc:
            # A duplicate caller must never terminalize a record whose original
            # worker still owns the lease.  Return the newest durable snapshot
            # so its controller can report progress and reconcile on retry.
            current = self._existing_job(delivery_id)
            if current is not None:
                self._require_task_binding(current, task_id)
                return current
            existing = self.store.reserve(job)
            if existing is not None:
                self._require_task_binding(existing, task_id)
                return existing
            return self._fail(job, "workflow-busy", _safe_message(exc))
        except Exception as exc:  # fail closed at the durable workflow boundary
            if reservation_owned:
                return self._fail(job, _failure_code(exc), _safe_message(exc))
            existing = self.store.reserve(job)
            if existing is not None:
                self._require_task_binding(existing, task_id)
                return existing
            return self._fail(job, _failure_code(exc), _safe_message(exc))

    def _existing_job(self, delivery_id: str) -> DeliveryJob | None:
        try:
            return self.store.get(delivery_id)
        except FileNotFoundError:
            return None

    @staticmethod
    def _require_task_binding(job: DeliveryJob, task_id: str) -> None:
        if job.task_id != task_id:
            raise ValueError(
                f"{job.delivery_id} is already bound to {job.task_id}, not {task_id}"
            )

    def _reconcile_interrupted(
        self,
        job: DeliveryJob,
        *,
        progress_callback: ProgressCallback | None,
    ) -> DeliveryJob:
        try:
            with self._workflow_lease():
                current = self.store.get(job.delivery_id)
                self._require_task_binding(current, job.task_id)
                if current.terminal:
                    return current
                return self._mark_interrupted(current, progress_callback)
        except _WorkflowLeaseTimeoutError:
            current = self.store.get(job.delivery_id)
            self._require_task_binding(current, job.task_id)
            return current

    def _mark_interrupted(
        self,
        job: DeliveryJob,
        progress_callback: ProgressCallback | None,
    ) -> DeliveryJob:
        job.status = "interrupted"
        job.phase = "interrupted"
        job.success = False
        job.failure_code = "interrupted-run"
        job.message = (
            "The previous build worker ended before this delivery finished. "
            "Start a new build request so MondayOS creates a new delivery identity; "
            "this identity will not replay mutations."
        )
        job.updated_at = _now()
        self.store.persist(job)
        self._notify(job, progress_callback)
        return job

    def _run_locked(
        self,
        job: DeliveryJob,
        task_data: dict[str, Any],
        builders: Sequence[PatchBuilder],
        progress_callback: ProgressCallback | None,
    ) -> DeliveryJob:
        self._update(job, phase="preparing", message="Preparing an isolated worktree")
        self._notify(job, progress_callback)
        self.repository.validate_trusted_root()
        repository_slug = self.repository.github_slug()
        base = self.repository.resolve_base()
        worktree = self.repository.create_worktree(job.task_id, base)
        job.base_ref = base.ref
        job.base_branch = base.branch
        job.base_sha = base.sha
        job.repository = repository_slug
        job.branch = worktree.branch
        job.worktree = str(worktree.path)
        self.store.persist(job)

        started = self.monday.task("start", task_id=job.task_id)
        if not getattr(started, "success", False):
            raise RuntimeError(getattr(started, "message", "Could not start task"))

        feedback = ""
        builder_index = 0
        patch_attempts = 0
        pending_provider_failures: list[dict[str, str]] = []
        all_provider_failures: list[dict[str, str]] = []
        while patch_attempts < self.max_attempts:
            if patch_attempts:
                # A repair is a complete replacement proposal against the
                # immutable base, never an incremental patch over a rejected
                # candidate.  The final failed candidate is intentionally left
                # intact because this reset runs only when a retry will start.
                self.repository.restore_worktree_to_base(worktree)
            self._update(
                job,
                phase="implementing",
                message=(
                    f"Requesting implementation candidate "
                    f"{patch_attempts + 1}/{self.max_attempts}"
                ),
            )
            self._notify(job, progress_callback)

            proposal = None
            builder: PatchBuilder | None = None
            while builder_index < len(builders):
                candidate = builders[builder_index]
                candidate_name = _safe_text(candidate.name, 200)
                try:
                    candidate_proposal = candidate.propose(
                        worktree=worktree.path,
                        objective=job.objective,
                        feedback=feedback,
                        timeout=self.builder_timeout,
                    )
                    if not candidate_proposal.success:
                        reason = candidate_proposal.message or (
                            "Builder did not produce a patch"
                        )
                        raise _BuilderProviderError(reason)
                except Exception as exc:
                    failure = _provider_failure(candidate_name, exc)
                    pending_provider_failures.append(failure)
                    all_provider_failures.append(failure)
                    builder_index += 1
                    next_name = (
                        _safe_text(builders[builder_index].name, 200)
                        if builder_index < len(builders)
                        else "no remaining provider"
                    )
                    self._update(
                        job,
                        builder=candidate_name,
                        message=(
                            f"{candidate_name} could not provide a patch; "
                            f"trying {next_name}"
                        ),
                    )
                    self._notify(job, progress_callback)
                    continue
                proposal = candidate_proposal
                builder = candidate
                break

            if proposal is None or builder is None:
                detail = _provider_failure_summary(all_provider_failures)
                return self._fail(
                    job,
                    "builders-failed",
                    f"All available coding providers failed; nothing was pushed. {detail}",
                )

            patch_attempts += 1
            number = patch_attempts
            builder_name = _safe_text(builder.name, 200)
            self._update(
                job,
                attempt=number,
                builder=builder_name,
                message=f"Implementation attempt {number}/{self.max_attempts}",
            )
            attempt = DeliveryAttempt(number=number, builder=builder_name)
            attempt.patch_summary = _safe_text(proposal.summary, 4_000)
            provider_failures = list(pending_provider_failures)
            pending_provider_failures.clear()

            try:
                proposed_paths(proposal.patch)
            except ArtifactSecurityError as exc:
                attempt.status = "patch-invalid"
                attempt.message = _safe_message(exc)
                job.attempts.append(_attempt_dict(attempt, provider_failures))
                self.store.persist(job)
                feedback = f"The prior patch violated artifact policy: {attempt.message}"
                continue
            self.repository.assert_head(worktree, base.sha)
            try:
                self.repository.apply_unified_patch(worktree, proposal.patch)
            except GitValidationError as exc:
                attempt.status = "patch-invalid"
                attempt.message = _safe_message(exc)
                job.attempts.append(_attempt_dict(attempt, provider_failures))
                self.store.persist(job)
                feedback = f"The prior patch could not be applied safely: {attempt.message}"
                continue
            try:
                changed_files = list(self.repository.validate_changed_paths(worktree))
                self.repository.stage(worktree, changed_files)
                staged_files = list(self.repository.staged_changed_paths(worktree))
                if sorted(changed_files) != sorted(staged_files):
                    raise ArtifactSecurityError(
                        "staged paths do not match the inspected path set"
                    )
                _require_index_only(self.repository.status(worktree))
                staged_diff = self.repository.staged_diff(worktree)
                if not staged_diff.strip():
                    raise ArtifactSecurityError("builder produced no effective staged change")
                scan_staged_diff(staged_diff)
            except ArtifactSecurityError as exc:
                attempt.status = "patch-invalid"
                attempt.message = _safe_message(exc)
                job.attempts.append(_attempt_dict(attempt, provider_failures))
                self.store.persist(job)
                feedback = f"The prior patch violated artifact policy: {attempt.message}"
                continue
            attempt.changed_files = staged_files

            self._update(job, phase="validating", message=f"Validating attempt {number}")
            self._notify(job, progress_callback)
            validation = self.validator.run(
                worktree=worktree.path,
                source_root=self.project_root,
                changed_files=staged_files,
            )
            validation_dicts = [
                _sanitize_validation(_validation_dict(item)) for item in validation
            ]
            attempt.verification = validation_dicts
            attempt.verification_sha256 = verification_digest(validation_dicts)
            if not validation or not all(_validation_success(item) for item in validation):
                attempt.status = "validation-failed"
                attempt.message = "Deterministic validation failed"
                job.attempts.append(_attempt_dict(attempt, provider_failures))
                self.store.persist(job)
                feedback = _validation_feedback(validation_dicts)
                continue

            _require_index_only(self.repository.status(worktree))
            staged_diff = self.repository.staged_diff(worktree)
            scan_staged_diff(staged_diff)
            attempt.diff_sha256, attempt.artifact_sha256 = artifact_digest(
                base_sha=base.sha,
                objective=job.objective,
                diff=staged_diff,
                verification_sha256=attempt.verification_sha256,
            )

            self._update(job, phase="reviewing", message=f"ChatGPT reviewing attempt {number}")
            self._notify(job, progress_callback)
            decision = _sanitize_review(
                self._review(
                    worktree=worktree.path,
                    objective=job.objective,
                    staged_diff=staged_diff,
                    diff_sha256=attempt.diff_sha256,
                    verification=validation_dicts,
                )
            )
            attempt.review = decision.to_dict()
            if decision.verdict == "block":
                attempt.status = "blocked"
                attempt.message = decision.summary
                job.attempts.append(_attempt_dict(attempt, provider_failures))
                self.store.persist(job)
                return self._reject(job, decision.summary or "Reviewer blocked delivery")
            if not decision.passed:
                attempt.status = "needs-changes"
                attempt.message = decision.summary
                job.attempts.append(_attempt_dict(attempt, provider_failures))
                self.store.persist(job)
                feedback = _review_feedback(decision)
                continue

            # The Reviewer is read-only, but authorization is still valid only
            # for the exact bytes and evidence it saw.  Recompute immediately.
            self.repository.assert_head(worktree, base.sha)
            _require_index_only(self.repository.status(worktree))
            final_diff = self.repository.staged_diff(worktree)
            final_diff_sha, final_artifact_sha = artifact_digest(
                base_sha=base.sha,
                objective=job.objective,
                diff=final_diff,
                verification_sha256=attempt.verification_sha256,
            )
            if (
                final_diff_sha != attempt.diff_sha256
                or final_artifact_sha != attempt.artifact_sha256
            ):
                raise ArtifactSecurityError("artifact changed after independent review")

            attempt.status = "approved"
            attempt.message = decision.summary
            job.attempts.append(_attempt_dict(attempt, provider_failures))
            job.changed_files = staged_files
            job.artifact_sha256 = final_artifact_sha
            job.reviewed_artifact_sha256 = final_artifact_sha
            self.store.persist(job)
            return self._deliver(
                job,
                task_data,
                worktree,
                staged_files,
                final_diff_sha,
                progress_callback,
            )

        return self._fail(
            job,
            "repair-exhausted",
            f"Stopped after {self.max_attempts} implementation attempts; nothing was pushed",
        )

    def _deliver(
        self,
        job: DeliveryJob,
        task_data: dict[str, Any],
        worktree: Worktree,
        changed_files: list[str],
        expected_diff_sha256: str,
        progress_callback: ProgressCallback | None,
    ) -> DeliveryJob:
        self._update(job, phase="committing", message="Committing the approved artifact")
        self._notify(job, progress_callback)
        commit = self.repository.commit(
            worktree,
            changed_files,
            f"{job.task_id}: autonomous build ({job.delivery_id})",
            expected_diff_sha256=expected_diff_sha256,
        )
        job.commit_sha = commit
        self.store.persist(job)

        self._update(job, phase="pushing", message="Pushing the approved branch")
        self._notify(job, progress_callback)
        pushed_sha = self.repository.push(worktree, commit)
        if pushed_sha != commit:
            raise ArtifactSecurityError("pushed SHA does not match the approved commit")
        job.pushed = True
        self.store.persist(job)

        self._update(job, phase="pull-request", message="Opening the GitHub pull request")
        self._notify(job, progress_callback)
        result = self.github.ensure_pull_request(
            repo_root=self.project_root,
            repository=job.repository,
            base=job.base_branch,
            branch=job.branch,
            title=_pr_title(job.task_id, str(task_data.get("title", "Autonomous build"))),
            body=_pr_body(job),
            expected_sha=commit,
        )
        if not result.success:
            raise RuntimeError(result.message or "GitHub pull request creation failed")
        job.pr_url = result.url
        job.pr_number = result.number
        job.status = "pr-open"
        job.phase = "completed"
        job.success = True
        job.message = f"ChatGPT approved the exact artifact and PR #{result.number} is open"
        job.updated_at = _now()
        self.store.persist(job)

        try:
            reviewed = self.monday.task(
                "review",
                task_id=job.task_id,
                changed_by=f"delivery:{job.delivery_id}",
                reason=f"Approved autonomous build opened {job.pr_url}",
            )
        except Exception:
            # GitHub is authoritative once the exact verified PR has been
            # durably recorded. A local task-store failure must not rewrite an
            # already published success as a failed delivery.
            reviewed = None
        if not getattr(reviewed, "success", False):
            job.message += "; task status could not be moved to REVIEW"
            self.store.persist(job)
        self._notify(job, progress_callback)
        try:
            self.repository.cleanup(worktree, delete_branch=False)
        except Exception:
            # The published commit/PR is authoritative.  A retained owned
            # worktree is recoverable local diagnostic state, not a failed PR.
            job.message += "; the local worktree was retained for cleanup"
            self.store.persist(job)
        return job

    def _select_builders(self) -> tuple[list[PatchBuilder], list[str]]:
        available: list[PatchBuilder] = []
        reasons: list[str] = []
        for builder in self.builders:
            availability = self._availability(builder)
            if availability["available"]:
                available.append(builder)
            else:
                reasons.append(f"{availability['tool']}: {availability['reason']}")
        return available, reasons

    def _review(
        self,
        *,
        worktree: Path,
        objective: str,
        staged_diff: str,
        diff_sha256: str,
        verification: list[dict[str, Any]],
    ) -> ReviewDecision:
        last_error: Exception | None = None
        for _ in range(2):
            try:
                return self.reviewer.review(
                    worktree=worktree,
                    objective=objective,
                    staged_diff=staged_diff,
                    diff_sha256=diff_sha256,
                    verification_json=json.dumps(verification, sort_keys=True),
                    timeout=self.reviewer_timeout,
                )
            except Exception as exc:
                last_error = exc
        assert last_error is not None
        raise RuntimeError(f"Independent reviewer failed twice: {_safe_message(last_error)}")

    def _availability(self, tool: Any) -> dict[str, Any]:
        try:
            result: ToolAvailability = tool.availability()
            return {
                "available": bool(result.available),
                "tool": _safe_text(result.tool, 200),
                "reason": _safe_text(result.reason, 2_000),
            }
        except Exception as exc:
            return {
                "available": False,
                "tool": _safe_text(getattr(tool, "name", type(tool).__name__), 200),
                "reason": _safe_message(exc),
            }

    def _repository_readiness(self) -> dict[str, Any]:
        try:
            self.repository.validate_trusted_root()
            slug = self.repository.github_slug()
            return {
                "available": True,
                "tool": "repository",
                "reason": f"trusted GitHub repository {slug}",
            }
        except Exception as exc:
            return {
                "available": False,
                "tool": "repository",
                "reason": _safe_message(exc),
            }

    def _validation_readiness(self) -> dict[str, Any]:
        availability = getattr(self.validator, "availability", None)
        if callable(availability):
            return self._availability(self.validator)
        if not isinstance(self.validator, MondayValidationRunner):
            return {
                "available": True,
                "tool": "validation",
                "reason": "configured validation adapter is ready",
            }
        if sys.platform != "darwin":
            return {
                "available": False,
                "tool": "validation",
                "reason": "autonomous validation currently requires macOS",
            }
        python = self.project_root / ".venv" / "bin" / "python"
        if not python.is_file() or not os.access(python, os.X_OK):
            return {
                "available": False,
                "tool": "validation",
                "reason": "project .venv/bin/python is unavailable",
            }
        try:
            result = _run_process(
                [os.fspath(python), "-I", "-c", "import pytest"],
                cwd=python.parent.parent,
                timeout=20,
                env=_controller_env(),
            )
        except Exception as exc:
            return {
                "available": False,
                "tool": "validation",
                "reason": f"validation environment check failed: {_safe_message(exc)}",
            }
        if result.returncode != 0:
            return {
                "available": False,
                "tool": "validation",
                "reason": "pytest is unavailable in the project virtual environment",
            }
        return {
            "available": True,
            "tool": "validation",
            "reason": "macOS Seatbelt and project pytest environment are ready",
        }

    def _update(self, job: DeliveryJob, **values: Any) -> None:
        for key, value in values.items():
            if key == "message":
                value = _safe_text(value, 2_000)
            elif key in {"builder", "reviewer"}:
                value = _safe_text(value, 200)
            setattr(job, key, value)
        job.updated_at = _now()
        self.store.persist(job)

    def _notify(self, job: DeliveryJob, callback: ProgressCallback | None) -> None:
        if callback is None:
            return
        try:
            callback(job.to_dict())
        except Exception:
            # Notifications are downstream of durable progress and can be
            # retried by the Telegram controller without replaying mutations.
            return

    def _fail(self, job: DeliveryJob, code: str, message: str) -> DeliveryJob:
        job.status = "failed"
        job.phase = "failed"
        job.success = False
        job.failure_code = code
        job.message = _safe_text(message or code, 2_000)
        job.updated_at = _now()
        self.store.persist(job)
        return job

    def _reject(self, job: DeliveryJob, message: str) -> DeliveryJob:
        job.status = "rejected"
        job.phase = "review-blocked"
        job.success = False
        job.failure_code = "review-blocked"
        job.message = _safe_text(message, 2_000)
        job.updated_at = _now()
        self.store.persist(job)
        return job

    @contextmanager
    def _workflow_lease(self) -> Iterator[None]:
        lock_dir = self.store.root / "workflow-locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(os.fsencode(self.project_root)).hexdigest()
        path = lock_dir / f"{digest}.lock"
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        deadline = time.monotonic() + self.lease_timeout
        try:
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise _WorkflowLeaseTimeoutError(
                            "another build already owns this repository"
                        ) from None
                    time.sleep(0.05)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _objective(task_id: str, task: dict[str, Any]) -> str:
    criteria = [
        str(item).strip()
        for item in task.get("acceptance_criteria", [])
        if str(item).strip()
    ]
    lines = [
        f"Task: {task_id}",
        f"Title: {str(task.get('title', '')).strip()}",
        "Objective:",
        str(task.get("objective", "")).strip(),
    ]
    if criteria:
        lines.extend(["Acceptance criteria:", *[f"- {item}" for item in criteria]])
    return _safe_text("\n".join(lines), 50_000)


def _require_index_only(entries: Sequence[ChangedPath]) -> None:
    if not entries:
        raise ArtifactSecurityError("the candidate has no changes")
    for entry in entries:
        status = entry.status
        if len(status) != 2 or status == "??" or status[1] != " ":
            raise ArtifactSecurityError(
                f"candidate contains an unstaged or untracked change: {entry.path}"
            )


def _validation_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, ValidationResult):
        return result.to_dict()
    if hasattr(result, "to_dict"):
        return dict(result.to_dict())
    if isinstance(result, dict):
        return dict(result)
    raise TypeError("validator returned an unsupported result")


def _sanitize_validation(result: dict[str, Any]) -> dict[str, Any]:
    """Keep controller-derived evidence without retaining candidate output."""
    raw_argv = result.get("argv", [])
    if isinstance(raw_argv, (list, tuple)):
        argv = [_safe_text(item, 1_000) for item in raw_argv[:100]]
    else:
        argv = [_safe_text(raw_argv, 1_000)]
    returncode = result.get("returncode", -1)
    try:
        returncode = int(returncode)
    except (TypeError, ValueError, OverflowError):
        returncode = -1
    success = bool(result.get("success", False))
    # Candidate tests control stdout and stderr on both pass and failure. Raw
    # diagnostics can encode source text, secrets, or instructions that regex
    # redaction cannot recognize, so neither models nor durable records receive
    # them. The controller-derived check identity, exit code, and result remain.
    output = (
        "Raw successful output withheld by the delivery controller."
        if success
        else "Raw failed output withheld by the delivery controller."
    )
    return {
        "name": _safe_text(result.get("name", "check"), 200),
        "argv": argv,
        "success": success,
        "returncode": returncode,
        "output": output,
    }


def _sanitize_review(decision: ReviewDecision) -> ReviewDecision:
    return ReviewDecision(
        verdict=_safe_text(decision.verdict, 40).strip().lower(),
        confidence=_safe_text(decision.confidence, 40).strip().lower(),
        summary=_safe_text(decision.summary, 4_000),
        findings=_safe_text_list(decision.findings, item_limit=2_000, list_limit=100),
        recommendations=_safe_text_list(
            decision.recommendations,
            item_limit=2_000,
            list_limit=100,
        ),
    )


def _attempt_dict(
    attempt: DeliveryAttempt,
    provider_failures: list[dict[str, str]],
) -> dict[str, Any]:
    payload = attempt.to_dict()
    if provider_failures:
        payload["provider_failures"] = [dict(item) for item in provider_failures]
    return payload


def _provider_failure(builder: str, exc: BaseException) -> dict[str, str]:
    return {
        "builder": _safe_text(builder, 200),
        "message": _safe_message(exc),
    }


def _provider_failure_summary(failures: list[dict[str, str]]) -> str:
    if not failures:
        return "No provider diagnostic was returned."
    return _safe_text(
        "; ".join(
            f"{item.get('builder', 'builder')}: {item.get('message', 'failed')}"
            for item in failures
        ),
        2_000,
    )


def _safe_text_list(
    values: Any,
    *,
    item_limit: int,
    list_limit: int,
) -> list[str]:
    if isinstance(values, (list, tuple)):
        items = values[:list_limit]
    elif values is None:
        items = []
    else:
        items = [values]
    return [_safe_text(item, item_limit) for item in items]


def _validation_success(result: Any) -> bool:
    if isinstance(result, dict):
        return bool(result.get("success"))
    return bool(getattr(result, "success", False))


def _validation_feedback(results: list[dict[str, Any]]) -> str:
    failed = [result for result in results if not result.get("success")]
    text = "\n\n".join(
        f"{item.get('name', 'check')} failed (exit {item.get('returncode', '?')}). "
        "Raw candidate-controlled diagnostics were withheld; inspect the fixed "
        "check and propose a complete corrected implementation."
        for item in failed
    )
    return (text or "Validation failed without diagnostics")[:12_000]


def _review_feedback(decision: ReviewDecision) -> str:
    findings = "\n".join(f"- {item}" for item in decision.findings)
    recommendations = "\n".join(f"- {item}" for item in decision.recommendations)
    return (
        f"Independent review: {decision.summary}\n"
        f"Findings:\n{findings or '- none supplied'}\n"
        f"Recommendations:\n{recommendations or '- none supplied'}"
    )[:12_000]


def _pr_title(task_id: str, title: str) -> str:
    single_line = (
        re.sub(r"\s+", " ", _safe_text(title, 1_000)).strip()[:120]
        or "Autonomous build"
    )
    return f"{task_id}: {single_line}"


def _pr_body(job: DeliveryJob) -> str:
    checks: list[str] = []
    if job.attempts:
        for result in job.attempts[-1].get("verification", []):
            mark = "passed" if result.get("success") else "failed"
            checks.append(f"- {result.get('name', 'check')}: {mark}")
    files = "\n".join(f"- `{path}`" for path in job.changed_files)
    return (
        "## MondayOS autonomous build\n\n"
        f"- Task: `{job.task_id}`\n"
        f"- Build: `{job.delivery_id}`\n"
        f"- Attempts: {len(job.attempts)}/{job.max_attempts}\n"
        "- Independent reviewer: ChatGPT/Codex\n"
        "- Reviewer verdict: pass (high confidence)\n"
        f"- Reviewed artifact: `{job.reviewed_artifact_sha256}`\n\n"
        "### Verification\n\n"
        f"{chr(10).join(checks) or '- No checks recorded'}\n\n"
        "### Changed files\n\n"
        f"{files or '- None'}\n\n"
        "> This workflow opens a pull request only. It cannot merge or deploy.\n"
    )


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _safe_message(exc: BaseException) -> str:
    return _safe_text(str(exc) or type(exc).__name__, 2_000)


def _safe_text(value: Any, limit: int) -> str:
    try:
        text = str(value)
    except Exception:
        text = type(value).__name__
    return _redact(text)[:limit]


def _failure_code(exc: BaseException) -> str:
    name = type(exc).__name__.replace("Error", "").replace("Exception", "")
    slug = re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()
    return slug or "delivery-failed"
