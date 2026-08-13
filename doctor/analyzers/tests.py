"""TestAnalyzer — inspects test coverage and the last verified run result."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from doctor.base import BaseAnalyzer
from doctor.finding import Finding, Severity
from doctor.result import AnalyzerResult

_CATEGORY = "tests"
_CACHE_DIR = ".pytest_cache"
_CACHE_NODEIDS = "v/cache/nodeids"
_CACHE_LASTFAILED = "v/cache/lastfailed"

# Verified run record written by pytest_terminal_summary in the root conftest.py.
_RUN_RECORD = "v/mondayos/last_run"

_EXCLUDE_PARTS = {"__pycache__", ".venv", ".git", "node_modules", ".mypy_cache", ".pytest_cache"}


class TestAnalyzer(BaseAnalyzer):
    """
    Checks test file presence, the result of the most recent pytest run, and
    coverage configuration.

    Does NOT execute pytest — it reads the run record the root conftest.py
    writes on every run. That record is authoritative because it describes one
    specific invocation. pytest's own `lastfailed` cache is only a fallback:
    it is a set of node ids to retry, not a run result, and pytest only removes
    an entry when that exact node id is re-run and passes. Failures for tests
    that were later renamed or deleted therefore stay in it permanently and
    would otherwise be reported as current failures forever.
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

        cache_dir = self._root / _CACHE_DIR
        if not cache_dir.exists():
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.INFO,
                title="Tests have not been run yet",
                recommendation="Run `pytest` to verify test health.",
            ))
            return _result(findings, start)

        record = _read_json(cache_dir / _RUN_RECORD)
        if isinstance(record, dict):
            findings.extend(_findings_from_run_record(record, self._root))
        else:
            findings.extend(_findings_from_lastfailed(cache_dir, self._root))

        # Coverage configuration
        findings.extend(_check_coverage_config(self._root))

        return _result(findings, start)


# ---------------------------------------------------------------------------
# Verified run record (preferred)
# ---------------------------------------------------------------------------

def _findings_from_run_record(record: dict[str, Any], root: Path) -> list[Finding]:
    """Build findings from the run record written by the last pytest invocation."""
    findings: list[Finding] = []

    passed = _as_int(record.get("passed"))
    skipped = _as_int(record.get("skipped"))
    failed = _as_int(record.get("failed")) + _as_int(record.get("errors"))
    failed_nodeids = [str(n) for n in record.get("failed_nodeids", []) if n]
    finished_at = str(record.get("finished_at", ""))

    if failed:
        findings.append(Finding(
            category=_CATEGORY,
            severity=Severity.CRITICAL,
            title=f"{failed} test(s) failed in last run",
            detail="\n".join(failed_nodeids[:20]),
            recommendation="Fix failing tests: run `pytest --tb=short` for details.",
            data={
                "failed_tests": failed,
                "failed_nodeids": failed_nodeids[:10],
                "finished_at": finished_at,
            },
        ))
    else:
        detail = f"{passed} passed, {skipped} skipped"
        if finished_at:
            detail += f" (run finished {finished_at})"
        findings.append(Finding(
            category=_CATEGORY,
            severity=Severity.OK,
            title=f"All {passed} test(s) passed in last run",
            detail=detail,
            data={
                "total_tests": passed + skipped,
                "passed": passed,
                "skipped": skipped,
                "finished_at": finished_at,
            },
        ))

    if not record.get("full_run", True):
        findings.append(Finding(
            category=_CATEGORY,
            severity=Severity.INFO,
            title="Last run covered only part of the suite",
            recommendation="Run `pytest` with no filters to verify the whole suite.",
            data={"full_run": False},
        ))

    findings.extend(_check_staleness(finished_at, root))
    return findings


def _check_staleness(finished_at: str, root: Path) -> list[Finding]:
    """Warn when source files changed after the recorded run finished."""
    run_ts = _parse_timestamp(finished_at)
    if run_ts is None:
        return []

    changed = [p for p in _collect_py_files(root) if p.stat().st_mtime > run_ts]
    if not changed:
        return []

    return [Finding(
        category=_CATEGORY,
        severity=Severity.INFO,
        title=f"{len(changed)} source file(s) changed since the last test run",
        detail="\n".join(str(p.relative_to(root)) for p in changed[:20]),
        recommendation="Re-run `pytest` to re-verify test health against current code.",
        data={"changed_files": len(changed)},
    )]


# ---------------------------------------------------------------------------
# pytest lastfailed cache (fallback only)
# ---------------------------------------------------------------------------

def _findings_from_lastfailed(cache_dir: Path, root: Path) -> list[Finding]:
    """
    Fall back to pytest's retry cache when no run record exists.

    Node ids naming tests that no longer exist are dropped: pytest can never
    clear those itself, so keeping them would report deleted or renamed tests
    as current failures.
    """
    findings: list[Finding] = []

    nodeids = _read_json(cache_dir / _CACHE_NODEIDS)
    total_tests = len(nodeids) if isinstance(nodeids, list) else 0

    lastfailed = _read_json(cache_dir / _CACHE_LASTFAILED)
    all_failed = list(lastfailed) if isinstance(lastfailed, dict) else []
    failed_tests = [n for n in all_failed if _test_still_exists(str(n), root)]
    stale = len(all_failed) - len(failed_tests)

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

    if stale:
        findings.append(Finding(
            category=_CATEGORY,
            severity=Severity.INFO,
            title=f"{stale} stale failure record(s) ignored",
            detail="These node ids name tests that no longer exist.",
            recommendation="Run `pytest` to record a verified result.",
            data={"stale_records": stale},
        ))

    return findings


def _test_still_exists(nodeid: str, root: Path) -> bool:
    """
    True unless the node id definitively names a test that is gone.

    Conservative: anything unreadable or unparseable counts as still present,
    so a real failure is never hidden.
    """
    file_part, _, rest = nodeid.partition("::")
    if not rest:
        return True

    path = root / file_part
    if not path.is_file():
        return False

    func = rest.split("::")[-1].split("[")[0]
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True
    return f"def {func}(" in source


# ---------------------------------------------------------------------------
# Coverage + helpers
# ---------------------------------------------------------------------------

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


def _collect_py_files(root: Path) -> list[Path]:
    return [
        p for p in root.rglob("*.py")
        if not (_EXCLUDE_PARTS & set(p.relative_to(root).parts))
    ]


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _parse_timestamp(value: str) -> float | None:
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _as_int(value: Any) -> int:
    return value if isinstance(value, int) else 0


def _result(findings: list[Finding], start: float) -> AnalyzerResult:
    return AnalyzerResult(
        name=TestAnalyzer.NAME,
        findings=findings,
        duration_ms=(time.monotonic() - start) * 1000,
    )
