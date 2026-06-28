"""GitAnalyzer — inspects repository git state."""
from __future__ import annotations

import subprocess
import time

from doctor.base import BaseAnalyzer
from doctor.finding import Finding, Severity
from doctor.result import AnalyzerResult

_CATEGORY = "git"


class GitAnalyzer(BaseAnalyzer):
    """Checks: repo presence, branch, dirty tree, recent commit history."""

    NAME = "git"

    def analyze(self) -> AnalyzerResult:
        start = time.monotonic()
        findings: list[Finding] = []

        if not (self._root / ".git").exists():
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.CRITICAL,
                title="Not a git repository",
                detail=f"{self._root} contains no .git directory.",
                recommendation="Run `git init` to initialise version control.",
            ))
            return _result(findings, start)

        # Branch
        branch = self._git("rev-parse", "--abbrev-ref", "HEAD")
        if branch:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.INFO,
                title=f"Current branch: {branch}",
                data={"branch": branch},
            ))

        # Dirty working tree
        porcelain = self._git("status", "--porcelain")
        if porcelain:
            changed = [ln for ln in porcelain.splitlines() if ln.strip()]
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.WARNING,
                title=f"Dirty working tree — {len(changed)} uncommitted change(s)",
                detail="\n".join(changed[:15]),
                recommendation="Commit or stash all changes before deployment.",
                data={"changed_files": len(changed)},
            ))
        else:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.OK,
                title="Working tree is clean",
            ))

        # Recent commits
        log = self._git("log", "--oneline", "-10")
        if log:
            commits = log.splitlines()
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.INFO,
                title=f"{len(commits)} recent commit(s)",
                detail="\n".join(commits),
                data={"recent_commits": commits},
            ))
        else:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.WARNING,
                title="No commits found",
                recommendation="Make an initial commit to begin tracking history.",
            ))

        # Unpushed commits (best-effort — may fail if no remote)
        unpushed = self._git("log", "@{u}..", "--oneline")
        if unpushed:
            n = len(unpushed.splitlines())
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.INFO,
                title=f"{n} unpushed commit(s) ahead of upstream",
                detail=unpushed,
                recommendation="Run `git push` to sync with remote.",
                data={"unpushed": n},
            ))

        return _result(findings, start)

    def _git(self, *args: str) -> str:
        try:
            r = subprocess.run(
                ["git", "-C", str(self._root), *args],
                capture_output=True, text=True, timeout=10,
            )
            return r.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""


def _result(findings: list[Finding], start: float) -> AnalyzerResult:
    return AnalyzerResult(
        name=GitAnalyzer.NAME,
        findings=findings,
        duration_ms=(time.monotonic() - start) * 1000,
    )
