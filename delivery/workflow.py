"""Durable, artifact-bound autonomous build and pull-request workflow."""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.identity import require_slug, slug
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
from delivery.git import BaseRevision, ChangedPath, GitRepository, GitValidationError, Worktree
from delivery.github import GhCliClient
from delivery.security import (
    ArtifactSecurityError,
    artifact_digest,
    proposed_paths,
    scan_staged_diff,
    verification_digest,
)
from delivery.store import DeliveryStore
from delivery.types import (
    DeliveryAttempt,
    DeliveryJob,
    bounded_delivery_retry_after_seconds,
    normalize_delivery_failure_class,
)
from delivery.validation import MondayValidationRunner, ValidationResult
from tasks.manager import TaskManager

ProgressCallback = Callable[[dict[str, Any]], None]
CheckpointCallback = Callable[[dict[str, Any]], None]


class _WorkflowLeaseTimeoutError(TimeoutError):
    """Another live delivery still owns this repository's workflow lease."""


class _BuilderProviderError(RuntimeError):
    """A builder was reachable but did not return a usable patch proposal."""

    def __init__(
        self,
        message: str,
        *,
        failure_class: str = "",
        retry_after_seconds: int = 0,
    ) -> None:
        super().__init__(message)
        self.failure_class = failure_class
        self.retry_after_seconds = retry_after_seconds


_TRANSIENT_ERRNOS = frozenset(
    {
        errno.EAGAIN,
        errno.EBUSY,
        errno.ECONNABORTED,
        errno.ECONNREFUSED,
        errno.ECONNRESET,
        errno.EHOSTDOWN,
        errno.EHOSTUNREACH,
        errno.ENETDOWN,
        errno.ENETRESET,
        errno.ENETUNREACH,
        errno.ETIMEDOUT,
    }
)


class DeliveryWorkflow:
    """Turn one MondayOS task into one independently reviewed GitHub PR.

    MondayOS remains the controller and system of record even when the target is
    a separately registered repository. It cannot merge, deploy, install
    dependencies, or execute a model-selected command. Builders return text
    patches; the controller owns every filesystem, validation, Git, and GitHub
    mutation.
    """

    def __init__(
        self,
        *,
        monday: Any,
        project_root: Path,
        repository_root: Path | None = None,
        project_name: str = "",
        expected_repository: str = "",
        expected_root_identity: tuple[int, int] | None = None,
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
        self.controller_root = Path(project_root).resolve(strict=True)
        self.project_root = self.controller_root  # compatibility for embedders/tests
        self.repository_root = (
            Path(repository_root).expanduser().absolute()
            if repository_root is not None
            else self.controller_root
        )
        self.project_name = (
            require_slug(project_name, ascii_only=True) if project_name else ""
        )
        self.expected_repository = str(expected_repository or "").strip()
        self.external_target = repository_root is not None
        if self.external_target:
            if not self.project_name:
                raise ValueError("external delivery requires a registered project identity")
            if not self.expected_repository:
                raise ValueError("external delivery requires a pinned GitHub repository")
            target_canonical = self.repository_root.resolve(strict=True)
            if _paths_overlap(self.controller_root, target_canonical):
                raise ValueError(
                    "external delivery repository must be separate from the MondayOS "
                    "controller directory"
                )
        if expected_root_identity is None:
            metadata = self.repository_root.stat()
            expected_root_identity = (metadata.st_dev, metadata.st_ino)
        if (
            not isinstance(expected_root_identity, tuple)
            or len(expected_root_identity) != 2
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in expected_root_identity
            )
        ):
            raise ValueError("expected_root_identity must be a device/inode integer tuple")
        self.root_device, self.root_inode = expected_root_identity
        self.store = DeliveryStore(self.controller_root)
        default_builders: Sequence[PatchBuilder] = (
            (CodexPatchBuilder(),)
            if self.external_target
            else (ClaudeCodePatchBuilder(), CodexPatchBuilder())
        )
        self.builders = list(builders if builders is not None else default_builders)
        self.reviewer = reviewer or CodexArtifactReviewer()
        self.validator = validator or MondayValidationRunner(source_root=self.controller_root)
        self.github = github or GhCliClient()
        self.repository = repository or GitRepository(
            self.repository_root,
            trusted_root=self.repository_root,
            runtime_root=self.store.root,
            expected_github_repository=self.expected_repository or None,
            expected_root_identity=(self.root_device, self.root_inode),
            require_embedded_git_dir=self.external_target,
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
            "project_name": self.project_name,
            "repo_root": str(self.repository_root),
            "expected_repository": self.expected_repository,
            "max_attempts": self.max_attempts,
            "delivery": "pull-request-only",
            "merge_enabled": False,
            "deploy_enabled": False,
        }

    def get(self, delivery_id: str) -> DeliveryJob:
        return self.store.get(delivery_id)

    def history(
        self,
        *,
        task_id: str | None = None,
        project_name: str | None = None,
        limit: int = 20,
    ) -> list[DeliveryJob]:
        canonical_project = slug(project_name) if project_name is not None else None
        return self.store.history(
            task_id=task_id,
            project_name=canonical_project,
            limit=limit,
        )

    def observe(self, delivery_id: str) -> DeliveryJob:
        """Observe one PR without changing its reviewed branch or GitHub state.

        Delivery and pull-request lifecycle are deliberately separate.  Opening
        the PR is an immutable successful build result; this method records what
        GitHub later reports for that exact reviewed head and reconciles the task
        only after a verified merge or terminal closure.
        """
        with self._observation_lease(delivery_id):
            try:
                with self._workflow_lease():
                    return self._observe_locked(delivery_id)
            except _WorkflowLeaseTimeoutError:
                # Publication owns this lease until its PR record, task
                # transition, and local cleanup have all settled. Do not write
                # a stale snapshot over the live worker's in-memory job.
                job = self.store.get(delivery_id)
                self._require_observation_binding(job)
                self._require_observable_pull_request(job)
                job.observation_success = False
                job.observation_message = (
                    "The delivery is still finalizing; retry pull-request observation."
                )
                return job

    def _observe_locked(self, delivery_id: str) -> DeliveryJob:
        """Observe while holding the target repository's workflow lease."""
        job = self.store.get(delivery_id)
        self._require_observation_binding(job)
        self._require_observable_pull_request(job)
        try:
            self.repository.validate_trusted_root()
            actual_repository = self.repository.github_slug()
            if actual_repository.casefold() != job.repository.casefold():
                raise ArtifactSecurityError(
                    "Delivery repository no longer matches the target origin"
                )
            observed = self.github.observe_pull_request(
                repo_root=self.repository_root,
                repository=job.repository,
                base=job.base_branch,
                branch=job.branch,
                number=job.pr_number,
                url=job.pr_url,
                expected_sha=job.commit_sha,
            )
        except Exception as exc:
            job.observation_success = False
            job.observation_message = _safe_text(
                f"GitHub observation is temporarily unavailable: {_safe_message(exc)}",
                2_000,
            )
            job.pr_observed_at = _now()
            job.updated_at = job.pr_observed_at
            self.store.persist(job)
            return job

        job.pr_observed_at = _now()
        job.updated_at = job.pr_observed_at
        job.observation_success = bool(getattr(observed, "success", False))
        if not job.observation_success:
            job.observation_message = _safe_text(
                getattr(observed, "message", "GitHub observation is unavailable"),
                2_000,
            )
            self.store.persist(job)
            return job

        pr_state = str(getattr(observed, "lifecycle", "unavailable"))
        checks_status = str(getattr(observed, "checks", "unobserved"))
        merge_commit_sha = str(getattr(observed, "merge_commit_sha", ""))
        if (
            pr_state not in {"open", "merged", "closed", "diverged"}
            or checks_status not in {"pending", "passed", "failed", "no-checks"}
            or (
                pr_state == "merged"
                and not re.fullmatch(
                    r"[0-9a-f]{40}(?:[0-9a-f]{24})?", merge_commit_sha
                )
            )
            or (pr_state != "merged" and merge_commit_sha)
        ):
            job.observation_success = False
            job.observation_message = (
                "GitHub returned an inconsistent pull-request observation; "
                "retry observation."
            )
            self.store.persist(job)
            return job
        raw_counts = getattr(observed, "check_counts", None)
        if not isinstance(raw_counts, dict):
            raw_counts = {
                "total": getattr(observed, "total_checks", 0),
                "pending": getattr(observed, "pending_checks", 0),
                "passed": getattr(observed, "passed_checks", 0),
                "failed": getattr(observed, "failed_checks", 0),
            }
        check_counts = _validated_check_counts(raw_counts, checks_status)
        if check_counts is None:
            job.observation_success = False
            job.observation_message = (
                "GitHub returned an inconsistent pull-request check summary; "
                "retry observation."
            )
            self.store.persist(job)
            return job
        job.pr_state = pr_state
        job.checks_status = checks_status
        job.check_counts = check_counts
        job.merge_commit_sha = merge_commit_sha
        if pr_state == "open":
            # A terminal PR can later be reopened. Its old task action is not
            # the reconciliation for the newly observed open state.
            job.task_reconciliation = ""
            job.task_reconciled_at = ""
        job.observation_message = self._observation_message(job)
        self.store.persist(job)

        if job.pr_state in {"merged", "closed", "diverged"}:
            try:
                self._reconcile_observed_task(job)
            except Exception:
                # The exact GitHub state remains useful, but task-store
                # unavailability is retryable and must not crash the observer.
                job.observation_success = False
                job.task_reconciliation = "failed"
                job.observation_message = (
                    f"{self._observation_message(job)} Task reconciliation is "
                    "temporarily unavailable; retry observation."
                )
            job.updated_at = _now()
            self.store.persist(job)
        return job

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
        raw_objective = _objective(task_id, task_data)
        if _redact(raw_objective) != raw_objective:
            raise ArtifactSecurityError(
                "task title, objective, or acceptance criteria contains credential-like material"
            )
        objective = raw_objective[:50_000]
        now = _now()
        job = DeliveryJob(
            delivery_id=delivery_id,
            task_id=task_id,
            objective=objective,
            repo_root=str(self.repository_root),
            project_name=self.project_name,
            expected_repository=self.expected_repository,
            root_device=self.root_device,
            root_inode=self.root_inode,
            max_attempts=self.max_attempts,
            created_at=now,
            updated_at=now,
        )
        existing = self._existing_job(delivery_id)
        if existing is not None:
            self._require_binding(existing, job)
            if existing.terminal:
                return existing
            return self._reconcile_interrupted(
                existing,
                progress_callback=progress_callback,
            )

        reservation_owned = False
        try:
            with self._workflow_lease() as workflow_guard_fd:
                try:
                    # A different identity must not bypass an unresolved run for
                    # this exact task and repository.  The original identity is
                    # the only safe key for publication reconciliation.
                    blocking = self._blocking_delivery(job)
                    if blocking is not None:
                        existing = self.store.reserve(job)
                        if existing is not None:
                            self._require_binding(existing, job)
                            return existing
                        reservation_owned = True
                        return self._fail(
                            job,
                            "unresolved-delivery",
                            (
                                f"Delivery {blocking.delivery_id} must be retried or "
                                "settled before starting another build for this task"
                            ),
                        )

                    # Reserve only after owning the repository-wide lease.  This
                    # closes the gap where a duplicate caller could otherwise see
                    # a fresh record before its live worker acquired the lease and
                    # incorrectly classify that live worker as crashed.
                    existing = self.store.reserve(job)
                    if existing is not None:
                        self._require_binding(existing, job)
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
                    target_error = self._task_target_error(task_data)
                    if target_error:
                        return self._fail(job, "project-mismatch", target_error)

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
                            failure_class=repository_ready.get("failure_class", ""),
                            retry_after_seconds=repository_ready.get(
                                "retry_after_seconds", 0
                            ),
                        )
                    validation_ready = self._validation_readiness()
                    if not validation_ready["available"]:
                        return self._fail(
                            job,
                            "validation-unavailable",
                            validation_ready["reason"],
                            failure_class=validation_ready.get("failure_class", ""),
                            retry_after_seconds=validation_ready.get(
                                "retry_after_seconds", 0
                            ),
                        )
                    (
                        builders,
                        builder_reasons,
                        builder_failure_class,
                        builder_retry_after,
                    ) = self._select_builders()
                    if not builders:
                        detail = "; ".join(builder_reasons) or "No builders are configured"
                        return self._fail(
                            job,
                            "builder-unavailable",
                            f"No authenticated coding builder is available. {detail}",
                            failure_class=builder_failure_class,
                            retry_after_seconds=builder_retry_after,
                        )
                    reviewer_ready = self._availability(self.reviewer)
                    if not reviewer_ready["available"]:
                        return self._fail(
                            job,
                            "reviewer-unavailable",
                            reviewer_ready["reason"],
                            failure_class=reviewer_ready["failure_class"],
                            retry_after_seconds=reviewer_ready["retry_after_seconds"],
                        )
                    github_ready = self._availability(self.github)
                    if not github_ready["available"]:
                        return self._fail(
                            job,
                            "github-unavailable",
                            github_ready["reason"],
                            failure_class=github_ready["failure_class"],
                            retry_after_seconds=github_ready["retry_after_seconds"],
                        )
                    job.builder = _safe_text(builders[0].name, 200)
                    self.store.persist(job)
                    return self._run_locked(
                        job,
                        task_data,
                        builders,
                        progress_callback,
                        workflow_guard_fd,
                    )
                except Exception as exc:
                    # Persist the outcome while this worker still owns the
                    # repository lease.  Otherwise a retry can acquire the
                    # lease, update the record, and then be overwritten here.
                    if reservation_owned:
                        if self._publication_started(job):
                            return self._publication_pending(job, exc)
                        failure_class = _exception_failure_class(exc)
                        if not failure_class:
                            # Preserve the schema-v1 override contract used by
                            # embedders that wrap _fail(job, code, message).
                            return self._fail(
                                job,
                                _failure_code(exc),
                                _safe_message(exc),
                            )
                        return self._fail(
                            job,
                            _failure_code(exc),
                            _safe_message(exc),
                            failure_class=failure_class,
                            retry_after_seconds=_exception_retry_after_seconds(exc),
                        )
                    raise
        except _WorkflowLeaseTimeoutError as exc:
            # A duplicate caller must never terminalize a record whose original
            # worker still owns the lease.  Return the newest durable snapshot
            # so its controller can report progress and reconcile on retry.
            current = self._existing_job(delivery_id)
            if current is not None:
                self._require_binding(current, job)
                return current
            existing = self.store.reserve(job)
            if existing is not None:
                self._require_binding(existing, job)
                return existing
            return self._fail(
                job,
                "workflow-busy",
                _safe_message(exc),
                failure_class="transient",
            )
        except Exception as exc:  # fail closed at the durable workflow boundary
            existing = self.store.reserve(job)
            if existing is not None:
                self._require_binding(existing, job)
                return existing
            failure_class = _exception_failure_class(exc)
            if not failure_class:
                return self._fail(job, _failure_code(exc), _safe_message(exc))
            return self._fail(
                job,
                _failure_code(exc),
                _safe_message(exc),
                failure_class=failure_class,
                retry_after_seconds=_exception_retry_after_seconds(exc),
            )

    def _existing_job(self, delivery_id: str) -> DeliveryJob | None:
        try:
            return self.store.get(delivery_id)
        except FileNotFoundError:
            return None

    @staticmethod
    def _require_binding(existing: DeliveryJob, requested: DeliveryJob) -> None:
        if existing.task_id != requested.task_id:
            raise ValueError(
                f"{existing.delivery_id} is already bound to {existing.task_id}, "
                f"not {requested.task_id}"
            )
        fields = (
            ("project", existing.project_name, requested.project_name),
            ("repository root", existing.repo_root, requested.repo_root),
            ("GitHub repository", existing.expected_repository, requested.expected_repository),
        )
        for label, prior, current in fields:
            if prior != current:
                raise ValueError(
                    f"{existing.delivery_id} is already bound to another {label}"
                )
        # Schema-v1 controller records predate filesystem fingerprints. They
        # remain valid only for the same self target established by the fields
        # above; a non-zero stored identity is always enforced exactly.
        if existing.root_device and existing.root_device != requested.root_device:
            raise ValueError(
                f"{existing.delivery_id} is already bound to another root device"
            )
        if existing.root_inode and existing.root_inode != requested.root_inode:
            raise ValueError(
                f"{existing.delivery_id} is already bound to another root inode"
            )

    def _task_target_error(self, task: dict[str, Any]) -> str:
        task_project = slug(str(task.get("project", "") or ""))
        if self.external_target:
            if not task_project:
                return (
                    "External delivery requires a task explicitly assigned to the "
                    f"{self.project_name!r} project"
                )
            if task_project != self.project_name:
                return (
                    f"Task belongs to project {task_project!r}, not "
                    f"{self.project_name!r}"
                )
            task_repository = str(
                task.get("project_delivery_repository", "") or ""
            )
            task_device = task.get("project_source_device")
            task_inode = task.get("project_source_inode")
            if not task_repository or task_device is None or task_inode is None:
                return (
                    "External delivery requires a task created with a pinned "
                    "project target; create a new task for the registered project"
                )
            if task_repository != self.expected_repository:
                return "Task is pinned to a different GitHub repository"
            if (
                isinstance(task_device, bool)
                or not isinstance(task_device, int)
                or isinstance(task_inode, bool)
                or not isinstance(task_inode, int)
                or task_device != self.root_device
                or task_inode != self.root_inode
            ):
                return "Task is pinned to a different project directory identity"
            return ""
        if task_project and task_project != "mondayos":
            return (
                f"Task belongs to external project {task_project!r}; select its "
                "registered target explicitly"
            )
        return ""

    def _require_observation_binding(self, job: DeliveryJob) -> None:
        """Require the observer to be constructed for the job's pinned target."""
        if job.project_name != self.project_name:
            raise ValueError(
                "Delivery belongs to another project; select its registered target"
            )
        if job.repo_root != str(self.repository_root):
            raise ValueError("Delivery belongs to another repository root")
        if (job.root_device, job.root_inode) != (self.root_device, self.root_inode):
            raise ValueError("Delivery repository identity no longer matches its record")
        if job.expected_repository != self.expected_repository:
            raise ValueError("Delivery belongs to another GitHub repository")

    @staticmethod
    def _require_observable_pull_request(job: DeliveryJob) -> None:
        if (
            not job.success
            or job.status != "pr-open"
            or job.phase != "completed"
            or not job.pushed
        ):
            raise ValueError("Only a successful PR-backed delivery can be observed")
        if (
            not job.repository
            or not job.base_branch
            or not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", job.base_sha)
            or not job.branch
            or job.pr_number <= 0
            or not job.pr_url
            or not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", job.commit_sha)
            or not re.fullmatch(r"[0-9a-f]{64}", job.artifact_sha256)
            or job.artifact_sha256 != job.reviewed_artifact_sha256
        ):
            raise ArtifactSecurityError(
                "Delivery record is missing its exact reviewed pull-request identity"
            )

    @staticmethod
    def _observation_message(job: DeliveryJob) -> str:
        if job.pr_state == "merged":
            short = job.merge_commit_sha[:12]
            suffix = f" as merge commit {short}" if short else ""
            return f"The exact reviewed pull request was merged{suffix}."
        if job.pr_state == "closed":
            return "The pull request was closed without merging the reviewed commit."
        if job.pr_state == "diverged":
            return "The pull request head no longer matches the reviewed commit."
        if job.pr_state == "open":
            labels = {
                "pending": "GitHub checks are still pending",
                "passed": "GitHub checks passed",
                "failed": "GitHub checks failed",
                "no-checks": "the repository reports no checks",
            }
            detail = labels.get(job.checks_status, "GitHub check state is unknown")
            return f"The pull request is open; {detail}."
        return "GitHub returned an unsupported pull-request state."

    def _reconcile_observed_task(self, job: DeliveryJob) -> None:
        """Apply a verified terminal PR outcome to the MondayOS task once."""
        task_manager = TaskManager(self.controller_root)
        with task_manager.lock(job.task_id):
            task = self.monday.task("get", task_id=job.task_id)
            if not getattr(task, "success", False):
                job.observation_success = False
                job.task_reconciliation = "failed"
                job.observation_message = (
                    f"{self._observation_message(job)} The task could not be read; "
                    "retry observation."
                )
                return
            task_data = dict(getattr(task, "data", {}) or {})
            target_error = self._task_target_error(task_data)
            if target_error:
                job.task_reconciliation = "target-changed"
                job.task_reconciled_at = _now()
                job.observation_message = (
                    f"{self._observation_message(job)} The task target changed; "
                    "no task state was changed."
                )
                return
            status = str(task_data.get("status", ""))
            now = _now()
            newer = self._newer_open_delivery(job)
            if newer is not None:
                job.task_reconciliation = "superseded"
                job.task_reconciled_at = now
                job.observation_message = (
                    f"{self._observation_message(job)} A newer pull request "
                    f"({newer.delivery_id}) remains authoritative for the task."
                )
                return

            if job.pr_state == "merged":
                if status == "completed":
                    job.task_reconciliation = "already-completed"
                    job.task_reconciled_at = now
                    job.observation_message = self._observation_message(job)
                    return
                if status == "cancelled":
                    job.task_reconciliation = "terminal-unchanged"
                    job.task_reconciled_at = now
                    job.observation_message = (
                        f"{self._observation_message(job)} The task was already cancelled."
                    )
                    return
                if status not in {"in-progress", "review"}:
                    job.task_reconciliation = "state-unchanged"
                    job.task_reconciled_at = now
                    job.observation_message = (
                        f"{self._observation_message(job)} The task remains {status}; "
                        "no human-managed state was overridden."
                    )
                    return
                result = self.monday.task(
                    "complete",
                    task_id=job.task_id,
                    changed_by=f"delivery-observer:{job.delivery_id}",
                    reason=(
                        f"Exact reviewed pull request merged: {job.pr_url} "
                        f"({job.merge_commit_sha})"
                    ),
                )
                if getattr(result, "success", False):
                    job.task_reconciliation = "completed"
                    job.task_reconciled_at = now
                    job.observation_message = (
                        f"{self._observation_message(job)} The task is now complete."
                    )
                    return
            else:
                if status == "in-progress":
                    job.task_reconciliation = "already-in-progress"
                    job.task_reconciled_at = now
                    job.observation_message = self._observation_message(job)
                    return
                if status in {"completed", "cancelled"}:
                    job.task_reconciliation = "terminal-unchanged"
                    job.task_reconciled_at = now
                    job.observation_message = (
                        f"{self._observation_message(job)} The task is already {status}."
                    )
                    return
                if status != "review":
                    job.task_reconciliation = "state-unchanged"
                    job.task_reconciled_at = now
                    job.observation_message = (
                        f"{self._observation_message(job)} The task remains {status}; "
                        "no human-managed state was overridden."
                    )
                    return
                result = self.monday.task("start", task_id=job.task_id)
                result_data = dict(getattr(result, "data", {}) or {})
                if (
                    getattr(result, "success", False)
                    and result_data.get("status") == "in-progress"
                ):
                    job.task_reconciliation = "reopened"
                    job.task_reconciled_at = now
                    job.observation_message = (
                        f"{self._observation_message(job)} The task returned to IN_PROGRESS."
                    )
                    return

        job.observation_success = False
        job.task_reconciliation = "failed"
        job.observation_message = (
            f"{self._observation_message(job)} Task reconciliation failed; "
            "retry observation."
        )

    def _newer_open_delivery(self, job: DeliveryJob) -> DeliveryJob | None:
        """Return a newer open delivery that supersedes this PR outcome."""
        current_key = (job.created_at, job.delivery_id)
        for candidate in self.store.history(task_id=job.task_id, limit=0):
            if (
                candidate.delivery_id != job.delivery_id
                and candidate.repo_root == job.repo_root
                and candidate.project_name == job.project_name
                and candidate.status == "pr-open"
                and candidate.success
                and candidate.pr_state not in {"merged", "closed", "diverged"}
                and (candidate.created_at, candidate.delivery_id) > current_key
            ):
                return candidate
        return None

    def _reconcile_interrupted(
        self,
        job: DeliveryJob,
        *,
        progress_callback: ProgressCallback | None,
    ) -> DeliveryJob:
        result: DeliveryJob
        recovered_worktree: Worktree | None = None
        try:
            with self._workflow_lease() as workflow_guard_fd:
                current = self.store.get(job.delivery_id)
                self._require_binding(current, job)
                if current.terminal:
                    return current
                if not self._publication_started(current):
                    result = self._mark_interrupted(current, None)
                else:
                    try:
                        recovered_worktree = self._recover_publication_worktree(current)
                        result = self._reconcile_publication(
                            current,
                            recovered_worktree,
                            workflow_guard_fd,
                        )
                    except Exception as exc:
                        result = self._publication_pending(current, exc)
                self._notify(result, progress_callback)
                if result.success and recovered_worktree is not None:
                    # Final task transition and cleanup remain inside the same
                    # repository lease as publication recovery. An observer
                    # must never persist against a stale in-memory finalizer.
                    return self._finish_published(
                        result,
                        recovered_worktree,
                        progress_callback,
                    )
                return result
        except _WorkflowLeaseTimeoutError:
            current = self.store.get(job.delivery_id)
            self._require_binding(current, job)
            return current

    def _blocking_delivery(self, requested: DeliveryJob) -> DeliveryJob | None:
        for existing in self.store.history(task_id=requested.task_id, limit=0):
            if (
                existing.delivery_id != requested.delivery_id
                and existing.repo_root == requested.repo_root
                and existing.project_name == requested.project_name
                and not existing.terminal
            ):
                return existing
        return None

    @staticmethod
    def _publication_started(job: DeliveryJob) -> bool:
        return bool(job.commit_sha) and (
            job.pushed or job.phase in {"pushing", "pull-request", "completed"}
        )

    def _recover_publication_worktree(self, job: DeliveryJob) -> Worktree:
        if (
            not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", job.commit_sha)
            or not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", job.base_sha)
            or not job.worktree
            or not job.branch
            or job.artifact_sha256 != job.reviewed_artifact_sha256
            or not re.fullmatch(r"[0-9a-f]{64}", job.artifact_sha256)
        ):
            raise ArtifactSecurityError(
                "publication record is missing its exact reviewed artifact identity"
            )
        return self.repository.recover_worktree(
            path=Path(job.worktree),
            branch=job.branch,
            base_sha=job.base_sha,
            owner_token=job.worktree_owner_token,
        )

    def _reconcile_publication(
        self,
        job: DeliveryJob,
        worktree: Worktree,
        workflow_guard_fd: int,
    ) -> DeliveryJob:
        published = self.repository.remote_branch_is_exact(worktree, job.commit_sha)
        if not published:
            if job.pushed or job.phase == "pull-request":
                raise ArtifactSecurityError(
                    "the previously verified remote branch is no longer present"
                )
            return self._mark_interrupted(job, None)

        task_manager = TaskManager(self.controller_root)
        with task_manager.lock(job.task_id) as publication_guard_fd:
            # An exact remote branch proves that the original worker crossed
            # the irreversible publication boundary while it owned the task
            # guard.  Finish that already-authorized transition even if the
            # task was subsequently terminalized after the controller died.
            job.pushed = True
            job.status = "running"
            job.failure_code = ""
            job.failure_class = ""
            job.retry_after_seconds = 0
            self._update(
                job,
                phase="pull-request",
                message="Reconciling the exact published branch with GitHub",
            )
            current_task = self.monday.task("get", task_id=job.task_id)
            current_task_data = dict(getattr(current_task, "data", {}) or {})
            title = str(current_task_data.get("title", "Autonomous build"))
            result = self.github.ensure_pull_request(
                repo_root=self.repository_root,
                repository=job.repository,
                base=job.base_branch,
                branch=job.branch,
                title=_pr_title(job.task_id, title),
                body=_pr_body(job),
                expected_sha=job.commit_sha,
                inherited_guard_fds=(publication_guard_fd, workflow_guard_fd),
            )
            if not result.success:
                return self._publication_pending(
                    job,
                    RuntimeError(result.message or "GitHub pull request state is unverified"),
                    failure_class=getattr(result, "failure_class", ""),
                    retry_after_seconds=getattr(result, "retry_after_seconds", 0),
                )
            return self._record_pr_success(job, result.url, result.number)

    def _mark_interrupted(
        self,
        job: DeliveryJob,
        progress_callback: ProgressCallback | None,
    ) -> DeliveryJob:
        job.status = "interrupted"
        job.phase = "interrupted"
        job.success = False
        job.failure_code = "interrupted-run"
        job.failure_class = "permanent"
        job.retry_after_seconds = 0
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
        workflow_guard_fd: int,
    ) -> DeliveryJob:
        self._update(job, phase="preparing", message="Preparing an isolated worktree")
        self._notify(job, progress_callback)
        self.repository.validate_trusted_root()
        repository_slug = self.repository.github_slug()
        if (
            self.expected_repository
            and repository_slug.casefold() != self.expected_repository.casefold()
        ):
            raise GitValidationError(
                "registered GitHub repository does not match the target origin"
            )
        base = self.repository.resolve_base()
        worktree = self.repository.create_worktree(job.task_id, base)
        job.base_ref = base.ref
        job.base_branch = base.branch
        job.base_sha = base.sha
        job.repository = repository_slug
        job.branch = worktree.branch
        job.worktree = str(worktree.path)
        job.worktree_owner_token = worktree.owner_token
        self.store.persist(job)

        started = self.monday.task("start", task_id=job.task_id)
        if not getattr(started, "success", False):
            raise RuntimeError(getattr(started, "message", "Could not start task"))

        feedback = ""
        builder_index = 0
        patch_attempts = 0
        pending_provider_failures: list[dict[str, Any]] = []
        all_provider_failures: list[dict[str, Any]] = []
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
                        raise _BuilderProviderError(
                            reason,
                            failure_class=getattr(
                                candidate_proposal,
                                "failure_class",
                                "",
                            ),
                            retry_after_seconds=getattr(
                                candidate_proposal,
                                "retry_after_seconds",
                                0,
                            ),
                        )
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
                failure_class, retry_after_seconds = _provider_failure_outcome(
                    all_provider_failures
                )
                return self._fail(
                    job,
                    "builders-failed",
                    f"All available coding providers failed; nothing was pushed. {detail}",
                    failure_class=failure_class,
                    retry_after_seconds=retry_after_seconds,
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
                source_root=self.controller_root,
                repository_root=self.repository_root,
                mondayos_tests=not self.external_target,
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
                project_name=job.project_name,
                repository=job.repository,
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
                project_name=job.project_name,
                repository=job.repository,
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
                base,
                worktree,
                staged_files,
                final_diff_sha,
                progress_callback,
                workflow_guard_fd,
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
        base: BaseRevision,
        worktree: Worktree,
        changed_files: list[str],
        expected_diff_sha256: str,
        progress_callback: ProgressCallback | None,
        workflow_guard_fd: int,
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

        # A task can be completed or cancelled while models and validation run.
        # Serialize the last persisted-state check with the irreversible push/PR
        # transition using the same task lock as every TaskManager mutation. If
        # terminalization wins the lock, delivery stops before publishing; if
        # delivery wins, the complete push/PR transition is one linearized unit.
        self._update(
            job,
            phase="publication-ready",
            message="Approved artifact is ready for the final task check and publication",
        )
        self._notify(job, progress_callback)
        task_manager = TaskManager(self.controller_root)
        with task_manager.lock(job.task_id) as publication_guard_fd:
            current_task = self.monday.task("get", task_id=job.task_id)
            current_task_data = dict(getattr(current_task, "data", {}) or {})
            if not getattr(current_task, "success", False):
                return self._fail(
                    job,
                    "task-not-found",
                    getattr(current_task, "message", "Task is unavailable"),
                )
            if current_task_data.get("status") in {"completed", "cancelled"}:
                return self._fail(
                    job,
                    "terminal-task",
                    "Task became completed or cancelled before publication; nothing was pushed",
                )
            target_error = self._task_target_error(current_task_data)
            if target_error:
                return self._fail(job, "project-mismatch", target_error)

            self._update(job, phase="pushing", message="Pushing the approved branch")
            inherited_guard_fds = (publication_guard_fd, workflow_guard_fd)
            pushed_sha = self.repository.push(
                worktree,
                commit,
                expected_base=base,
                inherited_guard_fds=inherited_guard_fds,
            )
            if pushed_sha != commit:
                raise ArtifactSecurityError("pushed SHA does not match the approved commit")
            job.pushed = True
            self.store.persist(job)

            self._update(job, phase="pull-request", message="Opening the GitHub pull request")
            result = self.github.ensure_pull_request(
                repo_root=self.repository_root,
                repository=job.repository,
                base=job.base_branch,
                branch=job.branch,
                title=_pr_title(
                    job.task_id,
                    str(current_task_data.get("title", task_data.get("title", "Autonomous build"))),
                ),
                body=_pr_body(job),
                expected_sha=commit,
                inherited_guard_fds=inherited_guard_fds,
            )
            if not result.success:
                pending = self._publication_pending(
                    job,
                    RuntimeError(result.message or "GitHub pull request state is unverified"),
                    failure_class=getattr(result, "failure_class", ""),
                    retry_after_seconds=getattr(result, "retry_after_seconds", 0),
                )
            else:
                pending = None
                self._record_pr_success(job, result.url, result.number)

        self._notify(job, progress_callback)
        if pending is not None:
            return pending
        return self._finish_published(job, worktree, progress_callback)

    def _record_pr_success(self, job: DeliveryJob, url: str, number: int) -> DeliveryJob:
        job.pr_url = url
        job.pr_number = number
        job.status = "pr-open"
        job.phase = "completed"
        job.success = True
        job.failure_code = ""
        job.failure_class = ""
        job.retry_after_seconds = 0
        job.message = f"ChatGPT approved the exact artifact and PR #{number} is open"
        job.updated_at = _now()
        self.store.persist(job)
        return job

    def _publication_pending(
        self,
        job: DeliveryJob,
        exc: BaseException,
        *,
        failure_class: str = "",
        retry_after_seconds: int = 0,
    ) -> DeliveryJob:
        job.status = "publication-pending"
        if job.phase == "completed":
            job.phase = "pull-request"
        job.success = False
        job.failure_code = ""
        classified = normalize_delivery_failure_class(failure_class)
        if not classified:
            classified = _exception_failure_class(exc) or "permanent"
        job.failure_class = classified
        job.retry_after_seconds = _classified_retry_after_seconds(
            retry_after_seconds or _exception_retry_after_seconds(exc),
            failure_class=classified,
        )
        job.message = _safe_text(
            (
                "Publication may have crossed an irreversible boundary and could not "
                f"be verified. Retry this exact delivery identity. {_safe_message(exc)}"
            ),
            2_000,
        )
        job.updated_at = _now()
        self.store.persist(job)
        return job

    def _finish_published(
        self,
        job: DeliveryJob,
        worktree: Worktree,
        progress_callback: ProgressCallback | None,
    ) -> DeliveryJob:
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
            try:
                self.store.persist(job)
            except Exception:
                # The exact PR success record is already durable.  A secondary
                # annotation must never downgrade or overwrite that outcome.
                pass
        self._notify(job, progress_callback)
        try:
            self.repository.cleanup(worktree, delete_branch=False)
        except Exception:
            # The published commit/PR is authoritative.  A retained owned
            # worktree is recoverable local diagnostic state, not a failed PR.
            job.message += "; the local worktree was retained for cleanup"
            try:
                self.store.persist(job)
            except Exception:
                pass
        return job

    def _select_builders(
        self,
    ) -> tuple[list[PatchBuilder], list[str], str, int]:
        available: list[PatchBuilder] = []
        reasons: list[str] = []
        unavailable: list[dict[str, Any]] = []
        for builder in self.builders:
            availability = self._availability(builder)
            if availability["available"]:
                available.append(builder)
            else:
                reasons.append(f"{availability['tool']}: {availability['reason']}")
                unavailable.append(availability)
        if available:
            return available, reasons, "", 0
        if any(item["failure_class"] == "policy" for item in unavailable):
            return available, reasons, "policy", 0
        if unavailable and all(
            item["failure_class"] == "transient" for item in unavailable
        ):
            retry_after_seconds = max(
                int(item["retry_after_seconds"]) for item in unavailable
            )
            return available, reasons, "transient", retry_after_seconds
        return available, reasons, "permanent", 0

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
        failure_class = _exception_failure_class(last_error)
        error = RuntimeError(
            f"Independent reviewer failed twice: {_safe_message(last_error)}"
        )
        error.failure_class = failure_class  # type: ignore[attr-defined]
        error.retry_after_seconds = (  # type: ignore[attr-defined]
            _exception_retry_after_seconds(last_error)
        )
        raise error from last_error

    def _availability(self, tool: Any) -> dict[str, Any]:
        try:
            result: ToolAvailability = tool.availability()
            failure_class = ""
            retry_after_seconds = 0
            if not bool(result.available):
                failure_class = normalize_delivery_failure_class(
                    getattr(result, "failure_class", "")
                )
                retry_after_seconds = _classified_retry_after_seconds(
                    getattr(result, "retry_after_seconds", 0),
                    failure_class=failure_class,
                )
            return {
                "available": bool(result.available),
                "tool": _safe_text(result.tool, 200),
                "reason": _safe_text(result.reason, 2_000),
                "failure_class": failure_class,
                "retry_after_seconds": retry_after_seconds,
            }
        except Exception as exc:
            failure_class = _exception_failure_class(exc)
            return {
                "available": False,
                "tool": _safe_text(getattr(tool, "name", type(tool).__name__), 200),
                "reason": _safe_message(exc),
                "failure_class": failure_class,
                "retry_after_seconds": _classified_retry_after_seconds(
                    _exception_retry_after_seconds(exc),
                    failure_class=failure_class,
                ),
            }

    def _repository_readiness(self) -> dict[str, Any]:
        try:
            self.repository.validate_trusted_root()
            repository = self.repository.github_slug()
            if (
                self.expected_repository
                and repository.casefold() != self.expected_repository.casefold()
            ):
                raise GitValidationError(
                    "registered GitHub repository does not match the target origin"
                )
            return {
                "available": True,
                "tool": "repository",
                "reason": f"trusted GitHub repository {repository}",
                "repository": repository,
            }
        except Exception as exc:
            failure_class = _exception_failure_class(exc)
            return {
                "available": False,
                "tool": "repository",
                "reason": _safe_message(exc),
                "failure_class": failure_class,
                "retry_after_seconds": _classified_retry_after_seconds(
                    _exception_retry_after_seconds(exc),
                    failure_class=failure_class,
                ),
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
        python = self.controller_root / ".venv" / "bin" / "python"
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
            failure_class = _exception_failure_class(exc)
            return {
                "available": False,
                "tool": "validation",
                "reason": f"validation environment check failed: {_safe_message(exc)}",
                "failure_class": failure_class,
                "retry_after_seconds": _classified_retry_after_seconds(
                    _exception_retry_after_seconds(exc),
                    failure_class=failure_class,
                ),
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

    def _fail(
        self,
        job: DeliveryJob,
        code: str,
        message: str,
        *,
        failure_class: str | None = None,
        retry_after_seconds: int = 0,
    ) -> DeliveryJob:
        job.status = "failed"
        job.phase = "failed"
        job.success = False
        job.failure_code = code
        classified = (
            _failure_class_for_code(code)
            if failure_class is None
            else normalize_delivery_failure_class(failure_class) or "permanent"
        )
        job.failure_class = classified
        job.retry_after_seconds = _classified_retry_after_seconds(
            retry_after_seconds,
            failure_class=classified,
        )
        job.message = _safe_text(message or code, 2_000)
        job.updated_at = _now()
        self.store.persist(job)
        return job

    def _reject(self, job: DeliveryJob, message: str) -> DeliveryJob:
        job.status = "rejected"
        job.phase = "review-blocked"
        job.success = False
        job.failure_code = "review-blocked"
        job.failure_class = "policy"
        job.retry_after_seconds = 0
        job.message = _safe_text(message, 2_000)
        job.updated_at = _now()
        self.store.persist(job)
        return job

    @contextmanager
    def _observation_lease(self, delivery_id: str) -> Iterator[None]:
        """Serialize read/reconcile cycles for one durable delivery identity."""
        if not re.fullmatch(r"delivery-[A-Za-z0-9-]{1,80}", delivery_id):
            raise ValueError("Invalid delivery identity")
        path = self.store.root / f".{delivery_id}.observe.lock"
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "a+", encoding="utf-8") as handle:
            metadata = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or metadata.st_nlink != 1
                or metadata.st_mode & 0o077
            ):
                raise ValueError("delivery observation lock is not controller-owned")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def _workflow_lease(self) -> Iterator[int]:
        lock_dir = self.store.root / "workflow-locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(os.fsencode(self.repository_root)).hexdigest()
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
            yield descriptor
        finally:
            # Do not issue LOCK_UN: an irreversible publication child may have
            # inherited this same open file description. Closing our copy keeps
            # the repository lease held until the final inherited descriptor
            # closes, including during graceful controller unwinding.
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
    return "\n".join(lines)


def _paths_overlap(first: Path, second: Path) -> bool:
    """Return whether either canonical directory contains the other."""
    return first == second or first in second.parents or second in first.parents


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
    provider_failures: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = attempt.to_dict()
    if provider_failures:
        payload["provider_failures"] = [dict(item) for item in provider_failures]
    return payload


def _provider_failure(builder: str, exc: BaseException) -> dict[str, Any]:
    failure_class = _exception_failure_class(exc)
    payload: dict[str, Any] = {
        "builder": _safe_text(builder, 200),
        "message": _safe_message(exc),
    }
    if failure_class:
        payload["failure_class"] = failure_class
        payload["retry_after_seconds"] = _classified_retry_after_seconds(
            _exception_retry_after_seconds(exc),
            failure_class=failure_class,
        )
    return payload


def _provider_failure_summary(failures: list[dict[str, Any]]) -> str:
    if not failures:
        return "No provider diagnostic was returned."
    return _safe_text(
        "; ".join(
            f"{item.get('builder', 'builder')}: {item.get('message', 'failed')}"
            for item in failures
        ),
        2_000,
    )


def _provider_failure_outcome(
    failures: list[dict[str, Any]],
) -> tuple[str, int]:
    """Retry only when every exhausted provider reported a typed outage."""
    if any(item.get("failure_class") == "policy" for item in failures):
        return "policy", 0
    if failures and all(item.get("failure_class") == "transient" for item in failures):
        retry_after_seconds = max(
            bounded_delivery_retry_after_seconds(
                item.get("retry_after_seconds"),
                failure_class="transient",
            )
            for item in failures
        )
        return (
            "transient",
            retry_after_seconds,
        )
    return "permanent", 0


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


def _validated_check_counts(value: Any, aggregate: str) -> dict[str, int] | None:
    """Validate one bounded aggregate without retaining check names or logs."""
    required = {"total", "passed", "failed", "pending"}
    if not isinstance(value, dict) or set(value) != required:
        return None
    if any(
        isinstance(value[key], bool)
        or not isinstance(value[key], int)
        or not 0 <= value[key] <= 500
        for key in required
    ):
        return None
    counts = {key: int(value[key]) for key in required}
    if counts["total"] != (
        counts["passed"] + counts["failed"] + counts["pending"]
    ):
        return None
    consistent = {
        "no-checks": counts["total"] == 0,
        "passed": counts["total"] > 0 and counts["passed"] == counts["total"],
        "pending": counts["pending"] > 0 and counts["failed"] == 0,
        "failed": counts["failed"] > 0,
    }
    if not consistent.get(aggregate, False):
        return None
    return counts


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
        f"- Project: `{job.project_name or 'mondayos'}`\n"
        f"- Repository: `{job.repository}`\n"
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
    return str(_redact(text))[:limit]


def _failure_class_for_code(code: str) -> str:
    if code in {
        "artifact-security",
        "git-validation",
        "project-mismatch",
        "review-blocked",
        "terminal-task",
        "unresolved-delivery",
    }:
        return "policy"
    return "permanent"


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(seen) < 8:
        seen.add(id(current))
        yield current
        next_exception = current.__cause__
        if next_exception is None and not current.__suppress_context__:
            next_exception = current.__context__
        current = next_exception


def _exception_failure_class(exc: BaseException) -> str:
    """Classify only explicit markers and typed controller exceptions.

    Exception messages are intentionally ignored: model, CLI, and remote-tool
    prose is untrusted and cannot authorize an automatic retry.
    """
    classified = ""
    for current in _exception_chain(exc):
        explicit = normalize_delivery_failure_class(
            getattr(current, "failure_class", "")
        )
        if explicit == "policy" or isinstance(
            current,
            (ArtifactSecurityError, GitValidationError),
        ):
            return "policy"
        if explicit == "permanent":
            classified = "permanent"
            continue
        if explicit == "transient" and not classified:
            classified = "transient"
        if isinstance(
            current,
            (subprocess.TimeoutExpired, TimeoutError, ConnectionError),
        ) and not classified:
            classified = "transient"
        if (
            isinstance(current, OSError)
            and current.errno in _TRANSIENT_ERRNOS
            and not classified
        ):
            classified = "transient"
    return classified


def _exception_retry_after_seconds(exc: BaseException) -> int:
    if _exception_failure_class(exc) != "transient":
        return 0
    for current in _exception_chain(exc):
        retry_after_seconds = bounded_delivery_retry_after_seconds(
            getattr(current, "retry_after_seconds", 0),
            failure_class="transient",
        )
        if retry_after_seconds:
            return retry_after_seconds
    return 0


def _classified_retry_after_seconds(
    value: object,
    *,
    failure_class: str,
) -> int:
    if failure_class != "transient":
        return 0
    return bounded_delivery_retry_after_seconds(
        value,
        failure_class="transient",
    )


def _failure_code(exc: BaseException) -> str:
    name = type(exc).__name__.replace("Error", "").replace("Exception", "")
    slug = re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()
    return slug or "delivery-failed"
