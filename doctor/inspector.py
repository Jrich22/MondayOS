"""RepositoryInspector — orchestrates all analyzers and produces a DoctorReport."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from doctor.analyzers import (
    CodeQualityAnalyzer,
    ConfigAnalyzer,
    DocumentationAnalyzer,
    GitAnalyzer,
    KnowledgeHealthAnalyzer,
    TaskHealthAnalyzer,
    TestAnalyzer,
)
from doctor.base import BaseAnalyzer
from doctor.finding import Severity
from doctor.result import AnalyzerResult, DoctorReport

# Ordered list of all registered analyzer classes.
# Add new analyzers here — the inspector discovers them automatically.
_REGISTERED_ANALYZERS: list[type[BaseAnalyzer]] = [
    GitAnalyzer,
    TestAnalyzer,
    CodeQualityAnalyzer,
    KnowledgeHealthAnalyzer,
    DocumentationAnalyzer,
    TaskHealthAnalyzer,
    ConfigAnalyzer,
]

# Maps NAME → class for selective execution
_ANALYZER_MAP: dict[str, type[BaseAnalyzer]] = {
    cls.NAME: cls for cls in _REGISTERED_ANALYZERS
}


class RepositoryInspector:
    """
    Runs a configurable set of analyzers over a MondayOS project root and
    aggregates their results into a DoctorReport.

    Analyzers are pluggable: add a new BaseAnalyzer subclass to
    _REGISTERED_ANALYZERS and it is automatically included in every run.

    Args:
        project_root:  Path to the project root to inspect.
        monday:        Optional Monday instance for analyzers that need API access.
        analyzer_names: If provided, only the named analyzers run.
    """

    def __init__(
        self,
        project_root: Path,
        monday: Any = None,
        analyzer_names: list[str] | None = None,
    ) -> None:
        self._root = project_root
        self._monday = monday

        if analyzer_names:
            self._analyzer_classes = [
                _ANALYZER_MAP[name]
                for name in analyzer_names
                if name in _ANALYZER_MAP
            ]
        else:
            self._analyzer_classes = list(_REGISTERED_ANALYZERS)

    @classmethod
    def available_analyzers(cls) -> list[str]:
        """Return the NAMEs of all registered analyzers in run order."""
        return [c.NAME for c in _REGISTERED_ANALYZERS]

    def run(self) -> DoctorReport:
        """
        Execute all configured analyzers sequentially.

        Each analyzer is wrapped in a try/except so one failure never
        silences subsequent analyzers. Failures are surfaced as CRITICAL
        findings in the analyzer's own AnalyzerResult.
        """
        total_start = time.monotonic()
        results: list[AnalyzerResult] = []

        for analyzer_cls in self._analyzer_classes:
            result = self._run_one(analyzer_cls)
            results.append(result)

        total_ms = (time.monotonic() - total_start) * 1000
        return DoctorReport.build(results, total_duration_ms=total_ms)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_one(self, analyzer_cls: type[BaseAnalyzer]) -> AnalyzerResult:
        from doctor.finding import Finding
        start = time.monotonic()
        try:
            analyzer = analyzer_cls(project_root=self._root, monday=self._monday)
            return analyzer.analyze()
        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            return AnalyzerResult(
                name=analyzer_cls.NAME,
                findings=[Finding(
                    category=analyzer_cls.NAME,
                    severity=Severity.CRITICAL,
                    title=f"Analyzer '{analyzer_cls.NAME}' raised an unexpected error",
                    detail=str(exc),
                    recommendation="Report this as a MondayOS bug.",
                )],
                duration_ms=duration_ms,
                error=str(exc),
            )
