"""Focused tests for delivery adapters, storage, and artifact security."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

import delivery.backends as backend_module
import delivery.github as github_module
import delivery.validation as validation_module
from delivery.backends import (
    ClaudeCodePatchBuilder,
    CodexArtifactReviewer,
    CodexPatchBuilder,
    ReviewDecision,
    _ProcessResult,
    _redact,
    _run_process,
)
from delivery.security import (
    ArtifactSecurityError,
    artifact_digest,
    proposed_paths,
    scan_staged_diff,
    verification_digest,
)
from delivery.store import DeliveryStore
from delivery.types import DeliveryJob
from delivery.validation import MondayValidationRunner, _sandbox_profile


def _patch() -> str:
    return (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1 +1 @@\n"
        "-OLD = True\n"
        "+OLD = False\n"
    )


def _fixture(*parts: str) -> str:
    """Assemble fake credentials without resembling one in the repository."""
    return "".join(parts)


def _context_repository(root: Path) -> None:
    subprocess.run(
        ["git", "init", "--initial-branch=main"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    (root / "app.py").write_text("OLD = True\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "app.py"],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.mark.parametrize(
    ("stdout", "stderr"),
    [
        ("Logged in using ChatGPT\n", ""),
        ("", "Logged in using ChatGPT\n"),
    ],
)
def test_codex_availability_accepts_chatgpt_status_from_either_stream(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    stderr: str,
) -> None:
    monkeypatch.setattr(
        backend_module,
        "_run_process",
        lambda *_args, **_kwargs: _ProcessResult(0, stdout, stderr),
    )

    availability = CodexPatchBuilder("/bin/codex").availability()

    assert availability.available is True
    assert availability.reason == "logged in with ChatGPT"


def test_codex_builder_uses_read_only_ephemeral_structured_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _context_repository(tmp_path)
    calls: list[dict[str, Any]] = []
    schemas: list[dict[str, Any]] = []
    real_run = backend_module._run_process

    def fake_run(argv: list[str], **kwargs: Any) -> _ProcessResult:
        if "--output-last-message" not in argv:
            return real_run(argv, **kwargs)
        calls.append(
            {
                "argv": argv,
                **kwargs,
                "runtime_was_directory": Path(kwargs["cwd"]).is_dir(),
            }
        )
        output = Path(argv[argv.index("--output-last-message") + 1])
        schema = json.loads(Path(argv[argv.index("--output-schema") + 1]).read_text())
        schemas.append(schema)
        payload = (
            {"paths": ["app.py"]}
            if "paths" in schema["properties"]
            else {"summary": "done", "patch": _patch()}
        )
        output.write_text(json.dumps(payload), encoding="utf-8")
        return _ProcessResult(0, "", "")

    monkeypatch.setattr(backend_module, "_run_process", fake_run)
    result = CodexPatchBuilder("/bin/codex").propose(
        worktree=tmp_path,
        objective="fix it; touch /tmp/never",
        feedback="none",
        timeout=30,
    )

    assert result.success is True
    assert result.patch == _patch()
    assert len(calls) == 2
    for call in calls:
        argv = call["argv"]
        assert argv[:2] == ["/bin/codex", "exec"]
        assert argv[argv.index("--sandbox") + 1] == "read-only"
        assert "--strict-config" in argv
        assert "--ephemeral" in argv
        assert "--skip-git-repo-check" in argv
        assert "--ignore-user-config" in argv
        assert "--ignore-rules" in argv
        assert argv.count("-c") == 3
        assert "project_doc_max_bytes=0" in argv
        assert "project_doc_fallback_filenames=[]" in argv
        assert "mcp_servers={}" in argv
        assert "skip_host_skill_discovery" in argv
        for feature in (
            "apps",
            "browser_use",
            "code_mode_host",
            "computer_use",
            "hooks",
            "plugins",
            "remote_plugin",
            "shell_tool",
            "shell_snapshot",
            "skill_search",
            "tool_call_mcp_elicitation",
            "unified_exec",
            "web_search_request",
            "workspace_dependencies",
            "worktrees",
        ):
            assert feature in argv
        assert "fix it" not in " ".join(argv)
        assert str(tmp_path) not in argv
        assert Path(call["cwd"]) == Path(argv[argv.index("--cd") + 1])
        assert call["runtime_was_directory"] is True
        assert Path(call["cwd"]).name.startswith("monday-delivery-codex-")
        assert "fix it; touch /tmp/never" in call["stdin"]
    assert '"path":"app.py"' in calls[0]["stdin"]
    assert '"content":"OLD = True\\n"' in calls[1]["stdin"]
    assert "clean exact-base checkout" in calls[0]["stdin"]
    assert "complete replacement patch" in calls[1]["stdin"]
    assert "incrementally" not in calls[1]["stdin"]
    assert schemas[0]["properties"]["paths"]["maxItems"] == backend_module._MAX_SELECTED_FILES
    assert schemas[1]["properties"]["patch"]["maxLength"] == backend_module._MAX_PATCH_BYTES


def test_codex_context_contains_only_tracked_regular_utf8_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _context_repository(tmp_path)
    (tmp_path / "binary.dat").write_bytes(b"\xff\xfe\x00")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text("OUTSIDE = True\n", encoding="utf-8")
    (tmp_path / "linked.py").symlink_to(outside)
    (tmp_path / "untracked.py").write_text("UNTRACKED = True\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "binary.dat", "linked.py"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    calls: list[dict[str, Any]] = []
    real_run = backend_module._run_process

    def fake_run(argv: list[str], **kwargs: Any) -> _ProcessResult:
        if "--output-last-message" not in argv:
            return real_run(argv, **kwargs)
        calls.append({"argv": argv, **kwargs})
        output = Path(argv[argv.index("--output-last-message") + 1])
        output.write_text(
            json.dumps(
                {"paths": ["app.py"]} if len(calls) == 1 else {"summary": "done", "patch": _patch()}
            ),
            encoding="utf-8",
        )
        return _ProcessResult(0, "", "")

    monkeypatch.setattr(backend_module, "_run_process", fake_run)
    proposal = CodexPatchBuilder("/bin/codex").propose(
        worktree=tmp_path,
        objective="change app",
        feedback="",
        timeout=30,
    )

    assert proposal.success is True
    manifest_prompt = calls[0]["stdin"]
    context_prompt = calls[1]["stdin"]
    assert "app.py" in manifest_prompt
    for forbidden in ("binary.dat", "linked.py", "untracked.py", "OUTSIDE = True"):
        assert forbidden not in manifest_prompt
        assert forbidden not in context_prompt


def test_codex_rejects_selector_path_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _context_repository(tmp_path)
    real_run = backend_module._run_process

    def fake_run(argv: list[str], **kwargs: Any) -> _ProcessResult:
        if "--output-last-message" not in argv:
            return real_run(argv, **kwargs)
        output = Path(argv[argv.index("--output-last-message") + 1])
        output.write_text(json.dumps({"paths": ["../outside.py"]}), encoding="utf-8")
        return _ProcessResult(0, "", "")

    monkeypatch.setattr(backend_module, "_run_process", fake_run)
    proposal = CodexPatchBuilder("/bin/codex").propose(
        worktree=tmp_path,
        objective="change app",
        feedback="",
        timeout=30,
    )

    assert proposal.success is False
    assert "unsafe path" in proposal.message


def test_codex_context_total_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _context_repository(tmp_path)
    (tmp_path / "second.py").write_text("SECOND = True\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "second.py"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    calls = 0
    real_run = backend_module._run_process

    def fake_run(argv: list[str], **kwargs: Any) -> _ProcessResult:
        nonlocal calls
        if "--output-last-message" not in argv:
            return real_run(argv, **kwargs)
        calls += 1
        output = Path(argv[argv.index("--output-last-message") + 1])
        output.write_text(json.dumps({"paths": ["app.py", "second.py"]}), encoding="utf-8")
        return _ProcessResult(0, "", "")

    monkeypatch.setattr(backend_module, "_MAX_CONTEXT_BYTES", 10)
    monkeypatch.setattr(backend_module, "_run_process", fake_run)
    proposal = CodexPatchBuilder("/bin/codex").propose(
        worktree=tmp_path,
        objective="change app",
        feedback="",
        timeout=30,
    )

    assert proposal.success is False
    assert "total byte limit" in proposal.message
    assert calls == 1


def test_codex_result_file_is_bounded_before_json_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _context_repository(tmp_path)
    real_run = backend_module._run_process

    def fake_run(argv: list[str], **kwargs: Any) -> _ProcessResult:
        if "--output-last-message" not in argv:
            return real_run(argv, **kwargs)
        output = Path(argv[argv.index("--output-last-message") + 1])
        output.write_text("x" * 65, encoding="utf-8")
        return _ProcessResult(0, "", "")

    monkeypatch.setattr(backend_module, "_MAX_MODEL_RESULT_BYTES", 64)
    monkeypatch.setattr(backend_module, "_run_process", fake_run)
    proposal = CodexPatchBuilder("/bin/codex").propose(
        worktree=tmp_path,
        objective="change app",
        feedback="",
        timeout=30,
    )

    assert proposal.success is False
    assert "output limit" in proposal.message


def test_codex_reviewer_is_fresh_read_only_and_schema_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> _ProcessResult:
        calls.append({"argv": argv, **kwargs})
        output = Path(argv[argv.index("--output-last-message") + 1])
        output.write_text(
            json.dumps(
                {
                    "verdict": "pass",
                    "confidence": "high",
                    "summary": "approved",
                    "findings": [],
                    "recommendations": [],
                }
            ),
            encoding="utf-8",
        )
        return _ProcessResult(0, "", "")

    monkeypatch.setattr(backend_module, "_run_process", fake_run)
    staged_diff = _patch()
    diff_sha256 = hashlib.sha256(staged_diff.encode("utf-8")).hexdigest()
    decision = CodexArtifactReviewer("/bin/codex").review(
        worktree=tmp_path,
        objective="objective",
        staged_diff=staged_diff,
        diff_sha256=diff_sha256,
        verification_json='[{"success": true}]',
        timeout=30,
    )

    assert decision.passed is True
    argv = calls[0]["argv"]
    assert "review" not in argv
    assert "--uncommitted" not in argv
    assert "--skip-git-repo-check" in argv
    assert str(tmp_path) not in argv
    assert Path(calls[0]["cwd"]) == Path(argv[argv.index("--cd") + 1])
    assert Path(calls[0]["cwd"]).name.startswith("monday-delivery-codex-")
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "--strict-config" in argv
    assert "project_doc_max_bytes=0" in argv
    assert "project_doc_fallback_filenames=[]" in argv
    assert "mcp_servers={}" in argv
    assert "skip_host_skill_discovery" in argv
    assert "shell_tool" in argv
    assert "unified_exec" in argv
    assert "CONTROLLER DIFF SHA256: " + diff_sha256 in calls[0]["stdin"]
    assert staged_diff in calls[0]["stdin"]


def test_codex_reviewer_rejects_a_diff_digest_mismatch_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launched = False

    def fake_run(*_args: Any, **_kwargs: Any) -> _ProcessResult:
        nonlocal launched
        launched = True
        return _ProcessResult(0, "", "")

    monkeypatch.setattr(backend_module, "_run_process", fake_run)

    with pytest.raises(ValueError, match="does not match its digest"):
        CodexArtifactReviewer("/bin/codex").review(
            worktree=tmp_path,
            objective="objective",
            staged_diff=_patch(),
            diff_sha256="0" * 64,
            verification_json="[]",
            timeout=30,
        )

    assert launched is False


def test_codex_reviewer_rejects_an_oversized_diff_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launched = False
    staged_diff = _patch()

    def fake_run(*_args: Any, **_kwargs: Any) -> _ProcessResult:
        nonlocal launched
        launched = True
        return _ProcessResult(0, "", "")

    monkeypatch.setattr(backend_module, "_MAX_REVIEW_DIFF_BYTES", 1)
    monkeypatch.setattr(backend_module, "_run_process", fake_run)

    with pytest.raises(ValueError, match="safe review limit"):
        CodexArtifactReviewer("/bin/codex").review(
            worktree=tmp_path,
            objective="objective",
            staged_diff=staged_diff,
            diff_sha256=hashlib.sha256(staged_diff.encode("utf-8")).hexdigest(),
            verification_json="[]",
            timeout=30,
        )

    assert launched is False


def test_review_pass_requires_no_findings() -> None:
    decision = ReviewDecision(
        verdict="pass",
        confidence="high",
        summary="Contradictory response",
        findings=["A material defect remains."],
    )

    assert decision.passed is False


def test_reviewer_payload_is_redacted_and_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(argv: list[str], **_kwargs: Any) -> _ProcessResult:
        output = Path(argv[argv.index("--output-last-message") + 1])
        output.write_text(
            json.dumps(
                {
                    "verdict": "pass",
                    "confidence": "high",
                    "summary": _fixture("token=", "abcdefghijklmnopqrstuvwxyz123456"),
                    "findings": ["x" * 10_000],
                    "recommendations": ["y" * 10_000],
                }
            ),
            encoding="utf-8",
        )
        return _ProcessResult(0, "", "")

    monkeypatch.setattr(backend_module, "_run_process", fake_run)
    staged_diff = _patch()
    decision = CodexArtifactReviewer("/bin/codex").review(
        worktree=tmp_path,
        objective="objective",
        staged_diff=staged_diff,
        diff_sha256=hashlib.sha256(staged_diff.encode("utf-8")).hexdigest(),
        verification_json="[]",
        timeout=30,
    )

    assert decision.passed is False
    assert "abcdefghijklmnopqrstuvwxyz" not in decision.summary
    assert "[REDACTED]" in decision.summary
    assert len(decision.findings[0].encode("utf-8")) <= backend_module._MAX_REVIEW_ITEM_BYTES
    assert len(decision.recommendations[0].encode("utf-8")) <= backend_module._MAX_REVIEW_ITEM_BYTES


def test_claude_builder_exposes_read_tools_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> _ProcessResult:
        calls.append({"argv": argv, **kwargs})
        payload = {"structured_output": {"summary": "done", "patch": _patch()}}
        return _ProcessResult(0, json.dumps(payload), "")

    monkeypatch.setattr(backend_module, "_run_process", fake_run)
    result = ClaudeCodePatchBuilder("/bin/claude").propose(
        worktree=tmp_path,
        objective="change app",
        feedback="",
        timeout=30,
    )

    assert result.success is True
    argv = calls[0]["argv"]
    assert "--restricted" in argv
    assert "--strict-mcp-config" in argv
    assert "--disable-slash-commands" in argv
    assert "--no-chrome" in argv
    assert argv[argv.index("--permission-mode") + 1] == "plan"
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    assert argv[argv.index("--tools") + 1] == "Read,Glob,Grep"
    assert "--dangerously-skip-permissions" not in argv
    assert "change app" not in " ".join(argv)
    assert "change app" in calls[0]["stdin"]


def test_process_capture_drains_both_streams_with_a_hard_memory_bound(tmp_path: Path) -> None:
    result = _run_process(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "sys.stdout.write('HEAD' + 'x' * 1000000 + 'TAIL'); sys.stdout.flush(); "
                "sys.stderr.write('HEAD' + 'y' * 1000000 + 'TAIL'); sys.stderr.flush()"
            ),
        ],
        cwd=tmp_path,
        timeout=10,
        env=backend_module._controller_env(),
    )

    assert result.returncode == 0
    assert len(result.stdout.encode("utf-8")) <= backend_module._MAX_CAPTURE_BYTES
    assert len(result.stderr.encode("utf-8")) <= backend_module._MAX_CAPTURE_BYTES
    assert result.stdout.startswith("HEAD")
    assert result.stderr.startswith("HEAD")
    assert "[output truncated]" in result.stdout
    assert "[output truncated]" in result.stderr
    assert result.stdout.endswith("TAIL")
    assert result.stderr.endswith("TAIL")


def test_process_stdin_is_literal_and_not_shell_interpreted(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    payload = f"hello; touch {marker}\n$(touch {marker})"
    result = _run_process(
        [sys.executable, "-c", "import sys; print(sys.stdin.read(), end='')"],
        cwd=tmp_path,
        timeout=5,
        env=backend_module._controller_env(),
        stdin=payload,
    )

    assert result.stdout == payload
    assert not marker.exists()


def test_process_runtime_storage_watchdog_fails_closed(tmp_path: Path) -> None:
    result = _run_process(
        [
            sys.executable,
            "-c",
            (
                "import pathlib, sys, time; "
                "pathlib.Path(sys.argv[1]).write_bytes(b'x' * 1048576); "
                "time.sleep(5)"
            ),
            str(tmp_path / "large.bin"),
        ],
        cwd=tmp_path,
        timeout=10,
        env=backend_module._controller_env(),
        runtime_root=tmp_path,
        max_runtime_bytes=4096,
        max_runtime_files=100,
    )

    assert result.returncode != 0
    assert "runtime storage limit exceeded" in result.stderr


def test_process_runtime_storage_watchdog_checks_a_fast_exit(tmp_path: Path) -> None:
    result = _run_process(
        [
            sys.executable,
            "-c",
            "import pathlib, sys; pathlib.Path(sys.argv[1]).write_bytes(b'x' * 1048576)",
            str(tmp_path / "large.bin"),
        ],
        cwd=tmp_path,
        timeout=10,
        env=backend_module._controller_env(),
        runtime_root=tmp_path,
        max_runtime_bytes=4096,
        max_runtime_files=100,
    )

    assert result.returncode != 0
    assert "runtime storage limit exceeded" in result.stderr


def test_process_runtime_storage_watchdog_counts_open_unlinked_files(
    tmp_path: Path,
) -> None:
    program = """
import os
import pathlib
import sys
import time

streams = []
payload = os.urandom(1024 * 1024)
for index in range(8):
    path = pathlib.Path(sys.argv[1]) / f"unlinked-{index}.bin"
    stream = path.open("wb")
    stream.write(payload)
    stream.flush()
    os.fsync(stream.fileno())
    path.unlink()
    streams.append(stream)
time.sleep(5)
"""
    result = _run_process(
        [sys.executable, "-c", program, str(tmp_path)],
        cwd=tmp_path,
        timeout=10,
        env=backend_module._controller_env(),
        runtime_root=tmp_path,
        max_runtime_bytes=2 * 1024 * 1024,
        max_runtime_files=100,
    )

    assert result.returncode != 0
    assert "runtime storage limit exceeded" in result.stderr


def test_runtime_storage_walk_stops_at_entry_limit_without_following_links(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    outside = tmp_path / "outside"
    runtime.mkdir()
    outside.mkdir()
    (outside / "controller-data.txt").write_text("private\n", encoding="utf-8")
    (runtime / "outside-alias").symlink_to(outside, target_is_directory=True)
    for index in range(50):
        (runtime / f"entry-{index:02d}.txt").write_text("x", encoding="utf-8")

    _, entries, exceeded = backend_module._runtime_usage(
        runtime,
        maximum_bytes=None,
        maximum_files=2,
    )

    assert exceeded is True
    assert entries == 3


def test_runtime_storage_walk_does_not_follow_directory_symlinks(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    outside = tmp_path / "outside"
    runtime.mkdir()
    outside.mkdir()
    (outside / "controller-data.txt").write_text("private\n", encoding="utf-8")
    (runtime / "outside-alias").symlink_to(outside, target_is_directory=True)

    _, entries, exceeded = backend_module._runtime_usage(
        runtime,
        maximum_bytes=None,
        maximum_files=10,
    )

    assert exceeded is False
    assert entries == 1


@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS process-group RSS")
def test_live_process_group_memory_watchdog(tmp_path: Path) -> None:
    result = _run_process(
        [
            sys.executable,
            "-c",
            "import time; payload = bytearray(96 * 1024 * 1024); time.sleep(5)",
        ],
        cwd=tmp_path,
        timeout=10,
        env=backend_module._controller_env(),
        max_group_rss_bytes=48 * 1024 * 1024,
    )

    assert result.returncode != 0
    assert "memory limit exceeded" in result.stderr


def _background_child_command() -> str:
    return (
        "import os, pathlib, signal, sys, time\n"
        "ready = pathlib.Path(sys.argv[1])\n"
        "stopped = pathlib.Path(sys.argv[2])\n"
        "def stop(*_args):\n"
        "    stopped.write_text('terminated', encoding='utf-8')\n"
        "    raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "ready.write_text(str(os.getpid()), encoding='utf-8')\n"
        "time.sleep(30)\n"
    )


def _parent_with_background_child(
    ready: Path,
    stopped: Path,
    child_pid: Path,
    *,
    wait: bool,
) -> list[str]:
    parent_code = (
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen(\n"
        "    [sys.executable, '-c', sys.argv[1], sys.argv[2], sys.argv[3]],\n"
        "    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
        ")\n"
        "pathlib.Path(sys.argv[4]).write_text(str(child.pid), encoding='utf-8')\n"
        "while not pathlib.Path(sys.argv[2]).exists():\n"
        "    time.sleep(0.01)\n"
    )
    if wait:
        parent_code += "time.sleep(30)\n"
    return [
        sys.executable,
        "-c",
        parent_code,
        _background_child_command(),
        str(ready),
        str(stopped),
        str(child_pid),
    ]


def _assert_process_gone(pid: int) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    raise AssertionError(f"background child {pid} survived process-group cleanup")


def test_background_child_is_terminated_after_parent_exits_normally(tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    stopped = tmp_path / "stopped"
    child_pid = tmp_path / "pid"
    result = _run_process(
        _parent_with_background_child(ready, stopped, child_pid, wait=False),
        cwd=tmp_path,
        timeout=5,
        env=backend_module._controller_env(),
    )

    assert result.returncode == 0
    assert stopped.read_text(encoding="utf-8") == "terminated"
    _assert_process_gone(int(child_pid.read_text(encoding="utf-8")))


def test_process_group_is_terminated_on_timeout(tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    stopped = tmp_path / "stopped"
    child_pid = tmp_path / "pid"
    with pytest.raises(subprocess.TimeoutExpired):
        _run_process(
            _parent_with_background_child(ready, stopped, child_pid, wait=True),
            cwd=tmp_path,
            timeout=0.25,
            env=backend_module._controller_env(),
        )

    assert stopped.read_text(encoding="utf-8") == "terminated"
    _assert_process_gone(int(child_pid.read_text(encoding="utf-8")))


def test_macos_validation_refuses_to_run_without_trusted_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    calls: list[list[str]] = []

    monkeypatch.setattr(validation_module.sys, "platform", "darwin")
    monkeypatch.setattr(validation_module, "_trusted_validation_git", lambda: "/usr/bin/git")

    def forbidden_run(argv: list[str], **_kwargs: Any) -> _ProcessResult:
        calls.append(argv)
        raise AssertionError("an unsandboxed validation command was launched")

    monkeypatch.setattr(validation_module, "_run_process", forbidden_run)
    with pytest.raises(RuntimeError, match="refusing to run repository tests"):
        MondayValidationRunner(timeout=1).run(
            worktree=worktree,
            source_root=tmp_path,
            changed_files=["app.py"],
        )
    assert calls == []


def test_validation_refuses_unsupported_platform_without_running_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    calls: list[list[str]] = []
    monkeypatch.setattr(validation_module.sys, "platform", "linux")
    monkeypatch.setattr(
        validation_module,
        "_run_process",
        lambda argv, **_kwargs: calls.append(argv),
    )

    with pytest.raises(RuntimeError, match="supported containment"):
        MondayValidationRunner(timeout=1).run(
            worktree=worktree,
            source_root=tmp_path,
            changed_files=["app.py"],
        )
    assert calls == []


def test_validation_availability_runs_contained_git_and_python_smoke_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    python.chmod(0o700)
    bootstrap = tmp_path / "delivery" / "sandbox_runner.py"
    bootstrap.parent.mkdir()
    bootstrap.write_text("# trusted test bootstrap\n", encoding="utf-8")
    calls: list[list[str]] = []

    monkeypatch.setattr(validation_module.sys, "platform", "darwin")
    monkeypatch.setattr(validation_module, "_trusted_validation_git", lambda: "/usr/bin/git")

    def fake_run(argv: list[str], **kwargs: Any) -> _ProcessResult:
        calls.append(argv)
        assert kwargs["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
        return _ProcessResult(0, "ready", "")

    monkeypatch.setattr(validation_module, "_run_process", fake_run)
    availability = MondayValidationRunner(source_root=tmp_path, timeout=1).availability()

    assert availability.available is True
    assert len(calls) == 2
    assert calls[0][:3] == calls[1][:3] == [
        str(python.absolute()),
        "-I",
        str(bootstrap.resolve()),
    ]
    assert calls[0][-7:] == [
        "/usr/bin/git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "rev-parse",
        "--is-inside-work-tree",
    ]
    assert calls[1][5] == str(python)
    assert calls[1][6:8] == ["-I", "-c"]


def test_validation_sandbox_explicitly_denies_worktree_git_writes(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    source = tmp_path / "source"
    runtime = tmp_path / "runtime"
    worktree.mkdir()
    source.mkdir()
    runtime.mkdir()
    profile = _sandbox_profile(worktree, source, runtime)
    git_path = json.dumps(str(worktree.resolve() / ".git"))
    source_git = json.dumps(str((source / ".git").resolve()))
    worktree_root = json.dumps(str(worktree.resolve()))

    assert f"(literal {worktree_root})" in profile
    assert f"(subpath {worktree_root})" in profile
    assert f"(literal {git_path})" in profile
    assert f"(subpath {git_path})" in profile
    assert f"(literal {source_git})" in profile
    assert f"(subpath {source_git})" in profile
    assert '(literal "/")' in profile
    assert '(literal "/dev/null")' in profile
    assert '(subpath "/private/var/select/developer_dir")' in profile
    assert '(subpath "/var/select/developer_dir")' in profile
    assert '(subpath "/System/Library")' in profile
    assert '(subpath "/System")' not in profile
    assert '(subpath "/Library")' not in profile
    assert '(subpath "/opt/homebrew")' not in profile
    for sensitive_root in (
        "/Library/Keychains",
        "/Library/Preferences",
        "/System/Volumes/Data",
        "/opt/homebrew/etc",
        "/opt/homebrew/var",
    ):
        assert f'(subpath "{sensitive_root}")' in profile
    assert "(allow mach-lookup)" not in profile
    assert "(allow process*)" not in profile
    assert "(allow sysctl-read)" not in profile
    assert "(allow process-exec)" in profile
    assert "(allow process-fork)" in profile
    assert "(deny process-info*)" in profile
    assert "(syscall-number SYS_setsid)" in profile
    assert "(syscall-number SYS_setpgid)" in profile
    assert "(syscall-number SYS_posix_spawn)" in profile
    assert "(process-path" not in profile
    assert "(deny file-read*" in profile
    assert profile.index("(deny file-write*") > profile.index("(allow file-write*")


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="requires macOS Seatbelt",
)
def test_live_validation_sandbox_keeps_worktree_read_only(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    runtime = tmp_path / "runtime"
    worktree.mkdir()
    runtime.mkdir()
    sentinel = worktree / "sentinel.txt"
    sentinel.write_text("reviewed\n", encoding="utf-8")
    allowed = runtime / "allowed.txt"
    hardlink = runtime / "worktree-hardlink.txt"
    host_secret = tmp_path / "controller-secret.txt"
    host_secret.write_text("must remain private\n", encoding="utf-8")
    data_alias = Path("/System/Volumes/Data") / host_secret.resolve().relative_to("/")
    assert data_alias.is_file()
    runner = MondayValidationRunner(
        source_root=Path(__file__).resolve().parent.parent,
        timeout=10,
    )
    environment = validation_module._validation_env(runtime)
    command = runner._sandboxed(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import os, pathlib, sys; "
                "sentinel = pathlib.Path(sys.argv[1]); "
                "allowed = pathlib.Path(sys.argv[2]); "
                "host_alias = pathlib.Path(sys.argv[3]); "
                "hardlink = pathlib.Path(sys.argv[4]); "
                "denied = False; "
                "\ntry:\n sentinel.write_text('swapped\\n', encoding='utf-8')"
                "\nexcept PermissionError:\n denied = True"
                "\nif not denied:\n raise SystemExit(9)"
                "\ntry:\n host_alias.read_text(encoding='utf-8')"
                "\nexcept PermissionError:\n pass"
                "\nelse:\n raise SystemExit(10)"
                "\ntry:\n os.link(sentinel, hardlink)"
                "\nexcept PermissionError:\n pass"
                "\nelse:\n raise SystemExit(11)"
                "\nallowed.write_text('runtime write allowed\\n', encoding='utf-8')"
            ),
            str(sentinel),
            str(allowed),
            str(data_alias),
            str(hardlink),
        ],
        worktree,
        Path(__file__).resolve().parent.parent,
        runtime,
        allow_repository_git=False,
    )

    result = _run_process(
        command,
        cwd=worktree,
        timeout=10,
        env=environment,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert sentinel.read_text(encoding="utf-8") == "reviewed\n"
    assert allowed.read_text(encoding="utf-8") == "runtime write allowed\n"
    assert not hardlink.exists()


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="requires macOS Seatbelt",
)
def test_live_validation_sandbox_denies_process_detachment(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    runtime = tmp_path / "runtime"
    worktree.mkdir()
    runtime.mkdir()
    runner = MondayValidationRunner(
        source_root=Path(__file__).resolve().parent.parent,
        timeout=10,
    )
    environment = validation_module._validation_env(runtime)
    program = """
import errno
import os
import pathlib
import resource
import signal
import subprocess
import sys

engine = pathlib.Path(sys.executable).resolve()
trusted_engine = pathlib.Path(os.environ["MONDAYOS_VALIDATION_ENGINE"]).resolve()
if engine != trusted_engine:
    raise SystemExit(15)

for kind in (
    resource.RLIMIT_NPROC,
    resource.RLIMIT_NOFILE,
    resource.RLIMIT_FSIZE,
    resource.RLIMIT_CPU,
    resource.RLIMIT_AS,
):
    if resource.getrlimit(kind)[1] == resource.RLIM_INFINITY:
        raise SystemExit(16)

pid = os.fork()
if pid == 0:
    hard_file_limit = resource.getrlimit(resource.RLIMIT_FSIZE)[1]
    try:
        with open(sys.argv[2], "wb") as stream:
            stream.seek(hard_file_limit)
            stream.write(b"x")
    except OSError as exc:
        os._exit(0 if exc.errno == errno.EFBIG else 17)
    os._exit(18)
_, status = os.waitpid(pid, 0)
if not (
    os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
) and not (
    os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGXFSZ
):
    raise SystemExit(19)

engine_record = pathlib.Path(sys.argv[3])
engine_record.write_text(f"{engine}\\n{trusted_engine}\\n", encoding="utf-8")
subprocess.run(
    [
        sys.executable,
        "-c",
        "import pathlib, sys; "
        "with_path = pathlib.Path(sys.argv[1]); "
        "with_path.write_text(with_path.read_text() + sys.executable + '\\\\n')",
        str(engine_record),
    ],
    check=True,
)

pid = os.fork()
if pid == 0:
    denied = 0
    for operation in (os.setsid, lambda: os.setpgid(0, 0)):
        try:
            operation()
        except PermissionError:
            denied += 1
    os._exit(0 if denied == 2 else 9)

_, status = os.waitpid(pid, 0)
if status != 0:
    raise SystemExit(10)

for options in ({"start_new_session": True}, {"process_group": 0}):
    try:
        child = subprocess.Popen([sys.executable, "-c", "pass"], **options)
    except PermissionError:
        continue
    child.wait()
    raise SystemExit(11)

for options in ({"setsid": True}, {"setpgroup": 0}):
    try:
        child_pid = os.posix_spawn(
            sys.executable,
            [sys.executable, "-c", "pass"],
            os.environ,
            **options,
        )
    except PermissionError:
        continue
    os.waitpid(child_pid, 0)
    raise SystemExit(12)

pid = os.fork()
if pid == 0:
    os.execve(
        "/usr/bin/touch",
        ["/usr/bin/touch", sys.argv[1]],
        os.environ,
    )
_, status = os.waitpid(pid, 0)
if status != 0:
    raise SystemExit(13)
"""
    fork_exec_marker = runtime / "fork-exec-ok.txt"
    oversized_file = runtime / "must-not-grow.bin"
    engine_record = runtime / "python-engine.txt"
    command = runner._sandboxed(
        [
            sys.executable,
            "-I",
            "-c",
            program,
            str(fork_exec_marker),
            str(oversized_file),
            str(engine_record),
        ],
        worktree,
        Path(__file__).resolve().parent.parent,
        runtime,
        allow_repository_git=False,
    )

    result = _run_process(
        command,
        cwd=worktree,
        timeout=10,
        env=environment,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert fork_exec_marker.is_file()
    engine_paths = [Path(value).resolve() for value in engine_record.read_text().splitlines()]
    assert len(engine_paths) == 3
    assert engine_paths[0] == engine_paths[1] == engine_paths[2]
    assert oversized_file.stat().st_size <= 16 * 1024 * 1024

    # A child that stays in the sandbox's process group must not outlive the
    # outer controller and write after its direct parent exits.
    lingering_pid = runtime / "lingering-child.pid"
    orphan_program = """
import os
import sys

pid = os.fork()
if pid == 0:
    with open(sys.argv[1], "w", encoding="utf-8") as stream:
        stream.write(str(os.getpid()))
    os.execve(
        "/bin/sleep",
        ["/bin/sleep", "30"],
        os.environ,
    )
"""
    orphan_command = runner._sandboxed(
        [sys.executable, "-I", "-c", orphan_program, str(lingering_pid)],
        worktree,
        Path(__file__).resolve().parent.parent,
        runtime,
        allow_repository_git=False,
    )
    orphan_result = _run_process(
        orphan_command,
        cwd=worktree,
        timeout=10,
        env=environment,
    )

    assert orphan_result.returncode == 0, orphan_result.stderr or orphan_result.stdout
    child_pid = int(lingering_pid.read_text(encoding="utf-8"))
    _assert_process_gone(child_pid)


def test_validation_uses_private_cleaned_runtime_and_records_socket_exclusion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    bootstrap = tmp_path / "delivery" / "sandbox_runner.py"
    bootstrap.parent.mkdir()
    bootstrap.write_text("# trusted test bootstrap\n", encoding="utf-8")
    environments: list[dict[str, str]] = []
    commands: list[list[str]] = []

    monkeypatch.setattr(validation_module.sys, "platform", "darwin")
    monkeypatch.setattr(validation_module, "_trusted_validation_git", lambda: "/usr/bin/git")

    def fake_run(argv: list[str], **kwargs: Any) -> _ProcessResult:
        commands.append(argv)
        environment = dict(kwargs["env"])
        environments.append(environment)
        home = Path(environment["HOME"])
        temp = Path(environment["TMPDIR"])
        assert home.is_dir()
        assert temp.is_dir()
        assert home.stat().st_mode & 0o777 == 0o700
        assert temp.stat().st_mode & 0o777 == 0o700
        assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
        assert environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
        assert environment["GIT_CONFIG_GLOBAL"] == os.devnull
        assert environment["GIT_CONFIG_SYSTEM"] == os.devnull
        assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
        assert environment["LANG"] == "C.UTF-8"
        assert environment["LC_ALL"] == "C.UTF-8"
        assert environment["HTTP_PROXY"] == "http://127.0.0.1:9"
        assert environment["HTTPS_PROXY"] == "http://127.0.0.1:9"
        assert environment["ALL_PROXY"] == "http://127.0.0.1:9"
        assert environment["MONDAYOS_INTERNAL_CONTAINED_VALIDATION"] == "1"
        assert "NO_PROXY" not in environment
        assert "no_proxy" not in environment
        return _ProcessResult(0, "passed", "")

    monkeypatch.setattr(validation_module, "_run_process", fake_run)
    results = MondayValidationRunner(timeout=1).run(
        worktree=worktree,
        source_root=tmp_path,
        changed_files=["app.py"],
    )

    assert len(results) == 2
    pytest_result = results[-1]
    assert pytest_result.argv[:12] == [
        str(python),
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-c",
        os.devnull,
        "--noconftest",
        "--rootdir",
        str(worktree.resolve()),
        "tests",
    ]
    for exclusion in validation_module._SANDBOXED_PYTEST_EXCLUSIONS:
        assert f"--deselect={exclusion}" in pytest_result.argv
        assert exclusion in pytest_result.output
    assert "Sandboxed validation exclusions" in pytest_result.output
    assert "source Git history is withheld" in pytest_result.output
    assert "candidate child-process signaling is denied" in pytest_result.output
    git_argv = commands[0][5:]
    assert git_argv == [
        "/usr/bin/git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "diff",
        "--cached",
        "--check",
        "--no-ext-diff",
        "--no-textconv",
    ]
    for environment in environments:
        assert not Path(environment["HOME"]).exists()
        assert not Path(environment["TMPDIR"]).exists()


@pytest.mark.parametrize(
    "patch",
    [
        "not a patch",
        "diff --git a/../escape b/../escape\n",
        "diff --git a/file.bin b/file.bin\nGIT binary patch\n",
        'diff --git "a/quoted file" "b/quoted file"\n',
    ],
)
def test_proposed_patch_screen_rejects_ambiguous_or_unsafe_input(patch: str) -> None:
    with pytest.raises(ArtifactSecurityError):
        proposed_paths(patch)


@pytest.mark.parametrize(
    "secret",
    [
        _fixture("-----BEGIN ", "PRIVATE KEY-----"),
        _fixture("AKIA", "ABCDEFGHIJKLMNOP"),
        _fixture("ghp_", "abcdefghijklmnopqrstuvwxyz1234567890"),
        _fixture("sk-", "abcdefghijklmnopqrstuvwxyz123456"),
        _fixture("123456789", ":", "abcdefghijklmnopqrstuvwxyz123456"),
        _fixture("xoxb-", "123456789012-abcdefghijklmnopqrstuvwxyz"),
        _fixture("sk_", "live_abcdefghijklmnopqrstuvwxyz"),
        _fixture("AIza", "abcdefghijklmnopqrstuvwxyz1234567890"),
        _fixture(
            "eyJhbGciOiJIUzI1NiJ9",
            ".eyJzdWIiOiIxMjM0NTY3ODkwIn0",
            ".signaturevalue",
        ),
        _fixture(
            "postgresql://",
            "service:supersecretpassword@database.internal/app",
        ),
        _fixture("Authorization: ", "Bearer ", "abcdefghijklmnopqrstuvwxyz"),
        _fixture("client_", "sec", 'ret = "this-is-a-real-secret-value"'),
        _fixture("-----BEGIN ENCRYPTED ", "PRIVATE KEY-----"),
        _fixture("token=", "abcdefghijklmnopqrstuvwxyz123456"),
    ],
)
def test_staged_diff_secret_scanner_rejects_added_credentials(secret: str) -> None:
    with pytest.raises(ArtifactSecurityError):
        scan_staged_diff(f"diff --git a/x b/x\n+++ b/x\n+{secret}\n")


def test_staged_diff_secret_scanner_rejects_removed_credentials() -> None:
    removed = _fixture("sk-", "abcdefghijklmnopqrstuvwxyz123456")

    with pytest.raises(ArtifactSecurityError):
        scan_staged_diff(
            "diff --git a/settings.py b/settings.py\n"
            "--- a/settings.py\n"
            "+++ b/settings.py\n"
            "@@ -1 +1 @@\n"
            "-API_" f"KEY = '{removed}'\n"
            "+API_" "KEY = os.environ['API_KEY']\n"
        )


def test_artifact_digest_changes_for_diff_evidence_or_objective() -> None:
    verification = [{"name": "tests", "success": True}]
    check = verification_digest(verification)
    first = artifact_digest(
        base_sha="a" * 40,
        objective="one",
        diff=_patch(),
        verification_sha256=check,
    )
    second = artifact_digest(
        base_sha="a" * 40,
        objective="two",
        diff=_patch(),
        verification_sha256=check,
    )
    third = artifact_digest(
        base_sha="a" * 40,
        objective="one",
        diff=_patch() + "\n",
        verification_sha256=check,
    )
    assert first != second
    assert first != third
    assert all(len(value) == 64 for value in first)


def test_delivery_store_reserves_identity_and_rejects_tampered_record(tmp_path: Path) -> None:
    store = DeliveryStore(tmp_path)
    job = DeliveryJob(
        delivery_id="delivery-one",
        task_id="TASK-0001",
        objective="objective",
        repo_root=str(tmp_path),
        created_at="2026-01-01T00:00:00+00:00",
    )

    assert store.reserve(job) is None
    existing = store.reserve(job)
    assert existing is not None
    assert existing.task_id == "TASK-0001"

    record = store.records / "delivery-one.json"
    payload = json.loads(record.read_text(encoding="utf-8"))
    payload["delivery_id"] = "delivery-another"
    record.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="identifies itself"):
        store.get("delivery-one")


def test_delivery_store_refuses_symlinked_runtime_ancestor(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "logs").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="real directory"):
        DeliveryStore(project)


@pytest.mark.parametrize(
    ("label", "value", "secret_fragment"),
    [
        ("assignment", "password=hunter2", "hunter2"),
        (
            "JSON assignment",
            _fixture('"access_', "to", 'ken": "very-secret-token-value"'),
            "very-secret",
        ),
        ("OpenAI", _fixture("sk-", "abcdefghijklmnopqrstuvwxyz"), "sk-"),
        ("AWS", _fixture("AKIA", "ABCDEFGHIJKLMNOP"), "AKIA"),
        (
            "GitHub classic",
            _fixture("ghp_", "abcdefghijklmnopqrstuvwxyz1234567890"),
            "ghp_",
        ),
        (
            "GitHub fine-grained",
            _fixture("github_", "pat_11AA22BB33CC44DD55EE66FF"),
            "github_pat_",
        ),
        (
            "Slack",
            _fixture("xoxb-", "123456789012-abcdefghijklmnopqrstuvwxyz"),
            "xoxb-",
        ),
        ("Stripe", _fixture("sk_", "live_abcdefghijklmnopqrstuvwxyz"), "sk_live_"),
        ("Google", _fixture("AIza", "abcdefghijklmnopqrstuvwxyz1234567890"), "AIza"),
        (
            "database URL",
            _fixture(
                "postgresql://",
                "service:supersecretpassword@database.internal/app",
            ),
            "supersecretpassword",
        ),
        (
            "private key",
            _fixture("-----BEGIN OPENSSH ", "PRIVATE KEY-----"),
            "PRIVATE KEY",
        ),
        (
            "PGP private key",
            _fixture("-----BEGIN PGP ", "PRIVATE KEY BLOCK-----"),
            "PGP PRIVATE KEY",
        ),
        (
            "JWT",
            _fixture(
                "eyJhbGciOiJIUzI1NiJ9",
                ".eyJzdWIiOiIxMjM0NTY3ODkwIn0",
                ".signature123",
            ),
            "eyJ",
        ),
        (
            "bearer",
            _fixture("Authorization: ", "Bearer ", "abcdefghijklmnopqrstuvwxyz"),
            "abcdefghijkl",
        ),
    ],
)
def test_redaction_removes_common_credentials(label: str, value: str, secret_fragment: str) -> None:
    del label
    redacted = _redact(value)
    assert secret_fragment not in redacted
    assert "[REDACTED]" in redacted


def test_github_reuses_only_open_pr_for_exact_reviewed_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha = "a" * 40
    row = {
        "number": 7,
        "url": "https://github.com/owner/repo/pull/7",
        "state": "OPEN",
        "baseRefName": "main",
        "headRefName": "codex/task-12345678",
        "headRefOid": sha,
        "isCrossRepository": False,
    }
    monkeypatch.setattr(
        github_module,
        "_run_process",
        lambda *_args, **_kwargs: _ProcessResult(0, json.dumps([row]), ""),
    )

    result = github_module.GhCliClient("/bin/gh").ensure_pull_request(
        repo_root=tmp_path,
        repository="owner/repo",
        base="main",
        branch="codex/task-12345678",
        title="Task",
        body="Body",
        expected_sha=sha,
    )

    assert result.success is True
    assert result.number == 7

    row["state"] = "CLOSED"
    rejected = github_module.GhCliClient("/bin/gh").ensure_pull_request(
        repo_root=tmp_path,
        repository="owner/repo",
        base="main",
        branch="codex/task-12345678",
        title="Task",
        body="Body",
        expected_sha=sha,
    )
    assert rejected.success is False


def test_github_verifies_new_pr_after_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha = "b" * 40
    responses = iter(
        [
            _ProcessResult(0, "[]", ""),
            _ProcessResult(0, "https://github.com/owner/repo/pull/9\n", ""),
            _ProcessResult(
                0,
                json.dumps(
                    {
                        "number": 9,
                        "url": "https://github.com/owner/repo/pull/9",
                        "state": "OPEN",
                        "baseRefName": "main",
                        "headRefName": "codex/task-12345678",
                        "headRefOid": sha,
                        "isCrossRepository": False,
                    }
                ),
                "",
            ),
        ]
    )
    monkeypatch.setattr(
        github_module,
        "_run_process",
        lambda *_args, **_kwargs: next(responses),
    )

    result = github_module.GhCliClient("/bin/gh").ensure_pull_request(
        repo_root=tmp_path,
        repository="owner/repo",
        base="main",
        branch="codex/task-12345678",
        title="Task",
        body="Body",
        expected_sha=sha,
    )
    assert result.success is True
    assert result.number == 9
