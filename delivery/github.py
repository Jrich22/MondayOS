"""Strict GitHub pull-request delivery through the authenticated gh CLI."""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from delivery.backends import ToolAvailability, _controller_env, _run_process

_GITHUB_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


@dataclass(frozen=True)
class PullRequestResult:
    success: bool
    url: str = ""
    number: int = 0
    message: str = ""


@dataclass(frozen=True)
class _VerifiedPullRequest:
    """Exact GitHub identity proven by a follow-up API read."""

    url: str
    number: int


class GhCliClient:
    """Create or reconcile one PR; never merge, close, or mutate another PR."""

    def __init__(self, executable: str | None = None) -> None:
        self._executable = executable or shutil.which("gh") or ""

    def availability(self) -> ToolAvailability:
        if not self._executable:
            return ToolAvailability(False, "github", "GitHub CLI is not installed")
        result = _run_process(
            [self._executable, "auth", "status", "--hostname", "github.com"],
            cwd=Path.cwd(),
            timeout=20,
            env=_controller_env(),
        )
        return ToolAvailability(
            result.returncode == 0,
            "github",
            "authenticated" if result.returncode == 0 else "GitHub CLI authentication failed",
        )

    def ensure_pull_request(
        self,
        *,
        repo_root: Path,
        repository: str,
        base: str,
        branch: str,
        title: str,
        body: str,
        expected_sha: str,
        timeout: float = 60,
    ) -> PullRequestResult:
        if not _GITHUB_REPO.fullmatch(repository):
            return PullRequestResult(False, message="Invalid GitHub repository identity")
        if not _COMMIT_SHA.fullmatch(expected_sha):
            return PullRequestResult(False, message="Invalid reviewed commit identity")
        existing = _run_process(
            [
                self._executable,
                "pr",
                "list",
                "--repo",
                repository,
                "--head",
                branch,
                "--state",
                "all",
                "--limit",
                "1",
                "--json",
                "number,url,state,baseRefName,headRefName,headRefOid,isCrossRepository",
            ],
            cwd=repo_root,
            timeout=timeout,
            env=_controller_env(),
        )
        if existing.returncode != 0:
            return PullRequestResult(False, message=existing.stderr or "Could not query GitHub")
        try:
            rows = json.loads(existing.stdout or "[]")
        except json.JSONDecodeError:
            return PullRequestResult(False, message="GitHub returned malformed PR data")
        if rows:
            row = rows[0]
            verified = _verified_pr(
                row,
                repository=repository,
                base=base,
                branch=branch,
                expected_sha=expected_sha,
            )
            if verified is None:
                return PullRequestResult(
                    False,
                    message="Existing pull request does not match the reviewed artifact",
                )
            return PullRequestResult(
                True,
                url=verified.url,
                number=verified.number,
                message=f"Reused existing PR #{verified.number}",
            )

        created = _run_process(
            [
                self._executable,
                "pr",
                "create",
                "--repo",
                repository,
                "--base",
                base,
                "--head",
                branch,
                "--title",
                title,
                "--body",
                body,
            ],
            cwd=repo_root,
            timeout=timeout,
            env=_controller_env(),
        )
        if created.returncode != 0:
            return PullRequestResult(False, message=created.stderr or "Could not create PR")
        url = created.stdout.strip().splitlines()[-1] if created.stdout.strip() else ""
        expected_prefix = f"https://github.com/{repository}/pull/"
        if not url.startswith(expected_prefix):
            return PullRequestResult(False, message="GitHub returned no valid PR URL")
        inspected = _run_process(
            [
                self._executable,
                "pr",
                "view",
                url,
                "--repo",
                repository,
                "--json",
                "number,url,state,baseRefName,headRefName,headRefOid,isCrossRepository",
            ],
            cwd=repo_root,
            timeout=timeout,
            env=_controller_env(),
        )
        if inspected.returncode != 0:
            return PullRequestResult(False, message="Could not verify the created PR")
        try:
            row = json.loads(inspected.stdout or "{}")
        except json.JSONDecodeError:
            return PullRequestResult(False, message="GitHub returned malformed PR verification")
        verified = _verified_pr(
            row,
            repository=repository,
            base=base,
            branch=branch,
            expected_sha=expected_sha,
        )
        if verified is None:
            return PullRequestResult(
                False,
                message="Created PR did not match the reviewed artifact",
            )
        return PullRequestResult(
            True,
            url=verified.url,
            number=verified.number,
            message=f"Opened PR #{verified.number}",
        )


def _verified_pr(
    row: object,
    *,
    repository: str,
    base: str,
    branch: str,
    expected_sha: str,
) -> _VerifiedPullRequest | None:
    if not isinstance(row, dict):
        return None
    url = str(row.get("url") or "")
    number = row.get("number")
    if isinstance(number, bool) or not isinstance(number, int):
        return None
    exact_number = number
    if (
        exact_number <= 0
        or row.get("state") != "OPEN"
        or row.get("baseRefName") != base
        or row.get("headRefName") != branch
        or row.get("headRefOid") != expected_sha
        or row.get("isCrossRepository") is not False
        or url != f"https://github.com/{repository}/pull/{exact_number}"
    ):
        return None
    return _VerifiedPullRequest(url=url, number=exact_number)
