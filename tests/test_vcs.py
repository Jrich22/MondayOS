"""
Tests for the project/repository boundary.

Git resolves upward. Run `git log` inside a nested project and git answers about
the enclosing repository — so asking Cue App what changed recently returned
MondayOS's commits, with a citation list identical to MondayOS's own.

The tests that matter are the ones proving the parent's history is *not
retrieved*. Making it unavailable is the fix; telling a narrator to ignore it
would not be, because the wrong evidence would still be in the answer's context.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.vcs import RepoScope, git, scope_for


def _run(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(cwd),
        },
    )


class _Fixture:
    """A parent repository with a nested project inside it."""

    def __init__(self, tmp: str) -> None:
        self.root = Path(tmp).resolve()
        _run(self.root, "init", "-q", "-b", "main", ".")

        (self.root / "parent.md").write_text("parent work\n")
        _run(self.root, "add", "-A")
        _run(self.root, "commit", "-qm", "PARENT: unrelated parent work")

        self.nested = self.root / "projects" / "child"
        self.nested.mkdir(parents=True)
        (self.nested / "child.md").write_text("child work\n")
        _run(self.root, "add", "-A")
        _run(self.root, "commit", "-qm", "CHILD: work inside the nested project")

        (self.root / "parent2.md").write_text("more parent work\n")
        _run(self.root, "add", "-A")
        _run(self.root, "commit", "-qm", "PARENT: more unrelated parent work")


class TestScopeResolution(unittest.TestCase):
    def test_a_standalone_repository_is_not_nested(self):
        with TemporaryDirectory() as tmp:
            fixture = _Fixture(tmp)
            scope = scope_for(fixture.root)
            self.assertTrue(scope.available)
            self.assertFalse(scope.nested)
            self.assertEqual(scope.pathspec, [])

    def test_a_nested_project_is_detected(self):
        with TemporaryDirectory() as tmp:
            fixture = _Fixture(tmp)
            scope = scope_for(fixture.nested)
            self.assertTrue(scope.available)
            self.assertTrue(scope.nested)
            self.assertEqual(scope.toplevel, fixture.root)
            self.assertEqual(scope.pathspec, ["--", str(fixture.nested)])

    def test_a_directory_with_no_repository_reports_absence(self):
        with TemporaryDirectory() as tmp:
            scope = scope_for(Path(tmp) / "nowhere")
            self.assertFalse(scope.available)
            self.assertFalse(scope.nested)
            self.assertIn("not under version control", scope.describe())

    def test_the_boundary_is_stated_for_a_nested_project(self):
        with TemporaryDirectory() as tmp:
            fixture = _Fixture(tmp)
            description = scope_for(fixture.nested).describe()
            self.assertIn("lives inside", description)
            self.assertIn("scoped to this project", description)


class TestHistoryScoping(unittest.TestCase):
    """The load-bearing behaviour: the parent's commits are never retrieved."""

    def test_a_nested_project_sees_only_its_own_commits(self):
        with TemporaryDirectory() as tmp:
            fixture = _Fixture(tmp)
            log = git(scope_for(fixture.nested), "log", "--format=%s")
            self.assertIn("CHILD: work inside the nested project", log)
            self.assertNotIn("PARENT:", log)

    def test_the_parent_still_sees_everything(self):
        with TemporaryDirectory() as tmp:
            fixture = _Fixture(tmp)
            log = git(scope_for(fixture.root), "log", "--format=%s")
            self.assertIn("PARENT: unrelated parent work", log)
            self.assertIn("CHILD: work inside the nested project", log)

    def test_a_nested_project_with_no_history_returns_nothing(self):
        """
        Absence must be reported, never substituted. An empty result is what lets
        a caller say "no commits touch this project" instead of borrowing.
        """
        with TemporaryDirectory() as tmp:
            fixture = _Fixture(tmp)
            empty = fixture.root / "projects" / "untouched"
            empty.mkdir(parents=True)
            log = git(scope_for(empty), "log", "--format=%s")
            self.assertEqual(log, "")

    def test_status_is_scoped_too(self):
        with TemporaryDirectory() as tmp:
            fixture = _Fixture(tmp)
            (fixture.root / "dirty-parent.md").write_text("x")
            (fixture.nested / "dirty-child.md").write_text("x")

            child = git(scope_for(fixture.nested), "status", "--porcelain")
            self.assertIn("dirty-child", child)
            self.assertNotIn("dirty-parent", child)

    def test_pathspec_can_be_disabled_for_commands_where_a_path_is_meaningless(self):
        """
        `rev-parse` answers about the repository by nature. A nested project
        genuinely shares its parent's branch, and saying so is accurate.
        """
        with TemporaryDirectory() as tmp:
            fixture = _Fixture(tmp)
            branch = git(
                scope_for(fixture.nested),
                "rev-parse",
                "--abbrev-ref",
                "HEAD",
                pathspec=False,
            )
            self.assertEqual(branch, "main")

    def test_no_repository_yields_no_history_rather_than_an_error(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(git(scope_for(Path(tmp) / "nope"), "log"), "")

    def test_a_broken_repository_degrades_rather_than_raising(self):
        scope = RepoScope(root=Path("/nonexistent"), toplevel=Path("/nonexistent"))
        self.assertEqual(git(scope, "log"), "")


class TestRealCorpora(unittest.TestCase):
    """
    Against the actual repositories, when they are present.

    Skipped rather than failed when a corpus is absent, so the suite stays green
    on a machine that does not have every project checked out.
    """

    REPO = Path(__file__).resolve().parent.parent

    def test_mondayos_is_standalone(self):
        scope = scope_for(self.REPO)
        self.assertTrue(scope.available)
        self.assertFalse(scope.nested)

    def test_nested_products_are_detected_and_scoped(self):
        for name in ("cue-app", "sourcingbot"):
            root = self.REPO / "projects" / name
            if not root.is_dir():
                self.skipTest(f"{name} not present")
            with self.subTest(project=name):
                scope = scope_for(root)
                self.assertTrue(scope.nested, f"{name} should be nested in MondayOS")
                log = git(scope, "log", "-40", "--format=%s")
                self.assertTrue(log, f"{name} should have its own commits")
                # The regression: MondayOS's own identity work must not appear.
                self.assertNotIn("identity policy and a shared sequence", log)
                self.assertNotIn("stop indexing MondayOS's own output", log)

    def test_a_standalone_external_repo_is_unchanged(self):
        root = Path("/Users/jrich/AI-Labs/WeatherBot")
        if not root.is_dir():
            self.skipTest("WeatherBot not present")
        scope = scope_for(root)
        self.assertFalse(scope.nested)
        self.assertEqual(scope.pathspec, [])


if __name__ == "__main__":
    unittest.main()
