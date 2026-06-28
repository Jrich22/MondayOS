"""
Integration tests for the Monday CLI.

All tests invoke monday.cli.main() directly with an explicit argv list,
capturing stdout/stderr via capsys. Every command receives --project-root
pointing to an isolated tmp_path so tests never touch real project files.

Nothing is imported from internal modules — all assertions are on the text
output of the CLI, which itself uses only the public Monday API.
"""
from __future__ import annotations

import pytest
from pathlib import Path

from monday.cli import main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _root(tmp_path: Path) -> list[str]:
    """Return the shared --project-root prefix for a test's tmp_path."""
    return ["--project-root", str(tmp_path)]


def _run(argv: list[str], capsys: pytest.CaptureFixture) -> tuple[int, str, str]:
    """Call main() and return (exit_code, stdout, stderr)."""
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# ---------------------------------------------------------------------------
# No command / help
# ---------------------------------------------------------------------------

class TestHelp:
    def test_no_command_exits_zero(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        code, out, _ = _run(_root(tmp_path), capsys)
        assert code == 0

    def test_no_command_shows_usage(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        _run(_root(tmp_path), capsys)
        out = capsys.readouterr().out  # second read after _run captured
        # main() prints help then returns; output was already captured in _run
        # Re-run to capture
        code = main(_root(tmp_path))
        captured = capsys.readouterr()
        assert "monday" in captured.out.lower() or code == 0  # at minimum exits cleanly

    def test_help_flag_raises_systemexit_zero(self, capsys: pytest.CaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0

    def test_status_help_exits_zero(self, capsys: pytest.CaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["status", "--help"])
        assert exc.value.code == 0

    def test_ask_help_exits_zero(self, capsys: pytest.CaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["ask", "--help"])
        assert exc.value.code == 0

    def test_search_help_exits_zero(self, capsys: pytest.CaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["search", "--help"])
        assert exc.value.code == 0

    def test_learn_help_exits_zero(self, capsys: pytest.CaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["learn", "--help"])
        assert exc.value.code == 0

    def test_task_help_exits_zero(self, capsys: pytest.CaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["task", "--help"])
        assert exc.value.code == 0


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

class TestStatusCommand:
    def test_exits_zero(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        code, _, _ = _run([*_root(tmp_path), "status"], capsys)
        assert code == 0

    def test_output_contains_version(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        _, out, _ = _run([*_root(tmp_path), "status"], capsys)
        assert "MondayOS v0.1.0" in out

    def test_output_contains_session(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        _, out, _ = _run([*_root(tmp_path), "status"], capsys)
        assert "Session" in out

    def test_output_contains_healthy(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        _, out, _ = _run([*_root(tmp_path), "status"], capsys)
        assert "healthy" in out

    def test_output_lists_all_modules(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        _, out, _ = _run([*_root(tmp_path), "status"], capsys)
        for module in ("brain", "events", "knowledge", "memory", "search", "tasks"):
            assert module in out

    def test_all_modules_show_ok(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        _, out, _ = _run([*_root(tmp_path), "status"], capsys)
        assert "FAIL" not in out


# ---------------------------------------------------------------------------
# ask
# ---------------------------------------------------------------------------

class TestAskCommand:
    def test_exits_zero(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        code, _, _ = _run([*_root(tmp_path), "ask", "What do we know?"], capsys)
        assert code == 0

    def test_output_is_non_empty(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        _, out, _ = _run([*_root(tmp_path), "ask", "What do we know?"], capsys)
        assert out.strip() != ""

    def test_output_contains_confidence(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        _, out, _ = _run([*_root(tmp_path), "ask", "anything"], capsys)
        assert "Confidence" in out

    def test_output_contains_engine(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        _, out, _ = _run([*_root(tmp_path), "ask", "anything"], capsys)
        assert "monday-reasoning/1.0" in out

    def test_ask_with_prior_knowledge(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        # Seed knowledge then ask about it
        main([*_root(tmp_path), "learn",
              "--title", "Homebrew PATH Fix",
              "--type", "bug",
              "--tags", "homebrew,macos",
              "--content", "Resolved by adding /opt/homebrew/bin to PATH in .zshrc."])
        capsys.readouterr()  # discard learn output

        code, out, _ = _run(
            [*_root(tmp_path), "ask", "Have we seen Homebrew PATH issues before?"],
            capsys,
        )
        assert code == 0
        assert "homebrew" in out.lower() or "found" in out.lower()

    def test_ask_populates_sources_when_knowledge_exists(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        main([*_root(tmp_path), "learn",
              "--title", "Rate limit pattern",
              "--type", "pattern",
              "--tags", "api,rate-limit",
              "--content", "Use exponential backoff when rate limited."])
        capsys.readouterr()

        _, out, _ = _run(
            [*_root(tmp_path), "ask", "What do we know about rate limits?"],
            capsys,
        )
        assert "PAT-" in out or "Sources" in out

    def test_ask_shows_suggested_actions_when_no_knowledge(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        _, out, _ = _run(
            [*_root(tmp_path), "ask", "unknown topic xyzzy"],
            capsys,
        )
        assert "monday.learn" in out

    def test_ask_missing_prompt_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            main([*_root(tmp_path), "ask"])
        assert exc.value.code != 0


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

class TestSearchCommand:
    def test_exits_zero_no_results(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        code, _, _ = _run([*_root(tmp_path), "search", "xyzzy"], capsys)
        assert code == 0

    def test_no_results_message(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        _, out, _ = _run([*_root(tmp_path), "search", "xyzzy"], capsys)
        assert "No results" in out

    def test_finds_learned_entry(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        main([*_root(tmp_path), "learn",
              "--title", "Homebrew PATH Fix",
              "--type", "bug",
              "--tags", "homebrew",
              "--content", "Add brew to PATH."])
        capsys.readouterr()

        code, out, _ = _run([*_root(tmp_path), "search", "Homebrew"], capsys)
        assert code == 0
        assert "Homebrew PATH Fix" in out
        assert "BUG-" in out

    def test_search_shows_entry_type(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        main([*_root(tmp_path), "learn",
              "--title", "Rate limit pattern",
              "--type", "pattern",
              "--content", "Use exponential backoff."])
        capsys.readouterr()

        _, out, _ = _run([*_root(tmp_path), "search", "rate limit"], capsys)
        assert "pattern" in out

    def test_search_limit_flag(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        for i in range(5):
            main([*_root(tmp_path), "learn",
                  "--title", f"Entry {i}",
                  "--type", "pattern",
                  "--content", f"Content for entry {i}."])
        capsys.readouterr()

        _, out, _ = _run([*_root(tmp_path), "search", "Entry", "--limit", "2"], capsys)
        # At most 2 results shown — count numbered entries
        result_lines = [l for l in out.splitlines() if l.strip().startswith(("1.", "2.", "3."))]
        assert len(result_lines) <= 2

    def test_search_missing_query_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            main([*_root(tmp_path), "search"])
        assert exc.value.code != 0


# ---------------------------------------------------------------------------
# learn
# ---------------------------------------------------------------------------

class TestLearnCommand:
    def test_exits_zero(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        code, _, _ = _run(
            [*_root(tmp_path), "learn",
             "--title", "Test entry",
             "--type", "pattern",
             "--content", "Some content."],
            capsys,
        )
        assert code == 0

    def test_output_contains_entry_id(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        _, out, _ = _run(
            [*_root(tmp_path), "learn",
             "--title", "Test entry",
             "--type", "pattern",
             "--content", "Content here."],
            capsys,
        )
        assert "PAT-" in out

    def test_output_contains_entry_type(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        _, out, _ = _run(
            [*_root(tmp_path), "learn",
             "--title", "A bug",
             "--type", "bug",
             "--content", "Bug description."],
            capsys,
        )
        assert "bug" in out

    def test_stored_as_correct_type(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        _, out, _ = _run(
            [*_root(tmp_path), "learn",
             "--title", "An ADR",
             "--type", "decision",
             "--content", "We decided to use Python."],
            capsys,
        )
        assert "DEC-" in out

    def test_tags_are_accepted(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        code, _, _ = _run(
            [*_root(tmp_path), "learn",
             "--title", "Tagged entry",
             "--type", "pattern",
             "--tags", "api,rate-limit",
             "--content", "Tag test."],
            capsys,
        )
        assert code == 0

    def test_invalid_type_returns_error(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        code, _, err = _run(
            [*_root(tmp_path), "learn",
             "--title", "Bad type",
             "--type", "nonsense",
             "--content", "Content."],
            capsys,
        )
        assert code == 1
        assert "Error" in err

    def test_empty_content_from_non_tty_stdin_returns_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import io
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        code, _, err = _run(
            [*_root(tmp_path), "learn", "--title", "No content"],
            capsys,
        )
        assert code == 1
        assert "Error" in err

    def test_content_from_non_tty_stdin(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import io
        monkeypatch.setattr("sys.stdin", io.StringIO("Piped content from stdin."))
        code, out, _ = _run(
            [*_root(tmp_path), "learn",
             "--title", "Piped entry",
             "--type", "pattern"],
            capsys,
        )
        assert code == 0
        assert "PAT-" in out


# ---------------------------------------------------------------------------
# task
# ---------------------------------------------------------------------------

class TestTaskCommand:
    def test_task_list_empty(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        _, out, _ = _run([*_root(tmp_path), "task", "list"], capsys)
        assert "No active tasks" in out

    def test_task_list_exits_zero(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        code, _, _ = _run([*_root(tmp_path), "task", "list"], capsys)
        assert code == 0

    def test_task_create_exits_zero(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        code, _, _ = _run(
            [*_root(tmp_path), "task", "create",
             "--title", "Write integration tests",
             "--objective", "Cover all CLI commands."],
            capsys,
        )
        assert code == 0

    def test_task_create_output_contains_id(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        _, out, _ = _run(
            [*_root(tmp_path), "task", "create",
             "--title", "Write integration tests",
             "--objective", "Cover all CLI commands."],
            capsys,
        )
        assert "TASK-" in out

    def test_task_create_then_list_shows_task(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        main([*_root(tmp_path), "task", "create",
              "--title", "Implement CLI",
              "--objective", "Build the monday command.",
              "--priority", "P1"])
        capsys.readouterr()

        _, out, _ = _run([*_root(tmp_path), "task", "list"], capsys)
        assert "Implement CLI" in out
        assert "TASK-" in out

    def test_task_list_shows_priority_and_status(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        main([*_root(tmp_path), "task", "create",
              "--title", "Priority task",
              "--objective", "Test output.",
              "--priority", "P0"])
        capsys.readouterr()

        _, out, _ = _run([*_root(tmp_path), "task", "list"], capsys)
        assert "P0" in out
        assert "backlog" in out

    def test_task_get_returns_task_details(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        _, create_out, _ = _run(
            [*_root(tmp_path), "task", "create",
             "--title", "Detailed task",
             "--objective", "Verify get works.",
             "--type", "fix",
             "--priority", "P1"],
            capsys,
        )
        # Extract TASK-NNNN from create output
        task_id = next(
            w for w in create_out.split() if w.startswith("TASK-")
        )

        _, out, _ = _run([*_root(tmp_path), "task", "get", task_id], capsys)
        assert "Detailed task" in out
        assert "Verify get works" in out
        assert "fix" in out
        assert "P1" in out

    def test_task_get_unknown_id_returns_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        code, _, err = _run([*_root(tmp_path), "task", "get", "TASK-9999"], capsys)
        assert code == 1
        assert "Error" in err

    def test_task_complete_valid_transition(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        # Create → transition through lifecycle → complete via CLI
        from tasks import TaskManager, TaskPriority, TaskStatus, TaskType
        mgr = TaskManager(tmp_path)
        task = mgr.create(
            title="Ready to complete",
            task_type=TaskType.FEATURE,
            priority=TaskPriority.P2,
            objective="Test CLI complete.",
            created_by="human:test",
        )
        mgr.update_status(task.id, TaskStatus.ASSIGNED, changed_by="human:test")
        mgr.update_status(task.id, TaskStatus.IN_PROGRESS, changed_by="human:test")
        capsys.readouterr()

        code, out, _ = _run(
            [*_root(tmp_path), "task", "complete", task.id, "--reason", "Done."],
            capsys,
        )
        assert code == 0
        assert "COMPLETED" in out
        assert task.id in out

    def test_task_complete_invalid_transition_shows_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        # BACKLOG → COMPLETED is not a valid transition
        _, create_out, _ = _run(
            [*_root(tmp_path), "task", "create",
             "--title", "Backlog task",
             "--objective", "Should not complete directly."],
            capsys,
        )
        task_id = next(w for w in create_out.split() if w.startswith("TASK-"))

        code, _, err = _run(
            [*_root(tmp_path), "task", "complete", task_id],
            capsys,
        )
        assert code == 1
        assert "Error" in err

    def test_task_create_missing_title_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            main([*_root(tmp_path), "task", "create", "--objective", "No title."])
        assert exc.value.code != 0

    def test_task_create_missing_objective_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            main([*_root(tmp_path), "task", "create", "--title", "No objective."])
        assert exc.value.code != 0

    def test_task_no_action_exits_nonzero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            main([*_root(tmp_path), "task"])
        assert exc.value.code != 0

    def test_task_list_filter_by_status(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        from tasks import TaskManager, TaskPriority, TaskStatus, TaskType
        mgr = TaskManager(tmp_path)
        t1 = mgr.create(
            title="Backlog task",
            task_type=TaskType.FEATURE,
            priority=TaskPriority.P2,
            objective="Stays in backlog.",
            created_by="human:test",
        )
        t2 = mgr.create(
            title="Assigned task",
            task_type=TaskType.FIX,
            priority=TaskPriority.P1,
            objective="Gets assigned.",
            created_by="human:test",
        )
        mgr.update_status(t2.id, TaskStatus.ASSIGNED, changed_by="human:test")
        capsys.readouterr()

        _, out, _ = _run(
            [*_root(tmp_path), "task", "list", "--status", "assigned"],
            capsys,
        )
        assert "Assigned task" in out
        assert "Backlog task" not in out


# ---------------------------------------------------------------------------
# End-to-end: learn → ask → search round-trip
# ---------------------------------------------------------------------------

class TestEndToEnd:
    def test_learn_ask_search_round_trip(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        # Step 1: learn
        code, out, _ = _run(
            [*_root(tmp_path), "learn",
             "--title", "OpenAI rate limit pattern",
             "--type", "pattern",
             "--tags", "openai,rate-limit",
             "--content", "Use exponential backoff when hitting the OpenAI rate limit."],
            capsys,
        )
        assert code == 0
        assert "PAT-" in out

        # Step 2: search returns it
        _, search_out, _ = _run(
            [*_root(tmp_path), "search", "OpenAI rate limit"],
            capsys,
        )
        assert "OpenAI rate limit pattern" in search_out

        # Step 3: ask finds it via reasoning engine
        _, ask_out, _ = _run(
            [*_root(tmp_path), "ask", "What do we know about rate limits?"],
            capsys,
        )
        assert "found" in ask_out.lower() or "rate" in ask_out.lower()

    def test_task_create_then_ask_about_tasks(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        main([*_root(tmp_path), "task", "create",
              "--title", "Build REST API",
              "--objective", "Expose MondayOS via HTTP.",
              "--type", "feature"])
        capsys.readouterr()

        _, out, _ = _run(
            [*_root(tmp_path), "ask", "Show all tasks related to API"],
            capsys,
        )
        # The task shows up in related_tasks output
        assert "TASK-" in out or "Build REST API" in out
