"""Deterministic verification for the trusted MondayOS delivery slice."""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from delivery.backends import (
    ToolAvailability,
    _controller_env,
    _run_process,
    _trusted_git_executable,
)

_APPLE_GIT_CANDIDATES = (
    "/Library/Developer/CommandLineTools/usr/bin/git",
    "/usr/bin/git",
)
_MAX_VALIDATION_RSS_BYTES = 1024 * 1024 * 1024
_MAX_VALIDATION_RUNTIME_BYTES = 256 * 1024 * 1024
_MAX_VALIDATION_RUNTIME_FILES = 20_000

_SANDBOXED_PYTEST_EXCLUSIONS: tuple[str, ...] = (
    # These four tests intentionally bind a real localhost HTTP server. The
    # sandbox denies all network access, including loopback. The same module's
    # route/service tests still run and cover the API without sockets.
    "tests/test_dashboard_api.py::TestLiveServer",
    # These tests deliberately inspect the checkout's real Git history. Test
    # code is denied source Git metadata so a candidate cannot recover deleted
    # credentials or mutate controller-owned repository state. Temp-repository
    # coverage in the same modules remains enabled.
    "tests/test_vcs.py::TestRealCorpora",
    "tests/test_project_isolation.py::TestRealCorpora",
    # These four process-runner tests must signal child process groups. The
    # validation sandbox intentionally withholds signal permission from
    # candidate-controlled tests; ordinary CI still exercises these tests.
    "tests/test_delivery_components.py::test_background_child_is_terminated_after_parent_exits_normally",
    "tests/test_delivery_components.py::test_process_group_is_terminated_on_timeout",
    "tests/test_delivery_git.py::TestCommandRunner::test_capture_is_bounded_and_timeout_kills_process_group",
    "tests/test_delivery_git.py::TestCommandRunner::test_lingering_descendant_consumes_deadline_and_marks_timeout",
    # These tests exercise the outer Seatbelt validation boundary itself. A
    # process already inside that boundary cannot safely nest it.
    "tests/test_delivery_components.py::test_live_validation_sandbox_keeps_worktree_read_only",
    "tests/test_delivery_components.py::test_live_validation_sandbox_denies_process_detachment",
    # The controller's aggregate-RSS watchdog deliberately queries the host
    # process table and the open-unlinked storage probe must own and terminate
    # their process groups. Candidate code is denied that host visibility and
    # nested controller ownership.
    "tests/test_delivery_components.py::test_live_process_group_memory_watchdog",
    "tests/test_delivery_components.py::test_process_runtime_storage_watchdog_counts_open_unlinked_files",
)

_SANDBOXED_PYTEST_EXCLUSION_EVIDENCE = (
    "live localhost sockets are denied",
    "source Git history is withheld from candidate code",
    "candidate child-process signaling is denied",
    "the outer sandbox self-test cannot run nested",
    "host resource-watchdog tests cannot run nested",
)


@dataclass(frozen=True)
class ValidationResult:
    name: str
    argv: list[str]
    success: bool
    returncode: int
    output: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class MondayValidationRunner:
    """Run fixed repository checks; task/model text never chooses argv."""

    name = "validation"

    def __init__(self, *, source_root: Path | None = None, timeout: float = 900) -> None:
        self._source_root = Path(source_root).resolve() if source_root is not None else None
        self._timeout = timeout

    def availability(self) -> ToolAvailability:
        """Prove that fixed Git and Python commands run inside the sandbox."""
        if sys.platform != "darwin":
            return ToolAvailability(False, self.name, "autonomous validation requires macOS")
        try:
            git = _trusted_validation_git()
        except OSError as exc:
            return ToolAvailability(False, self.name, str(exc))

        source_root = self._source_root or Path.cwd().resolve()
        python = source_root / ".venv" / "bin" / "python"
        if not python.is_file() or not os.access(python, os.X_OK):
            return ToolAvailability(
                False,
                self.name,
                "project .venv/bin/python is unavailable",
            )
        bootstrap = source_root / "delivery" / "sandbox_runner.py"
        if not bootstrap.is_file():
            return ToolAvailability(
                False,
                self.name,
                "trusted macOS sandbox bootstrap is unavailable",
            )

        try:
            with tempfile.TemporaryDirectory(prefix="monday-validation-smoke-") as runtime_raw:
                runtime_root = Path(runtime_raw).resolve()
                os.chmod(runtime_root, 0o700)
                environment = _validation_env(runtime_root)
                smoke_checks = (
                    (
                        [
                            git,
                            "-c",
                            "core.fsmonitor=false",
                            "-c",
                            "core.hooksPath=/dev/null",
                            "rev-parse",
                            "--is-inside-work-tree",
                        ],
                        True,
                    ),
                    (
                        [
                            str(python),
                            "-I",
                            "-c",
                            (
                                "import os, resource, sys, pytest\n"
                                "fd = os.open(os.devnull, os.O_WRONLY)\n"
                                "os.close(fd)\n"
                                "for options in ({'setsid': True}, {'setpgroup': 0}):\n"
                                "    try:\n"
                                "        pid = os.posix_spawn("
                                "sys.executable, [sys.executable, '-c', 'pass'], "
                                "os.environ, **options)\n"
                                "    except PermissionError:\n"
                                "        continue\n"
                                "    os.waitpid(pid, 0)\n"
                                "    raise SystemExit(9)\n"
                                "for kind in (resource.RLIMIT_NPROC, resource.RLIMIT_NOFILE, "
                                "resource.RLIMIT_FSIZE, resource.RLIMIT_CPU, "
                                "resource.RLIMIT_AS):\n"
                                "    if resource.getrlimit(kind)[1] == resource.RLIM_INFINITY:\n"
                                "        raise SystemExit(10)\n"
                            ),
                        ],
                        False,
                    ),
                )
                for argv, allow_repository_git in smoke_checks:
                    command = self._sandboxed(
                        argv,
                        source_root,
                        source_root,
                        runtime_root,
                        allow_repository_git=allow_repository_git,
                    )
                    result = _run_process(
                        command,
                        cwd=source_root,
                        timeout=min(self._timeout, 20),
                        env=environment,
                        max_group_rss_bytes=_MAX_VALIDATION_RSS_BYTES,
                        runtime_root=runtime_root,
                        max_runtime_bytes=_MAX_VALIDATION_RUNTIME_BYTES,
                        max_runtime_files=_MAX_VALIDATION_RUNTIME_FILES,
                    )
                    if result.returncode != 0:
                        detail = (result.stderr or result.stdout).strip()
                        return ToolAvailability(
                            False,
                            self.name,
                            f"sandbox smoke check failed (exit {result.returncode}): {detail}",
                        )
        except Exception as exc:
            return ToolAvailability(
                False,
                self.name,
                f"validation environment check failed: {exc}",
            )
        return ToolAvailability(True, self.name, "sandboxed Git and pytest runtime are ready")

    def run(
        self,
        *,
        worktree: Path,
        source_root: Path,
        changed_files: list[str],
    ) -> list[ValidationResult]:
        del changed_files  # Reserved for narrower deterministic checks later.
        try:
            git = _trusted_validation_git()
        except OSError as exc:
            raise RuntimeError(str(exc)) from exc
        commands: list[tuple[str, list[str], bool]] = [
            (
                "diff-check",
                [
                    git,
                    "-c",
                    "core.fsmonitor=false",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "diff",
                    "--cached",
                    "--check",
                    "--no-ext-diff",
                    "--no-textconv",
                ],
                True,
            ),
        ]
        if (worktree / "pyproject.toml").is_file():
            source_python = source_root / ".venv" / "bin" / "python"
            python = str(source_python) if source_python.is_file() else sys.executable
            pytest_argv = [
                python,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "-c",
                os.devnull,
                "--noconftest",
                "--rootdir",
                str(Path(worktree).resolve()),
                "tests",
            ]
            pytest_argv.extend(
                f"--deselect={selection}" for selection in _SANDBOXED_PYTEST_EXCLUSIONS
            )
            commands.append(("pytest", pytest_argv, False))

        results: list[ValidationResult] = []
        with tempfile.TemporaryDirectory(prefix="monday-validation-") as runtime_raw:
            runtime_root = Path(runtime_raw).resolve()
            os.chmod(runtime_root, 0o700)
            environment = _validation_env(runtime_root)
            for name, argv, allow_repository_git in commands:
                command = self._sandboxed(
                    argv,
                    worktree,
                    source_root,
                    runtime_root,
                    allow_repository_git=allow_repository_git,
                )
                result = _run_process(
                    command,
                    cwd=worktree,
                    timeout=self._timeout,
                    env=environment,
                    max_group_rss_bytes=_MAX_VALIDATION_RSS_BYTES,
                    runtime_root=runtime_root,
                    max_runtime_bytes=_MAX_VALIDATION_RUNTIME_BYTES,
                    max_runtime_files=_MAX_VALIDATION_RUNTIME_FILES,
                )
                output = "\n".join(
                    part for part in (result.stdout, result.stderr) if part
                ).strip()
                if name == "pytest":
                    exclusions = ", ".join(_SANDBOXED_PYTEST_EXCLUSIONS)
                    reasons = "; ".join(_SANDBOXED_PYTEST_EXCLUSION_EVIDENCE)
                    output = (
                        f"{output}\nSandboxed validation exclusions: {exclusions}. "
                        f"Reasons: {reasons}."
                    ).strip()
                results.append(
                    ValidationResult(
                        name=name,
                        argv=argv,
                        success=result.returncode == 0,
                        returncode=result.returncode,
                        output=output[-20_000:],
                    )
                )
                if result.returncode != 0:
                    break
        return results

    @staticmethod
    def as_json(results: list[ValidationResult]) -> str:
        return json.dumps([result.to_dict() for result in results], sort_keys=True)

    def _sandboxed(
        self,
        argv: list[str],
        worktree: Path,
        source_root: Path,
        runtime_root: Path,
        *,
        allow_repository_git: bool,
    ) -> list[str]:
        if sys.platform != "darwin":
            raise RuntimeError(
                "delivery validation currently requires the macOS sandbox; "
                "refusing to run repository tests without supported containment"
            )
        # Keep the virtual-environment symlink intact. Resolving it to the
        # Homebrew Cellar interpreter loses the venv's site-packages under -I.
        python = (source_root / ".venv" / "bin" / "python").absolute()
        bootstrap = (source_root / "delivery" / "sandbox_runner.py").resolve()
        if not python.is_file() or not os.access(python, os.X_OK) or not bootstrap.is_file():
            raise RuntimeError(
                "the trusted macOS validation bootstrap is unavailable; "
                "refusing to run repository tests without a sandbox"
            )
        profile = _sandbox_profile(
            worktree,
            source_root,
            runtime_root,
            allow_repository_git=allow_repository_git,
        )
        suffix = "git" if allow_repository_git else "tests"
        profile_path = runtime_root / f"sandbox-{suffix}.sb"
        profile_path.write_text(profile, encoding="utf-8")
        return [str(python), "-I", str(bootstrap), str(profile_path), "--", *argv]


def _validation_env(runtime_root: Path) -> dict[str, str]:
    home = runtime_root / "home"
    temp = runtime_root / "tmp"
    home.mkdir(mode=0o700)
    temp.mkdir(mode=0o700)
    env = _controller_env()
    env.update({
        # Force network-aware libraries down a numeric, unreachable proxy path.
        # On macOS, urllib otherwise asks SystemConfiguration and mDNSResponder
        # for proxy/DNS data; granting those Mach services would create a
        # confused-deputy exfiltration path even though direct sockets are
        # denied by the sandbox.
        "ALL_PROXY": "http://127.0.0.1:9",
        "CI": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": str(home),
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        # Trusted process helpers see this only after entering the sandbox. It
        # tells them not to request a nested session, which the sandbox denies;
        # the outer controller remains the sole process-group owner.
        "MONDAYOS_INTERNAL_CONTAINED_VALIDATION": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PATH": (
            "/Library/Developer/CommandLineTools/usr/bin:"
            "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"
        ),
        "TMPDIR": str(temp),
        "all_proxy": "http://127.0.0.1:9",
        "http_proxy": "http://127.0.0.1:9",
        "https_proxy": "http://127.0.0.1:9",
    })
    env.pop("CODEX_HOME", None)
    env.pop("CLAUDE_CONFIG_DIR", None)
    for key in list(env):
        if any(word in key.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "API_KEY")):
            env.pop(key, None)
    return env


def _trusted_validation_git() -> str:
    """Return a fixed Apple Git executable that candidate code cannot replace."""
    for candidate in _APPLE_GIT_CANDIDATES:
        try:
            resolved = Path(_trusted_git_executable(candidate))
            metadata = resolved.stat()
        except OSError:
            continue
        if (
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_uid == 0
            and metadata.st_mode & 0o022 == 0
        ):
            return os.fspath(resolved)
    raise OSError("a root-owned, non-writable Apple Git executable is unavailable")


def _sandbox_profile(
    worktree: Path,
    source_root: Path,
    runtime_root: Path,
    *,
    allow_repository_git: bool = False,
) -> str:
    worktree_root = worktree.resolve()
    worktree_git = worktree_root / ".git"
    source_git = (source_root / ".git").resolve()
    allowed_read = [
        "/System/Library",
        "/usr/bin",
        "/usr/lib",
        "/usr/share",
        "/usr/sbin",
        "/bin",
        "/sbin",
        "/Library/Apple",
        "/Library/Frameworks",
        "/Library/Developer/CommandLineTools",
        "/opt/homebrew/bin",
        "/opt/homebrew/sbin",
        "/opt/homebrew/Cellar",
        "/opt/homebrew/opt",
        "/opt/homebrew/lib",
        "/opt/homebrew/share",
        # Apple's /usr/bin/git consults this system-owned selector before
        # dispatching to the Command Line Tools Git binary.
        "/var/select/developer_dir",
        "/private/var/select/developer_dir",
        str(worktree_root),
        str((source_root / ".venv").resolve()),
        str(runtime_root.resolve()),
        "/dev/null",
    ]
    if allow_repository_git:
        allowed_read.append(str(source_git))
    # macOS Seatbelt evaluates traversal of each absolute path separately from
    # access beneath it. Permit only the exact ancestor directories needed to
    # reach the allowlisted roots; without these literals, current macOS aborts
    # even a fixed executable before it starts.
    read_ancestors: set[str] = set()
    for raw_path in allowed_read:
        path = Path(raw_path)
        current = path if path.is_dir() else path.parent
        read_ancestors.update(str(parent) for parent in (current, *current.parents))
    literal_reads = "\n".join(
        f'  (literal {json.dumps(path)})'
        for path in sorted(read_ancestors, key=lambda value: (value.count("/"), value))
    )
    reads = "\n".join(f'  (subpath {json.dumps(path)})' for path in allowed_read)
    # Candidate-controlled tests must never write the staged worktree. Otherwise
    # conftest can swap in passing source and restore the reviewed bytes before
    # the controller's post-validation Git and digest checks. All legitimate
    # test output belongs under the private HOME/TMPDIR runtime.
    writable = [str(runtime_root.resolve())]
    writes = "\n".join(f'  (subpath {json.dumps(path)})' for path in writable)
    git_deny = json.dumps(str(worktree_git))
    source_git_deny = json.dumps(str(source_git))
    sensitive_read_denies = "\n".join(
        (
            '  (subpath "/Library/Keychains")',
            '  (subpath "/Library/Preferences")',
            # /System/Volumes/Data is an alternate path to the writable host
            # volume, including user homes, Keychains, and the source checkout.
            '  (subpath "/System/Volumes/Data")',
            '  (subpath "/opt/homebrew/etc")',
            '  (subpath "/opt/homebrew/var")',
        )
    )
    read_deny = ""
    if not allow_repository_git:
        read_deny = (
            "(deny file-read*\n"
            f"  (literal {git_deny})\n"
            f"  (subpath {git_deny})\n"
            f"  (literal {source_git_deny})\n"
            f"  (subpath {source_git_deny})\n"
            ")\n"
        )
    return (
        "(version 1)\n"
        "(deny default)\n"
        # Pytest and the repository's temporary-Git tests need child creation,
        # but candidate code never needs to inspect sibling controller/bot
        # processes or their environments.  Avoid the much broader `process*`
        # and `sysctl-read` permissions, which would defeat environment
        # scrubbing by exposing same-user process metadata.
        "(allow process-exec)\n"
        "(allow process-fork)\n"
        "(deny process-info*)\n"
        # Ordinary subprocesses are needed by the test suite, but every
        # descendant must remain in the controller-owned session/process group
        # so timeout cleanup cannot be escaped by daemonizing or requesting a
        # new process group. These names are compiled by the macOS sandbox
        # library against the running syscall table.
        "(deny syscall-unix\n"
        "  (syscall-number SYS_posix_spawn)\n"
        "  (syscall-number SYS_setsid)\n"
        "  (syscall-number SYS_setpgid)\n"
        ")\n"
        "(allow file-read*\n"
        f"{literal_reads}\n"
        f"{reads}\n)\n"
        "(deny file-read*\n"
        f"{sensitive_read_denies}\n)\n"
        "(allow file-write*\n"
        '  (literal "/dev/null")\n'
        f"{writes}\n)\n"
        f"{read_deny}"
        # Keep the candidate worktree and both Git metadata locations explicitly
        # immutable even if a future profile change broadens another write rule.
        "(deny file-write*\n"
        f"  (literal {json.dumps(str(worktree_root))})\n"
        f"  (subpath {json.dumps(str(worktree_root))})\n"
        f"  (literal {git_deny})\n"
        f"  (subpath {git_deny})\n"
        f"  (literal {source_git_deny})\n"
        f"  (subpath {source_git_deny})\n"
        ")\n"
        "(deny network*)\n"
    )
