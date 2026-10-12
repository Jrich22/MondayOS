"""Trusted bootstrap for commands executed inside the macOS validation sandbox.

``sandbox-exec`` launches its target with ``posix_spawn``. Delivery needs to
deny that syscall after containment begins because its session/process-group
flags can otherwise bypass direct ``setsid`` and ``setpgid`` syscall rules.
This bootstrap starts outside the sandbox, loads the fixed profile in-process,
then either executes a fixed command with ``execve`` or runs fixed Python code
and pytest in-process.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import resource
import subprocess
import sys
from pathlib import Path
from types import ModuleType

_MAX_EXTRA_PROCESSES = 16
_MAX_OPEN_FILES = 128
_MAX_FILE_BYTES = 16 * 1024 * 1024
_MAX_CPU_SECONDS = 120
_MAX_ADDRESS_HEADROOM_BYTES = 2 * 1024 * 1024 * 1024


def _arguments(argv: list[str]) -> tuple[Path, tuple[str, ...]]:
    if len(argv) < 3 or argv[1] != "--":
        raise ValueError("usage: sandbox_runner.py PROFILE -- COMMAND [ARG ...]")
    profile = Path(argv[0]).resolve(strict=True)
    command = tuple(argv[2:])
    if not command or not os.path.isabs(command[0]):
        raise ValueError("sandbox command must use an absolute executable path")
    return profile, command


def _is_pytest(command: tuple[str, ...]) -> bool:
    if len(command) < 3 or command[1:3] != ("-m", "pytest"):
        return False
    try:
        return Path(command[0]).resolve(strict=True) == Path(sys.executable).resolve(
            strict=True
        )
    except OSError:
        return False


def _is_isolated_python_code(command: tuple[str, ...]) -> bool:
    if len(command) < 4 or command[1:3] != ("-I", "-c"):
        return False
    try:
        return Path(command[0]).resolve(strict=True) == Path(sys.executable).resolve(
            strict=True
        )
    except OSError:
        return False


def _apply_profile(profile: bytes) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("the validation sandbox bootstrap requires macOS")
    library_path = ctypes.util.find_library("sandbox")
    if not library_path:
        raise RuntimeError("the macOS sandbox library is unavailable")
    library = ctypes.CDLL(library_path)
    sandbox_init = library.sandbox_init
    sandbox_init.argtypes = [
        ctypes.c_char_p,
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_char_p),
    ]
    sandbox_init.restype = ctypes.c_int
    sandbox_free_error = library.sandbox_free_error
    sandbox_free_error.argtypes = [ctypes.c_char_p]
    sandbox_free_error.restype = None

    error_buffer = ctypes.c_char_p()
    result = int(sandbox_init(profile, 0, ctypes.byref(error_buffer)))
    error = error_buffer.value.decode("utf-8", errors="replace") if error_buffer.value else ""
    if error_buffer.value:
        sandbox_free_error(error_buffer)
    if result != 0:
        raise RuntimeError(f"macOS sandbox profile was rejected: {error or 'unknown error'}")


def _current_process_executable() -> Path:
    """Return the kernel's real process image, not a framework launcher path."""
    library_path = ctypes.util.find_library("proc")
    if not library_path:
        raise RuntimeError("the macOS process library is unavailable")
    library = ctypes.CDLL(library_path)
    proc_pidpath = library.proc_pidpath
    proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    proc_pidpath.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(4096)
    length = int(proc_pidpath(os.getpid(), buffer, len(buffer)))
    if length <= 0:
        raise RuntimeError("could not resolve the trusted Python process image")
    executable = Path(os.fsdecode(buffer.value)).resolve(strict=True)
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise RuntimeError("the trusted Python process image is not executable")
    return executable


def _user_process_count() -> int:
    result = subprocess.run(  # noqa: S603 - fixed platform utility before sandboxing
        ["/bin/ps", "-axo", "uid="],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
        env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
    )
    uid = os.getuid()
    count = sum(1 for value in result.stdout.splitlines() if value.strip() == str(uid))
    if count <= 0:
        raise RuntimeError("could not establish the current user process count")
    return count


def _current_virtual_bytes() -> int:
    """Measure this interpreter before installing an incremental AS ceiling."""
    result = subprocess.run(  # noqa: S603 - fixed platform utility before sandboxing
        ["/bin/ps", "-o", "vsz=", "-p", str(os.getpid())],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
        env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
    )
    values = result.stdout.split()
    if len(values) != 1:
        raise RuntimeError("could not establish current virtual memory usage")
    virtual_kibibytes = int(values[0])
    if virtual_kibibytes <= 0:
        raise RuntimeError("could not establish current virtual memory usage")
    return virtual_kibibytes * 1024


def _set_hard_limit(kind: int, requested: int) -> None:
    soft, hard = resource.getrlimit(kind)
    finite_existing = [
        value for value in (soft, hard) if value != resource.RLIM_INFINITY
    ]
    ceiling = min(requested, *finite_existing) if finite_existing else requested
    if ceiling < 0:
        raise RuntimeError("invalid inherited resource limit")
    resource.setrlimit(kind, (ceiling, ceiling))


def _apply_resource_limits() -> None:
    """Bound candidate availability impact before importing any candidate code."""
    _set_hard_limit(resource.RLIMIT_NPROC, _user_process_count() + _MAX_EXTRA_PROCESSES)
    _set_hard_limit(resource.RLIMIT_NOFILE, _MAX_OPEN_FILES)
    _set_hard_limit(resource.RLIMIT_FSIZE, _MAX_FILE_BYTES)
    _set_hard_limit(resource.RLIMIT_CPU, _MAX_CPU_SECONDS)
    _set_hard_limit(
        resource.RLIMIT_AS,
        _current_virtual_bytes() + _MAX_ADDRESS_HEADROOM_BYTES,
    )
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _prepare_python_children(engine: Path) -> None:
    # Keep nested tests on fork/exec and point sys.executable at the real
    # framework engine. This avoids the Homebrew launcher, whose transition to
    # the engine itself uses the unconditionally denied posix_spawn syscall.
    subprocess.__dict__["_USE_POSIX_SPAWN"] = False
    sys.executable = os.fspath(engine)
    os.environ.pop("__PYVENV_LAUNCHER__", None)
    os.environ["MONDAYOS_VALIDATION_ENGINE"] = os.fspath(engine)
    os.environ["PYTHONEXECUTABLE"] = os.fspath(engine)
    site_paths = [path for path in sys.path if "site-packages" in path and Path(path).is_dir()]
    if site_paths:
        os.environ["PYTHONPATH"] = os.pathsep.join(site_paths)


def _run_pytest(pytest_module: ModuleType, command: tuple[str, ...]) -> int:
    # Python's macOS subprocess fast path uses posix_spawn. The sandbox denies
    # that syscall to prevent kernel-level session detachment, so repository
    # tests use the ordinary fork/exec path instead. Candidate code cannot
    # re-enable posix_spawn successfully because the kernel rule remains active.
    sys.path[0] = os.getcwd()
    sys.argv = [command[0], *command[3:]]
    result = pytest_module.main(list(command[3:]))
    return int(result)


def _run_isolated_python_code(command: tuple[str, ...]) -> int:
    sys.argv = ["-c", *command[4:]]
    namespace = {"__name__": "__main__", "__package__": None}
    exec(compile(command[3], "<string>", "exec"), namespace)  # noqa: S102
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    profile_path, command = _arguments(arguments)
    profile = profile_path.read_bytes()

    pytest_mode = _is_pytest(command)
    python_code_mode = _is_isolated_python_code(command)
    engine = _current_process_executable()
    _apply_resource_limits()
    _apply_profile(profile)
    if pytest_mode:
        # Import the trusted harness only after containment, while sys.path is
        # still isolated from the candidate worktree. Collection and every
        # candidate-controlled import happen later in pytest.main().
        _prepare_python_children(engine)
        import pytest

        return _run_pytest(pytest, command)
    if python_code_mode:
        _prepare_python_children(engine)
        return _run_isolated_python_code(command)

    os.execve(command[0], command, dict(os.environ))
    raise AssertionError("execve unexpectedly returned")


if __name__ == "__main__":
    raise SystemExit(main())
