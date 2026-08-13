"""
Root pytest configuration.

Adds the project root to sys.path so all MondayOS packages are importable
without installation when running `pytest` from the project root.

Also records a verified result for every run into the pytest cache, so
`monday doctor` can report the outcome of the *latest* run instead of
inferring it from pytest's `lastfailed` cache. That cache is not a run
record: pytest only drops a node id from it when that exact node id is
re-run and passes, so failures for tests that were later renamed or
deleted persist there forever.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

# Cache key for the verified run record. pytest stores this at
# .pytest_cache/v/mondayos/last_run — see doctor/analyzers/tests.py.
RUN_RECORD_KEY = "mondayos/last_run"


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: Any) -> None:
    """Persist the outcome of this run for the `tests` health analyzer."""
    if config.getoption("collectonly", False):
        return

    cache = getattr(config, "cache", None)
    if cache is None:  # -p no:cacheprovider
        return

    stats = terminalreporter.stats
    failures = list(stats.get("failed", [])) + list(stats.get("error", []))

    cache.set(RUN_RECORD_KEY, {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "exit_status": int(exitstatus),
        "passed": len(stats.get("passed", [])),
        "failed": len(stats.get("failed", [])),
        "errors": len(stats.get("error", [])),
        "skipped": len(stats.get("skipped", [])),
        "xfailed": len(stats.get("xfailed", [])),
        "xpassed": len(stats.get("xpassed", [])),
        "deselected": len(stats.get("deselected", [])),
        "failed_nodeids": sorted({r.nodeid for r in failures})[:50],
        "full_run": _is_full_run(config, stats),
    })


def _is_full_run(config: Any, stats: dict[str, list[Any]]) -> bool:
    """
    True when this invocation exercised the whole default suite.

    A filtered or subset run is still a real result, but it cannot confirm
    the health of tests it never collected, so Doctor reports it differently.
    """
    option = config.option
    if getattr(option, "keyword", "") or getattr(option, "markexpr", ""):
        return False
    if getattr(option, "lf", False) or getattr(option, "failedfirst", False):
        return False
    if stats.get("deselected"):
        return False

    invocation_dir = Path(str(config.invocation_params.dir))
    testpaths = config.getini("testpaths") or [str(invocation_dir)]
    default_targets = {_resolve(p, invocation_dir) for p in testpaths}
    selected = {_resolve(a.split("::")[0], invocation_dir) for a in config.args}
    return selected == default_targets


def _resolve(target: str, base: Path) -> Path:
    return (base / target).resolve()
