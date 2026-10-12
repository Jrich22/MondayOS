"""Strict Git and worktree primitives for autonomous delivery.

This module is intentionally narrower than a general Git wrapper.  It operates
only on one explicitly trusted repository, creates job-owned worktrees, and
stages only caller-named text files after applying a conservative path policy.
Every child process is an argv vector; shell parsing is never involved.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO
from urllib.parse import urlparse

DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_OUTPUT = 256_000
DEFAULT_MAX_FILE_BYTES = 2_000_000
_SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_BRANCH_RE = re.compile(r"^codex/[a-z0-9][a-z0-9-]{0,79}-[0-9a-f]{8}$")
_OWNER_FILE_SUFFIX = ".owner.json"
_OWNER_SCHEMA = 2
_CONTAINED_VALIDATION_ENV = "MONDAYOS_INTERNAL_CONTAINED_VALIDATION"
_CHILD_ENV_ALLOWLIST = {
    "HOME",
    "LANG",
    "LC_ALL",
    "LOGNAME",
    "PATH",
    "SHELL",
    "SSH_AUTH_SOCK",
    "TMPDIR",
    "USER",
}


class GitError(RuntimeError):
    """Base class for a fail-closed delivery Git error."""


class GitValidationError(GitError):
    """The repository, worktree, or proposed path failed validation."""


class GitCommandError(GitError):
    """A checked argv-only command failed."""

    def __init__(self, result: CommandResult) -> None:
        self.result = result
        reason = "timed out" if result.timed_out else f"exited {result.returncode}"
        detail = result.stderr.strip() or result.stdout.strip()
        message = f"command {reason}: {result.display_argv}"
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)


@dataclass(frozen=True)
class CommandResult:
    """Bounded output and exact identity of one argv-only process."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    stdout_invalid_utf8: bool = False
    stderr_invalid_utf8: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    @property
    def display_argv(self) -> str:
        """A diagnostic representation; never suitable for execution."""
        return repr(list(self.argv))

    def raise_for_status(self) -> CommandResult:
        if not self.ok:
            raise GitCommandError(self)
        return self


@dataclass(frozen=True)
class BaseRevision:
    remote: str
    branch: str
    ref: str
    sha: str


@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: str
    base_sha: str
    owner_marker: Path
    owner_token: str


@dataclass(frozen=True)
class ChangedPath:
    path: str
    status: str
    original_path: str = ""


class _BoundedCollector:
    def __init__(self, stream: IO[bytes], limit: int) -> None:
        self._stream = stream
        self._limit = limit
        self._data = bytearray()
        self.truncated = False

    def read(self) -> None:
        try:
            while True:
                chunk = self._stream.read(65_536)
                if not chunk:
                    return
                room = self._limit - len(self._data)
                if room > 0:
                    self._data.extend(chunk[:room])
                if len(chunk) > room:
                    self.truncated = True
        except (OSError, ValueError):
            # A lingering descendant may keep a pipe open after its parent has
            # exited.  The runner closes that pipe after killing the process
            # group so capture can still finish within a bound.
            return

    def text(self) -> str:
        return bytes(self._data).decode("utf-8", errors="replace")

    @property
    def invalid_utf8(self) -> bool:
        try:
            bytes(self._data).decode("utf-8")
        except UnicodeDecodeError:
            return True
        return False


def run_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout: float = DEFAULT_TIMEOUT,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT,
    env: Mapping[str, str] | None = None,
    check: bool = False,
) -> CommandResult:
    """Run an argv vector with bounded capture and process-group timeout kill.

    The function does not raise for a child-process failure unless ``check`` is
    true.  Spawn/configuration failures remain Python exceptions because no
    meaningful command result exists for them.
    """
    exact_argv = tuple(str(part) for part in argv)
    if not exact_argv or any(not part or "\x00" in part for part in exact_argv):
        raise ValueError("argv must contain non-empty, NUL-free arguments")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if max_output_bytes < 0:
        raise ValueError("max_output_bytes cannot be negative")

    # Delivery runs inside processes that may hold provider, Telegram, and
    # GitHub credentials.  Start from a small operational allowlist instead of
    # copying that ambient environment into every Git child.  Overrides are
    # subject to the same allowlist, so callers cannot reintroduce a token,
    # secret, password, API key, or Git repository redirection variable.
    child_env = {
        key: os.environ[key] for key in _CHILD_ENV_ALLOWLIST if key in os.environ
    }
    if env is not None:
        for key in _CHILD_ENV_ALLOWLIST:
            if key in env:
                child_env[key] = str(env[key])
    child_env["GIT_TERMINAL_PROMPT"] = "0"
    child_env["GIT_ALLOW_PROTOCOL"] = "https:ssh:file"
    # Formula-level system configuration lives under Homebrew's writable
    # prefix and is not needed by the controller. Ignore it deterministically;
    # user-level credential helpers remain available through the private HOME
    # when the trusted controller performs an authenticated fetch or push.
    child_env["GIT_CONFIG_NOSYSTEM"] = "1"
    child_env["GIT_CONFIG_SYSTEM"] = os.devnull
    child_env["GIT_NO_REPLACE_OBJECTS"] = "1"
    # The outer validation controller owns the sandbox's isolated process
    # group. Its syscall policy denies nested setsid/setpgid, so contained Git
    # children remain in that group and are cleaned up by the outer runner.
    isolate_process_group = os.environ.get(_CONTAINED_VALIDATION_ENV) != "1"
    process = subprocess.Popen(  # noqa: S603 - exact argv is the security boundary
        exact_argv,
        cwd=os.fspath(cwd),
        env=child_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=isolate_process_group,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    stdout = _BoundedCollector(process.stdout, max_output_bytes)
    stderr = _BoundedCollector(process.stderr, max_output_bytes)
    readers = [
        threading.Thread(target=stdout.read, name="delivery-stdout", daemon=True),
        threading.Thread(target=stderr.read, name="delivery-stderr", daemon=True),
    ]
    for reader in readers:
        reader.start()

    deadline = time.monotonic() + timeout
    timed_out = False
    try:
        returncode = process.wait(timeout=max(0.001, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        timed_out = True
        if isolate_process_group:
            _kill_process_group(process.pid)
        else:
            _kill_process(process)
        returncode = process.wait()
    if not timed_out:
        while time.monotonic() < deadline:
            group_alive = isolate_process_group and _process_group_exists(process.pid)
            if not any(reader.is_alive() for reader in readers) and not group_alive:
                break
            remaining = deadline - time.monotonic()
            for reader in readers:
                reader.join(timeout=max(0.0, min(0.01, remaining)))
        group_alive = isolate_process_group and _process_group_exists(process.pid)
        if any(reader.is_alive() for reader in readers) or group_alive:
            timed_out = True
            if isolate_process_group:
                _kill_process_group(process.pid)
            else:
                _kill_process(process)
    for reader in readers:
        reader.join(timeout=0.2)
    if any(reader.is_alive() for reader in readers):
        process.stdout.close()
        process.stderr.close()
        for reader in readers:
            reader.join(timeout=0.2)

    result = CommandResult(
        argv=exact_argv,
        returncode=returncode,
        stdout=stdout.text(),
        stderr=stderr.text(),
        timed_out=timed_out,
        stdout_truncated=stdout.truncated,
        stderr_truncated=stderr.truncated,
        stdout_invalid_utf8=stdout.invalid_utf8,
        stderr_invalid_utf8=stderr.invalid_utf8,
    )
    if check:
        result.raise_for_status()
    return result


def _process_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill_process_group(process_group: int) -> None:
    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _kill_process(process: subprocess.Popen[bytes]) -> None:
    """Kill only the nested child; the outer sandbox owns descendant cleanup."""
    if process.poll() is not None:
        return
    try:
        process.kill()
    except ProcessLookupError:
        pass


class GitRepository:
    """Mutation-safe operations for one configured repository."""

    def __init__(
        self,
        repo_root: Path,
        *,
        trusted_root: Path,
        runtime_root: Path,
        git_binary: str = "git",
        command_timeout: float = DEFAULT_TIMEOUT,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_patch_bytes: int = 5_000_000,
        lock_timeout: float = 30.0,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.trusted_root = Path(trusted_root)
        self.runtime_root = Path(runtime_root)
        executable = git_binary if Path(git_binary).is_absolute() else shutil.which(git_binary)
        if not executable:
            raise GitValidationError("configured Git executable was not found")
        try:
            resolved_git = Path(executable).resolve(strict=True)
        except OSError as exc:
            raise GitValidationError("configured Git executable is unavailable") from exc
        if not resolved_git.is_file() or not os.access(resolved_git, os.X_OK):
            raise GitValidationError("configured Git executable is not executable")
        self.git_binary = os.fspath(resolved_git)
        self.command_timeout = command_timeout
        self.max_output_bytes = max_output_bytes
        self.max_file_bytes = max_file_bytes
        self.max_patch_bytes = max_patch_bytes
        self.lock_timeout = lock_timeout
        self._lock_guard = threading.RLock()
        self._lock_depth = 0

    def validate_trusted_root(self) -> Path:
        """Return the canonical root only when it is exactly the trusted repo."""
        configured = self.repo_root.absolute()
        trusted = self.trusted_root.absolute()
        if configured != trusted:
            raise GitValidationError("repository path is not the configured trusted root")
        if configured.is_symlink():
            raise GitValidationError("trusted repository root cannot be a symlink")
        try:
            canonical = configured.resolve(strict=True)
            trusted_canonical = trusted.resolve(strict=True)
        except OSError as exc:
            raise GitValidationError(f"trusted repository is unavailable: {exc}") from exc
        if canonical != trusted_canonical or not canonical.is_dir():
            raise GitValidationError("repository does not resolve to the trusted root")
        result = self._git(canonical, "rev-parse", "--show-toplevel", check=True)
        try:
            actual = Path(result.stdout.strip()).resolve(strict=True)
        except OSError as exc:
            raise GitValidationError("Git returned an invalid repository root") from exc
        if actual != canonical:
            raise GitValidationError("configured path is not the Git repository top level")
        return canonical

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Serialize delivery for this repository; reentrant on this instance."""
        deadline = time.monotonic() + self.lock_timeout
        acquired = self._lock_guard.acquire(timeout=self.lock_timeout)
        if not acquired:
            raise GitValidationError("timed out waiting for in-process repository lock")
        try:
            if self._lock_depth:
                self._lock_depth += 1
                try:
                    yield
                finally:
                    self._lock_depth -= 1
                return

            root = self.validate_trusted_root()
            lock_dir = self._runtime_directory("locks")
            digest = hashlib.sha256(os.fsencode(root)).hexdigest()
            lock_path = lock_dir / f"{digest}.lock"
            flags = os.O_RDWR | os.O_CREAT
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                descriptor = os.open(lock_path, flags, 0o600)
            except OSError as exc:
                raise GitValidationError(f"cannot open repository lock: {exc}") from exc
            try:
                while True:
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise GitValidationError(
                                "timed out waiting for repository lock"
                            ) from None
                        time.sleep(0.05)
                self._lock_depth = 1
                try:
                    yield
                finally:
                    self._lock_depth = 0
            finally:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)
        finally:
            self._lock_guard.release()

    def resolve_base(self, remote: str = "origin") -> BaseRevision:
        """Fetch and resolve the remote's configured default branch to one SHA."""
        root = self.validate_trusted_root()
        self._validate_remote(remote)
        with self.lock():
            self._validated_remote_identity(root, remote)
            self._git(root, "fetch", "--no-tags", "--prune", remote, check=True)
            symbolic = self._git(
                root,
                "symbolic-ref",
                "--quiet",
                "--short",
                f"refs/remotes/{remote}/HEAD",
            )
            if not symbolic.ok:
                self._git(root, "remote", "set-head", remote, "--auto", check=True)
                symbolic = self._git(
                    root,
                    "symbolic-ref",
                    "--quiet",
                    "--short",
                    f"refs/remotes/{remote}/HEAD",
                    check=True,
                )
            remote_branch = symbolic.stdout.strip()
            prefix = f"{remote}/"
            if not remote_branch.startswith(prefix):
                raise GitValidationError("origin default branch could not be resolved")
            branch = remote_branch[len(prefix) :]
            self._validate_ref_component(branch)
            ref = f"refs/remotes/{remote}/{branch}"
            sha = self._git(root, "rev-parse", "--verify", f"{ref}^{{commit}}", check=True)
            exact_sha = sha.stdout.strip().lower()
            if not _SHA_RE.fullmatch(exact_sha):
                raise GitValidationError("remote default branch did not resolve to an exact SHA")
            return BaseRevision(remote=remote, branch=branch, ref=ref, sha=exact_sha)

    def create_worktree(self, task_id: str, base: BaseRevision) -> Worktree:
        """Create a unique branch/worktree pair owned by this delivery job."""
        root = self.validate_trusted_root()
        if not _SHA_RE.fullmatch(base.sha):
            raise GitValidationError("base revision must be an exact commit SHA")
        safe_task = _safe_task_slug(task_id)
        worktree_root = self._runtime_directory("worktrees")
        marker_root = self._runtime_directory("owners")
        with self.lock():
            for _ in range(16):
                suffix = secrets.token_hex(4)
                branch = f"codex/{safe_task}-{suffix}"
                if not _BRANCH_RE.fullmatch(branch):
                    raise GitValidationError("generated branch name failed validation")
                path = worktree_root / f"{safe_task}-{suffix}"
                marker = marker_root / f"{safe_task}-{suffix}{_OWNER_FILE_SUFFIX}"
                if path.exists() or marker.exists():
                    continue
                token = secrets.token_hex(32)
                self._git(
                    root,
                    "worktree",
                    "add",
                    "--no-track",
                    "-b",
                    branch,
                    os.fspath(path),
                    base.sha,
                    check=True,
                )
                try:
                    trusted_common = self._resolved_git_path(root, "--git-common-dir")
                    worktree_common = self._resolved_git_path(path, "--git-common-dir")
                    worktree_git_dir = self._resolved_git_path(path, "--absolute-git-dir")
                    if worktree_common != trusted_common:
                        raise GitValidationError(
                            "new worktree is not linked to the trusted repository"
                        )
                    _write_owner_marker(
                        marker,
                        root,
                        path,
                        branch,
                        base.sha,
                        token,
                        worktree_git_dir,
                        worktree_common,
                    )
                except Exception:
                    self._git(root, "worktree", "remove", os.fspath(path))
                    self._git(root, "branch", "-d", branch)
                    raise
                return Worktree(path, branch, base.sha, marker, token)
        raise GitValidationError("could not allocate a unique worktree")

    def status(self, worktree: Worktree) -> list[ChangedPath]:
        path = self._validate_owned_worktree(worktree)
        result = self._git(
            path,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            check=True,
        )
        if result.stdout_truncated or result.stdout_invalid_utf8:
            raise GitValidationError("Git status output is oversized or not valid UTF-8")
        return _parse_porcelain_v1_z(result.stdout)

    def current_head(self, worktree: Worktree) -> str:
        """Return the exact commit checked out in an owned worktree."""
        root = self._validate_owned_worktree(worktree)
        result = self._git(root, "rev-parse", "--verify", "HEAD^{commit}", check=True)
        sha = result.stdout.strip().lower()
        if not _SHA_RE.fullmatch(sha):
            raise GitValidationError("worktree HEAD is not an exact commit SHA")
        return sha

    def assert_head(self, worktree: Worktree, expected_sha: str | None = None) -> str:
        """Fail when a coding tool moved HEAD outside the controller's control."""
        expected = expected_sha or worktree.base_sha
        if not _SHA_RE.fullmatch(expected):
            raise GitValidationError("expected HEAD must be an exact commit SHA")
        actual = self.current_head(worktree)
        if actual != expected:
            raise GitValidationError(f"worktree HEAD changed: expected {expected}, got {actual}")
        return actual

    def restore_worktree_to_base(self, worktree: Worktree) -> str:
        """Restore one verified job-owned worktree to its exact base commit.

        This is intentionally destructive only inside a worktree whose owner
        marker, repository link, branch, and ref all still match the controller's
        immutable ``Worktree`` record.  It removes staged, unstaged, untracked,
        and ignored candidate artifacts, then proves that both tracked state and
        ignored state are empty before another patch attempt may begin.
        """
        self._validate_owned_worktree(worktree)
        with self.lock():
            root = self._validate_owned_worktree(worktree)
            self._assert_owned_ref(worktree, root, worktree.base_sha)
            self._git(
                root,
                "reset",
                "--hard",
                "--no-recurse-submodules",
                worktree.base_sha,
                check=True,
            )
            self._git(root, "clean", "-d", "-f", "-f", "-x", "--", check=True)

            # Re-resolve the marker and repository linkage after the destructive
            # commands instead of trusting the pre-reset path identity.
            root = self._validate_owned_worktree(worktree)
            self._assert_owned_ref(worktree, root, worktree.base_sha)
            status = self._git(
                root,
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
                "--ignored=matching",
                check=True,
            )
            if status.stdout_truncated or status.stdout_invalid_utf8:
                raise GitValidationError("post-restore Git status is oversized or not valid UTF-8")
            if status.stdout:
                raise GitValidationError("worktree restore did not produce a clean base")
        return worktree.base_sha

    def remote_url(self, remote: str = "origin") -> str:
        """Return the configured remote URL after validating this repository."""
        root = self.validate_trusted_root()
        self._validate_remote(remote)
        fetch_urls, _push_urls, _identity = self._validated_remote_identity(root, remote)
        return fetch_urls[0]

    def github_slug(self, remote: str = "origin") -> str:
        """Return ``owner/repository`` for a github.com origin URL."""
        root = self.validate_trusted_root()
        self._validate_remote(remote)
        _fetch_urls, _push_urls, identity = self._validated_remote_identity(root, remote)
        kind, value = identity
        if kind != "github":
            raise GitValidationError("origin is not a supported github.com repository URL")
        return value

    def apply_unified_patch(self, worktree: Worktree, patch: str | bytes) -> tuple[str, ...]:
        """Apply a bounded text patch after Git and path-policy preflight checks.

        If validating the resulting files fails (for example because a patch
        makes a file oversized), the exact patch is reversed before the error is
        re-raised.  Existing staged changes are rejected so the controller owns
        an unambiguous candidate artifact.
        """
        root = self._validate_owned_worktree(worktree)
        try:
            data = patch.encode("utf-8") if isinstance(patch, str) else bytes(patch)
            data.decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError) as exc:
            raise GitValidationError("patch must be valid UTF-8 text") from exc
        if not data or len(data) > self.max_patch_bytes or b"\x00" in data:
            raise GitValidationError("patch is empty, binary, or exceeds the size limit")
        patch_root = self._runtime_directory("patches")
        descriptor, name = tempfile.mkstemp(prefix="delivery-", suffix=".patch", dir=patch_root)
        patch_path = Path(name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            with self.lock():
                if self._staged_paths(root):
                    raise GitValidationError(
                        "cannot apply a patch over pre-existing staged changes"
                    )
                numstat = self._git(
                    root,
                    "apply",
                    "--numstat",
                    "-z",
                    "--",
                    os.fspath(patch_path),
                    check=True,
                )
                if numstat.stdout_truncated or numstat.stdout_invalid_utf8:
                    raise GitValidationError("patch path list is oversized or not valid UTF-8")
                paths = _parse_apply_numstat(numstat.stdout)
                if not paths:
                    raise GitValidationError("patch contains no file changes")
                summary = self._git(
                    root,
                    "apply",
                    "--summary",
                    "--",
                    os.fspath(patch_path),
                    check=True,
                ).stdout
                lowered = summary.lower()
                if " mode 120000" in lowered or " mode 160000" in lowered:
                    raise GitValidationError("patches may not create symlinks or gitlinks")
                for relative in paths:
                    _enforce_path_policy(relative)
                    candidate = root.joinpath(*PurePosixPath(relative).parts)
                    _validate_path_components(root, candidate)
                    if candidate.exists():
                        _validate_text_file(candidate, self.max_file_bytes)
                self._git(
                    root,
                    "apply",
                    "--check",
                    "--whitespace=error-all",
                    "--",
                    os.fspath(patch_path),
                    check=True,
                )
                self._git(
                    root,
                    "apply",
                    "--whitespace=error-all",
                    "--",
                    os.fspath(patch_path),
                    check=True,
                )
                try:
                    return self.validate_changed_paths(worktree, paths)
                except Exception:
                    reverse = self._git(
                        root,
                        "apply",
                        "--reverse",
                        "--check",
                        "--",
                        os.fspath(patch_path),
                    )
                    if not reverse.ok:
                        raise GitValidationError(
                            "unsafe patch result could not be rolled back; worktree is quarantined"
                        ) from None
                    self._git(
                        root,
                        "apply",
                        "--reverse",
                        "--",
                        os.fspath(patch_path),
                        check=True,
                    )
                    raise
        finally:
            try:
                patch_path.unlink()
            except FileNotFoundError:
                pass

    def validate_changed_paths(
        self,
        worktree: Worktree,
        paths: Iterable[str] | None = None,
    ) -> tuple[str, ...]:
        """Validate all changed paths, or an exact caller-supplied subset."""
        root = self._validate_owned_worktree(worktree)
        entries = self.status(worktree)
        changed_names: set[str] = set()
        for entry in entries:
            changed_names.add(entry.path)
            if entry.original_path:
                changed_names.add(entry.original_path)
        requested = changed_names if paths is None else set(paths)
        if not requested:
            raise GitValidationError("no changed paths were supplied")
        if not requested.issubset(changed_names):
            extra = ", ".join(sorted(requested - changed_names))
            raise GitValidationError(f"path is not an observed change: {extra}")
        validated: list[str] = []
        for raw_path in sorted(requested):
            relative = _validate_relative_path(raw_path)
            _enforce_path_policy(relative)
            candidate = root.joinpath(*PurePosixPath(relative).parts)
            _validate_path_components(root, candidate)
            if candidate.exists():
                _validate_text_file(candidate, self.max_file_bytes)
            else:
                self._validate_deleted_tracked_file(root, relative)
            validated.append(relative)
        return tuple(validated)

    def diff(
        self,
        worktree: Worktree,
        paths: Iterable[str],
        *,
        cached: bool = False,
        check: bool = True,
    ) -> CommandResult:
        root = self._validate_owned_worktree(worktree)
        exact_paths = self.validate_changed_paths(worktree, paths)
        args = ["diff", "--no-color", "--no-ext-diff"]
        if cached:
            args.append("--cached")
        args.extend(["--", *exact_paths])
        return self._git(root, *args, check=check)

    def staged_changed_paths(self, worktree: Worktree) -> tuple[str, ...]:
        """Return and validate the exact path set currently in the index."""
        root = self._validate_owned_worktree(worktree)
        with self.lock():
            self._assert_owned_ref(worktree, root, worktree.base_sha)
            tree = self._write_tree(root)
            paths = self._tree_changed_paths(root, worktree.base_sha, tree)
            self._validate_tree_paths(root, worktree.base_sha, tree, paths)
            return paths

    def staged_diff(self, worktree: Worktree) -> str:
        """Return the exact staged full-index patch for artifact hashing/review."""
        root = self._validate_owned_worktree(worktree)
        with self.lock():
            self._assert_owned_ref(worktree, root, worktree.base_sha)
            tree = self._write_tree(root)
            paths = self._tree_changed_paths(root, worktree.base_sha, tree)
            if not paths:
                raise GitValidationError("there is no staged candidate diff")
            self._validate_tree_paths(root, worktree.base_sha, tree, paths)
            return self._tree_diff(root, worktree.base_sha, tree, paths)

    def stage(self, worktree: Worktree, paths: Iterable[str]) -> tuple[str, ...]:
        """Stage only explicit, observed, policy-compliant paths."""
        root = self._validate_owned_worktree(worktree)
        exact_paths = self.validate_changed_paths(worktree, paths)
        with self.lock():
            self._assert_owned_ref(worktree, root, worktree.base_sha)
            self._assert_no_filter_drivers(root, exact_paths)
            already_staged = self._staged_paths(root)
            unexpected = set(already_staged) - set(exact_paths)
            if unexpected:
                detail = sorted(unexpected)
                raise GitValidationError(
                    f"index already contains paths outside the explicit stage set: {detail}"
                )
            self._git(root, "add", "--", *exact_paths, check=True)
            tree = self._write_tree(root)
            staged = self._tree_changed_paths(root, worktree.base_sha, tree)
            unexpected = set(staged) - set(exact_paths)
            if unexpected:
                raise GitValidationError(
                    f"index contains paths outside the explicit stage set: {sorted(unexpected)}"
                )
            self._validate_tree_paths(root, worktree.base_sha, tree, staged)
        return exact_paths

    def unstage(self, worktree: Worktree, paths: Iterable[str]) -> tuple[str, ...]:
        """Unstage one exact validated candidate while preserving working files."""
        root = self._validate_owned_worktree(worktree)
        exact_paths = tuple(sorted({_validate_relative_path(path) for path in paths}))
        if not exact_paths:
            raise GitValidationError("unstage requires the complete staged path set")
        for path in exact_paths:
            _enforce_path_policy(path)
        with self.lock():
            self._assert_owned_ref(worktree, root, worktree.base_sha)
            tree = self._write_tree(root)
            staged = self._tree_changed_paths(root, worktree.base_sha, tree)
            if set(staged) != set(exact_paths):
                raise GitValidationError("paths do not exactly match the staged candidate")
            self._validate_tree_paths(root, worktree.base_sha, tree, staged)
            self._git(root, "reset", "--quiet", "HEAD", "--", *exact_paths, check=True)
            empty_tree = self._write_tree(root)
            if self._tree_changed_paths(root, worktree.base_sha, empty_tree):
                raise GitValidationError("Git did not fully unstage the candidate")
        return exact_paths

    def commit(
        self,
        worktree: Worktree,
        paths: Iterable[str],
        message: str,
        *,
        expected_diff_sha256: str,
    ) -> str:
        """Commit only the immutable tree whose diff received exact approval."""
        root = self._validate_owned_worktree(worktree)
        exact_paths = tuple(sorted({_validate_relative_path(path) for path in paths}))
        if not exact_paths:
            raise GitValidationError("commit requires explicit paths")
        for path in exact_paths:
            _enforce_path_policy(path)
        if not message.strip() or "\x00" in message:
            raise GitValidationError("commit message must be non-empty and NUL-free")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_diff_sha256):
            raise GitValidationError("expected diff digest must be lowercase SHA-256")
        with self.lock():
            self._assert_owned_ref(worktree, root, worktree.base_sha)
            tree = self._write_tree(root)
            staged = self._tree_changed_paths(root, worktree.base_sha, tree)
            if set(staged) != set(exact_paths):
                raise GitValidationError("staged paths do not exactly match the commit path set")
            self._validate_tree_paths(root, worktree.base_sha, tree, staged)
            diff = self._tree_diff(root, worktree.base_sha, tree, staged)
            actual_digest = hashlib.sha256(diff.encode("utf-8")).hexdigest()
            if actual_digest != expected_diff_sha256:
                raise GitValidationError("staged artifact no longer matches the approved diff")
            result = self._git(
                root,
                "-c",
                "user.name=MondayOS",
                "-c",
                "user.email=mondayos@users.noreply.github.com",
                "commit-tree",
                tree,
                "-p",
                worktree.base_sha,
                "-m",
                message,
                check=True,
            )
            commit_sha = result.stdout.strip().lower()
            if not _SHA_RE.fullmatch(commit_sha):
                raise GitValidationError("commit-tree did not produce an exact SHA")
            branch_ref = f"refs/heads/{worktree.branch}"
            self._git(
                root,
                "update-ref",
                branch_ref,
                commit_sha,
                worktree.base_sha,
                check=True,
            )
            self._verify_commit(root, commit_sha, tree, worktree.base_sha)
            self._assert_owned_ref(worktree, root, commit_sha)
            return commit_sha

    def push(self, worktree: Worktree, expected_sha: str, remote: str = "origin") -> str:
        """Push this unique branch without force and return the exact HEAD SHA."""
        root = self._validate_owned_worktree(worktree)
        self._validate_remote(remote)
        if not _SHA_RE.fullmatch(expected_sha):
            raise GitValidationError("expected push commit must be an exact SHA")
        if not _BRANCH_RE.fullmatch(worktree.branch):
            raise GitValidationError("worktree branch is invalid")
        with self.lock():
            self._validated_remote_identity(self.validate_trusted_root(), remote)
            self._assert_owned_ref(worktree, root, expected_sha)
            self._git(
                root,
                "push",
                remote,
                f"{expected_sha}:refs/heads/{worktree.branch}",
                check=True,
            )
            remote_ref = f"refs/heads/{worktree.branch}"
            result = self._git(root, "ls-remote", "--refs", remote, remote_ref, check=True)
            records = [line.split("\t", 1) for line in result.stdout.splitlines() if line]
            if records != [[expected_sha, remote_ref]]:
                raise GitValidationError("remote branch does not equal the approved commit")
            self._assert_owned_ref(worktree, root, expected_sha)
            return expected_sha

    def cleanup(self, worktree: Worktree, *, delete_branch: bool = False) -> None:
        """Remove only a worktree whose exact ownership marker still matches."""
        root = self.validate_trusted_root()
        self._validate_owned_worktree(worktree)
        with self.lock():
            self._git(root, "worktree", "remove", os.fspath(worktree.path), check=True)
            worktree.owner_marker.unlink()
            if delete_branch:
                # No force: a branch containing unmerged work must be preserved.
                self._git(root, "branch", "-d", worktree.branch, check=True)

    def _assert_owned_ref(self, worktree: Worktree, root: Path, expected_sha: str) -> None:
        branch = self._git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=True)
        if branch.stdout.strip() != worktree.branch:
            raise GitValidationError("worktree is no longer on its owned branch")
        branch_ref = f"refs/heads/{worktree.branch}"
        local = self._git(root, "rev-parse", "--verify", f"{branch_ref}^{{commit}}", check=True)
        head = self._git(root, "rev-parse", "--verify", "HEAD^{commit}", check=True)
        local_sha = local.stdout.strip().lower()
        head_sha = head.stdout.strip().lower()
        if local_sha != expected_sha or head_sha != expected_sha:
            raise GitValidationError("owned branch or HEAD moved from the expected commit")

    def _write_tree(self, root: Path) -> str:
        result = self._git(root, "write-tree", check=True)
        tree = result.stdout.strip().lower()
        if not _SHA_RE.fullmatch(tree):
            raise GitValidationError("index did not resolve to an exact tree")
        return tree

    def _tree_changed_paths(self, root: Path, base_sha: str, tree: str) -> tuple[str, ...]:
        result = self._git(
            root,
            "diff",
            "--name-only",
            "-z",
            "--no-renames",
            "--no-textconv",
            base_sha,
            tree,
            "--",
            check=True,
        )
        if result.stdout_truncated or result.stdout_invalid_utf8:
            raise GitValidationError("tree path list is oversized or not valid UTF-8")
        return tuple(sorted(path for path in result.stdout.split("\x00") if path))

    def _validate_tree_paths(
        self,
        root: Path,
        base_sha: str,
        tree: str,
        paths: Iterable[str],
    ) -> None:
        for raw_path in paths:
            relative = _validate_relative_path(raw_path)
            _enforce_path_policy(relative)
            result = self._git(root, "ls-tree", "-z", tree, "--", relative, check=True)
            if not result.stdout:
                self._validate_tree_blob(root, base_sha, relative)
                continue
            mode, blob_sha = _parse_ls_tree_blob(result.stdout, relative)
            if mode not in {"100644", "100755"}:
                raise GitValidationError(f"tree path is not a regular file: {relative}")
            self._validate_blob(root, blob_sha, relative)

    def _validate_tree_blob(self, root: Path, treeish: str, relative: str) -> None:
        result = self._git(root, "ls-tree", "-z", treeish, "--", relative, check=True)
        if not result.stdout:
            raise GitValidationError(f"deleted path was not present in the base: {relative}")
        mode, blob_sha = _parse_ls_tree_blob(result.stdout, relative)
        if mode not in {"100644", "100755"}:
            raise GitValidationError(f"base path is not a regular file: {relative}")
        self._validate_blob(root, blob_sha, relative)

    def _tree_diff(
        self,
        root: Path,
        base_sha: str,
        tree: str,
        paths: Iterable[str],
    ) -> str:
        exact_paths = tuple(paths)
        self._git(root, "diff", "--check", base_sha, tree, "--", *exact_paths, check=True)
        result = self._git(
            root,
            "diff",
            "--binary",
            "--full-index",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            base_sha,
            tree,
            "--",
            *exact_paths,
            check=True,
        )
        if result.stdout_truncated or result.stdout_invalid_utf8:
            raise GitValidationError("tree diff is oversized or not valid UTF-8")
        return result.stdout

    def _verify_commit(self, root: Path, commit_sha: str, tree: str, parent: str) -> None:
        actual_tree = self._git(
            root,
            "rev-parse",
            "--verify",
            f"{commit_sha}^{{tree}}",
            check=True,
        ).stdout.strip()
        ancestry = self._git(root, "rev-list", "--parents", "-n", "1", commit_sha, check=True)
        if actual_tree != tree or ancestry.stdout.strip().split() != [commit_sha, parent]:
            raise GitValidationError(
                "created commit tree or parent does not match the approved artifact"
            )

    def _staged_paths(self, root: Path) -> tuple[str, ...]:
        result = self._git(root, "diff", "--cached", "--name-only", "-z", check=True)
        if result.stdout_truncated or result.stdout_invalid_utf8:
            raise GitValidationError("staged path list is oversized or not valid UTF-8")
        return tuple(sorted(path for path in result.stdout.split("\x00") if path))

    def _assert_no_filter_drivers(self, root: Path, paths: Iterable[str]) -> None:
        """Reject candidate paths whose attributes could execute Git filters."""
        exact_paths = tuple(paths)
        result = self._git(
            root,
            "check-attr",
            "-z",
            "filter",
            "--",
            *exact_paths,
            check=True,
        )
        if result.stdout_truncated or result.stdout_invalid_utf8:
            raise GitValidationError("Git attribute output is oversized or not valid UTF-8")
        records = result.stdout.split("\x00")
        if records and records[-1] == "":
            records.pop()
        if len(records) != len(exact_paths) * 3:
            raise GitValidationError("Git returned malformed filter attributes")
        seen: set[str] = set()
        allowed = set(exact_paths)
        for index in range(0, len(records), 3):
            path, attribute, value = records[index : index + 3]
            if path not in allowed or path in seen or attribute != "filter":
                raise GitValidationError("Git returned malformed filter attributes")
            seen.add(path)
            if value not in {"unspecified", "unset"}:
                raise GitValidationError(
                    f"Git clean/smudge filters are not permitted for candidate path: {path}"
                )
        if seen != allowed:
            raise GitValidationError("Git returned incomplete filter attributes")

    def _validate_deleted_tracked_file(self, root: Path, relative: str) -> None:
        result = self._git(root, "ls-tree", "-z", "HEAD", "--", relative, check=True)
        record = result.stdout.removesuffix("\x00")
        metadata, separator, name = record.partition("\t")
        fields = metadata.split()
        if not separator or name != relative or len(fields) != 3:
            raise GitValidationError(f"missing path is not a tracked deletion: {relative}")
        mode, object_type, blob_sha = fields
        if (
            mode not in {"100644", "100755"}
            or object_type != "blob"
            or not _SHA_RE.fullmatch(blob_sha)
        ):
            raise GitValidationError(f"tracked path is not a regular file: {relative}")
        self._validate_blob(root, blob_sha, relative)

    def _validate_index_path(self, root: Path, relative: str) -> None:
        result = self._git(root, "ls-files", "-s", "--", relative, check=True)
        if not result.stdout.strip():
            self._validate_deleted_tracked_file(root, relative)
            return
        records = [line for line in result.stdout.splitlines() if line]
        if len(records) != 1:
            raise GitValidationError(f"index has unresolved stages for: {relative}")
        metadata, separator, name = records[0].partition("\t")
        fields = metadata.split()
        if not separator or name != relative or len(fields) != 3:
            raise GitValidationError(f"index entry is malformed: {relative}")
        mode, blob_sha, stage = fields
        if mode not in {"100644", "100755"} or stage != "0" or not _SHA_RE.fullmatch(blob_sha):
            raise GitValidationError(f"index path is not a regular file: {relative}")
        self._validate_blob(root, blob_sha, relative)

    def _validate_blob(self, root: Path, blob_sha: str, relative: str) -> None:
        size_result = self._git(root, "cat-file", "-s", blob_sha, check=True)
        try:
            size = int(size_result.stdout.strip())
        except ValueError as exc:
            raise GitValidationError(f"cannot determine blob size: {relative}") from exc
        if size > self.max_file_bytes:
            raise GitValidationError(f"file exceeds size limit: {relative}")
        blob = self._git(
            root,
            "cat-file",
            "blob",
            blob_sha,
            check=True,
            max_output_bytes=self.max_file_bytes + 1,
        )
        if blob.stdout_truncated or blob.stdout_invalid_utf8:
            raise GitValidationError(f"file is oversized or not valid UTF-8: {relative}")
        _validate_text(blob.stdout.encode("utf-8"), relative)

    def _validate_owned_worktree(self, worktree: Worktree) -> Path:
        root = self.validate_trusted_root()
        expected_parent = self._runtime_directory("worktrees")
        expected_marker_parent = self._runtime_directory("owners")
        try:
            actual = worktree.path.resolve(strict=True)
            actual.relative_to(expected_parent)
            actual_marker = worktree.owner_marker.resolve(strict=True)
            actual_marker.relative_to(expected_marker_parent)
        except (OSError, ValueError) as exc:
            raise GitValidationError("worktree is outside the supplied runtime root") from exc
        expected_marker_name = f"{actual.name}{_OWNER_FILE_SUFFIX}"
        if actual_marker.name != expected_marker_name:
            raise GitValidationError("worktree ownership marker has an invalid location")
        marker = _read_owner_marker(worktree.owner_marker)
        trusted_common = self._resolved_git_path(root, "--git-common-dir")
        worktree_common = self._resolved_git_path(actual, "--git-common-dir")
        worktree_git_dir = self._resolved_git_path(actual, "--absolute-git-dir")
        expected = {
            "schema": _OWNER_SCHEMA,
            "repo_root": os.fspath(root),
            "worktree": os.fspath(actual),
            "branch": worktree.branch,
            "base_sha": worktree.base_sha,
            "owner_token": worktree.owner_token,
            "git_dir": os.fspath(worktree_git_dir),
            "git_common_dir": os.fspath(worktree_common),
        }
        if marker != expected:
            raise GitValidationError("worktree ownership marker does not match")
        if worktree_common != trusted_common:
            raise GitValidationError("worktree is not linked to the trusted repository")
        git_root = self._git(actual, "rev-parse", "--show-toplevel", check=True).stdout.strip()
        try:
            if Path(git_root).resolve(strict=True) != actual:
                raise GitValidationError("owned worktree is not a Git top level")
        except OSError as exc:
            raise GitValidationError("owned worktree Git root is invalid") from exc
        return actual

    def _runtime_directory(self, name: str) -> Path:
        if name not in {"locks", "worktrees", "owners", "patches"}:
            raise GitValidationError("runtime directory name is invalid")
        runtime = self.runtime_root.absolute()
        runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = runtime.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise GitValidationError("runtime root must be a real directory")
        canonical_runtime = runtime.resolve(strict=True)
        directory = runtime / name
        if directory.exists() or directory.is_symlink():
            child_metadata = directory.lstat()
            if stat.S_ISLNK(child_metadata.st_mode) or not stat.S_ISDIR(child_metadata.st_mode):
                raise GitValidationError(f"runtime {name} path must be a real directory")
        else:
            directory.mkdir(mode=0o700)
        resolved = directory.resolve(strict=True)
        try:
            resolved.relative_to(canonical_runtime)
        except ValueError as exc:
            raise GitValidationError(f"runtime {name} path escapes its root") from exc
        return resolved

    def _resolved_git_path(self, cwd: Path, query: str) -> Path:
        args = ["rev-parse"]
        if query == "--git-common-dir":
            args.append("--path-format=absolute")
        args.append(query)
        raw = self._git(cwd, *args, check=True).stdout.strip()
        try:
            return Path(raw).resolve(strict=True)
        except OSError as exc:
            raise GitValidationError(f"Git returned an invalid {query} path") from exc

    def _git(
        self,
        cwd: Path,
        *args: str,
        check: bool = False,
        max_output_bytes: int | None = None,
    ) -> CommandResult:
        return run_command(
            [
                self.git_binary,
                "-c",
                "core.quotePath=false",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-c",
                "core.fsmonitor=false",
                *args,
            ],
            cwd=cwd,
            timeout=self.command_timeout,
            max_output_bytes=(
                self.max_output_bytes if max_output_bytes is None else max_output_bytes
            ),
            check=check,
        )

    @staticmethod
    def _validate_remote(remote: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", remote):
            raise GitValidationError("remote name is invalid")

    def _validated_remote_identity(
        self,
        root: Path,
        remote: str,
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, str]]:
        fetch = self._git(root, "remote", "get-url", "--all", remote, check=True)
        push = self._git(root, "remote", "get-url", "--push", "--all", remote, check=True)
        fetch_urls = tuple(line for line in fetch.stdout.splitlines() if line)
        push_urls = tuple(line for line in push.stdout.splitlines() if line)
        if not fetch_urls or not push_urls:
            raise GitValidationError("remote must have fetch and push URLs")
        identities = {_remote_identity(value) for value in (*fetch_urls, *push_urls)}
        if len(identities) != 1:
            raise GitValidationError(
                "remote fetch and push URLs do not identify the same repository"
            )
        return fetch_urls, push_urls, identities.pop()

    def _validate_ref_component(self, branch: str) -> None:
        if not branch or branch.startswith("-") or ".." in branch or "@{" in branch:
            raise GitValidationError("default branch name is invalid")
        self._git(self.repo_root, "check-ref-format", "--branch", branch, check=True)


def _safe_task_slug(task_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", task_id.strip().lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)[:64].rstrip("-")
    if not slug:
        slug = "task"
    return slug


def _remote_identity(value: str) -> tuple[str, str]:
    """Return a safe transport identity without invoking a Git remote helper."""
    if not value or "\x00" in value or "\n" in value or "\r" in value:
        raise GitValidationError("remote URL is missing or malformed")
    scp_match = re.fullmatch(
        r"git@github\.com:([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?",
        value,
    )
    if scp_match:
        return "github", scp_match.group(1)

    parsed = urlparse(value)
    if parsed.scheme == "https":
        if (
            parsed.hostname != "github.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.query
            or parsed.fragment
            or parsed.params
        ):
            raise GitValidationError("HTTPS remotes must be uncredentialed github.com URLs")
        path = parsed.path.lstrip("/").removesuffix(".git")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", path):
            raise GitValidationError("GitHub remote does not identify one owner/repository")
        return "github", path
    if parsed.scheme == "ssh":
        if (
            parsed.hostname != "github.com"
            or parsed.username != "git"
            or parsed.password is not None
            or parsed.port not in {None, 22}
            or parsed.query
            or parsed.fragment
            or parsed.params
        ):
            raise GitValidationError("SSH remotes must use git@github.com")
        path = parsed.path.lstrip("/").removesuffix(".git")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", path):
            raise GitValidationError("GitHub remote does not identify one owner/repository")
        return "github", path
    if not parsed.scheme and Path(value).is_absolute():
        try:
            resolved = Path(value).resolve(strict=True)
        except OSError as exc:
            raise GitValidationError("local remote path is unavailable") from exc
        if not resolved.is_dir():
            raise GitValidationError("local remote path is not a directory")
        return "local", os.fspath(resolved)
    raise GitValidationError("remote protocol is not permitted")


def _validate_relative_path(raw_path: str) -> str:
    if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path or "\\" in raw_path:
        raise GitValidationError("changed path must be a non-empty POSIX path")
    path = PurePosixPath(raw_path)
    if path.is_absolute() or raw_path != path.as_posix():
        raise GitValidationError(f"changed path is not normalized: {raw_path!r}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise GitValidationError(f"changed path escapes the worktree: {raw_path!r}")
    return path.as_posix()


def _enforce_path_policy(relative: str) -> None:
    path = PurePosixPath(relative)
    lower_parts = tuple(part.lower() for part in path.parts)
    basename = lower_parts[-1]
    top = lower_parts[0]
    if ".git" in lower_parts:
        raise GitValidationError(f"Git internals are protected: {relative}")
    if basename.startswith(".env"):
        raise GitValidationError(f"environment files are protected: {relative}")
    if lower_parts[:2] == (".github", "workflows"):
        raise GitValidationError(f"workflow definitions are protected: {relative}")
    if top in {".agents", ".claude", ".codex", ".mondayos"} or basename in {
        "agents.md",
        "claude.md",
        "skill.md",
        ".cursorrules",
        ".mcp.json",
        "copilot-instructions.md",
    }:
        raise GitValidationError(f"AI/controller instruction files are protected: {relative}")
    if basename in {".gitmodules", ".gitattributes"}:
        raise GitValidationError(f"Git configuration files are protected: {relative}")
    if basename in {
        ".pytest.ini",
        ".pytest.toml",
        "conftest.py",
        "pytest.ini",
        "pytest.toml",
        "tox.ini",
    }:
        raise GitValidationError(f"test harness configuration is protected: {relative}")
    if top in {"deploy", "deployment", "service", "services"}:
        raise GitValidationError(f"deployment/service definitions are protected: {relative}")
    if basename.endswith((".service", ".timer", ".socket", ".plist")) or basename in {
        "dockerfile",
        "procfile",
    } or basename.startswith(("docker-compose.", "compose.")):
        raise GitValidationError(f"deployment/service definitions are protected: {relative}")
    if basename.endswith((".pem", ".key", ".p12", ".pfx")) or basename in {
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
    }:
        raise GitValidationError(f"key material is protected: {relative}")
    manifests = {
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "pipfile",
        "pipfile.lock",
        "poetry.lock",
        "uv.lock",
        "cargo.toml",
        "cargo.lock",
        "go.mod",
        "go.sum",
        "gemfile",
        "gemfile.lock",
        "composer.json",
        "composer.lock",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "gradle.lockfile",
    }
    if basename in manifests or (
        basename.startswith("requirements") and basename.endswith((".txt", ".in"))
    ):
        raise GitValidationError(f"dependency manifests and locks are protected: {relative}")


def _validate_path_components(root: Path, candidate: Path) -> None:
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise GitValidationError("changed path escapes the worktree") from exc
    current = root
    for part in relative.parts:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            return
        if stat.S_ISLNK(mode):
            raise GitValidationError(f"symlink changes are not permitted: {relative.as_posix()}")


def _validate_text_file(path: Path, max_file_bytes: int) -> None:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise GitValidationError(f"special files are not permitted: {path.name}")
    if metadata.st_size > max_file_bytes:
        raise GitValidationError(f"file exceeds size limit: {path.name}")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise GitValidationError(f"cannot read changed file: {path.name}") from exc
    _validate_text(data, path.name)


def _validate_text(data: bytes, label: str) -> None:
    if b"\x00" in data:
        raise GitValidationError(f"binary files are not permitted: {label}")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GitValidationError(f"non-UTF-8 files are not permitted: {label}") from exc


def _parse_porcelain_v1_z(raw: str) -> list[ChangedPath]:
    records = raw.split("\x00")
    entries: list[ChangedPath] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4 or record[2] != " ":
            raise GitValidationError("Git returned malformed status data")
        status_code = record[:2]
        path = record[3:]
        original = ""
        if "R" in status_code or "C" in status_code:
            if index >= len(records) or not records[index]:
                raise GitValidationError("Git returned malformed rename status")
            original = records[index]
            index += 1
        entries.append(ChangedPath(path=path, status=status_code, original_path=original))
    return entries


def _parse_apply_numstat(raw: str) -> tuple[str, ...]:
    """Parse ordinary ``git apply --numstat -z`` records; reject rename ambiguity."""
    records = raw.split("\x00")
    paths: list[str] = []
    for record in records:
        if not record:
            continue
        fields = record.split("\t", 2)
        if len(fields) != 3 or not fields[2]:
            # With -z, rename/copy records use an empty path followed by two
            # extra NUL records.  Requiring delete+add keeps the policy exact.
            raise GitValidationError("rename/copy patches are not supported")
        additions, deletions, raw_path = fields
        if additions == "-" or deletions == "-":
            raise GitValidationError("binary patches are not permitted")
        if not additions.isdigit() or not deletions.isdigit():
            raise GitValidationError("patch numstat output is malformed")
        paths.append(_validate_relative_path(raw_path))
    if len(set(paths)) != len(paths):
        raise GitValidationError("patch contains duplicate file records")
    return tuple(sorted(paths))


def _parse_ls_tree_blob(raw: str, expected_path: str) -> tuple[str, str]:
    record = raw.removesuffix("\x00")
    metadata, separator, name = record.partition("\t")
    fields = metadata.split()
    if (
        not separator
        or name != expected_path
        or len(fields) != 3
        or fields[1] != "blob"
        or not _SHA_RE.fullmatch(fields[2])
    ):
        raise GitValidationError(f"tree entry is malformed: {expected_path}")
    return fields[0], fields[2]


def _write_owner_marker(
    marker: Path,
    repo_root: Path,
    worktree: Path,
    branch: str,
    base_sha: str,
    token: str,
    git_dir: Path,
    git_common_dir: Path,
) -> None:
    payload = {
        "schema": _OWNER_SCHEMA,
        "repo_root": os.fspath(repo_root.resolve(strict=True)),
        "worktree": os.fspath(worktree.resolve(strict=True)),
        "branch": branch,
        "base_sha": base_sha,
        "owner_token": token,
        "git_dir": os.fspath(git_dir),
        "git_common_dir": os.fspath(git_common_dir),
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(marker, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            marker.unlink()
        except OSError:
            pass
        raise


def _read_owner_marker(marker: Path) -> dict[str, object]:
    try:
        metadata = marker.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise GitValidationError("worktree ownership marker is not a regular file")
        if metadata.st_size > 4_096:
            raise GitValidationError("worktree ownership marker is oversized")
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GitValidationError("worktree ownership marker is missing or invalid") from exc
    if not isinstance(payload, dict):
        raise GitValidationError("worktree ownership marker is malformed")
    return payload
