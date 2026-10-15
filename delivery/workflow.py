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
from delivery.git import (
    BaseRevision,
    ChangedPath,
    GitCommandError,
    GitRepository,
    GitValidationError,
    Worktree,
)
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
_MAX_PREDECESSOR_DEPTH = 10
_DELIVERY_ID = re.compile(r"^delivery-[A-Za-z0-9-]{1,80}$")
_FINAL_TASK_FAILURE_CODES = frozenset(
    {"project-mismatch", "task-changed", "terminal-task"}
)


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

        if job.pr_state == "open" and job.review_reconciliation == "review-pending":
            try:
                self._reconcile_open_pr_review(job)
            except Exception:
                # GitHub observation succeeded.  The independent task repair is
                # retryable and must not erase that exact remote state.
                job.review_reconciliation = "review-pending"
                job.review_reconciled_at = ""
                self._set_review_reconciliation_message(job)
            if job.review_reconciliation == "review-pending":
                job.observation_success = False
                job.observation_message = (
                    f"{self._observation_message(job)} Task reconciliation is "
                    "temporarily unavailable; retry observation."
                )
            job.updated_at = _now()
            self.store.persist(job)
        elif job.pr_state in {"merged", "closed", "diverged"}:
            if job.review_reconciliation == "review-pending":
                # Terminal GitHub truth supersedes the earlier desire to place
                # an open pull request in REVIEW.  Never move the task to
                # REVIEW immediately before completing or reopening it.
                job.review_reconciliation = "terminal-unchanged"
                job.review_reconciled_at = _now()
            try:
                self._reconcile_observed_task(job)
            except Exception:
                # The exact GitHub state remains useful, but task-store
                # unavailability is retryable and must not crash the observer.
                job.task_reconciliation = "failed"
                job.task_reconciled_at = ""
                job.observation_message = (
                    f"{self._observation_message(job)} Task reconciliation is "
                    "temporarily unavailable; retry task reconciliation."
                )
            job.updated_at = _now()
            self.store.persist(job)
        return job

    def run(
        self,
        *,
        task_id: str,
        delivery_id: str | None = None,
        predecessor_delivery_id: str | None = None,
        retry_root_delivery_id: str | None = None,
        retry_attempt: int = 0,
        progress_callback: ProgressCallback | None = None,
        checkpoint_callback: CheckpointCallback | None = None,
    ) -> DeliveryJob:
        """Run or idempotently retrieve one delivery identity."""
        delivery_id = delivery_id or f"delivery-{uuid.uuid4().hex[:16]}"
        if not _DELIVERY_ID.fullmatch(delivery_id):
            raise ValueError("Invalid delivery identity")
        (
            predecessor_delivery_id,
            retry_root_delivery_id,
            retry_attempt,
        ) = _validated_retry_identity(
            delivery_id=delivery_id,
            predecessor_delivery_id=predecessor_delivery_id,
            retry_root_delivery_id=retry_root_delivery_id,
            retry_attempt=retry_attempt,
        )
        now = _now()
        job = DeliveryJob(
            delivery_id=delivery_id,
            task_id=task_id,
            objective="",
            repo_root=str(self.repository_root),
            project_name=self.project_name,
            expected_repository=self.expected_repository,
            root_device=self.root_device,
            root_inode=self.root_inode,
            max_attempts=self.max_attempts,
            created_at=now,
            updated_at=now,
            predecessor_delivery_id=predecessor_delivery_id,
            retry_root_delivery_id=retry_root_delivery_id,
            retry_attempt=retry_attempt,
            deferred_commit_authorization=True,
        )
        existing = self._existing_job(delivery_id)
        if existing is not None:
            self._require_binding(existing, job)
            if existing.terminal:
                if existing.review_reconciliation == "review-pending":
                    return self._repair_open_pr_review(
                        existing,
                        requested=job,
                        progress_callback=progress_callback,
                    )
                return existing
            return self._reconcile_interrupted(
                existing,
                progress_callback=progress_callback,
            )

        # Live task content authorizes only a genuinely new delivery. Exact-ID
        # recovery is bound to its durable reviewed artifact and must remain
        # reachable even if the task is later edited after publication.
        task_response = self.monday.task("get", task_id=task_id)
        task_data = dict(getattr(task_response, "data", {}) or {})
        raw_objective = _objective(task_id, task_data)
        if _redact(raw_objective) != raw_objective:
            raise ArtifactSecurityError(
                "task title, objective, or acceptance criteria contains credential-like material"
            )
        if len(raw_objective) > 50_000:
            raise ArtifactSecurityError(
                "canonical task content exceeds the 50,000-character delivery limit"
            )
        job.objective = raw_objective

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
                    if job.predecessor_delivery_id:
                        try:
                            asserted_predecessor = self.store.get(
                                job.predecessor_delivery_id
                            )
                        except (FileNotFoundError, OSError, ValueError) as exc:
                            if _exception_failure_class(exc) == "transient":
                                raise
                            raise ArtifactSecurityError(
                                "delivery retry predecessor is unavailable"
                            ) from exc
                        self._require_retry_predecessor_binding(
                            asserted_predecessor,
                            job,
                        )
                    required_predecessor = self._required_retry_predecessor(job)
                    if required_predecessor != job.predecessor_delivery_id:
                        raise ArtifactSecurityError(
                            "delivery retry predecessor does not match durable history"
                        )
                    if required_predecessor:
                        self._require_retry_predecessor_chain(job)
                    existing = self.store.reserve(job)
                    if existing is not None:
                        self._require_binding(existing, job)
                        if existing.terminal:
                            if existing.review_reconciliation == "review-pending":
                                self._reconcile_open_pr_review(existing)
                                self._notify(existing, progress_callback)
                            return existing
                        return self._reconcile_interrupted_locked(
                            existing,
                            workflow_guard_fd=workflow_guard_fd,
                            progress_callback=progress_callback,
                        )
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
                    task_status = str(task_data.get("status") or "")
                    if task_status in {"completed", "cancelled"}:
                        return self._fail(
                            job,
                            "terminal-task",
                            "Completed or cancelled tasks cannot run",
                        )
                    if task_status not in {"backlog", "assigned", "in-progress"}:
                        return self._fail(
                            job,
                            "task-changed",
                            "Only backlog, assigned, or in-progress tasks can start a build",
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
            (
                "delivery predecessor",
                existing.predecessor_delivery_id,
                requested.predecessor_delivery_id,
            ),
            (
                "retry root",
                existing.retry_root_delivery_id,
                requested.retry_root_delivery_id,
            ),
            (
                "retry attempt",
                existing.retry_attempt,
                requested.retry_attempt,
            ),
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
                job.task_reconciliation = "failed"
                job.task_reconciled_at = ""
                job.observation_message = (
                    f"{self._observation_message(job)} The task could not be read; "
                    "retry task reconciliation."
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

        job.task_reconciliation = "failed"
        job.task_reconciled_at = ""
        job.observation_message = (
            f"{self._observation_message(job)} Task reconciliation failed; "
            "retry task reconciliation."
        )

    def _newer_open_delivery(self, job: DeliveryJob) -> DeliveryJob | None:
        """Return a newer open delivery that supersedes this PR outcome."""
        current_key = (job.created_at, job.delivery_id)
        for candidate in self.store.history(task_id=job.task_id, limit=0):
            if (
                candidate.delivery_id != job.delivery_id
                and candidate.repo_root == job.repo_root
                and candidate.project_name == job.project_name
                and candidate.expected_repository.casefold()
                == job.expected_repository.casefold()
                and candidate.repository.casefold() == job.repository.casefold()
                and candidate.root_device == job.root_device
                and candidate.root_inode == job.root_inode
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
        try:
            with self._workflow_lease() as workflow_guard_fd:
                return self._reconcile_interrupted_locked(
                    job,
                    workflow_guard_fd=workflow_guard_fd,
                    progress_callback=progress_callback,
                )
        except _WorkflowLeaseTimeoutError:
            current = self.store.get(job.delivery_id)
            self._require_binding(current, job)
            return current

    def _reconcile_interrupted_locked(
        self,
        job: DeliveryJob,
        *,
        workflow_guard_fd: int,
        progress_callback: ProgressCallback | None,
    ) -> DeliveryJob:
        """Reconcile one possibly abandoned identity while holding the workflow lease."""
        recovered_worktree: Worktree | None = None
        current = self.store.get(job.delivery_id)
        self._require_binding(current, job)
        if current.terminal:
            return current
        if not self._publication_started(current):
            result = self._mark_interrupted(
                current,
                None,
                retry_safe=self._prepublication_interruption_is_retryable(current),
            )
        else:
            try:
                recovered_worktree = self._recover_publication_worktree(current)
                result = self._reconcile_publication(
                    current,
                    recovered_worktree,
                    workflow_guard_fd,
                    progress_callback,
                )
            except Exception as exc:
                result = self._publication_pending(current, exc)
        self._notify(result, progress_callback)
        if result.success and recovered_worktree is not None:
            # Final task transition and cleanup remain inside the same
            # repository lease as publication recovery. An observer must never
            # persist against a stale in-memory finalizer.
            return self._finish_published(
                result,
                recovered_worktree,
                progress_callback,
            )
        return result

    def _repair_open_pr_review(
        self,
        job: DeliveryJob,
        *,
        requested: DeliveryJob,
        progress_callback: ProgressCallback | None,
    ) -> DeliveryJob:
        """Repair only an explicitly pending PR-open task transition."""
        try:
            with self._workflow_lease():
                current = self.store.get(job.delivery_id)
                self._require_binding(current, requested)
                if current.review_reconciliation == "review-pending":
                    self._reconcile_open_pr_review(current)
                    self._notify(current, progress_callback)
                return current
        except _WorkflowLeaseTimeoutError:
            current = self.store.get(job.delivery_id)
            self._require_binding(current, requested)
            return current

    def _blocking_delivery(self, requested: DeliveryJob) -> DeliveryJob | None:
        for existing in self._matching_deliveries(requested):
            if not existing.terminal or self._terminal_delivery_blocks_successor(
                existing
            ):
                return existing
        return None

    def _matching_deliveries(self, requested: DeliveryJob) -> list[DeliveryJob]:
        return [
            existing
            for existing in self.store.history(task_id=requested.task_id, limit=0)
            if existing.delivery_id != requested.delivery_id
            and existing.repo_root == requested.repo_root
            and existing.project_name == requested.project_name
        ]

    @staticmethod
    def _terminal_delivery_blocks_successor(job: DeliveryJob) -> bool:
        if _resolved_pull_request(job):
            return False
        live_pr = (
            job.status == "pr-open"
            and job.success
            and job.pr_state not in {"merged", "closed", "diverged"}
        )
        unproven_interruption = (
            job.status == "interrupted" and job.failure_class != "transient"
        )
        unresolved_publication = _publication_evidence(job) and not (
            _prepared_commit_failure_shape(job) or _retry_source_shape(job)
        )
        return (
            live_pr
            or unproven_interruption
            or unresolved_publication
        )

    def _required_retry_predecessor(self, requested: DeliveryJob) -> str:
        """Derive the only permissible retry source from durable target history."""
        records = self._matching_deliveries(requested)
        children: set[str] = set()
        retry_sources: list[DeliveryJob] = []
        terminal_retry_children: list[DeliveryJob] = []
        prepared_terminal_failures: list[DeliveryJob] = []
        for record in records:
            if not _stored_retry_identity_is_valid(record):
                raise ArtifactSecurityError("delivery retry lineage is malformed")
            if (
                record.status == "pr-open"
                and record.success
                and record.pr_state not in {"merged", "closed", "diverged"}
            ):
                raise ArtifactSecurityError(
                    "an open delivery already exists for this task and repository"
                )
            if record.status == "interrupted":
                if not _retry_source_shape(record):
                    raise ArtifactSecurityError(
                        "an interrupted delivery still requires reconciliation"
                    )
                retry_sources.append(record)
            elif _retry_source_shape(record):
                retry_sources.append(record)
            elif _retry_terminal_child_shape(record):
                terminal_retry_children.append(record)
            elif _prepared_commit_failure_shape(record):
                prepared_terminal_failures.append(record)
            elif _resolved_pull_request(record):
                continue
            elif _publication_evidence(record):
                raise ArtifactSecurityError(
                    "a delivery has unresolved publication evidence"
                )

        for source in retry_sources:
            if not self._retry_predecessor_is_safe(source):
                raise ArtifactSecurityError(
                    "a delivery retry source is no longer proven unpublished"
                )
            if source.retry_attempt:
                # A predecessor is consumed only by a successor that is itself
                # still an authorized retry source. A permanent or otherwise
                # unsafe terminal child cannot erase the safe lineage leaf and
                # thereby authorize an unrelated fresh delivery.
                children.add(source.predecessor_delivery_id)
        retry_sources_by_id = {
            source.delivery_id: source for source in retry_sources
        }
        for failure in prepared_terminal_failures:
            if not self._stopped_delivery_is_safe(failure):
                raise ArtifactSecurityError(
                    "a prepared delivery failure is no longer unpublished"
                )
        for child in terminal_retry_children:
            predecessor = retry_sources_by_id.get(child.predecessor_delivery_id)
            if predecessor is None:
                raise ArtifactSecurityError(
                    "a terminal retry child has no safe predecessor"
                )
            self._require_retry_predecessor_binding(predecessor, child)
            self._require_retry_predecessor_chain(child)
            if not self._stopped_delivery_is_safe(child):
                raise ArtifactSecurityError(
                    "a terminal retry child is not proven unpublished"
                )
            children.add(child.predecessor_delivery_id)
        leaves = [source for source in retry_sources if source.delivery_id not in children]
        if len(leaves) > 1:
            raise ArtifactSecurityError(
                "multiple delivery retry sources require operator reconciliation"
            )
        return leaves[0].delivery_id if leaves else ""

    def _stopped_delivery_is_safe(self, job: DeliveryJob) -> bool:
        """Prove that a terminal prepublication delivery stayed unpublished."""
        if not (
            _prepared_commit_failure_shape(job)
            or _retry_terminal_child_shape(job)
        ):
            return False
        worktree_state = (
            job.base_sha,
            job.repository,
            job.branch,
            job.worktree,
            job.worktree_owner_token,
        )
        if not any(worktree_state):
            return not job.prepared_commit_sha
        if (
            not all(worktree_state)
            or re.fullmatch(
                r"[0-9a-f]{40}(?:[0-9a-f]{24})?",
                job.base_sha,
            )
            is None
            or re.fullmatch(r"[0-9a-f]{64}", job.worktree_owner_token) is None
        ):
            return False
        try:
            if self.repository.github_slug().casefold() != job.repository.casefold():
                return False
            worktree = self.repository.recover_worktree(
                path=Path(job.worktree),
                branch=job.branch,
                base_sha=job.base_sha,
                owner_token=job.worktree_owner_token,
            )
            return self.repository.remote_branch_is_absent(
                worktree,
                expected_local_sha=job.prepared_commit_sha or job.base_sha,
            )
        except Exception as exc:
            # An unavailable proof service is not evidence that a branch is
            # absent, so publication still stops. Preserve the typed transient
            # error, however, so the canonical retry child becomes the next
            # durable lineage leaf instead of permanently consuming its ID as
            # a policy refusal. Definitive false results and nontransient proof
            # failures remain unsafe.
            if _exception_failure_class(exc) == "transient":
                raise
            return False

    def _require_retry_predecessor_chain(self, successor: DeliveryJob) -> None:
        """Re-prove every predecessor is still unpublished before retry work."""
        if not successor.predecessor_delivery_id:
            return
        chain = _expected_retry_predecessors(successor)
        lineage_ids = {successor.delivery_id, *chain}
        for attempt, predecessor_id in enumerate(chain, start=1):
            try:
                predecessor = self.store.get(predecessor_id)
            except (FileNotFoundError, OSError, ValueError) as exc:
                if _exception_failure_class(exc) == "transient":
                    raise
                raise ArtifactSecurityError(
                    "delivery retry predecessor is unavailable"
                ) from exc
            self._require_retry_predecessor_binding(predecessor, successor)
            expected_predecessor = (
                ""
                if attempt == 1
                else chain[attempt - 2]
            )
            if attempt == 1:
                identity_valid = (
                    predecessor.predecessor_delivery_id == ""
                    and predecessor.retry_root_delivery_id == ""
                    and predecessor.retry_attempt == 0
                )
            else:
                identity_valid = (
                    predecessor.predecessor_delivery_id == expected_predecessor
                    and predecessor.retry_root_delivery_id
                    == successor.retry_root_delivery_id
                    and predecessor.retry_attempt == attempt
                )
            if not identity_valid:
                raise ArtifactSecurityError(
                    "delivery retry predecessor lineage changed"
                )
            if not self._retry_predecessor_is_safe(predecessor):
                raise ArtifactSecurityError(
                    "delivery retry predecessor is no longer proven unpublished"
                )
        if any(
            candidate.delivery_id not in lineage_ids
            and candidate.status == "pr-open"
            and candidate.success
            and candidate.pr_state not in {"merged", "closed", "diverged"}
            for candidate in self._matching_deliveries(successor)
        ):
            raise ArtifactSecurityError(
                "another open delivery supersedes this retry"
            )

    @staticmethod
    def _require_retry_predecessor_binding(
        predecessor: DeliveryJob,
        successor: DeliveryJob,
    ) -> None:
        if (
            predecessor.delivery_id == successor.delivery_id
            or predecessor.task_id != successor.task_id
            or predecessor.project_name != successor.project_name
            or predecessor.repo_root != successor.repo_root
            or predecessor.expected_repository != successor.expected_repository
            or predecessor.root_device != successor.root_device
            or predecessor.root_inode != successor.root_inode
            or (
                predecessor.repository
                and successor.repository
                and predecessor.repository.casefold()
                != successor.repository.casefold()
            )
        ):
            raise ArtifactSecurityError(
                "delivery retry predecessor changed task or repository identity"
            )

    def _retry_predecessor_is_safe(self, job: DeliveryJob) -> bool:
        """Return true only while a retry source remains provably unpublished."""
        if (
            not job.terminal
            or job.success
            or job.failure_class != "transient"
            or job.pushed
            or bool(job.pr_url)
            or job.pr_number
            or bool(job.review_reconciliation)
            or bool(job.review_reconciled_at)
            or bool(job.merge_commit_sha)
            or job.pr_state != "unobserved"
            or job.checks_status != "unobserved"
            or bool(job.check_counts)
            or bool(job.pr_observed_at)
            or job.observation_success
            or bool(job.observation_message)
            or bool(job.task_reconciliation)
            or bool(job.task_reconciled_at)
        ):
            return False
        ordinary_transient = (
            job.status == "failed"
            and job.phase == "failed"
            and not job.commit_sha
            and not job.prepared_commit_sha
            and not job.interrupted_from_phase
            and not job.interruption_proof_sha256
        )
        prepared_transient = (
            job.failure_class == "transient"
            and _prepared_commit_failure_shape(job)
        )
        interruption_phase = job.interrupted_from_phase
        proven_interruption = (
            job.status == "interrupted"
            and job.phase == "interrupted"
            and job.failure_code == "interrupted-run"
            and job.retry_after_seconds == 0
            and interruption_phase
            in {
                "implementing",
                "validating",
                "reviewing",
                "committing",
                "publication-ready",
                "pushing",
            }
            and re.fullmatch(r"[0-9a-f]{64}", job.interruption_proof_sha256)
            is not None
            and job.interruption_proof_sha256
            == _interruption_proof(job, interruption_phase)
            and (
                not job.commit_sha
                or re.fullmatch(
                    r"[0-9a-f]{40}(?:[0-9a-f]{24})?",
                    job.commit_sha,
                )
                is not None
            )
        )
        if not (ordinary_transient or prepared_transient or proven_interruption):
            return False

        if proven_interruption:
            if interruption_phase == "pushing":
                if (
                    not job.deferred_commit_authorization
                    or job.commit_sha
                    or (
                        job.prepared_commit_sha
                        and re.fullmatch(
                            r"[0-9a-f]{40}(?:[0-9a-f]{24})?",
                            job.prepared_commit_sha,
                        )
                        is None
                    )
                    or job.artifact_sha256 != job.reviewed_artifact_sha256
                    or re.fullmatch(r"[0-9a-f]{64}", job.artifact_sha256)
                    is None
                ):
                    return False
            elif job.prepared_commit_sha:
                return False
            elif interruption_phase in {"implementing", "validating", "reviewing"}:
                if job.commit_sha:
                    return False
            elif interruption_phase == "publication-ready":
                if job.deferred_commit_authorization:
                    if (
                        job.commit_sha
                        or job.artifact_sha256 != job.reviewed_artifact_sha256
                        or re.fullmatch(r"[0-9a-f]{64}", job.artifact_sha256)
                        is None
                    ):
                        return False
                elif not job.commit_sha:
                    return False

        worktree_state = (
            job.base_sha,
            job.repository,
            job.branch,
            job.worktree,
            job.worktree_owner_token,
        )
        if not any(worktree_state):
            return ordinary_transient
        if (
            not all(worktree_state)
            or re.fullmatch(
                r"[0-9a-f]{40}(?:[0-9a-f]{24})?",
                job.base_sha,
            )
            is None
            or re.fullmatch(r"[0-9a-f]{64}", job.worktree_owner_token) is None
        ):
            return False
        try:
            if self.repository.github_slug().casefold() != job.repository.casefold():
                return False
            worktree = self.repository.recover_worktree(
                path=Path(job.worktree),
                branch=job.branch,
                base_sha=job.base_sha,
                owner_token=job.worktree_owner_token,
            )
            expected_local_sha: str | None = None
            if prepared_transient:
                expected_local_sha = job.prepared_commit_sha
            elif (
                interruption_phase == "pushing"
                and job.deferred_commit_authorization
            ):
                # An empty prepared checkpoint can mean the worker died inside
                # commit(), after HEAD moved but before the SHA was returned.
                expected_local_sha = job.prepared_commit_sha or None
            elif job.commit_sha:
                expected_local_sha = job.commit_sha
            elif (
                ordinary_transient
                or interruption_phase
                in {"implementing", "validating", "reviewing"}
                or (
                    interruption_phase == "committing"
                    and job.deferred_commit_authorization
                )
                or (
                    interruption_phase == "publication-ready"
                    and job.deferred_commit_authorization
                )
            ):
                expected_local_sha = job.base_sha
            return self.repository.remote_branch_is_absent(
                worktree,
                expected_local_sha=expected_local_sha,
            )
        except Exception as exc:
            # Stop publication on an unavailable proof, but preserve typed
            # transient outages so admission or the final chain gate records a
            # retryable lineage outcome rather than a permanent policy refusal.
            if _exception_failure_class(exc) == "transient":
                raise
            return False

    @staticmethod
    def _publication_started(job: DeliveryJob) -> bool:
        return bool(job.commit_sha) and (
            job.pushed or job.phase in {"pushing", "pull-request", "completed"}
        )

    def _prepublication_interruption_is_retryable(self, job: DeliveryJob) -> bool:
        """Authorize a successor only after proving no remote branch exists."""
        eligible_phases = {
            "implementing",
            "validating",
            "reviewing",
            "committing",
            "publication-ready",
            "pushing",
        }
        if (
            job.status != "running"
            or job.phase not in eligible_phases
            or job.success
            or job.pushed
            or bool(job.pr_url)
            or job.pr_number
            or bool(job.review_reconciliation)
            or bool(job.review_reconciled_at)
            or bool(job.merge_commit_sha)
            or job.pr_state != "unobserved"
            or job.checks_status != "unobserved"
            or bool(job.check_counts)
            or bool(job.pr_observed_at)
            or job.observation_success
            or bool(job.observation_message)
            or bool(job.task_reconciliation)
            or bool(job.task_reconciled_at)
            or not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", job.base_sha)
            or not job.repository
            or not job.branch
            or not job.worktree
            or not re.fullmatch(r"[0-9a-f]{64}", job.worktree_owner_token)
        ):
            return False
        if job.phase == "pushing":
            if (
                not job.deferred_commit_authorization
                or job.commit_sha
                or (
                    job.prepared_commit_sha
                    and re.fullmatch(
                        r"[0-9a-f]{40}(?:[0-9a-f]{24})?",
                        job.prepared_commit_sha,
                    )
                    is None
                )
                or job.artifact_sha256 != job.reviewed_artifact_sha256
                or re.fullmatch(r"[0-9a-f]{64}", job.artifact_sha256) is None
            ):
                return False
        elif job.prepared_commit_sha:
            return False
        elif job.phase == "publication-ready":
            if job.deferred_commit_authorization:
                if (
                    job.commit_sha
                    or job.artifact_sha256 != job.reviewed_artifact_sha256
                    or re.fullmatch(r"[0-9a-f]{64}", job.artifact_sha256) is None
                ):
                    return False
            elif not job.commit_sha:
                # Legacy publication-ready records were created only after a
                # local commit. Missing identity remains ambiguous forever.
                return False
        elif job.phase in {"implementing", "validating", "reviewing"}:
            if job.commit_sha:
                return False
        elif job.commit_sha and not re.fullmatch(
            r"[0-9a-f]{40}(?:[0-9a-f]{24})?", job.commit_sha
        ):
            return False
        try:
            if self.repository.github_slug().casefold() != job.repository.casefold():
                return False
            worktree = self.repository.recover_worktree(
                path=Path(job.worktree),
                branch=job.branch,
                base_sha=job.base_sha,
                owner_token=job.worktree_owner_token,
            )
            expected_local_sha: str | None = None
            if job.prepared_commit_sha:
                expected_local_sha = job.prepared_commit_sha
            elif job.commit_sha:
                expected_local_sha = job.commit_sha
            elif (
                job.phase in {"implementing", "validating", "reviewing"}
                or (
                    job.phase == "committing"
                    and job.deferred_commit_authorization
                )
                or (
                    job.phase == "publication-ready"
                    and job.deferred_commit_authorization
                )
            ):
                expected_local_sha = job.base_sha
            # For a marked pushing record with both SHA fields empty, a crash
            # may have happened inside commit() after HEAD moved. The ordering
            # marker proves push was still unreachable, so ownership plus exact
            # remote absence is sufficient without guessing the local SHA.
            return self.repository.remote_branch_is_absent(
                worktree,
                expected_local_sha=expected_local_sha,
            )
        except Exception as exc:
            # Keep the exact nonterminal identity recoverable when the proof
            # dependency is temporarily unavailable. It cannot be marked safe
            # without the proof, but it also must not be terminalized as a
            # permanent ambiguity solely because of a typed transient outage.
            if _exception_failure_class(exc) == "transient":
                raise
            return False

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
        progress_callback: ProgressCallback | None,
    ) -> DeliveryJob:
        published = self.repository.remote_branch_is_exact(worktree, job.commit_sha)
        if not published:
            if job.pushed or job.phase == "pull-request":
                raise ArtifactSecurityError(
                    "the previously verified remote branch is no longer present"
                )
            # The push subprocess may have crossed the remote boundary before
            # the local checkpoint, and a branch can later be deleted. Current
            # absence therefore cannot authorize a fresh identity once the
            # controller entered the pushing phase.
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
            self._notify(job, progress_callback)
            result = self.github.ensure_pull_request(
                repo_root=self.repository_root,
                repository=job.repository,
                base=job.base_branch,
                branch=job.branch,
                title=_pr_title(job.task_id, "Autonomous build"),
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
            published_job = self._record_pr_success(job, result.url, result.number)
            self._reconcile_open_pr_review(published_job)
            return published_job

    def _mark_interrupted(
        self,
        job: DeliveryJob,
        progress_callback: ProgressCallback | None,
        *,
        retry_safe: bool = False,
    ) -> DeliveryJob:
        interrupted_from_phase = job.phase
        job.interrupted_from_phase = interrupted_from_phase
        job.interruption_proof_sha256 = (
            _interruption_proof(job, interrupted_from_phase)
            if retry_safe
            else ""
        )
        job.status = "interrupted"
        job.phase = "interrupted"
        job.success = False
        job.failure_code = "interrupted-run"
        job.failure_class = "transient" if retry_safe else "permanent"
        job.retry_after_seconds = 0
        if retry_safe:
            job.message = (
                "The previous build worker ended before this delivery finished. "
                "MondayOS proved that its branch was not published and may create "
                "a fresh delivery identity without replaying this one."
            )
        else:
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
        task_manager = TaskManager(self.controller_root)
        with task_manager.lock(job.task_id):
            current_task = self.monday.task("get", task_id=job.task_id)
            current_task_data = dict(getattr(current_task, "data", {}) or {})
            task_failure = self._build_start_task_failure(
                job,
                current_task,
                current_task_data,
            )
            if task_failure is not None:
                return self._fail(job, *task_failure)
            started = self.monday.task("start", task_id=job.task_id)
            if not getattr(started, "success", False):
                return self._fail(
                    job,
                    "task-changed",
                    "Task no longer authorizes the build start",
                )
            started_task = self.monday.task("get", task_id=job.task_id)
            started_task_data = dict(getattr(started_task, "data", {}) or {})
            task_failure = self._publication_task_failure(
                job,
                started_task,
                started_task_data,
            )
            if task_failure is not None:
                return self._fail(job, *task_failure)
            task_data = started_task_data

        worktree = self.repository.create_worktree(job.task_id, base)
        job.base_ref = base.ref
        job.base_branch = base.branch
        job.base_sha = base.sha
        job.repository = repository_slug
        job.branch = worktree.branch
        job.worktree = str(worktree.path)
        job.worktree_owner_token = worktree.owner_token
        self.store.persist(job)

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
            task_failure = self._publication_task_failure(
                job,
                current_task,
                current_task_data,
            )
            if task_failure is not None:
                return self._fail(job, *task_failure)
            if job.predecessor_delivery_id:
                # The retry delay and implementation work leave time for an
                # older branch or a newer PR to appear. Revalidate the entire
                # authorization chain under both workflow and task locks at the
                # last reversible point before publication.
                self._require_retry_predecessor_chain(job)
            job.review_expected_task_revision = task_manager.revision(job.task_id)
            self.store.persist(job)

            self._update(job, phase="pushing", message="Pushing the approved branch")
            self._notify(job, progress_callback)
            inherited_guard_fds = (publication_guard_fd, workflow_guard_fd)
            final_task = self.monday.task("get", task_id=job.task_id)
            final_task_data = dict(getattr(final_task, "data", {}) or {})
            task_failure = self._publication_task_failure(
                job,
                final_task,
                final_task_data,
            )
            if task_failure is not None:
                return self._fail(job, *task_failure)
            try:
                current_revision = task_manager.revision(job.task_id)
            except Exception:
                return self._fail(
                    job,
                    "task-changed",
                    "Task state could not be revalidated before publication; "
                    "nothing was pushed",
                )
            if current_revision != job.review_expected_task_revision:
                return self._fail(
                    job,
                    "task-changed",
                    "Task changed after final publication authorization; "
                    "nothing was pushed",
                )
            commit = self.repository.commit(
                worktree,
                changed_files,
                f"{job.task_id}: autonomous build ({job.delivery_id})",
                expected_diff_sha256=expected_diff_sha256,
            )
            job.prepared_commit_sha = commit
            self.store.persist(job)
            post_commit_task = self.monday.task("get", task_id=job.task_id)
            post_commit_task_data = dict(
                getattr(post_commit_task, "data", {}) or {}
            )
            task_failure = self._publication_task_failure(
                job,
                post_commit_task,
                post_commit_task_data,
            )
            if task_failure is not None:
                return self._fail(job, *task_failure)
            try:
                post_commit_revision = task_manager.revision(job.task_id)
            except Exception:
                return self._fail(
                    job,
                    "task-changed",
                    "Task state could not be revalidated after preparing the commit; "
                    "nothing was pushed",
                )
            if post_commit_revision != job.review_expected_task_revision:
                return self._fail(
                    job,
                    "task-changed",
                    "Task changed while preparing the approved commit; "
                    "nothing was pushed",
                )
            required_predecessor = self._required_retry_predecessor(job)
            if required_predecessor != job.predecessor_delivery_id:
                raise ArtifactSecurityError(
                    "delivery retry predecessor changed before publication"
                )
            if job.predecessor_delivery_id:
                # Progress callbacks run before this final proof. Keep the
                # proof immediately adjacent to the irreversible push.
                self._require_retry_predecessor_chain(job)
            job.commit_sha = commit
            job.prepared_commit_sha = ""
            try:
                self.store.persist(job)
            except Exception:
                # Promotion is the durable publication boundary. If its
                # checkpoint fails, push is still unreachable; restore the
                # in-memory prepared shape so the outer failure checkpoint
                # cannot misclassify this reversible window as publication.
                job.commit_sha = ""
                job.prepared_commit_sha = commit
                raise
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
            self._notify(job, progress_callback)
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
                self._reconcile_open_pr_review(job)

        self._notify(job, progress_callback)
        if pending is not None:
            return pending
        return self._finish_published(job, worktree, progress_callback)

    def _build_start_task_failure(
        self,
        job: DeliveryJob,
        task_response: Any,
        task_data: dict[str, Any],
    ) -> tuple[str, str] | None:
        """Return why the latest task state cannot authorize a build start."""
        if not getattr(task_response, "success", False):
            return (
                "task-not-found",
                getattr(task_response, "message", "Task is unavailable"),
            )
        status = str(task_data.get("status") or "")
        if status in {"completed", "cancelled"}:
            return "terminal-task", "Completed or cancelled tasks cannot run"
        if status not in {"backlog", "assigned", "in-progress"}:
            return (
                "task-changed",
                "Task no longer authorizes the build start",
            )
        target_error = self._task_target_error(task_data)
        if target_error:
            return "project-mismatch", target_error
        try:
            current_content = _objective(job.task_id, task_data)
        except Exception:
            return "task-changed", "Task content cannot authorize the build start"
        if current_content != job.objective:
            return (
                "task-changed",
                "Task title, objective, or acceptance criteria changed before build start",
            )
        return None

    def _publication_task_failure(
        self,
        job: DeliveryJob,
        task_response: Any,
        task_data: dict[str, Any],
    ) -> tuple[str, str] | None:
        """Return a fail-closed reason when a task no longer authorizes push."""
        if not getattr(task_response, "success", False):
            return (
                "task-not-found",
                getattr(task_response, "message", "Task is unavailable"),
            )
        status = str(task_data.get("status") or "")
        if status in {"completed", "cancelled"}:
            return (
                "terminal-task",
                "Task became completed or cancelled before publication; "
                "nothing was pushed",
            )
        if status != "in-progress":
            return (
                "task-changed",
                "Task is no longer in progress; nothing was pushed",
            )
        target_error = self._task_target_error(task_data)
        if target_error:
            return "project-mismatch", target_error
        try:
            reviewed_content = _objective(job.task_id, task_data)
        except Exception:
            return (
                "task-changed",
                "Task content could not be revalidated before publication; "
                "nothing was pushed",
            )
        if reviewed_content != job.objective:
            return (
                "task-changed",
                "Task title, objective, or acceptance criteria changed after review; "
                "nothing was pushed",
            )
        return None

    def _record_pr_success(self, job: DeliveryJob, url: str, number: int) -> DeliveryJob:
        job.pr_url = url
        job.pr_number = number
        job.status = "pr-open"
        job.phase = "completed"
        job.success = True
        job.failure_code = ""
        job.failure_class = ""
        job.retry_after_seconds = 0
        job.review_reconciliation = "review-pending"
        job.review_reconciled_at = ""
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
            self._reconcile_open_pr_review(job)
        except Exception:
            # GitHub is authoritative once the exact verified PR has been
            # durably recorded. A local task-store failure must not rewrite an
            # already published success as a failed delivery.
            job.review_reconciliation = "review-pending"
            job.review_reconciled_at = ""
            self._set_review_reconciliation_message(job)
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

    def _reconcile_open_pr_review(self, job: DeliveryJob) -> None:
        """Move one explicitly pending PR-backed task to REVIEW, once.

        The reviewed pull request is already durable when this runs.  Only the
        exact ``review-pending`` marker authorizes a task mutation; an absent or
        invalid legacy value is deliberately non-actionable because it cannot
        distinguish an outage from a later human restart.
        """
        if (
            job.review_reconciliation != "review-pending"
            or bool(job.review_reconciled_at)
        ):
            return
        settled = "review-pending"
        reconciled_at = ""
        try:
            if job.pr_state in {"merged", "closed", "diverged"}:
                # Observation may crash after saving terminal PR truth but
                # before retiring this marker. Terminal truth always wins.
                settled = "terminal-unchanged"
                reconciled_at = _now()
            elif job.pr_state not in {"unobserved", "open"}:
                raise ArtifactSecurityError(
                    "Delivery pull-request state cannot authorize task review"
                )
            else:
                self._require_observation_binding(job)
                self._require_observable_pull_request(job)
                self.repository.validate_trusted_root()
                if self.repository.github_slug().casefold() != job.repository.casefold():
                    raise ArtifactSecurityError(
                        "Delivery repository no longer matches the target origin"
                    )
                task_manager = TaskManager(self.controller_root)
                with task_manager.lock(job.task_id):
                    task = self.monday.task("get", task_id=job.task_id)
                    if getattr(task, "success", False):
                        task_data = dict(getattr(task, "data", {}) or {})
                        if self._task_target_error(task_data):
                            settled = "target-changed"
                        elif self._newer_open_delivery(job) is not None:
                            settled = "superseded"
                        else:
                            status = str(task_data.get("status", ""))
                            if status == "review":
                                settled = "reviewed"
                            elif status in {"completed", "cancelled"}:
                                settled = "terminal-unchanged"
                            elif status != "in-progress":
                                settled = "state-unchanged"
                            elif not job.review_expected_task_revision:
                                settled = "legacy-unverifiable"
                            elif (
                                task_manager.revision(job.task_id)
                                != job.review_expected_task_revision
                            ):
                                settled = "state-changed"
                            else:
                                reviewed = self.monday.task(
                                    "review",
                                    task_id=job.task_id,
                                    changed_by=f"delivery:{job.delivery_id}",
                                    reason=f"Approved autonomous build opened {job.pr_url}",
                                )
                                reviewed_data = dict(
                                    getattr(reviewed, "data", {}) or {}
                                )
                                if (
                                    getattr(reviewed, "success", False)
                                    and reviewed_data.get("status") == "review"
                                ):
                                    settled = "reviewed"
                    if settled != "review-pending":
                        reconciled_at = _now()
        except Exception:
            # The exact PR success is immutable; retain an explicit retry marker
            # and a bounded message instead of converting delivery to failure.
            settled = "review-pending"
            reconciled_at = ""

        job.review_reconciliation = settled
        job.review_reconciled_at = reconciled_at
        self._set_review_reconciliation_message(job)
        job.updated_at = _now()
        try:
            self.store.persist(job)
        except Exception:
            # The durable PR record already says review-pending.  If the task
            # write succeeded, replay observes REVIEW and settles it exactly
            # once; until then the public snapshot must not claim durable repair.
            job.review_reconciliation = "review-pending"
            job.review_reconciled_at = ""
            self._set_review_reconciliation_message(job)

    @staticmethod
    def _set_review_reconciliation_message(job: DeliveryJob) -> None:
        cleanup_retained = "the local worktree was retained for cleanup" in job.message
        base = f"ChatGPT approved the exact artifact and PR #{job.pr_number} is open"
        if job.review_reconciliation == "review-pending":
            job.message = f"{base}; task status could not be moved to REVIEW"
        elif job.review_reconciliation == "reviewed":
            job.message = base
        else:
            job.message = f"{base}; task status was left unchanged"
        if cleanup_retained:
            job.message += "; the local worktree was retained for cleanup"

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


def _validated_retry_identity(
    *,
    delivery_id: str,
    predecessor_delivery_id: object,
    retry_root_delivery_id: object,
    retry_attempt: object,
) -> tuple[str, str, int]:
    predecessor = str(predecessor_delivery_id or "")
    root = str(retry_root_delivery_id or "")
    if isinstance(retry_attempt, bool) or not isinstance(retry_attempt, int):
        raise ValueError("Invalid delivery retry attempt")
    if not predecessor:
        if root or retry_attempt != 0:
            raise ValueError("Incomplete delivery retry identity")
        return "", "", 0
    if (
        not _DELIVERY_ID.fullmatch(delivery_id)
        or not _DELIVERY_ID.fullmatch(predecessor)
        or not _DELIVERY_ID.fullmatch(root)
        or not 2 <= retry_attempt <= _MAX_PREDECESSOR_DEPTH
        or delivery_id != _workflow_retry_delivery_id(root, retry_attempt)
        or predecessor
        != (
            root
            if retry_attempt == 2
            else _workflow_retry_delivery_id(root, retry_attempt - 1)
        )
    ):
        raise ValueError("Invalid delivery retry identity")
    return predecessor, root, retry_attempt


def _workflow_retry_delivery_id(root_delivery_id: str, attempt: int) -> str:
    value = f"{root_delivery_id}-r{attempt}"
    if not _DELIVERY_ID.fullmatch(value):
        raise ValueError("Invalid delivery retry identity")
    return value


def _stored_retry_identity_is_valid(job: DeliveryJob) -> bool:
    if job.retry_attempt == 0:
        return not job.predecessor_delivery_id and not job.retry_root_delivery_id
    if (
        not 2 <= job.retry_attempt <= _MAX_PREDECESSOR_DEPTH
        or not _DELIVERY_ID.fullmatch(job.retry_root_delivery_id)
        or not _DELIVERY_ID.fullmatch(job.predecessor_delivery_id)
    ):
        return False
    try:
        return (
            job.delivery_id
            == _workflow_retry_delivery_id(
                job.retry_root_delivery_id,
                job.retry_attempt,
            )
            and job.predecessor_delivery_id
            == (
                job.retry_root_delivery_id
                if job.retry_attempt == 2
                else _workflow_retry_delivery_id(
                    job.retry_root_delivery_id,
                    job.retry_attempt - 1,
                )
            )
        )
    except ValueError:
        return False


def _expected_retry_predecessors(successor: DeliveryJob) -> list[str]:
    if not _stored_retry_identity_is_valid(successor) or successor.retry_attempt < 2:
        raise ArtifactSecurityError("delivery retry lineage is invalid")
    root = successor.retry_root_delivery_id
    return [
        root if attempt == 1 else _workflow_retry_delivery_id(root, attempt)
        for attempt in range(1, successor.retry_attempt)
    ]


def _publication_evidence(job: DeliveryJob) -> bool:
    """Return whether a record contains any durable publication lifecycle state."""
    return bool(
        job.commit_sha
        or job.prepared_commit_sha
        or job.pushed
        or job.pr_url
        or job.pr_number != 0
        or job.review_reconciliation
        or job.review_reconciled_at
        or job.merge_commit_sha
        or job.pr_state != "unobserved"
        or job.checks_status != "unobserved"
        or job.check_counts
        or job.pr_observed_at
        or job.observation_success
        or job.observation_message
        or job.task_reconciliation
        or job.task_reconciled_at
    )


def _resolved_pull_request(job: DeliveryJob) -> bool:
    """Accept only a complete, observed terminal PR as settled history."""
    sha_pattern = r"[0-9a-f]{40}(?:[0-9a-f]{24})?"
    repository_pattern = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
    expected_url = (
        f"https://github.com/{job.repository}/pull/{job.pr_number}"
        if job.repository and job.pr_number > 0
        else ""
    )
    merge_identity_is_valid = (
        re.fullmatch(sha_pattern, job.merge_commit_sha) is not None
        if job.pr_state == "merged"
        else not job.merge_commit_sha
    )
    return (
        job.status == "pr-open"
        and job.phase == "completed"
        and job.success
        and job.pushed
        and not job.prepared_commit_sha
        and not job.failure_code
        and not job.failure_class
        and job.retry_after_seconds == 0
        and re.fullmatch(repository_pattern, job.repository) is not None
        and bool(job.base_branch)
        and re.fullmatch(sha_pattern, job.base_sha) is not None
        and bool(job.branch)
        and re.fullmatch(sha_pattern, job.commit_sha) is not None
        and re.fullmatch(r"[0-9a-f]{64}", job.artifact_sha256) is not None
        and job.artifact_sha256 == job.reviewed_artifact_sha256
        and job.pr_number > 0
        and job.pr_url.casefold() == expected_url.casefold()
        and job.pr_state in {"merged", "closed", "diverged"}
        and job.checks_status in {"pending", "passed", "failed", "no-checks"}
        and _validated_check_counts(job.check_counts, job.checks_status) is not None
        and merge_identity_is_valid
        and bool(job.pr_observed_at)
        and job.observation_success
    )


def _prepared_commit_failure_shape(job: DeliveryJob) -> bool:
    """Accept only a pristine failed record from the prepared-commit window."""
    return (
        job.status == "failed"
        and job.phase == "failed"
        and not job.success
        and job.deferred_commit_authorization
        and not job.commit_sha
        and re.fullmatch(
            r"[0-9a-f]{40}(?:[0-9a-f]{24})?",
            job.prepared_commit_sha,
        )
        is not None
        and not job.pushed
        and not job.pr_url
        and job.pr_number == 0
        and not job.review_reconciliation
        and not job.review_reconciled_at
        and not job.merge_commit_sha
        and job.pr_state == "unobserved"
        and job.checks_status == "unobserved"
        and not job.check_counts
        and not job.pr_observed_at
        and not job.observation_success
        and not job.observation_message
        and not job.task_reconciliation
        and not job.task_reconciled_at
        and not job.interrupted_from_phase
        and not job.interruption_proof_sha256
        and re.fullmatch(r"[0-9a-f]{64}", job.review_expected_task_revision)
        is not None
        and re.fullmatch(r"[0-9a-f]{64}", job.artifact_sha256) is not None
        and job.artifact_sha256 == job.reviewed_artifact_sha256
        and re.fullmatch(
            r"[0-9a-f]{40}(?:[0-9a-f]{24})?",
            job.base_sha,
        )
        is not None
        and bool(job.repository)
        and bool(job.branch)
        and bool(job.worktree)
        and re.fullmatch(r"[0-9a-f]{64}", job.worktree_owner_token) is not None
    )


def _retry_terminal_child_shape(job: DeliveryJob) -> bool:
    """Return whether a terminal retry child can retire a safe lineage."""
    return (
        _stored_retry_identity_is_valid(job)
        and job.retry_attempt >= 2
        and (
            (
                not job.prepared_commit_sha
                and job.status == "failed"
                and job.phase == "failed"
                and not job.success
                and job.failure_code in _FINAL_TASK_FAILURE_CODES
                and job.failure_class == "policy"
                and job.retry_after_seconds == 0
                and not job.commit_sha
            )
            or (
                job.failure_class != "transient"
                and _prepared_commit_failure_shape(job)
            )
        )
        and not job.pushed
        and not job.pr_url
        and job.pr_number == 0
        and not job.review_reconciliation
        and not job.review_reconciled_at
        and not job.merge_commit_sha
        and job.pr_state == "unobserved"
        and job.checks_status == "unobserved"
        and not job.check_counts
        and not job.pr_observed_at
        and not job.observation_success
        and not job.observation_message
        and not job.task_reconciliation
        and not job.task_reconciled_at
        and not job.interrupted_from_phase
        and not job.interruption_proof_sha256
    )


def _retry_source_shape(job: DeliveryJob) -> bool:
    if (
        not job.terminal
        or job.success
        or job.failure_class != "transient"
        or job.pushed
        or bool(job.pr_url)
        or job.pr_number
        or bool(job.review_reconciliation)
        or bool(job.review_reconciled_at)
        or bool(job.merge_commit_sha)
        or job.pr_state != "unobserved"
        or job.checks_status != "unobserved"
        or bool(job.check_counts)
        or bool(job.pr_observed_at)
        or job.observation_success
        or bool(job.observation_message)
        or bool(job.task_reconciliation)
        or bool(job.task_reconciled_at)
    ):
        return False
    ordinary = (
        job.status == "failed"
        and job.phase == "failed"
        and not job.commit_sha
        and not job.prepared_commit_sha
    )
    prepared = (
        job.failure_class == "transient"
        and _prepared_commit_failure_shape(job)
    )
    interrupted = (
        job.status == "interrupted"
        and job.phase == "interrupted"
        and job.failure_code == "interrupted-run"
        and job.retry_after_seconds == 0
        and (
            not job.commit_sha
            or re.fullmatch(
                r"[0-9a-f]{40}(?:[0-9a-f]{24})?",
                job.commit_sha,
            )
            is not None
        )
    )
    return ordinary or prepared or interrupted


def _interruption_proof(job: DeliveryJob, source_phase: str) -> str:
    payload = {
        "delivery_id": job.delivery_id,
        "task_id": job.task_id,
        "repo_root": job.repo_root,
        "project_name": job.project_name,
        "expected_repository": job.expected_repository,
        "root_device": job.root_device,
        "root_inode": job.root_inode,
        "base_ref": job.base_ref,
        "base_branch": job.base_branch,
        "base_sha": job.base_sha,
        "repository": job.repository,
        "branch": job.branch,
        "worktree": job.worktree,
        "worktree_owner_token": job.worktree_owner_token,
        "commit_sha": job.commit_sha,
        "created_at": job.created_at,
        "predecessor_delivery_id": job.predecessor_delivery_id,
        "retry_root_delivery_id": job.retry_root_delivery_id,
        "retry_attempt": job.retry_attempt,
        "source_phase": source_phase,
    }
    if job.prepared_commit_sha:
        # Preserve every legacy proof digest byte-for-byte. Only the new
        # prepared-commit crash window adds this field.
        payload["prepared_commit_sha"] = job.prepared_commit_sha
    if job.deferred_commit_authorization:
        # Preserve every legacy proof digest byte-for-byte. New deferred-commit
        # records bind their explicit ordering marker into the proof.
        payload["deferred_commit_authorization"] = True
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


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
        "task-changed",
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
            isinstance(current, GitCommandError)
            and current.result.timed_out
            and not classified
        ):
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
