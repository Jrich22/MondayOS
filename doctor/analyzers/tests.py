"""TestAnalyzer — inspects test coverage and last-run results from pytest cache."""
from __future__ import annotations

import json
import time
from pathlib import Path

from doctor.base import BaseAnalyzer
from doctor.finding import Finding, Severity
from doctor.result import AnalyzerResult

_CATEGORY = "tests"
_CACHE_DIR = ".pytest_cache"
_CACHE_NODEIDS = "v/cache/nodeids"
_CACHE_LASTFAILED = "v/cache/lastfailed"


class TestAnalyzer(BaseAnalyzer):
    """
    Checks test file presence, last-run results from .pytest_cache, and
    coverage configuration.

    Does NOT execute pytest — reads only cached results. This keeps
    `monday doctor` fast and non-invasive.
    """

    NAME = "tests"

    def analyze(self) -> AnalyzerResult:
        start = time.monotonic()
        findings: list[Finding] = []

        # Locate test files
        test_files = list(self._root.rglob("test_*.py")) + list(self._root.rglob("*_test.py"))
        # Filter out __pycache__ and .venv paths
        test_files = [
            f for f in test_files
            if "__pycache__" not in f.parts and ".venv" not in f.parts
        ]

        if not test_files:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.CRITICAL,
                title="No test files found",
                recommendation="Add tests under a tests/ directory (e.g. tests/test_*.py).",
                data={"test_files": 0},
            ))
            return _result(findings, start)

        findings.append(Finding(
            category=_CATEGORY,
            severity=Severity.INFO,
            title=f"{len(test_files)} test file(s) found",
            data={"test_files": len(test_files)},
        ))

        # Read pytest cache
        cache_dir = self._root / _CACHE_DIR
        if not cache_dir.exists():
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.INFO,
                title="Tests have not been run yet",
                recommendation="Run `pytest` to verify test health.",
            ))
            return _result(findings, start)

        # Total tests collected (from last run)
        nodeids_path = cache_dir / _CACHE_NODEIDS
        total_tests = 0
        if nodeids_path.exists():
            try:
                nodeids = json.loads(nodeids_path.read_text())
                total_tests = len(nodeids)
            except (json.JSONDecodeError, OSError):
                pass

        # Last-failed tests
        lastfailed_path = cache_dir / _CACHE_LASTFAILED
        failed_tests: list[str] = []
        if lastfailed_path.exists():
            try:
                data = json.loads(lastfailed_path.read_text())
                failed_tests = list(data.keys())
            except (json.JSONDecodeError, OSError):
                pass

        if failed_tests:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.CRITICAL,
                title=f"{len(failed_tests)} test(s) failed in last run",
                detail="\n".join(failed_tests[:20]),
                recommendation="Fix failing tests: run `pytest --tb=short` for details.",
                data={"failed_tests": len(failed_tests), "failed_nodeids": failed_tests[:10]},
            ))
        elif total_tests > 0:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.OK,
                title=f"All {total_tests} test(s) passed in last run",
                data={"total_tests": total_tests},
            ))
        else:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.INFO,
                title="Tests were collected but run results unavailable",
                recommendation="Run `pytest` to capture current test status.",
            ))

        # Coverage configuration
        findings.extend(_check_coverage_config(self._root))

        return _result(findings, start)


def _check_coverage_config(root: Path) -> list[Finding]:
    """Check whether coverage is configured and whether a .coverage file exists."""
    findings: list[Finding] = []

    pyproject = root / "pyproject.toml"
    has_cov_config = False
    if pyproject.exists():
        text = pyproject.read_text(encoding="utf-8", errors="replace")
        has_cov_config = "[tool.coverage" in text

    if not has_cov_config:
        findings.append(Finding(
            category=_CATEGORY,
            severity=Severity.INFO,
            title="No coverage configuration found",
            recommendation="Add [tool.coverage.run] to pyproject.toml.",
        ))
        return findings

    coverage_file = root / ".coverage"
    if coverage_file.exists():
        findings.append(Finding(
            category=_CATEGORY,
            severity=Severity.OK,
            title="Coverage data present (.coverage file found)",
            recommendation="Run `pytest --cov` to refresh coverage data.",
        ))
    else:
        findings.append(Finding(
            category=_CATEGORY,
            severity=Severity.INFO,
            title="Coverage configured but no .coverage file found",
            recommendation="Run `pytest --cov` to generate coverage data.",
        ))

    return findings


def _result(findings: list[Finding], start: float) -> AnalyzerResult:
    return AnalyzerResult(
        name=TestAnalyzer.NAME,
        findings=findings,
        duration_ms=(time.monotonic() - start) * 1000,
    )
