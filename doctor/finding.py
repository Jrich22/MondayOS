"""Finding — a single health observation produced by an analyzer."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    CRITICAL = "critical"   # blocks correctness / deployment
    WARNING = "warning"     # degrades quality; should be fixed
    INFO = "info"           # informational; no action required
    OK = "ok"               # explicit pass (used to confirm a check passed)


@dataclass
class Finding:
    """
    A single observation from an analyzer.

    Every analyzer produces zero or more Findings. The severity determines
    how the finding affects the health score and how it is displayed.

    Attributes:
        category:       Short label for the check area (e.g. "git", "tests").
        severity:       How serious this finding is.
        title:          One-line description shown in summary output.
        detail:         Optional multi-line elaboration shown in verbose mode.
        recommendation: Actionable fix, ranked in output by severity.
        data:           Structured payload for JSON output and programmatic use.
    """

    category: str
    severity: Severity
    title: str
    detail: str = ""
    recommendation: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity.value,
            "title": self.title,
            "detail": self.detail,
            "recommendation": self.recommendation,
            "data": self.data,
        }
