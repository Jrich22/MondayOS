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
from delivery.types import MAX_DELIVERY_RETRY_AFTER_SECONDS, DeliveryJob
from delivery.validation import MondayValidationRunner, _sandbox_profile

_TEST_CLI = os.fspath(Path(sys.executable).resolve())


@pytest.fixture(autouse=True)
def _trust_test_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let fake CLI tests use the CI Python without weakening production roots."""
    roots = (*backend_module._TRUSTED_EXECUTABLE_ROOTS, Path(_TEST_CLI).parent)
    monkeypatch.setattr(backend_module, "_TRUSTED_EXECUTABLE_ROOTS", roots)


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

    availability = CodexPatchBuilder(_TEST_CLI).availability()

    assert availability.available is True
    assert availability.reason == "logged in with ChatGPT"


def test_subscription_cli_discovery_never_uses_ambient_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "path-cli-ran"
    for name in ("codex", "claude", "gh"):
        executable = tmp_path / name
        executable.write_text(
            f"#!/bin/sh\ntouch '{marker!s}'\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", os.fspath(tmp_path))
    candidates = dict(backend_module._TRUSTED_TOOL_CANDIDATES)
    candidates.update({"codex": (), "claude": (), "gh": ()})
    monkeypatch.setattr(backend_module, "_TRUSTED_TOOL_CANDIDATES", candidates)

    assert CodexPatchBuilder()._executable == ""
    assert ClaudeCodePatchBuilder()._executable == ""
    assert github_module.GhCliClient()._executable == ""
    assert os.fspath(tmp_path) not in backend_module._controller_env()["PATH"].split(
        os.pathsep
    )
    assert not marker.exists()


def test_apple_command_line_tools_git_is_in_a_trusted_installation_root() -> None:
    candidate = Path(backend_module._TRUSTED_TOOL_CANDIDATES["git"][0])

    assert any(
        candidate.is_relative_to(root)
        for root in backend_module._TRUSTED_EXECUTABLE_ROOTS
    )


def test_target_repository_cli_is_rejected_before_any_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _context_repository(tmp_path)
    marker = tmp_path / "target-cli-ran"
    executable = tmp_path / "controller-cli"
    executable.write_text(
        f"#!/bin/sh\ntouch '{marker!s}'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setattr(backend_module, "_TRUSTED_EXECUTABLE_ROOTS", (tmp_path,))

    codex = CodexPatchBuilder(os.fspath(executable))
    claude = ClaudeCodePatchBuilder(os.fspath(executable))
    github = github_module.GhCliClient(os.fspath(executable))
    codex_result = codex.propose(
        worktree=tmp_path,
        objective="change app",
        feedback="",
        timeout=30,
    )
    claude_result = claude.propose(
        worktree=tmp_path,
        objective="change app",
        feedback="",
        timeout=30,
    )
    github_result = github.ensure_pull_request(
        repo_root=tmp_path,
        repository="owner/repo",
        base="main",
        branch="codex/task-12345678",
        title="Task",
        body="Body",
        expected_sha="a" * 40,
    )

    assert codex_result.success is False
    assert claude_result.success is False
    assert github_result.success is False
    assert "inside the target repository" in codex_result.message
    assert "inside the target repository" in claude_result.message
    assert "inside the target repository" in github_result.message
    assert not marker.exists()


def test_pinned_cli_inode_replacement_is_rejected_before_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    installation = tmp_path / "installation"
    project.mkdir()
    installation.mkdir()
    _context_repository(project)
    marker = tmp_path / "replacement-ran"
    executable = installation / "codex"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(backend_module, "_TRUSTED_EXECUTABLE_ROOTS", (tmp_path,))
    builder = CodexPatchBuilder(os.fspath(executable))

    # Keep the original inode allocated so filesystems that eagerly reuse
    # deleted inode numbers cannot make the replacement look unchanged.
    executable.rename(installation / "codex-original")
    executable.write_text(
        f"#!/bin/sh\ntouch '{marker!s}'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    result = builder.propose(
        worktree=project,
        objective="change app",
        feedback="",
        timeout=30,
    )

    assert result.success is False
    assert "identity changed" in result.message
    assert not marker.exists()


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
    result = CodexPatchBuilder(_TEST_CLI).propose(
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
        assert argv[:2] == [_TEST_CLI, "exec"]
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
        assert Path(call["env"]["TMPDIR"]) == Path(call["cwd"])
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
    (tmp_path / ".env.production").write_text(
        "INTERNAL_CODENAME=blue-orchid\n",
        encoding="utf-8",
    )
    protected_files = {
        ".npmrc": "REGISTRY_ALIAS=violet-comet\n",
        ".pypirc": "PROFILE_ALIAS=amber-sparrow\n",
        ".netrc": "MACHINE_ALIAS=silver-maple\n",
        "credentials/session.json": '{"label":"quiet-river"}\n',
        "secrets/runtime.txt": "RUNTIME_ALIAS=golden-canyon\n",
    }
    for relative, content in protected_files.items():
        protected = tmp_path / relative
        protected.parent.mkdir(parents=True, exist_ok=True)
        protected.write_text(content, encoding="utf-8")
    subprocess.run(
        [
            "git",
            "add",
            "binary.dat",
            "linked.py",
            ".env.production",
            *protected_files,
        ],
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
    proposal = CodexPatchBuilder(_TEST_CLI).propose(
        worktree=tmp_path,
        objective="change app",
        feedback="",
        timeout=30,
    )

    assert proposal.success is True
    manifest_prompt = calls[0]["stdin"]
    context_prompt = calls[1]["stdin"]
    assert "app.py" in manifest_prompt
    for forbidden in (
        "binary.dat",
        "linked.py",
        "untracked.py",
        ".env.production",
        "blue-orchid",
        ".npmrc",
        "violet-comet",
        ".pypirc",
        "amber-sparrow",
        ".netrc",
        "silver-maple",
        "credentials/session.json",
        "quiet-river",
        "secrets/runtime.txt",
        "golden-canyon",
        "OUTSIDE = True",
    ):
        assert forbidden not in manifest_prompt
        assert forbidden not in context_prompt


@pytest.mark.parametrize(
    "relative",
    (
        ".env.production",
        ".npmrc",
        ".pypirc",
        ".netrc",
        "credentials/session.json",
        "secrets/runtime.txt",
    ),
)
def test_selected_context_revalidates_protected_paths(
    tmp_path: Path,
    relative: str,
) -> None:
    _context_repository(tmp_path)
    payload = b"INTERNAL_CODENAME=blue-orchid\n"
    protected = tmp_path / relative
    protected.parent.mkdir(parents=True, exist_ok=True)
    protected.write_bytes(payload)
    entry = backend_module._ManifestEntry(
        path=relative,
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    with pytest.raises(ValueError, match="protected path") as error:
        backend_module._selected_repository_context(
            tmp_path,
            (relative,),
            (entry,),
        )
    assert relative not in str(error.value)
    assert "blue-orchid" not in str(error.value)


def test_codex_rejects_credential_shaped_filenames_before_model_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subprocess.run(
        ["git", "init", "--initial-branch=main"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    secret = _fixture("ghp_", "abcdefghijklmnopqrstuvwxyz1234567890")
    secret_path = tmp_path / f"token={secret}"
    secret_path.write_text("SAFE = True\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "--", secret_path.name],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    model_launched = False
    real_run = backend_module._run_process

    def fake_run(argv: list[str], **kwargs: Any) -> _ProcessResult:
        nonlocal model_launched
        if "--output-last-message" in argv:
            model_launched = True
        return real_run(argv, **kwargs)

    monkeypatch.setattr(backend_module, "_run_process", fake_run)
    proposal = CodexPatchBuilder(_TEST_CLI).propose(
        worktree=tmp_path,
        objective="change app",
        feedback="",
        timeout=30,
    )

    assert proposal.success is False
    assert model_launched is False
    assert secret not in proposal.message
    assert proposal.message


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
    proposal = CodexPatchBuilder(_TEST_CLI).propose(
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
    proposal = CodexPatchBuilder(_TEST_CLI).propose(
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
    proposal = CodexPatchBuilder(_TEST_CLI).propose(
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
    decision = CodexArtifactReviewer(_TEST_CLI).review(
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
    assert Path(calls[0]["env"]["TMPDIR"]) == Path(calls[0]["cwd"])
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
        CodexArtifactReviewer(_TEST_CLI).review(
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
        CodexArtifactReviewer(_TEST_CLI).review(
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
    decision = CodexArtifactReviewer(_TEST_CLI).review(
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


def test_claude_builder_uses_filtered_context_from_sterile_tool_free_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _context_repository(tmp_path)
    protected_files = {
        ".env.production": "INTERNAL_CODENAME=blue-orchid\n",
        ".npmrc": "REGISTRY_ALIAS=violet-comet\n",
        "credentials/session.json": '{"label":"quiet-river"}\n',
        ".claude/CLAUDE.md": "PROJECT_INSTRUCTION=amber-sparrow\n",
        "docs/AGENTS.md": "CONTROLLER_NOTE=golden-canyon\n",
        ".github/workflows/release.yml": "RELEASE_ALIAS=silver-maple\n",
        "deploy/worker.plist": "SERVICE_ALIAS=crimson-forest\n",
        "pyproject.toml": "DEPENDENCY_ALIAS=indigo-harbor\n",
    }
    for relative, content in protected_files.items():
        protected = tmp_path / relative
        protected.parent.mkdir(parents=True, exist_ok=True)
        protected.write_text(content, encoding="utf-8")
    subprocess.run(
        ["git", "add", *protected_files],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    calls: list[dict[str, Any]] = []
    schemas: list[dict[str, Any]] = []
    real_run = backend_module._run_process

    def fake_run(argv: list[str], **kwargs: Any) -> _ProcessResult:
        if "--json-schema" not in argv:
            return real_run(argv, **kwargs)
        calls.append({"argv": argv, **kwargs})
        schema = json.loads(argv[argv.index("--json-schema") + 1])
        schemas.append(schema)
        structured = (
            {"paths": ["app.py"]}
            if "paths" in schema["properties"]
            else {"summary": "done", "patch": _patch()}
        )
        return _ProcessResult(
            0,
            json.dumps({"structured_output": structured}),
            "",
        )

    monkeypatch.setattr(backend_module, "_run_process", fake_run)
    result = ClaudeCodePatchBuilder(_TEST_CLI).propose(
        worktree=tmp_path,
        objective="change app",
        feedback="",
        timeout=30,
    )

    assert result.success is True
    assert result.patch == _patch()
    assert len(calls) == 2
    for call in calls:
        argv = call["argv"]
        assert "--restricted" in argv
        assert "--safe-mode" in argv
        assert "--strict-mcp-config" in argv
        assert "--disable-slash-commands" in argv
        assert "--no-chrome" in argv
        assert argv[argv.index("--permission-mode") + 1] == "plan"
        assert argv[argv.index("--permission-prompts") + 1] == "none"
        assert argv[argv.index("--system-prompt") + 1] == (
            backend_module._CLAUDE_SYSTEM_PROMPT
        )
        assert "--append-system-prompt" not in argv
        assert "--append-system-prompt-file" not in argv
        assert "--system-prompt-file" not in argv
        assert argv[argv.index("--tools") + 1] == ""
        assert "Read,Glob,Grep" not in argv
        assert "--dangerously-skip-permissions" not in argv
        assert "change app" not in " ".join(argv)
        assert str(tmp_path) not in argv
        assert Path(call["cwd"]) != tmp_path
        assert not Path(call["cwd"]).is_relative_to(tmp_path)
        assert Path(call["env"]["TMPDIR"]) == Path(call["cwd"])
        assert call["max_stdout_bytes"] == backend_module._MAX_MODEL_RESULT_BYTES
        assert Path(call["cwd"]).name.startswith("monday-delivery-claude-")
        assert not Path(call["cwd"]).exists()
        assert "change app" in call["stdin"]

    manifest_prompt = calls[0]["stdin"]
    context_prompt = calls[1]["stdin"]
    assert '"path":"app.py"' in manifest_prompt
    assert '"content":"OLD = True\\n"' in context_prompt
    for forbidden in (
        ".env.production",
        "blue-orchid",
        ".npmrc",
        "violet-comet",
        "credentials/session.json",
        "quiet-river",
        ".claude/CLAUDE.md",
        "amber-sparrow",
        "docs/AGENTS.md",
        "golden-canyon",
        ".github/workflows/release.yml",
        "silver-maple",
        "deploy/worker.plist",
        "crimson-forest",
        "pyproject.toml",
        "indigo-harbor",
    ):
        assert forbidden not in manifest_prompt
        assert forbidden not in context_prompt
    assert schemas[0]["properties"]["paths"]["maxItems"] == (
        backend_module._MAX_SELECTED_FILES
    )
    assert schemas[1]["properties"]["patch"]["maxLength"] == (
        backend_module._MAX_PATCH_BYTES
    )


def test_claude_rejects_selector_escape_before_patch_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _context_repository(tmp_path)
    calls = 0
    real_run = backend_module._run_process

    def fake_run(argv: list[str], **kwargs: Any) -> _ProcessResult:
        nonlocal calls
        if "--json-schema" not in argv:
            return real_run(argv, **kwargs)
        calls += 1
        return _ProcessResult(
            0,
            json.dumps({"structured_output": {"paths": ["../outside.py"]}}),
            "",
        )

    monkeypatch.setattr(backend_module, "_run_process", fake_run)

    proposal = ClaudeCodePatchBuilder(_TEST_CLI).propose(
        worktree=tmp_path,
        objective="change app",
        feedback="",
        timeout=30,
    )

    assert proposal.success is False
    assert "unsafe path" in proposal.message
    assert calls == 1


def test_claude_fails_closed_when_temporary_runtime_overlaps_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _context_repository(tmp_path)
    model_calls = 0
    real_run = backend_module._run_process

    def fake_run(argv: list[str], **kwargs: Any) -> _ProcessResult:
        nonlocal model_calls
        if "--json-schema" in argv:
            model_calls += 1
            return _ProcessResult(0, "{}", "")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(backend_module, "_run_process", fake_run)
    monkeypatch.setattr(backend_module.tempfile, "tempdir", str(tmp_path))

    proposal = ClaudeCodePatchBuilder(_TEST_CLI).propose(
        worktree=tmp_path,
        objective="change app",
        feedback="",
        timeout=30,
    )

    assert proposal.success is False
    assert "runtime overlaps" in proposal.message
    assert model_calls == 0
    assert not list(tmp_path.glob("monday-delivery-claude-*"))


def test_claude_fails_closed_when_temporary_runtime_is_in_parent_git_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = tmp_path / "controller"
    controller.mkdir()
    subprocess.run(
        ["git", "init", "--initial-branch=main"],
        cwd=controller,
        check=True,
        capture_output=True,
    )
    worktree = controller / "logs" / "delivery" / "worktrees" / "job"
    worktree.mkdir(parents=True)
    _context_repository(worktree)
    temp_base = controller / "model-temp"
    temp_base.mkdir()
    model_calls = 0
    real_run = backend_module._run_process

    def fake_run(argv: list[str], **kwargs: Any) -> _ProcessResult:
        nonlocal model_calls
        if "--json-schema" in argv:
            model_calls += 1
            return _ProcessResult(0, "{}", "")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(backend_module, "_run_process", fake_run)
    monkeypatch.setattr(backend_module.tempfile, "tempdir", str(temp_base))

    proposal = ClaudeCodePatchBuilder(_TEST_CLI).propose(
        worktree=worktree,
        objective="change app",
        feedback="",
        timeout=30,
    )

    assert proposal.success is False
    assert "inside a Git repository" in proposal.message
    assert model_calls == 0
    assert not list(temp_base.glob("monday-delivery-claude-*"))


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


def test_process_capture_can_use_the_bounded_model_result_envelope(tmp_path: Path) -> None:
    payload_bytes = 1_000_000
    result = _run_process(
        [sys.executable, "-c", f"import sys; sys.stdout.write('x' * {payload_bytes})"],
        cwd=tmp_path,
        timeout=10,
        env=backend_module._controller_env(),
        max_stdout_bytes=backend_module._MAX_MODEL_RESULT_BYTES,
    )

    assert result.returncode == 0
    assert len(result.stdout.encode("utf-8")) == payload_bytes
    assert "[output truncated]" not in result.stdout

    oversized = _run_process(
        [
            sys.executable,
            "-c",
            f"import sys; sys.stdout.write('x' * {backend_module._MAX_MODEL_RESULT_BYTES + 1})",
        ],
        cwd=tmp_path,
        timeout=10,
        env=backend_module._controller_env(),
        max_stdout_bytes=backend_module._MAX_MODEL_RESULT_BYTES,
    )

    assert oversized.returncode == 0
    assert len(oversized.stdout.encode("utf-8")) <= backend_module._MAX_MODEL_RESULT_BYTES
    assert "[output truncated]" in oversized.stdout

    with pytest.raises(ValueError, match="stdout limit"):
        _run_process(
            [sys.executable, "-c", "pass"],
            cwd=tmp_path,
            timeout=10,
            env=backend_module._controller_env(),
            max_stdout_bytes=backend_module._MAX_MODEL_RESULT_BYTES + 1,
        )


def test_claude_rejects_a_truncated_structured_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(argv: list[str], **kwargs: Any) -> _ProcessResult:
        del argv, kwargs
        return _ProcessResult(0, '{"structured_output":' + "\n[output truncated]", "")

    monkeypatch.setattr(backend_module, "_run_process", fake_run)

    with pytest.raises(ValueError, match="exceeded the safe output limit"):
        backend_module._claude_json(
            _TEST_CLI,
            prompt="prompt",
            schema=backend_module._patch_schema(),
            forbidden_root=tmp_path,
            timeout=30,
        )


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


def test_process_inherits_only_valid_explicit_guard_descriptors(tmp_path: Path) -> None:
    guard_path = tmp_path / "publication.lock"
    descriptor = os.open(guard_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        metadata = os.fstat(descriptor)
        result = _run_process(
            [
                sys.executable,
                "-c",
                (
                    "import os,sys; value=os.fstat(int(sys.argv[1])); "
                    "raise SystemExit(0 if (value.st_dev,value.st_ino)=="
                    "(int(sys.argv[2]),int(sys.argv[3])) else 9)"
                ),
                str(descriptor),
                str(metadata.st_dev),
                str(metadata.st_ino),
            ],
            cwd=tmp_path,
            timeout=5,
            env=backend_module._controller_env(),
            inherited_guard_fds=(descriptor,),
        )
        assert result.returncode == 0

        with pytest.raises(ValueError, match="tuple"):
            _run_process(
                [sys.executable, "-c", "pass"],
                cwd=tmp_path,
                timeout=5,
                env=backend_module._controller_env(),
                inherited_guard_fds=[descriptor],  # type: ignore[arg-type]
            )
    finally:
        os.close(descriptor)

    with pytest.raises(ValueError, match="not open"):
        _run_process(
            [sys.executable, "-c", "pass"],
            cwd=tmp_path,
            timeout=5,
            env=backend_module._controller_env(),
            inherited_guard_fds=(descriptor,),
        )


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


def test_external_validation_uses_only_controller_runtime_and_target_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = tmp_path / "controller"
    repository = tmp_path / "target-repository"
    worktree = tmp_path / "target-worktree"
    controller.mkdir()
    repository.mkdir()
    worktree.mkdir()
    (controller / ".git").mkdir()
    (repository / ".git").mkdir()
    (worktree / ".git").write_text("gitdir: external\n", encoding="utf-8")
    (worktree / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n",
        encoding="utf-8",
    )

    controller_python = controller / ".venv" / "bin" / "python"
    controller_python.parent.mkdir(parents=True)
    controller_python.symlink_to(sys.executable)
    controller_bootstrap = controller / "delivery" / "sandbox_runner.py"
    controller_bootstrap.parent.mkdir()
    controller_bootstrap.write_text("# trusted controller bootstrap\n", encoding="utf-8")

    executed = tmp_path / "target-runtime-executed"
    target_python = worktree / ".venv" / "bin" / "python"
    target_python.parent.mkdir(parents=True)
    target_python.write_text(
        f"#!/bin/sh\ntouch {executed}\nexit 91\n",
        encoding="utf-8",
    )
    target_python.chmod(0o700)
    target_bootstrap = worktree / "delivery" / "sandbox_runner.py"
    target_bootstrap.parent.mkdir()
    target_bootstrap.write_text(
        f"from pathlib import Path\nPath({str(executed)!r}).touch()\n",
        encoding="utf-8",
    )

    commands: list[list[str]] = []
    profiles: dict[str, str] = {}
    monkeypatch.setattr(validation_module.sys, "platform", "darwin")
    monkeypatch.setattr(validation_module, "_trusted_validation_git", lambda: "/usr/bin/git")

    def fake_run(argv: list[str], **_kwargs: Any) -> _ProcessResult:
        commands.append(argv)
        profile_path = Path(argv[3])
        profiles[profile_path.name] = profile_path.read_text(encoding="utf-8")
        return _ProcessResult(0, "passed", "")

    monkeypatch.setattr(validation_module, "_run_process", fake_run)
    results = MondayValidationRunner(
        source_root=controller,
        timeout=1,
        mondayos_tests=False,
    ).run(
        worktree=worktree,
        source_root=controller,
        repository_root=repository,
        changed_files=["app.py"],
    )

    assert len(results) == 2
    assert len(commands) == 2
    for command in commands:
        assert command[:3] == [
            str(controller_python.absolute()),
            "-I",
            str(controller_bootstrap.resolve()),
        ]
        assert str(target_python) not in command
        assert str(target_bootstrap) not in command
    assert results[-1].argv[0] == str(controller_python)
    assert not any(
        argument.startswith("--deselect=") for argument in results[-1].argv
    )
    assert "Sandboxed validation exclusions" not in results[-1].output
    assert not executed.exists()

    target_git = json.dumps(str((repository / ".git").resolve()))
    controller_git = json.dumps(str((controller / ".git").resolve()))
    git_profile = profiles["sandbox-git.sb"]
    git_read_allow = git_profile.split("(allow file-read*\n", 1)[1].split(
        ")\n(deny file-read*",
        1,
    )[0]
    assert f"(subpath {target_git})" in git_read_allow
    assert f"(subpath {controller_git})" not in git_read_allow
    assert f"(subpath {controller_git})" in git_profile

    test_profile = profiles["sandbox-tests.sb"]
    test_read_allow = test_profile.split("(allow file-read*\n", 1)[1].split(
        ")\n(deny file-read*",
        1,
    )[0]
    assert f"(subpath {target_git})" not in test_read_allow
    assert f"(subpath {target_git})" in test_profile


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
    project_bound = artifact_digest(
        base_sha="a" * 40,
        objective="one",
        diff=_patch(),
        verification_sha256=check,
        project_name="weather-portal",
        repository="acme/weather-portal",
    )
    repository_bound = artifact_digest(
        base_sha="a" * 40,
        objective="one",
        diff=_patch(),
        verification_sha256=check,
        project_name="weather-portal",
        repository="acme/another-repository",
    )
    assert first != second
    assert first != third
    assert first != project_bound
    assert project_bound != repository_bound
    assert all(len(value) == 64 for pair in (first, project_bound) for value in pair)


def test_delivery_job_preserves_the_schema_v1_positional_status_argument() -> None:
    job = DeliveryJob("delivery-one", "TASK-0001", "objective", "/repo", "running")

    assert job.status == "running"
    assert job.project_name == ""


def test_delivery_job_failure_classification_is_bounded_and_legacy_safe() -> None:
    transient = DeliveryJob(
        "delivery-transient",
        "TASK-0001",
        "objective",
        "/repo",
        failure_class="transient",
        retry_after_seconds=MAX_DELIVERY_RETRY_AFTER_SECONDS + 1,
    )

    payload = transient.to_dict()
    assert payload["failure_class"] == "transient"
    assert payload["retry_after_seconds"] == MAX_DELIVERY_RETRY_AFTER_SECONDS
    restored = DeliveryJob.from_dict(payload)
    assert restored.failure_class == "transient"
    assert restored.retry_after_seconds == MAX_DELIVERY_RETRY_AFTER_SECONDS

    legacy = dict(payload)
    legacy.pop("failure_class")
    legacy.pop("retry_after_seconds")
    restored_legacy = DeliveryJob.from_dict(legacy)
    assert restored_legacy.failure_class == ""
    assert restored_legacy.retry_after_seconds == 0

    malformed = dict(payload, failure_class="Transient", retry_after_seconds=30)
    restored_malformed = DeliveryJob.from_dict(malformed)
    assert restored_malformed.failure_class == ""
    assert restored_malformed.retry_after_seconds == 0

    unsafe = DeliveryJob(
        "delivery-unsafe-class",
        "TASK-0001",
        "objective",
        "/repo",
        failure_class="transient please retry",
        retry_after_seconds=30,
    ).to_dict()
    assert unsafe["failure_class"] == ""
    assert unsafe["retry_after_seconds"] == 0


@pytest.mark.parametrize(
    ("reconciliation", "revision"),
    [
        ("review-pending please retry", "A" * 64),
        ("review-pending", "a" * 63),
        ("review-pending", "g" * 64),
        ("review-pending", "2026-10-13T00:00:00+00:00"),
        (7, ["a" * 64]),
    ],
)
def test_delivery_job_review_reconciliation_serialization_fails_closed(
    reconciliation: object,
    revision: object,
) -> None:
    payload = DeliveryJob(
        "delivery-review-reconciliation",
        "TASK-0001",
        "objective",
        "/repo",
        review_reconciliation="review-pending",
        review_reconciled_at="",
        review_expected_task_revision="A" * 64,
    ).to_dict()
    payload["review_reconciliation"] = reconciliation
    payload["review_expected_task_revision"] = revision

    restored = DeliveryJob.from_dict(payload)

    assert restored.review_reconciliation == (
        "review-pending" if reconciliation == "review-pending" else ""
    )
    assert restored.review_expected_task_revision == (
        "a" * 64 if revision == "A" * 64 else ""
    )
    serialized = restored.to_dict()
    assert serialized["review_reconciliation"] == restored.review_reconciliation
    assert (
        serialized["review_expected_task_revision"]
        == restored.review_expected_task_revision
    )


def test_delivery_store_reserves_identity_and_rejects_tampered_record(tmp_path: Path) -> None:
    store = DeliveryStore(tmp_path)
    owner_token = "a" * 64
    job = DeliveryJob(
        delivery_id="delivery-one",
        task_id="TASK-0001",
        objective="objective",
        repo_root=str(tmp_path),
        created_at="2026-01-01T00:00:00+00:00",
        worktree_owner_token=owner_token,
    )

    assert "worktree_owner_token" not in job.to_dict()
    assert store.reserve(job) is None
    existing = store.reserve(job)
    assert existing is not None
    assert existing.task_id == "TASK-0001"
    assert existing.worktree_owner_token == owner_token

    record = store.records / "delivery-one.json"
    payload = json.loads(record.read_text(encoding="utf-8"))
    assert payload["worktree_owner_token"] == owner_token
    payload["delivery_id"] = "delivery-another"
    record.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="identifies itself"):
        store.get("delivery-one")


def test_delivery_store_round_trips_structured_transient_failure(tmp_path: Path) -> None:
    store = DeliveryStore(tmp_path)
    job = DeliveryJob(
        delivery_id="delivery-transient-store",
        task_id="TASK-0001",
        objective="objective",
        repo_root=str(tmp_path),
        status="failed",
        phase="failed",
        failure_code="builder-unavailable",
        failure_class="transient",
        retry_after_seconds=75,
    )

    store.persist(job)
    restored = store.get(job.delivery_id)

    assert restored.failure_class == "transient"
    assert restored.retry_after_seconds == 75
    payload = json.loads(
        (store.records / "delivery-transient-store.json").read_text(encoding="utf-8")
    )
    assert payload["failure_class"] == "transient"
    assert payload["retry_after_seconds"] == 75

    payload["failure_class"] = "temporary outage"
    payload["retry_after_seconds"] = 75
    (store.records / "delivery-transient-store.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    fail_closed = store.get(job.delivery_id)
    assert fail_closed.failure_class == ""
    assert fail_closed.retry_after_seconds == 0


def test_delivery_store_history_filters_real_records_by_project(tmp_path: Path) -> None:
    store = DeliveryStore(tmp_path)
    for delivery_id, project_name in (
        ("delivery-controller", ""),
        ("delivery-weather", "weather-portal"),
        ("delivery-other", "another-project"),
    ):
        store.persist(
            DeliveryJob(
                delivery_id=delivery_id,
                task_id="TASK-0001",
                objective="objective",
                repo_root=str(tmp_path),
                project_name=project_name,
                created_at=delivery_id,
            )
        )

    assert [
        job.delivery_id
        for job in store.history(project_name="weather-portal")
    ] == ["delivery-weather"]
    assert [job.delivery_id for job in store.history(project_name="")] == [
        "delivery-controller"
    ]


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
        "url": "https://github.com/Owner/Repo/pull/7",
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

    result = github_module.GhCliClient(_TEST_CLI).ensure_pull_request(
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
    rejected = github_module.GhCliClient(_TEST_CLI).ensure_pull_request(
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
            _ProcessResult(0, "https://github.com/Owner/Repo/pull/9\n", ""),
            _ProcessResult(
                0,
                json.dumps(
                    {
                        "number": 9,
                        "url": "https://github.com/Owner/Repo/pull/9",
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
    calls: list[dict[str, Any]] = []

    def fake_run(_argv: list[str], **kwargs: Any) -> _ProcessResult:
        calls.append(dict(kwargs))
        return next(responses)

    monkeypatch.setattr(github_module, "_run_process", fake_run)
    guard = tmp_path / "publication.lock"
    descriptor = os.open(guard, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        result = github_module.GhCliClient(_TEST_CLI).ensure_pull_request(
            repo_root=tmp_path,
            repository="owner/repo",
            base="main",
            branch="codex/task-12345678",
            title="Task",
            body="Body",
            expected_sha=sha,
            inherited_guard_fds=(descriptor,),
        )
    finally:
        os.close(descriptor)
    assert result.success is True
    assert result.number == 9
    assert calls[0].get("inherited_guard_fds", ()) == ()
    assert calls[1]["inherited_guard_fds"] == (descriptor,)
    assert calls[2].get("inherited_guard_fds", ()) == ()


def _observed_pr_row(
    *,
    state: str = "OPEN",
    head_sha: str = "a" * 40,
    checks: list[object] | None = None,
    merge_sha: str = "",
) -> dict[str, object]:
    return {
        "number": 7,
        "url": "https://github.com/Owner/Repo/pull/7",
        "state": state,
        "mergeCommit": {"oid": merge_sha} if merge_sha else None,
        "baseRefName": "main",
        "headRefName": "codex/task-12345678",
        "headRefOid": head_sha,
        "isCrossRepository": False,
        "statusCheckRollup": [] if checks is None else checks,
    }


def _rest_check_run(
    identifier: int,
    *,
    sha: str = "a" * 40,
    status: str = "completed",
    conclusion: str | None = "success",
) -> dict[str, object]:
    return {
        "id": identifier,
        "head_sha": sha,
        "status": status,
        "conclusion": conclusion,
    }


def _rest_commit_status(identifier: int, *, state: str = "success") -> dict[str, object]:
    return {"id": identifier, "state": state}


def _rest_check_run_payload(
    checks: list[object], *, total_count: int | None = None
) -> dict[str, object]:
    return {
        "total_count": len(checks) if total_count is None else total_count,
        "check_runs": checks,
    }


def _rest_commit_status_payload(
    checks: list[object],
    *,
    sha: str = "a" * 40,
    total_count: int | None = None,
    state: str | None = None,
) -> dict[str, object]:
    states = [
        str(check.get("state", ""))
        for check in checks
        if isinstance(check, dict)
    ]
    if state is None:
        if any(item in {"failure", "error"} for item in states):
            state = "failure"
        elif not states or any(item in {"pending", "expected"} for item in states):
            state = "pending"
        else:
            state = "success"
    return {
        "sha": sha,
        "state": state,
        "total_count": len(checks) if total_count is None else total_count,
        "statuses": checks,
    }


def _observe_pr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    row: object,
) -> github_module.PullRequestObservation:
    payload_row: object = row
    raw_checks: object = []
    if isinstance(row, dict):
        payload_row = dict(row)
        raw_checks = payload_row.pop("statusCheckRollup", [])
    check_runs: list[object] = []
    statuses: list[object] = []
    if isinstance(raw_checks, list):
        for index, check in enumerate(raw_checks, start=1):
            if isinstance(check, dict) and check.get("__typename") == "StatusContext":
                statuses.append(
                    _rest_commit_status(index, state=str(check.get("state", "")).lower())
                )
            elif isinstance(check, dict) and check.get("__typename") == "CheckRun":
                conclusion = check.get("conclusion")
                check_runs.append(
                    _rest_check_run(
                        index,
                        sha=str(row.get("headRefOid", "")),
                        status=str(check.get("status", "")).lower(),
                        conclusion=(
                            str(conclusion).lower() if isinstance(conclusion, str) else None
                        ),
                    )
                )
            else:
                check_runs.append({})

    def fake_run(argv: list[str], **_kwargs: object) -> _ProcessResult:
        if argv[1:3] == ["pr", "view"]:
            return _ProcessResult(0, json.dumps(payload_row), "")
        if argv[1] == "api" and str(argv[2]).endswith("/check-runs"):
            return _ProcessResult(0, json.dumps(_rest_check_run_payload(check_runs)), "")
        if argv[1] == "api" and str(argv[2]).endswith("/status"):
            return _ProcessResult(
                0,
                json.dumps(
                    _rest_commit_status_payload(
                        statuses,
                        sha=str(row.get("headRefOid", "")) if isinstance(row, dict) else "",
                    )
                ),
                "",
            )
        raise AssertionError(f"unexpected GitHub observer command: {argv!r}")

    monkeypatch.setattr(
        github_module,
        "_run_process",
        fake_run,
    )
    return github_module.GhCliClient(_TEST_CLI).observe_pull_request(
        repo_root=tmp_path,
        repository="owner/repo",
        base="main",
        branch="codex/task-12345678",
        number=7,
        url="https://github.com/Owner/Repo/pull/7",
        expected_sha="a" * 40,
    )


@pytest.mark.parametrize(
    ("raw_state", "expected_lifecycle", "merge_sha"),
    [
        ("OPEN", "open", ""),
        ("CLOSED", "closed", ""),
        ("MERGED", "merged", "b" * 40),
    ],
)
def test_github_observer_classifies_exact_pr_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw_state: str,
    expected_lifecycle: str,
    merge_sha: str,
) -> None:
    result = _observe_pr(
        tmp_path,
        monkeypatch,
        _observed_pr_row(state=raw_state, merge_sha=merge_sha),
    )

    assert result.success is True
    assert result.lifecycle == expected_lifecycle
    assert result.checks == "no-checks"
    assert result.merge_commit_sha == merge_sha
    assert result.total_checks == 0


@pytest.mark.parametrize(
    ("checks", "expected_state", "expected_counts"),
    [
        ([], "no-checks", (0, 0, 0, 0)),
        (
            [
                {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"},
                {"__typename": "StatusContext", "state": "SUCCESS"},
            ],
            "passed",
            (2, 0, 2, 0),
        ),
        (
            [
                {"__typename": "CheckRun", "status": "IN_PROGRESS", "conclusion": ""},
                {"__typename": "StatusContext", "state": "SUCCESS"},
            ],
            "pending",
            (2, 1, 1, 0),
        ),
        (
            [
                {"__typename": "CheckRun", "status": "QUEUED", "conclusion": ""},
                {"__typename": "StatusContext", "state": "ERROR"},
                {"__typename": "StatusContext", "state": "SUCCESS"},
            ],
            "failed",
            (3, 1, 1, 1),
        ),
    ],
)
def test_github_observer_classifies_only_structured_rest_check_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    checks: list[object],
    expected_state: str,
    expected_counts: tuple[int, int, int, int],
) -> None:
    result = _observe_pr(tmp_path, monkeypatch, _observed_pr_row(checks=checks))

    assert result.success is True
    assert result.checks == expected_state
    assert (
        result.total_checks,
        result.pending_checks,
        result.passed_checks,
        result.failed_checks,
    ) == expected_counts


def test_github_observer_reports_diverged_head_without_reading_replacement_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checks = [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}]
    result = _observe_pr(
        tmp_path,
        monkeypatch,
        _observed_pr_row(head_sha="c" * 40, checks=checks),
    )

    assert result.success is True
    assert result.lifecycle == "diverged"
    assert result.checks == "no-checks"
    assert result.total_checks == 0
    assert result.merge_commit_sha == ""


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("number", 8),
        ("url", "https://github.com/Owner/Repo/pull/8"),
        ("baseRefName", "release"),
        ("headRefName", "codex/other-12345678"),
        ("isCrossRepository", True),
    ],
)
def test_github_observer_rejects_mismatched_pr_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    row = _observed_pr_row()
    row[field] = value

    result = _observe_pr(tmp_path, monkeypatch, row)

    assert result.success is False
    assert result.lifecycle == "unavailable"
    assert result.checks == "no-checks"


@pytest.mark.parametrize(
    "overrides",
    [
        {"state": "UNKNOWN"},
        {"state": "UNKNOWN", "headRefOid": "c" * 40},
        {"headRefOid": "not-a-sha"},
        {"statusCheckRollup": [{"__typename": "Unknown", "state": "SUCCESS"}]},
        {"statusCheckRollup": [{}] * 501},
        {"state": "MERGED", "mergeCommit": None},
        {"state": "MERGED", "mergeCommit": None, "headRefOid": "c" * 40},
        {"state": "OPEN", "mergeCommit": {"oid": "b" * 40}},
    ],
)
def test_github_observer_fails_closed_on_malformed_or_inconsistent_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
) -> None:
    row = _observed_pr_row()
    row.update(overrides)

    result = _observe_pr(tmp_path, monkeypatch, row)

    assert result.success is False
    assert result.lifecycle == "unavailable"


def test_github_observer_fails_closed_when_pr_identity_field_is_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _observed_pr_row()
    del row["mergeCommit"]

    result = _observe_pr(tmp_path, monkeypatch, row)

    assert result.success is False
    assert result.lifecycle == "unavailable"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repository", "../repo"),
        ("base", "-main"),
        ("branch", "codex/../main"),
        ("number", True),
        ("url", "https://github.com/owner/repo/pull/8"),
        ("url", 7),
        ("expected_sha", "bad"),
        ("expected_sha", None),
        ("timeout", float("inf")),
        ("timeout", 10**400),
    ],
)
def test_github_observer_rejects_invalid_inputs_before_querying(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    def unexpected_run(*_args: object, **_kwargs: object) -> _ProcessResult:
        raise AssertionError("invalid input must not invoke gh")

    monkeypatch.setattr(github_module, "_run_process", unexpected_run)
    kwargs: dict[str, object] = {
        "repo_root": tmp_path,
        "repository": "owner/repo",
        "base": "main",
        "branch": "codex/task-12345678",
        "number": 7,
        "url": "https://github.com/Owner/Repo/pull/7",
        "expected_sha": "a" * 40,
        "timeout": 60,
    }
    kwargs[field] = value

    result = github_module.GhCliClient(_TEST_CLI).observe_pull_request(**kwargs)  # type: ignore[arg-type]

    assert result.success is False
    assert result.lifecycle == "unavailable"


def test_github_observer_uses_minimal_read_only_rest_queries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _observed_pr_row()
    row.pop("statusCheckRollup")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv: list[str], **kwargs: object) -> _ProcessResult:
        calls.append((argv, kwargs))
        if argv[1:3] == ["pr", "view"]:
            return _ProcessResult(0, json.dumps(row), "")
        if str(argv[2]).endswith("/check-runs"):
            return _ProcessResult(0, json.dumps(_rest_check_run_payload([])), "")
        return _ProcessResult(0, json.dumps(_rest_commit_status_payload([])), "")

    monkeypatch.setattr(github_module, "_run_process", fake_run)
    result = github_module.GhCliClient(_TEST_CLI).observe_pull_request(
        repo_root=tmp_path,
        repository="owner/repo",
        base="main",
        branch="codex/task-12345678",
        number=7,
        url="https://github.com/Owner/Repo/pull/7",
        expected_sha="a" * 40,
        timeout=12,
    )

    assert result.success is True
    assert result.checks == "no-checks"
    assert len(calls) == 4
    assert calls[0][0] == calls[3][0]
    assert "statusCheckRollup" not in calls[0][0][-1]
    for argv, call_kwargs in calls:
        assert call_kwargs["cwd"] == tmp_path
        assert 0 < call_kwargs["timeout"] <= 12.0
        assert "inherited_guard_fds" not in call_kwargs
        assert not {"merge", "checks", "run"}.intersection(argv)
    for argv, _kwargs in calls[1:3]:
        assert argv[1] == "api"
        assert argv[3:5] == ["--method", "GET"]
        assert f"commits/{'a' * 40}/" in argv[2]
        jq_filter = argv[argv.index("--jq") + 1]
        assert not {"name", "description", "annotations", "output"}.intersection(
            jq_filter.replace("{", " ").replace("}", " ").split()
        )
    assert "filter=latest" in calls[1][0]


def test_github_observer_rest_check_runs_catch_a_failure_omitted_by_rollup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _observed_pr_row()
    row.pop("statusCheckRollup")
    all_runs = [_rest_check_run(index) for index in range(1, 101)] + [
        _rest_check_run(101, conclusion="failure")
    ]
    check_calls = 0

    def fake_run(argv: list[str], **_kwargs: object) -> _ProcessResult:
        nonlocal check_calls
        if argv[1:3] == ["pr", "view"]:
            return _ProcessResult(0, json.dumps(row), "")
        if str(argv[2]).endswith("/check-runs"):
            check_calls += 1
            page = int(next(item.split("=", 1)[1] for item in argv if item.startswith("page=")))
            start = (page - 1) * 100
            return _ProcessResult(
                0,
                json.dumps(_rest_check_run_payload(all_runs[start : start + 100], total_count=101)),
                "",
            )
        return _ProcessResult(0, json.dumps(_rest_commit_status_payload([])), "")

    monkeypatch.setattr(github_module, "_run_process", fake_run)
    result = github_module.GhCliClient(_TEST_CLI).observe_pull_request(
        repo_root=tmp_path,
        repository="owner/repo",
        base="main",
        branch="codex/task-12345678",
        number=7,
        url="https://github.com/Owner/Repo/pull/7",
        expected_sha="a" * 40,
    )

    assert result.success is True
    assert result.checks == "failed"
    assert result.total_checks == 101
    assert result.passed_checks == 100
    assert result.failed_checks == 1
    assert check_calls == 2


def test_github_observer_accepts_exactly_500_checks_across_both_rest_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _observed_pr_row()
    row.pop("statusCheckRollup")
    runs = [_rest_check_run(index) for index in range(1, 251)]
    statuses = [_rest_commit_status(index) for index in range(1, 251)]

    def fake_run(argv: list[str], **_kwargs: object) -> _ProcessResult:
        if argv[1:3] == ["pr", "view"]:
            return _ProcessResult(0, json.dumps(row), "")
        page = int(next(item.split("=", 1)[1] for item in argv if item.startswith("page=")))
        start = (page - 1) * 100
        if str(argv[2]).endswith("/check-runs"):
            payload = _rest_check_run_payload(runs[start : start + 100], total_count=250)
        else:
            payload = _rest_commit_status_payload(
                statuses[start : start + 100], total_count=250, state="success"
            )
        return _ProcessResult(0, json.dumps(payload), "")

    monkeypatch.setattr(github_module, "_run_process", fake_run)
    result = github_module.GhCliClient(_TEST_CLI).observe_pull_request(
        repo_root=tmp_path,
        repository="owner/repo",
        base="main",
        branch="codex/task-12345678",
        number=7,
        url="https://github.com/Owner/Repo/pull/7",
        expected_sha="a" * 40,
    )

    assert result.success is True
    assert result.checks == "passed"
    assert result.total_checks == 500
    assert result.passed_checks == 500


def test_github_observer_rejects_more_than_500_combined_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _observed_pr_row()
    row.pop("statusCheckRollup")

    def fake_run(argv: list[str], **_kwargs: object) -> _ProcessResult:
        if argv[1:3] == ["pr", "view"]:
            return _ProcessResult(0, json.dumps(row), "")
        if str(argv[2]).endswith("/check-runs"):
            return _ProcessResult(
                0,
                json.dumps(_rest_check_run_payload([_rest_check_run(1)])),
                "",
            )
        return _ProcessResult(
            0,
            json.dumps(
                _rest_commit_status_payload(
                    [_rest_commit_status(index) for index in range(1, 101)],
                    total_count=500,
                    state="success",
                )
            ),
            "",
        )

    monkeypatch.setattr(github_module, "_run_process", fake_run)
    result = github_module.GhCliClient(_TEST_CLI).observe_pull_request(
        repo_root=tmp_path,
        repository="owner/repo",
        base="main",
        branch="codex/task-12345678",
        number=7,
        url="https://github.com/Owner/Repo/pull/7",
        expected_sha="a" * 40,
    )

    assert result.success is False
    assert result.lifecycle == "unavailable"


@pytest.mark.parametrize("mode", ["truncated", "count-change", "duplicate", "wrong-sha"])
def test_github_observer_fails_closed_on_ambiguous_check_run_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    row = _observed_pr_row()
    row.pop("statusCheckRollup")
    calls = 0

    def fake_run(argv: list[str], **_kwargs: object) -> _ProcessResult:
        nonlocal calls
        if argv[1:3] == ["pr", "view"]:
            return _ProcessResult(0, json.dumps(row), "")
        if not str(argv[2]).endswith("/check-runs"):
            raise AssertionError("status query must not follow invalid check-run data")
        calls += 1
        if mode == "truncated":
            payload = _rest_check_run_payload([_rest_check_run(1)], total_count=2)
        elif mode == "wrong-sha":
            payload = _rest_check_run_payload([_rest_check_run(1, sha="b" * 40)])
        elif calls == 1:
            payload = _rest_check_run_payload(
                [_rest_check_run(index) for index in range(1, 101)], total_count=101
            )
        elif mode == "count-change":
            payload = _rest_check_run_payload([_rest_check_run(101)], total_count=102)
        else:
            payload = _rest_check_run_payload([_rest_check_run(1)], total_count=101)
        return _ProcessResult(0, json.dumps(payload), "")

    monkeypatch.setattr(github_module, "_run_process", fake_run)
    result = github_module.GhCliClient(_TEST_CLI).observe_pull_request(
        repo_root=tmp_path,
        repository="owner/repo",
        base="main",
        branch="codex/task-12345678",
        number=7,
        url="https://github.com/Owner/Repo/pull/7",
        expected_sha="a" * 40,
    )

    assert result.success is False
    assert result.lifecycle == "unavailable"


@pytest.mark.parametrize(
    "status_payload",
    [
        _rest_commit_status_payload(
            [_rest_commit_status(1)],
            sha="b" * 40,
        ),
        _rest_commit_status_payload(
            [_rest_commit_status(1)],
            state="failure",
        ),
        _rest_commit_status_payload(
            [_rest_commit_status(1)],
            total_count=2,
            state="success",
        ),
    ],
)
def test_github_observer_fails_closed_on_ambiguous_commit_status_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_payload: dict[str, object],
) -> None:
    row = _observed_pr_row()
    row.pop("statusCheckRollup")

    def fake_run(argv: list[str], **_kwargs: object) -> _ProcessResult:
        if argv[1:3] == ["pr", "view"]:
            return _ProcessResult(0, json.dumps(row), "")
        if str(argv[2]).endswith("/check-runs"):
            return _ProcessResult(0, json.dumps(_rest_check_run_payload([])), "")
        return _ProcessResult(0, json.dumps(status_payload), "")

    monkeypatch.setattr(github_module, "_run_process", fake_run)
    result = github_module.GhCliClient(_TEST_CLI).observe_pull_request(
        repo_root=tmp_path,
        repository="owner/repo",
        base="main",
        branch="codex/task-12345678",
        number=7,
        url="https://github.com/Owner/Repo/pull/7",
        expected_sha="a" * 40,
    )

    assert result.success is False
    assert result.lifecycle == "unavailable"


def test_github_observer_fails_closed_if_head_changes_during_rest_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_row = _observed_pr_row()
    first_row.pop("statusCheckRollup")
    final_row = _observed_pr_row(head_sha="b" * 40)
    final_row.pop("statusCheckRollup")
    identity_rows = iter([first_row, final_row])

    def fake_run(argv: list[str], **_kwargs: object) -> _ProcessResult:
        if argv[1:3] == ["pr", "view"]:
            return _ProcessResult(0, json.dumps(next(identity_rows)), "")
        if str(argv[2]).endswith("/check-runs"):
            return _ProcessResult(0, json.dumps(_rest_check_run_payload([])), "")
        return _ProcessResult(0, json.dumps(_rest_commit_status_payload([])), "")

    monkeypatch.setattr(github_module, "_run_process", fake_run)
    result = github_module.GhCliClient(_TEST_CLI).observe_pull_request(
        repo_root=tmp_path,
        repository="owner/repo",
        base="main",
        branch="codex/task-12345678",
        number=7,
        url="https://github.com/Owner/Repo/pull/7",
        expected_sha="a" * 40,
    )

    assert result.success is False
    assert result.lifecycle == "unavailable"


@pytest.mark.parametrize(
    ("response", "stdout"),
    [
        (1, "{}"),
        (0, "not-json"),
        (0, "[]"),
    ],
)
def test_github_observer_returns_unavailable_for_query_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    response: int,
    stdout: str,
) -> None:
    monkeypatch.setattr(
        github_module,
        "_run_process",
        lambda *_args, **_kwargs: _ProcessResult(response, stdout, "sensitive output"),
    )

    result = github_module.GhCliClient(_TEST_CLI).observe_pull_request(
        repo_root=tmp_path,
        repository="owner/repo",
        base="main",
        branch="codex/task-12345678",
        number=7,
        url="https://github.com/Owner/Repo/pull/7",
        expected_sha="a" * 40,
    )

    assert result.success is False
    assert result.lifecycle == "unavailable"
    assert "sensitive output" not in result.message
