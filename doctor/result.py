"""AnalyzerResult and DoctorReport — aggregated outputs of repository inspection."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from doctor.finding import Finding, Severity


@dataclass
class AnalyzerResult:
    """
    Output of a single analyzer run.

    Attributes:
        name:        Analyzer identifier (matches BaseAnalyzer.NAME).
        findings:    All observations from this analyzer, in discovery order.
        duration_ms: Wall-clock time the analyzer took, in milliseconds.
        error:       If the analyzer raised an unhandled exception, its str() is here.
    """

    name: str
    findings: list[Finding]
    duration_ms: float = 0.0
    error: str = ""

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.CRITICAL)

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.WARNING)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "duration_ms": round(self.duration_ms, 1),
            "error": self.error,
            "findings": [f.to_dict() for f in self.findings],
        }


def _compute_health_score(results: list[AnalyzerResult]) -> int:
    """
    Derive a 0–100 health score from analyzer findings.

    Deduction schedule (capped per severity class):
      CRITICAL: 15 points each, max 45 total
      WARNING:   5 points each, max 30 total
    """
    all_findings = [f for r in results for f in r.findings]
    n_critical = sum(1 for f in all_findings if f.severity == Severity.CRITICAL)
    n_warning = sum(1 for f in all_findings if f.severity == Severity.WARNING)
    deduction = min(n_critical * 15, 45) + min(n_warning * 5, 30)
    return max(0, 100 - deduction)


def _health_grade(score: int) -> str:
    if score >= 90:
        return "Excellent"
    if score >= 75:
        return "Good"
    if score >= 60:
        return "Fair"
    if score >= 40:
        return "Poor"
    return "Critical"


@dataclass
class DoctorReport:
    """
    Complete repository health report.

    Built by RepositoryInspector after all analyzers finish.
    """

    health_score: int
    grade: str
    results: list[AnalyzerResult]
    generated_at: str = ""
    total_duration_ms: float = 0.0

    @classmethod
    def build(cls, results: list[AnalyzerResult], total_duration_ms: float = 0.0) -> DoctorReport:
        score = _compute_health_score(results)
        grade = _health_grade(score)
        return cls(
            health_score=score,
            grade=grade,
            results=results,
            generated_at=datetime.now(tz=timezone.utc).isoformat(),
            total_duration_ms=round(total_duration_ms, 1),
        )

    @property
    def all_findings(self) -> list[Finding]:
        return [f for r in self.results for f in r.findings]

    @property
    def findings_by_severity(self) -> dict[Severity, list[Finding]]:
        out: dict[Severity, list[Finding]] = {s: [] for s in Severity}
        for f in self.all_findings:
            out[f.severity].append(f)
        return out

    @property
    def recommendations(self) -> list[str]:
        """Ranked recommendations: CRITICALs → WARNINGs → INFOs."""
        recs: list[str] = []
        for sev in (Severity.CRITICAL, Severity.WARNING, Severity.INFO):
            for f in self.findings_by_severity[sev]:
                if f.recommendation and f.recommendation not in recs:
                    recs.append(f.recommendation)
        return recs

    def to_dict(self) -> dict[str, Any]:
        fbs = self.findings_by_severity
        return {
            "health_score": self.health_score,
            "grade": self.grade,
            "generated_at": self.generated_at,
            "total_duration_ms": self.total_duration_ms,
            "summary": {
                "critical": len(fbs[Severity.CRITICAL]),
                "warning": len(fbs[Severity.WARNING]),
                "info": len(fbs[Severity.INFO]),
                "ok": len(fbs[Severity.OK]),
            },
            "recommendations": self.recommendations,
            "analyzers": [r.to_dict() for r in self.results],
        }
