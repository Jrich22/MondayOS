"""
ExecutionReport — the persisted record of a single orchestrated execution.

Every Monday.execute() call produces exactly one ExecutionReport, written to
logs/executions/{execution_id}.json. The report is the audit trail: it records
which provider ran, what was asked, how long it took, whether it succeeded, what
changed, what knowledge was captured, and the validator's confidence.
"""
from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from brain.providers.base import (
    safe_provider_identifier,
    safe_provider_model,
    safe_provider_stop_reason,
)
from core.secure_io import write_json_atomic_at

_PROVIDER_FAILURE_CODES = frozenset({
    "authentication-failed",
    "provider-error",
    "rate-limited",
    "unavailable",
})
_PROVIDER_UNAVAILABLE_CODES = frozenset({
    "authentication-failed",
    "missing-credential",
    "missing-sdk",
    "model-missing",
    "provider-error",
    "provider-not-configured",
    "provider-status-invalid",
    "rate-limited",
    "service-unreachable",
    "unavailable",
})
_ERROR_BY_STATUS = {
    "blocked": "Execution was blocked by a safety gate.",
    "failed": "Execution failed. Check provider configuration or retry later.",
    "skipped": "Execution was skipped.",
    "unavailable": "Provider is unavailable. Check its configuration and retry.",
    "validation-failed": "Execution result failed validation.",
}
_EXECUTION_STATUSES = frozenset({
    "",
    "blocked",
    "completed",
    "dry-run",
    "executed",
    "failed",
    "in-progress",
    "planned",
    "review",
    "skipped",
    "unavailable",
    "validation-failed",
})


def _persisted_provider(value: Any) -> str:
    """Bound a persisted provider label while preserving an intentional blank."""
    if type(value) is str and not value:
        return ""
    return safe_provider_identifier(value, fallback="custom")


def sanitize_execution_record(value: Any) -> dict[str, Any]:
    """Remove legacy provider diagnostics from one persisted execution record.

    Provider output remains intact in ``result_full``/``result_excerpt``. Only
    provenance and diagnostic fields—which historically copied SDK/provider
    prose—are reduced to MondayOS-owned labels and codes.
    """
    if type(value) is not dict:
        return {}
    clean = dict(value)
    provider = _persisted_provider(value.get("provider_used", ""))
    clean["provider_used"] = provider
    clean["model_used"] = safe_provider_model(provider)
    stop_reason = safe_provider_stop_reason(value.get("stop_reason", ""))
    clean["stop_reason"] = stop_reason
    clean["truncated"] = (
        value.get("truncated") is True
        or stop_reason in {"length", "max_tokens"}
    )

    attempts: list[dict[str, str]] = []
    raw_attempts = value.get("provider_attempts", [])
    if type(raw_attempts) is list:
        for raw in raw_attempts:
            if type(raw) is not dict:
                continue
            attempt_provider = _persisted_provider(raw.get("provider", ""))
            raw_status = raw.get("status", "")
            status = raw_status if type(raw_status) is str else ""
            raw_reason = raw.get("reason", "")
            reason = raw_reason if type(raw_reason) is str else ""
            if status == "completed":
                reason = ""
            elif status == "unavailable":
                reason = (
                    reason if reason in _PROVIDER_UNAVAILABLE_CODES else "unavailable"
                )
            else:
                status = "failed"
                reason = reason if reason in _PROVIDER_FAILURE_CODES else "provider-error"
            attempts.append({
                "provider": attempt_provider,
                "status": status,
                "reason": reason,
            })
    clean["provider_attempts"] = attempts

    raw_error = value.get("error", "")
    raw_status = value.get("status", "")
    status = (
        raw_status
        if type(raw_status) is str and raw_status in _EXECUTION_STATUSES
        else ("failed" if type(raw_error) is str and raw_error else "")
    )
    clean["status"] = status
    if type(raw_error) is str and raw_error:
        clean["error"] = _ERROR_BY_STATUS.get(status, "")
    else:
        clean["error"] = ""
    return clean


def _open_report_directory(logs_dir: Path) -> int:
    """Create/open the report directory without following its writable parent."""
    logs_dir = logs_dir.absolute()
    parent = logs_dir.parent
    # The project root may itself live below a normal platform symlink (macOS
    # uses /var -> /private/var for temporary directories). Treat that supplied
    # root as trusted, but never follow the writable ``logs`` parent or the
    # ``executions`` leaf where an attacker could redirect reports.
    parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    before_parent = os.lstat(parent)
    if not stat.S_ISDIR(before_parent.st_mode):
        raise OSError("execution report parent is not a regular directory")
    parent_fd = os.open(parent, flags)
    try:
        opened_parent = os.fstat(parent_fd)
        current_parent = os.lstat(parent)
        expected_parent = (before_parent.st_dev, before_parent.st_ino)
        if (
            not stat.S_ISDIR(opened_parent.st_mode)
            or not stat.S_ISDIR(current_parent.st_mode)
            or (opened_parent.st_dev, opened_parent.st_ino) != expected_parent
            or (current_parent.st_dev, current_parent.st_ino) != expected_parent
        ):
            raise OSError("execution report parent changed while it was opened")

        try:
            os.mkdir(logs_dir.name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        directory_fd = os.open(logs_dir.name, flags, dir_fd=parent_fd)
        opened = os.fstat(directory_fd)
        current = os.stat(logs_dir.name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            os.close(directory_fd)
            raise OSError("execution report directory changed while it was opened")
        return directory_fd
    finally:
        os.close(parent_fd)


def _make_existing_reports_private(directory_fd: int) -> None:
    """Best-effort migration of existing execution reports to safe private form.

    Reports predate the private-file guarantee, so a new report write is also
    the migration point for older JSON files. Each entry is opened without
    following links and checked through the resulting descriptor before its
    permissions are changed. Valid legacy JSON also has provider provenance and
    diagnostics reduced before atomic replacement. A malformed entry must never
    prevent the new audit record from being written.
    """
    try:
        entries = os.listdir(directory_fd)
    except OSError:
        return

    open_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    open_flags |= getattr(os, "O_NOFOLLOW", 0)
    for name in entries:
        if not name.endswith(".json"):
            continue
        try:
            fd = os.open(name, open_flags, dir_fd=directory_fd)
        except OSError:
            continue
        replacement: dict[str, Any] | None = None
        expected_identity: tuple[int, int] | None = None
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                continue
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
                continue
            if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                continue
            if opened.st_size <= 64 * 1024 * 1024:
                chunks: list[bytes] = []
                while True:
                    chunk = os.read(fd, 1024 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                parsed = json.loads(b"".join(chunks).decode("utf-8"))
                if (
                    type(parsed) is dict
                    and type(parsed.get("execution_id")) is str
                    and parsed["execution_id"] == Path(name).stem
                ):
                    candidate = sanitize_execution_record(parsed)
                    if candidate != parsed or stat.S_IMODE(opened.st_mode) != 0o600:
                        replacement = candidate
                        expected_identity = (opened.st_dev, opened.st_ino)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            continue
        finally:
            os.close(fd)
        if replacement is not None and expected_identity is not None:
            try:
                write_json_atomic_at(
                    directory_fd,
                    name,
                    replacement,
                    expected_identity=expected_identity,
                )
            except (OSError, TypeError, ValueError):
                continue


def _write_report_atomic(directory_fd: int, name: str, value: dict[str, Any]) -> None:
    """Atomically replace one private report inside an already-open directory."""
    write_json_atomic_at(directory_fd, name, value)


class ExecutionMode(Enum):
    """
    Safety mode governing how far an execution is allowed to act.

    DRY_RUN     — plan and select a provider, but make no provider call and no
                  mutations. Reports what *would* happen.
    REVIEW      — execute through the provider and capture knowledge, but stop
                  the task at REVIEW for a human to approve. No file
                  modification. This is the default.
    AUTONOMOUS  — full execution: completes the task automatically. File
                  modification is only permitted here, and only when explicitly
                  enabled (autonomous_enabled=True).
    """

    DRY_RUN = "dry-run"
    REVIEW = "review"
    AUTONOMOUS = "autonomous"

    @classmethod
    def from_str(cls, value: str) -> "ExecutionMode":
        """Parse a mode string. Accepts aliases. Raises ValueError if unknown."""
        normalized = (value or "").strip().lower().replace("_", "-")
        aliases = {
            "dry-run": cls.DRY_RUN,
            "dry": cls.DRY_RUN,
            "review": cls.REVIEW,
            "review-required": cls.REVIEW,
            "autonomous": cls.AUTONOMOUS,
            "auto": cls.AUTONOMOUS,
        }
        if normalized not in aliases:
            valid = sorted({m.value for m in cls})
            raise ValueError(f"Unknown execution mode {value!r}. Valid modes: {valid}")
        return aliases[normalized]


@dataclass
class ExecutionReport:
    """
    Complete record of one orchestrated execution.

    All fields have defaults so a report can always be constructed and
    serialised, even for an execution that failed before doing any work.
    """

    execution_id: str
    task_id: str
    mode: str = ""
    policy: str = ""

    # Provider + prompt
    provider_used: str = ""
    model_used: str = ""
    prompt_summary: str = ""
    provider_attempts: list[dict[str, Any]] = field(default_factory=list)

    # Timing
    started_at: str = ""
    completed_at: str = ""
    duration_ms: float = 0.0

    # Outcome
    success: bool = False
    status: str = ""              # planned|dry-run|skipped|blocked|failed|validation-failed|review|completed
    error: str = ""

    # Effects
    files_changed: list[str] = field(default_factory=list)
    knowledge_captured: list[str] = field(default_factory=list)
    follow_up_tasks: list[str] = field(default_factory=list)
    task_status: str = ""

    # Quality
    confidence: float = 0.0
    validation: dict[str, Any] = field(default_factory=dict)

    # Detail
    plan: dict[str, Any] = field(default_factory=dict)
    result_excerpt: str = ""
    # Full, untruncated provider response, preserved for humans (structured
    # verdict parsing consumes this; result_excerpt remains a short preview).
    result_full: str = ""
    # True when the provider stopped because it hit the output-token limit, so
    # the response may be missing content it intended to emit. Verdict parsing
    # uses this to explain an invalid verdict rather than silently passing.
    truncated: bool = False
    stop_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write_log(self, logs_dir: Path) -> Path:
        """
        Persist this report as JSON to logs_dir/{execution_id}.json.

        Returns the path written. Creates logs_dir if needed.
        """
        logs_dir = Path(logs_dir)
        filename = f"{self.execution_id}.json"
        if (
            not self.execution_id
            or len(filename) > 160
            or Path(filename).name != filename
        ):
            raise ValueError("execution_id cannot be used as a report filename")
        path = logs_dir / filename
        # The report contains prompts and provider output.  Atomic replacement
        # prevents partial audit records and creates the file with private 0600
        # permissions instead of inheriting a process-wide umask.
        directory_fd = _open_report_directory(logs_dir)
        try:
            _make_existing_reports_private(directory_fd)
            _write_report_atomic(
                directory_fd,
                filename,
                sanitize_execution_record(self.to_dict()),
            )
        finally:
            os.close(directory_fd)
        return path
