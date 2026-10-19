"""Durable records for artifact-bound autonomous delivery jobs."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

DELIVERY_FAILURE_CLASSES = frozenset({"transient", "permanent", "policy"})
MAX_DELIVERY_RETRY_AFTER_SECONDS = 86_400
REVIEW_RECONCILIATION_STATES = frozenset(
    {
        "",
        "review-pending",
        "reviewed",
        "terminal-unchanged",
        "state-unchanged",
        "state-changed",
        "target-changed",
        "superseded",
        "legacy-unverifiable",
    }
)


@dataclass
class DeliveryAttempt:
    """One bounded implementation/verification/review attempt."""

    number: int
    builder: str = ""
    patch_summary: str = ""
    changed_files: list[str] = field(default_factory=list)
    verification: list[dict[str, Any]] = field(default_factory=list)
    verification_sha256: str = ""
    diff_sha256: str = ""
    artifact_sha256: str = ""
    review: dict[str, Any] = field(default_factory=dict)
    status: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeliveryAttempt:
        return cls(
            number=int(data.get("number", 0)),
            builder=str(data.get("builder", "")),
            patch_summary=str(data.get("patch_summary", "")),
            changed_files=[str(item) for item in data.get("changed_files", [])],
            verification=list(data.get("verification", [])),
            verification_sha256=str(data.get("verification_sha256", "")),
            diff_sha256=str(data.get("diff_sha256", "")),
            artifact_sha256=str(data.get("artifact_sha256", "")),
            review=dict(data.get("review", {})),
            status=str(data.get("status", "")),
            message=str(data.get("message", "")),
        )


@dataclass
class DeliveryJob:
    """Parent audit record for one isolated code-delivery workflow."""

    delivery_id: str
    task_id: str
    objective: str
    repo_root: str
    status: str = "reserved"
    phase: str = "reserved"
    success: bool = False
    base_ref: str = ""
    base_branch: str = ""
    base_sha: str = ""
    repository: str = ""
    branch: str = ""
    worktree: str = ""
    builder: str = ""
    reviewer: str = "codex-chatgpt"
    max_attempts: int = 3
    attempt: int = 0
    attempts: list[dict[str, Any]] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    artifact_sha256: str = ""
    reviewed_artifact_sha256: str = ""
    commit_sha: str = ""
    pushed: bool = False
    pr_number: int = 0
    pr_url: str = ""
    failure_code: str = ""
    message: str = ""
    created_at: str = ""
    updated_at: str = ""
    # Appended to preserve the positional constructor contract of schema-v1
    # delivery records while extending their durable target identity.
    project_name: str = ""
    expected_repository: str = ""
    root_device: int = 0
    root_inode: int = 0
    worktree_owner_token: str = ""
    # Pull-request lifecycle is observed after the immutable delivery result.
    # These fields never change the reviewed artifact or the original build
    # outcome; they record what GitHub later reported for that exact commit.
    pr_state: str = "unobserved"
    checks_status: str = "unobserved"
    check_counts: dict[str, int] = field(default_factory=dict)
    merge_commit_sha: str = ""
    pr_observed_at: str = ""
    observation_success: bool = False
    observation_message: str = ""
    task_reconciliation: str = ""
    task_reconciled_at: str = ""
    # Appended for schema compatibility. Empty/unknown values are deliberately
    # not retryable; retry hints are honored only for classified transients.
    failure_class: str = ""
    retry_after_seconds: int = 0
    # Opening the exact reviewed PR and moving its task to REVIEW are separate
    # durable effects.  A task-store outage after publication must therefore be
    # visible and repairable without replaying any GitHub or repository action.
    review_reconciliation: str = ""
    review_reconciled_at: str = ""
    review_expected_task_revision: str = ""
    # A successor delivery may run only while every controller-proved
    # predecessor remains unpublished. Persisting the immediate predecessor
    # makes that authorization chain auditable and restart-safe.
    predecessor_delivery_id: str = ""
    retry_root_delivery_id: str = ""
    retry_attempt: int = 0
    interrupted_from_phase: str = ""
    interruption_proof_sha256: str = ""
    # A local commit is not publication authority. It is checkpointed here
    # until the post-commit task gate promotes it to ``commit_sha``.
    prepared_commit_sha: str = ""
    # New workflows deliberately defer the local commit until every progress
    # callback has run.  Legacy records default false so a publication-ready
    # record without a commit cannot be reinterpreted as safely unpublished.
    deferred_commit_authorization: bool = False

    @property
    def terminal(self) -> bool:
        return self.status in {"pr-open", "failed", "interrupted", "rejected"}

    def to_dict(self, *, include_private: bool = False) -> dict[str, Any]:
        payload = asdict(self)
        failure_class = normalize_delivery_failure_class(self.failure_class)
        payload["failure_class"] = failure_class
        payload["retry_after_seconds"] = bounded_delivery_retry_after_seconds(
            self.retry_after_seconds,
            failure_class=failure_class,
        )
        payload["review_reconciliation"] = normalize_review_reconciliation(
            self.review_reconciliation
        )
        payload["review_expected_task_revision"] = normalize_task_revision(
            self.review_expected_task_revision
        )
        if not include_private:
            payload.pop("worktree_owner_token", None)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeliveryJob:
        failure_class = normalize_delivery_failure_class(data.get("failure_class"))
        return cls(
            delivery_id=str(data.get("delivery_id", "")),
            task_id=str(data.get("task_id", "")),
            objective=str(data.get("objective", "")),
            repo_root=str(data.get("repo_root", "")),
            project_name=str(data.get("project_name", "")),
            expected_repository=str(data.get("expected_repository", "")),
            root_device=int(data.get("root_device", 0) or 0),
            root_inode=int(data.get("root_inode", 0) or 0),
            status=str(data.get("status", "reserved")),
            phase=str(data.get("phase", "reserved")),
            success=bool(data.get("success", False)),
            base_ref=str(data.get("base_ref", "")),
            base_branch=str(data.get("base_branch", "")),
            base_sha=str(data.get("base_sha", "")),
            repository=str(data.get("repository", "")),
            branch=str(data.get("branch", "")),
            worktree=str(data.get("worktree", "")),
            worktree_owner_token=str(data.get("worktree_owner_token", "")),
            builder=str(data.get("builder", "")),
            reviewer=str(data.get("reviewer", "codex-chatgpt")),
            max_attempts=int(data.get("max_attempts", 3)),
            attempt=int(data.get("attempt", 0)),
            attempts=list(data.get("attempts", [])),
            changed_files=[str(item) for item in data.get("changed_files", [])],
            artifact_sha256=str(data.get("artifact_sha256", "")),
            reviewed_artifact_sha256=str(data.get("reviewed_artifact_sha256", "")),
            commit_sha=str(data.get("commit_sha", "")),
            pushed=bool(data.get("pushed", False)),
            pr_number=int(data.get("pr_number", 0)),
            pr_url=str(data.get("pr_url", "")),
            failure_code=str(data.get("failure_code", "")),
            message=str(data.get("message", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            pr_state=str(data.get("pr_state", data.get("pr_lifecycle", "unobserved"))),
            checks_status=str(data.get("checks_status", "unobserved")),
            check_counts=_check_counts(data.get("check_counts")),
            merge_commit_sha=str(data.get("merge_commit_sha", "")),
            pr_observed_at=str(data.get("pr_observed_at", "")),
            observation_success=bool(data.get("observation_success", False)),
            observation_message=str(data.get("observation_message", "")),
            task_reconciliation=str(data.get("task_reconciliation", "")),
            task_reconciled_at=str(data.get("task_reconciled_at", "")),
            failure_class=failure_class,
            retry_after_seconds=bounded_delivery_retry_after_seconds(
                data.get("retry_after_seconds"),
                failure_class=failure_class,
            ),
            review_reconciliation=normalize_review_reconciliation(
                data.get("review_reconciliation")
            ),
            review_reconciled_at=str(data.get("review_reconciled_at", "")),
            review_expected_task_revision=normalize_task_revision(
                data.get("review_expected_task_revision")
            ),
            predecessor_delivery_id=str(data.get("predecessor_delivery_id", "")),
            retry_root_delivery_id=str(data.get("retry_root_delivery_id", "")),
            retry_attempt=_stored_retry_attempt(data.get("retry_attempt", 0)),
            interrupted_from_phase=str(data.get("interrupted_from_phase", "")),
            interruption_proof_sha256=str(
                data.get("interruption_proof_sha256", "")
            ),
            prepared_commit_sha=str(data.get("prepared_commit_sha", "")),
            deferred_commit_authorization=(
                data.get("deferred_commit_authorization") is True
            ),
        )


def normalize_delivery_failure_class(value: object) -> str:
    """Accept only the exact durable taxonomy; prose can never enable retries."""
    if isinstance(value, str) and value in DELIVERY_FAILURE_CLASSES:
        return value
    return ""


def _stored_retry_attempt(value: object) -> int:
    """Preserve malformed lineage as invalid instead of coercing it into authority."""
    if isinstance(value, bool) or not isinstance(value, int):
        return -1
    return value


def normalize_review_reconciliation(value: object) -> str:
    """Accept only controller-owned open-PR task reconciliation states."""
    if isinstance(value, str) and value in REVIEW_RECONCILIATION_STATES:
        return value
    return ""


def normalize_task_revision(value: object) -> str:
    """Return one canonical SHA-256 task revision, or no mutation authority."""
    if isinstance(value, str) and len(value) == 64:
        lowered = value.lower()
        if all(character in "0123456789abcdef" for character in lowered):
            return lowered
    return ""


def bounded_delivery_retry_after_seconds(
    value: object,
    *,
    failure_class: str,
) -> int:
    """Return one bounded retry hint only for classified transient failures."""
    if (
        failure_class != "transient"
        or isinstance(value, bool)
        or not isinstance(value, int)
    ):
        return 0
    return min(max(0, value), MAX_DELIVERY_RETRY_AFTER_SECONDS)


def _check_counts(value: object) -> dict[str, int]:
    """Read only a complete, bounded aggregate from a durable record."""
    required = {"total", "passed", "failed", "pending"}
    if not isinstance(value, dict) or set(value) != required:
        return {}
    if any(
        isinstance(value[key], bool)
        or not isinstance(value[key], int)
        or not 0 <= value[key] <= 500
        for key in required
    ):
        return {}
    counts = {key: int(value[key]) for key in required}
    if counts["total"] != (
        counts["passed"] + counts["failed"] + counts["pending"]
    ):
        return {}
    return counts
