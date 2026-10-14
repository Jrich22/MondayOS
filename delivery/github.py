"""Strict GitHub pull-request delivery through the authenticated gh CLI."""
from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from delivery.backends import (
    ToolAvailability,
    _controller_env,
    _resolve_trusted_executable,
    _run_process,
    _validated_executable_for_target,
)

_GITHUB_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_GIT_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/+\-]{0,254}$")
_MAX_STATUS_CHECKS = 500
_STATUS_CHECK_PAGE_SIZE = 100
_MAX_STATUS_CHECK_PAGES = _MAX_STATUS_CHECKS // _STATUS_CHECK_PAGE_SIZE
_GITHUB_API_VERSION = "2022-11-28"
_CHECK_RUNS_JQ = (
    "{total_count, check_runs: [.check_runs[] | "
    "{id, head_sha, status, conclusion}]}"
)
_COMMIT_STATUSES_JQ = (
    "{sha, state, total_count, statuses: [.statuses[] | {id, state}]}"
)

_PR_OBSERVATION_FIELDS = (
    "number",
    "url",
    "state",
    "mergeCommit",
    "baseRefName",
    "headRefName",
    "headRefOid",
    "isCrossRepository",
)

PullRequestLifecycle = Literal["open", "merged", "closed", "diverged", "unavailable"]
PullRequestCheckState = Literal["pending", "passed", "failed", "no-checks"]


@dataclass(frozen=True)
class PullRequestResult:
    success: bool
    url: str = ""
    number: int = 0
    message: str = ""
    failure_class: str = ""
    retry_after_seconds: int = 0


@dataclass(frozen=True)
class PullRequestObservation:
    """Bounded, read-only observation of one exact pull-request artifact."""

    success: bool
    lifecycle: PullRequestLifecycle = "unavailable"
    checks: PullRequestCheckState = "no-checks"
    merge_commit_sha: str = ""
    total_checks: int = 0
    pending_checks: int = 0
    passed_checks: int = 0
    failed_checks: int = 0
    message: str = ""


@dataclass(frozen=True)
class _VerifiedPullRequest:
    """Exact GitHub identity proven by a follow-up API read."""

    url: str
    number: int


@dataclass(frozen=True)
class _CheckRollupSummary:
    state: PullRequestCheckState
    total: int
    pending: int
    passed: int
    failed: int


@dataclass(frozen=True)
class _RestCheckPage:
    checks: list[object]
    total_checks: int
    identifiers: frozenset[int]
    aggregate_state: str = ""


class GhCliClient:
    """Create or reconcile one PR; never merge, close, or mutate another PR."""

    def __init__(self, executable: str | None = None) -> None:
        self._trusted_executable = _resolve_trusted_executable("gh", executable)
        self._executable = (
            self._trusted_executable.path if self._trusted_executable is not None else ""
        )

    def availability(self) -> ToolAvailability:
        try:
            executable = _validated_executable_for_target(
                self._trusted_executable,
                None,
                "GitHub",
            )
        except OSError:
            return ToolAvailability(False, "github", "GitHub CLI is not installed")
        result = _run_process(
            [executable, "auth", "status", "--hostname", "github.com"],
            cwd=Path.cwd(),
            timeout=20,
            env=_controller_env(),
        )
        return ToolAvailability(
            result.returncode == 0,
            "github",
            "authenticated" if result.returncode == 0 else "GitHub CLI authentication failed",
        )

    def observe_pull_request(
        self,
        *,
        repo_root: Path,
        repository: str,
        base: str,
        branch: str,
        number: int,
        url: str,
        expected_sha: str,
        timeout: float = 60,
    ) -> PullRequestObservation:
        """Observe one exact PR and its aggregate checks without mutating GitHub."""
        if not _valid_repository(repository):
            return _unavailable_observation("Invalid GitHub repository identity")
        if not _valid_branch(base) or not _valid_branch(branch):
            return _unavailable_observation("Invalid pull-request branch identity")
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            return _unavailable_observation("Invalid pull-request number")
        if not isinstance(url, str) or _pr_url_number(url, repository) != number:
            return _unavailable_observation("Invalid pull-request URL identity")
        if not isinstance(expected_sha, str) or not _COMMIT_SHA.fullmatch(expected_sha):
            return _unavailable_observation("Invalid reviewed commit identity")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
            return _unavailable_observation("Invalid GitHub observation timeout")
        try:
            observation_timeout = float(timeout)
        except OverflowError:
            return _unavailable_observation("Invalid GitHub observation timeout")
        if not math.isfinite(observation_timeout) or observation_timeout <= 0:
            return _unavailable_observation("Invalid GitHub observation timeout")
        try:
            executable = _validated_executable_for_target(
                self._trusted_executable,
                repo_root,
                "GitHub",
            )
        except OSError as exc:
            return _unavailable_observation(str(exc))

        deadline = time.monotonic() + observation_timeout

        def read_json(arguments: list[str]) -> tuple[object | None, str]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, "GitHub observation timed out"
            observed = _run_process(
                [executable, *arguments],
                cwd=repo_root,
                timeout=remaining,
                env=_controller_env(),
            )
            if observed.returncode != 0:
                return None, "Could not observe GitHub pull request"
            try:
                return json.loads(observed.stdout or "{}"), ""
            except json.JSONDecodeError:
                return None, "GitHub returned malformed PR observation"

        identity_arguments = [
            "pr",
            "view",
            url,
            "--repo",
            repository,
            "--json",
            ",".join(_PR_OBSERVATION_FIELDS),
        ]
        initial_payload, error = read_json(identity_arguments)
        if error:
            return _unavailable_observation(error)
        initial_row = _validated_observation_row(initial_payload)
        if initial_row is None:
            return _unavailable_observation("GitHub returned malformed PR observation")
        initial = _observation_from_row(
            {**initial_row, "statusCheckRollup": []},
            repository=repository,
            base=base,
            branch=branch,
            number=number,
            url=url,
            expected_sha=expected_sha,
        )
        if not initial.success or initial.lifecycle == "diverged":
            return initial

        owner, name = repository.split("/", 1)
        endpoint_root = f"repos/{owner}/{name}/commits/{expected_sha}"
        checks: list[object] = []
        check_ids: set[int] = set()
        check_total: int | None = None
        for page_number in range(1, _MAX_STATUS_CHECK_PAGES + 1):
            payload, error = read_json(
                [
                    "api",
                    f"{endpoint_root}/check-runs",
                    "--method",
                    "GET",
                    "-H",
                    "Accept: application/vnd.github+json",
                    "-H",
                    f"X-GitHub-Api-Version: {_GITHUB_API_VERSION}",
                    "-F",
                    "filter=latest",
                    "-F",
                    f"per_page={_STATUS_CHECK_PAGE_SIZE}",
                    "-F",
                    f"page={page_number}",
                    "--jq",
                    _CHECK_RUNS_JQ,
                ]
            )
            if error:
                return _unavailable_observation(error)
            page = _check_run_page_from_payload(payload, expected_sha=expected_sha)
            if page is None:
                return _unavailable_observation("GitHub returned malformed check-run data")
            if check_total is None:
                check_total = page.total_checks
            elif page.total_checks != check_total:
                return _unavailable_observation(
                    "GitHub check-run count changed during observation"
                )
            if check_ids.intersection(page.identifiers):
                return _unavailable_observation("GitHub returned duplicate check runs")
            check_ids.update(page.identifiers)
            checks.extend(page.checks)
            if len(checks) > page.total_checks:
                return _unavailable_observation("GitHub returned too many check runs")
            if len(checks) == page.total_checks:
                break
            if len(page.checks) != _STATUS_CHECK_PAGE_SIZE:
                return _unavailable_observation("GitHub returned incomplete check-run data")
        else:
            return _unavailable_observation("GitHub returned incomplete check-run data")

        status_checks: list[object] = []
        status_ids: set[int] = set()
        status_total: int | None = None
        combined_status = ""
        for page_number in range(1, _MAX_STATUS_CHECK_PAGES + 1):
            payload, error = read_json(
                [
                    "api",
                    f"{endpoint_root}/status",
                    "--method",
                    "GET",
                    "-H",
                    "Accept: application/vnd.github+json",
                    "-H",
                    f"X-GitHub-Api-Version: {_GITHUB_API_VERSION}",
                    "-F",
                    f"per_page={_STATUS_CHECK_PAGE_SIZE}",
                    "-F",
                    f"page={page_number}",
                    "--jq",
                    _COMMIT_STATUSES_JQ,
                ]
            )
            if error:
                return _unavailable_observation(error)
            page = _commit_status_page_from_payload(payload, expected_sha=expected_sha)
            if page is None:
                return _unavailable_observation("GitHub returned malformed commit-status data")
            if status_total is None:
                status_total = page.total_checks
                combined_status = page.aggregate_state
                if len(checks) + status_total > _MAX_STATUS_CHECKS:
                    return _unavailable_observation(
                        "Pull request has too many checks for bounded observation"
                    )
            elif (
                page.total_checks != status_total
                or page.aggregate_state != combined_status
            ):
                return _unavailable_observation(
                    "GitHub commit-status summary changed during observation"
                )
            if status_ids.intersection(page.identifiers):
                return _unavailable_observation("GitHub returned duplicate commit statuses")
            status_ids.update(page.identifiers)
            status_checks.extend(page.checks)
            if len(status_checks) > page.total_checks:
                return _unavailable_observation("GitHub returned too many commit statuses")
            if len(status_checks) == page.total_checks:
                break
            if len(page.checks) != _STATUS_CHECK_PAGE_SIZE:
                return _unavailable_observation(
                    "GitHub returned incomplete commit-status data"
                )
        else:
            return _unavailable_observation("GitHub returned incomplete commit-status data")

        status_summary = _summarize_check_rollup(status_checks)
        expected_combined_status = _expected_combined_status(status_summary)
        if status_summary is None or combined_status != expected_combined_status:
            return _unavailable_observation(
                "GitHub returned an inconsistent commit-status summary"
            )
        checks.extend(status_checks)

        final_payload, error = read_json(identity_arguments)
        if error:
            return _unavailable_observation(error)
        row = _validated_observation_row(final_payload)
        if row is None:
            return _unavailable_observation("GitHub returned malformed PR observation")
        if row.get("headRefOid") != expected_sha:
            return _unavailable_observation(
                "Pull-request head changed during GitHub observation"
            )
        row["statusCheckRollup"] = checks
        return _observation_from_row(
            row,
            repository=repository,
            base=base,
            branch=branch,
            number=number,
            url=url,
            expected_sha=expected_sha,
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
        inherited_guard_fds: tuple[int, ...] = (),
    ) -> PullRequestResult:
        try:
            executable = _validated_executable_for_target(
                self._trusted_executable,
                repo_root,
                "GitHub",
            )
        except OSError as exc:
            return PullRequestResult(False, message=str(exc))
        if not _GITHUB_REPO.fullmatch(repository):
            return PullRequestResult(False, message="Invalid GitHub repository identity")
        if not _COMMIT_SHA.fullmatch(expected_sha):
            return PullRequestResult(False, message="Invalid reviewed commit identity")
        existing = _run_process(
            [
                executable,
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
                executable,
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
            inherited_guard_fds=inherited_guard_fds,
        )
        if created.returncode != 0:
            return PullRequestResult(False, message=created.stderr or "Could not create PR")
        url = created.stdout.strip().splitlines()[-1] if created.stdout.strip() else ""
        if _pr_url_number(url, repository) is None:
            return PullRequestResult(False, message="GitHub returned no valid PR URL")
        inspected = _run_process(
            [
                executable,
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


def _validated_observation_row(payload: object) -> dict[str, object] | None:
    """Require every identity field requested from ``gh pr view``."""
    if not isinstance(payload, dict) or any(
        field not in payload for field in _PR_OBSERVATION_FIELDS
    ):
        return None
    return {field: payload[field] for field in _PR_OBSERVATION_FIELDS}


def _check_run_page_from_payload(
    payload: object,
    *,
    expected_sha: str,
) -> _RestCheckPage | None:
    if not isinstance(payload, dict) or not {"total_count", "check_runs"} <= payload.keys():
        return None
    total = payload["total_count"]
    raw_checks = payload["check_runs"]
    if not _valid_rest_page_shape(total, raw_checks):
        return None
    checks: list[object] = []
    identifiers: set[int] = set()
    assert isinstance(raw_checks, list)
    for raw_check in raw_checks:
        if not isinstance(raw_check, dict) or not {
            "id",
            "head_sha",
            "status",
            "conclusion",
        } <= raw_check.keys():
            return None
        identifier = raw_check["id"]
        status = raw_check["status"]
        conclusion = raw_check["conclusion"]
        if (
            isinstance(identifier, bool)
            or not isinstance(identifier, int)
            or identifier <= 0
            or identifier in identifiers
            or raw_check["head_sha"] != expected_sha
            or not isinstance(status, str)
            or (conclusion is not None and not isinstance(conclusion, str))
        ):
            return None
        normalized: dict[str, object] = {
            "__typename": "CheckRun",
            "status": status.upper(),
            "conclusion": conclusion.upper() if isinstance(conclusion, str) else None,
        }
        if _classify_check(normalized) is None:
            return None
        identifiers.add(identifier)
        checks.append(normalized)
    assert isinstance(total, int)
    return _RestCheckPage(checks, total, frozenset(identifiers))


def _commit_status_page_from_payload(
    payload: object,
    *,
    expected_sha: str,
) -> _RestCheckPage | None:
    if not isinstance(payload, dict) or not {
        "sha",
        "state",
        "total_count",
        "statuses",
    } <= payload.keys():
        return None
    total = payload["total_count"]
    raw_checks = payload["statuses"]
    aggregate_state = payload["state"]
    if (
        payload["sha"] != expected_sha
        or not isinstance(aggregate_state, str)
        or aggregate_state not in {"pending", "success", "failure"}
        or not _valid_rest_page_shape(total, raw_checks)
    ):
        return None
    checks: list[object] = []
    identifiers: set[int] = set()
    assert isinstance(raw_checks, list)
    for raw_check in raw_checks:
        if not isinstance(raw_check, dict) or not {"id", "state"} <= raw_check.keys():
            return None
        identifier = raw_check["id"]
        state = raw_check["state"]
        if (
            isinstance(identifier, bool)
            or not isinstance(identifier, int)
            or identifier <= 0
            or identifier in identifiers
            or not isinstance(state, str)
        ):
            return None
        normalized: dict[str, object] = {
            "__typename": "StatusContext",
            "state": state.upper(),
        }
        if _classify_check(normalized) is None:
            return None
        identifiers.add(identifier)
        checks.append(normalized)
    assert isinstance(total, int)
    return _RestCheckPage(
        checks,
        total,
        frozenset(identifiers),
        aggregate_state=aggregate_state,
    )


def _valid_rest_page_shape(total: object, checks: object) -> bool:
    return (
        not isinstance(total, bool)
        and isinstance(total, int)
        and 0 <= total <= _MAX_STATUS_CHECKS
        and isinstance(checks, list)
        and len(checks) <= _STATUS_CHECK_PAGE_SIZE
    )


def _expected_combined_status(summary: _CheckRollupSummary | None) -> str:
    if summary is None:
        return ""
    if summary.failed:
        return "failure"
    if summary.pending or summary.total == 0:
        return "pending"
    return "success"


def _observation_identity_error(
    row: object,
    *,
    repository: str,
    base: str,
    branch: str,
    number: int,
    url: str,
) -> str:
    if not isinstance(row, dict):
        return "GitHub returned malformed PR observation"
    observed_number = row.get("number")
    observed_url = row.get("url")
    if (
        isinstance(observed_number, bool)
        or not isinstance(observed_number, int)
        or observed_number != number
        or not isinstance(observed_url, str)
        or observed_url.casefold() != url.casefold()
        or _pr_url_number(observed_url, repository) != number
        or row.get("baseRefName") != base
        or row.get("headRefName") != branch
        or row.get("isCrossRepository") is not False
    ):
        return "Observed pull request does not match the requested identity"
    observed_sha = row.get("headRefOid")
    if not isinstance(observed_sha, str) or not _COMMIT_SHA.fullmatch(observed_sha):
        return "GitHub returned malformed PR commit identity"
    return ""


def _observation_from_row(
    row: object,
    *,
    repository: str,
    base: str,
    branch: str,
    number: int,
    url: str,
    expected_sha: str,
) -> PullRequestObservation:
    identity_error = _observation_identity_error(
        row,
        repository=repository,
        base=base,
        branch=branch,
        number=number,
        url=url,
    )
    if identity_error:
        return _unavailable_observation(identity_error)
    assert isinstance(row, dict)
    observed_sha = str(row["headRefOid"])
    checks = _summarize_check_rollup(row.get("statusCheckRollup"))
    if checks is None:
        return _unavailable_observation("GitHub returned malformed PR check data")

    raw_state = row.get("state")
    lifecycle: PullRequestLifecycle
    if raw_state == "OPEN":
        lifecycle = "open"
    elif raw_state == "MERGED":
        lifecycle = "merged"
    elif raw_state == "CLOSED":
        lifecycle = "closed"
    else:
        return _unavailable_observation("GitHub returned an unknown PR lifecycle")

    merge_commit_sha = ""
    raw_merge_commit = row.get("mergeCommit")
    if lifecycle == "merged":
        if not isinstance(raw_merge_commit, dict):
            return _unavailable_observation("GitHub returned no valid merge commit identity")
        raw_merge_sha = raw_merge_commit.get("oid")
        if not isinstance(raw_merge_sha, str) or not _COMMIT_SHA.fullmatch(raw_merge_sha):
            return _unavailable_observation("GitHub returned no valid merge commit identity")
        merge_commit_sha = raw_merge_sha
    elif raw_merge_commit is not None:
        return _unavailable_observation("GitHub returned an inconsistent merge identity")

    if observed_sha != expected_sha:
        return _successful_observation(
            lifecycle="diverged",
            checks=checks,
            message="Pull-request head diverged from the reviewed commit",
        )

    return _successful_observation(
        lifecycle=lifecycle,
        checks=checks,
        merge_commit_sha=merge_commit_sha,
        message=f"Observed {lifecycle} PR #{number}",
    )


def _successful_observation(
    *,
    lifecycle: PullRequestLifecycle,
    checks: _CheckRollupSummary,
    message: str,
    merge_commit_sha: str = "",
) -> PullRequestObservation:
    return PullRequestObservation(
        True,
        lifecycle=lifecycle,
        checks=checks.state,
        merge_commit_sha=merge_commit_sha,
        total_checks=checks.total,
        pending_checks=checks.pending,
        passed_checks=checks.passed,
        failed_checks=checks.failed,
        message=message,
    )


def _unavailable_observation(message: str) -> PullRequestObservation:
    return PullRequestObservation(False, message=message)


def _summarize_check_rollup(raw_rollup: object) -> _CheckRollupSummary | None:
    if not isinstance(raw_rollup, list) or len(raw_rollup) > _MAX_STATUS_CHECKS:
        return None
    if not raw_rollup:
        return _CheckRollupSummary("no-checks", 0, 0, 0, 0)

    pending = 0
    passed = 0
    failed = 0
    for raw_check in raw_rollup:
        state = _classify_check(raw_check)
        if state is None:
            return None
        if state == "pending":
            pending += 1
        elif state == "passed":
            passed += 1
        else:
            failed += 1
    aggregate: PullRequestCheckState
    if failed:
        aggregate = "failed"
    elif pending:
        aggregate = "pending"
    else:
        aggregate = "passed"
    return _CheckRollupSummary(aggregate, len(raw_rollup), pending, passed, failed)


def _classify_check(raw_check: object) -> Literal["pending", "passed", "failed"] | None:
    if not isinstance(raw_check, dict):
        return None
    check_type = raw_check.get("__typename")
    if check_type == "CheckRun":
        status = raw_check.get("status")
        if status in {"QUEUED", "IN_PROGRESS", "WAITING", "PENDING", "REQUESTED"}:
            return "pending"
        if status != "COMPLETED":
            return None
        conclusion = raw_check.get("conclusion")
        if conclusion in {"SUCCESS", "NEUTRAL", "SKIPPED"}:
            return "passed"
        if conclusion in {
            "FAILURE",
            "CANCELLED",
            "TIMED_OUT",
            "ACTION_REQUIRED",
            "STALE",
            "STARTUP_FAILURE",
        }:
            return "failed"
        return None
    if check_type == "StatusContext":
        state = raw_check.get("state")
        if state == "SUCCESS":
            return "passed"
        if state in {"PENDING", "EXPECTED"}:
            return "pending"
        if state in {"FAILURE", "ERROR"}:
            return "failed"
    return None


def _valid_branch(value: object) -> bool:
    if not isinstance(value, str) or not _GIT_BRANCH.fullmatch(value):
        return False
    if value.endswith(("/", ".", ".lock")) or ".." in value or "//" in value:
        return False
    return all(part and not part.startswith(".") for part in value.split("/"))


def _valid_repository(value: object) -> bool:
    if not isinstance(value, str) or not _GITHUB_REPO.fullmatch(value):
        return False
    owner, repository = value.split("/", 1)
    return owner not in {".", ".."} and repository not in {".", ".."}


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
        or _pr_url_number(url, repository) != exact_number
    ):
        return None
    return _VerifiedPullRequest(url=url, number=exact_number)


def _pr_url_number(url: str, repository: str) -> int | None:
    """Validate one exact github.com PR URL and return its positive number."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.netloc.casefold() != "github.com"
        or parsed.hostname is None
        or parsed.hostname.casefold() != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    parts = parsed.path.split("/")
    if len(parts) != 5 or parts[0] or parts[3] != "pull":
        return None
    expected_owner, expected_repo = repository.split("/", 1)
    owner, repo, raw_number = parts[1], parts[2], parts[4]
    # The split must end at the number with no trailing slash or extra segment.
    if owner.casefold() != expected_owner.casefold():
        return None
    if repo.casefold() != expected_repo.casefold() or not raw_number.isascii():
        return None
    if not raw_number.isdigit():
        return None
    number = int(raw_number)
    return number if number > 0 and raw_number == str(number) else None
