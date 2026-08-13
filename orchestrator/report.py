"""
ExecutionReport — the persisted record of a single orchestrated execution.

Every Monday.execute() call produces exactly one ExecutionReport, written to
logs/executions/{execution_id}.json. The report is the audit trail: it records
which provider ran, what was asked, how long it took, whether it succeeded, what
changed, what knowledge was captured, and the validator's confidence.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


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
        logs_dir.mkdir(parents=True, exist_ok=True)
        path = logs_dir / f"{self.execution_id}.json"
        path.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return path
