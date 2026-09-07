"""
The benchmark result, and the verdict it produces.

The baseline is a committed artifact, so its format is a contract: a future
change has to produce a diff a human can read and argue with. Values that move
for reasons unrelated to MondayOS's behaviour — file counts, timings — live in a
`volatile` block that is recorded and never compared, because a benchmark that
fails whenever someone adds a file is a benchmark people learn to ignore.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA = 1

BASELINE_PATH = Path(__file__).resolve().parent / "baseline.json"

STALE_BASELINE_MESSAGE = (
    "Benchmark improved; committed baseline is stale. "
    "Review the change and re-record intentionally with: python -m benchmark --record"
)


@dataclass
class CorpusReport:
    project: str
    available: bool
    reason: str = ""
    assertions: dict[str, int] = field(default_factory=dict)
    observations: dict[str, Any] = field(default_factory=dict)
    volatile: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, include_volatile: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "available": self.available,
            "reason": self.reason,
            "assertions": self.assertions,
            "observations": self.observations,
        }
        if include_volatile:
            out["volatile"] = self.volatile
        return out


@dataclass
class Report:
    corpora: dict[str, CorpusReport] = field(default_factory=dict)

    def to_dict(self, include_volatile: bool = True) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "generated_from": "structure only; no model provider was contacted",
            "corpora": {
                slug: self.corpora[slug].to_dict(include_volatile) for slug in sorted(self.corpora)
            },
        }

    def stable(self) -> dict[str, Any]:
        """
        The comparable part of the report.

        Everything except `volatile`. This is what determinism is asserted over
        and what the baseline compares, so ordinary source growth cannot fail a
        run.
        """
        return self.to_dict(include_volatile=False)

    def json(self, include_volatile: bool = True) -> str:
        return json.dumps(self.to_dict(include_volatile), indent=2, sort_keys=True) + "\n"


@dataclass
class Verdict:
    """What a run concluded, and why."""

    failures: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures and not self.improvements

    def render(self) -> str:
        lines: list[str] = []
        if self.skipped:
            lines.append("Skipped:")
            lines.extend(f"  {s}" for s in self.skipped)
        if self.failures:
            lines.append("FAILURES:")
            lines.extend(f"  {f}" for f in self.failures)
        if self.improvements:
            lines.append(STALE_BASELINE_MESSAGE)
            lines.extend(f"  {i}" for i in self.improvements)
        if self.ok:
            lines.append("Benchmark matches the committed baseline.")
        return "\n".join(lines)


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return {}
    return data


def save_baseline(report: Report, path: Path = BASELINE_PATH) -> None:
    """
    Write the baseline.

    Volatile values are included for human context — a reader comparing two
    baselines wants to know the corpus grew — but `compare` never reads them.
    """
    path.write_text(report.json(include_volatile=True), encoding="utf-8")
