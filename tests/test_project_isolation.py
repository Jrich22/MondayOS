"""
Cross-project evidence isolation.

Treated as a correctness property, not a presentation one: the wrong evidence
must be *unavailable*, not merely unmentioned. Every test here asserts on
retrieved data — citations, graph nodes — rather than on rendered text, because
an answer that never receives the parent repository's commits cannot cite them,
while an answer told not to mention them still has them in context.

The regression: `projects/cue-app` resolves its git toplevel to MondayOS, so an
unscoped `git log` returned MondayOS's commits and "what changed recently?" for
Cue App produced a citation list identical to MondayOS's own.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from intelligence.graph import build as build_graph
from intelligence.index import build as build_index
from intelligence.models import NodeKind
from intelligence.questions import QuestionEngine

REPO = Path(__file__).resolve().parent.parent


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


class _Nested:
    """A parent repository with a busy history and a quiet nested project."""

    def __init__(self, tmp: str) -> None:
        self.root = Path(tmp).resolve()
        _run(self.root, "init", "-q", "-b", "main", ".")
        (self.root / "parent.py").write_text("class ParentThing:\n    pass\n")
        _run(self.root, "add", "-A")
        _run(self.root, "commit", "-qm", "PARENTWORK: sweeping parent refactor")

        self.child = self.root / "projects" / "child"
        self.child.mkdir(parents=True)
        (self.child / "child.py").write_text("class ChildThing:\n    pass\n")
        _run(self.root, "add", "-A")
        _run(self.root, "commit", "-qm", "CHILDWORK: the nested project's own change")

        for n in range(3):
            (self.root / f"more{n}.py").write_text(f"x = {n}\n")
            _run(self.root, "add", "-A")
            _run(self.root, "commit", "-qm", f"PARENTWORK: unrelated parent change {n}")

    def engine(self, root: Path, name: str) -> tuple[QuestionEngine, object]:
        index = build_index(name, root, cache_root=self.root / ".idx" / name)
        graph = build_graph(index, tasks=[], knowledge=[])
        return QuestionEngine(index, graph, []), graph


class TestNestedProjectHistory(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fixture = _Nested(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_nested_project_never_cites_the_parents_commits(self):
        """The regression, asserted on retrieved citations rather than prose."""
        engine, _ = self.fixture.engine(self.fixture.child, "child")
        answer = engine.ask("What changed recently?")
        labels = [c.label for c in answer.evidence.citations]
        self.assertTrue(any("CHILDWORK" in label for label in labels), labels)
        self.assertFalse([label for label in labels if "PARENTWORK" in label], labels)

    def test_the_parent_still_sees_its_own_history(self):
        engine, _ = self.fixture.engine(self.fixture.root, "parent")
        labels = [c.label for c in engine.ask("What changed recently?").evidence.citations]
        self.assertTrue(any("PARENTWORK" in label for label in labels))

    def test_graph_commit_nodes_are_scoped(self):
        """
        Not just the answer: COMMIT and PULL_REQUEST nodes feed initiative
        membership and drift detection, so an unscoped graph poisons those too.
        """
        _, graph = self.fixture.engine(self.fixture.child, "child")
        labels = [n.label for n in graph.of_kind(NodeKind.COMMIT)]
        self.assertTrue(any("CHILDWORK" in label for label in labels), labels)
        self.assertFalse([label for label in labels if "PARENTWORK" in label], labels)

    def test_a_nested_project_with_no_history_says_so_rather_than_borrowing(self):
        """
        Absence stated, never substituted. "No commits" would read as "nothing
        happened"; the truth is "nothing touched this project", and the parent
        being busy is exactly when the difference matters.
        """
        quiet = self.fixture.root / "projects" / "quiet"
        quiet.mkdir(parents=True)
        (quiet / "untouched.py").write_text("x = 1\n")
        engine, _ = self.fixture.engine(quiet, "quiet")
        answer = engine.ask("What changed recently?")
        self.assertEqual(answer.evidence.citations, [])
        self.assertIn("touch this project", answer.finding)
        self.assertNotIn("PARENTWORK", answer.finding)

    def test_a_project_without_version_control_reports_that(self):
        with TemporaryDirectory() as plain:
            root = Path(plain)
            (root / "thing.py").write_text("x = 1\n")
            engine, _ = self.fixture.engine(root, "plain")
            answer = engine.ask("What changed recently?")
            self.assertIn("not under version control", answer.finding)


class TestRealCorpora(unittest.TestCase):
    """
    Against the actual projects, skipping any that is not checked out.

    MondayOS's own work is the marker: if a nested product cites it, the boundary
    has failed.
    """

    MARKERS = (
        "identity policy",
        "sequence allocator",
        "stop indexing MondayOS",
        "conversation router",
        "canonical slug",
    )

    def _cites(self, name: str, root: Path) -> tuple[list[str], list[str]]:
        index = build_index(name, root, cache_root=Path("/tmp/s1-iso") / name)
        graph = build_graph(index, tasks=[], knowledge=[])
        answer = QuestionEngine(index, graph, []).ask("What changed recently?")
        return (
            [c.label for c in answer.evidence.citations],
            [n.label for n in graph.of_kind(NodeKind.COMMIT)],
        )

    def test_nested_products_do_not_cite_mondayos_work(self):
        for name in ("cue-app", "sourcingbot"):
            root = REPO / "projects" / name
            if not root.is_dir():
                self.skipTest(f"{name} not present")
            with self.subTest(project=name):
                cites, nodes = self._cites(name, root)
                self.assertTrue(cites, f"{name} should have its own commits")
                for label in cites + nodes:
                    for marker in self.MARKERS:
                        self.assertNotIn(
                            marker.lower(),
                            label.lower(),
                            f"{name} cited MondayOS work: {label}",
                        )

    def test_mondayos_still_sees_its_own_history(self):
        cites, nodes = self._cites("mondayos", REPO)
        self.assertTrue(cites)
        self.assertTrue(nodes)

    def test_a_standalone_external_repo_is_unaffected(self):
        root = Path("/Users/jrich/AI-Labs/WeatherBot")
        if not root.is_dir():
            self.skipTest("WeatherBot not present")
        cites, nodes = self._cites("weatherbot", root)
        self.assertTrue(cites, "WeatherBot should keep its existing behaviour")
        self.assertTrue(nodes)


if __name__ == "__main__":
    unittest.main()
