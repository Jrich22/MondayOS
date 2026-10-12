"""Durable records for artifact-bound autonomous delivery jobs."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


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

    @property
    def terminal(self) -> bool:
        return self.status in {"pr-open", "failed", "interrupted", "rejected"}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeliveryJob:
        return cls(
            delivery_id=str(data.get("delivery_id", "")),
            task_id=str(data.get("task_id", "")),
            objective=str(data.get("objective", "")),
            repo_root=str(data.get("repo_root", "")),
            status=str(data.get("status", "reserved")),
            phase=str(data.get("phase", "reserved")),
            success=bool(data.get("success", False)),
            base_ref=str(data.get("base_ref", "")),
            base_branch=str(data.get("base_branch", "")),
            base_sha=str(data.get("base_sha", "")),
            repository=str(data.get("repository", "")),
            branch=str(data.get("branch", "")),
            worktree=str(data.get("worktree", "")),
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
        )
