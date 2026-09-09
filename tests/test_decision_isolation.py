"""
Decision records belong to the project that wrote them.

A repository can contain other repositories' worth of work. MondayOS keeps Cue
App and sourcingBOT under `projects/`, each with its own `docs/DECISIONS.md`, its
own ADR-001, and its own reasoning. Before this, MondayOS's decision graph held
forty nodes of which nineteen were sourcingBOT's, and asking MondayOS why
something was decided could answer with another project's decision.

`citations_outside_root` never caught it, because
`projects/sourcingbot/docs/DECISIONS.md` genuinely *is* inside MondayOS's root.
The boundary that matters is the project's, not the filesystem's.

These tests assert the property at **retrieval**: the wrong decision must be
unavailable, not merely unmentioned. An answer that omits bad evidence by
judgement is one prompt change away from including it.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.boundary import nested_roots, outside
from intelligence.graph import build as build_graph
from intelligence.graph import project_boundaries
from intelligence.index import build as build_index
from intelligence.models import NodeKind
from intelligence.questions import QuestionEngine

PARENT_ADRS = """# Decisions

## ADR-001: The parent stores everything as files

**Status:** Accepted

The parent project keeps its own reasoning about caching here.

## ADR-002: The parent uses a single scheduler

**Status:** Accepted

Scheduling is centralised.
"""

NESTED_ADRS = """# Decisions

## ADR-001: The nested app renders on the client

**Status:** Accepted

The nested project made a completely different call about caching.

## ADR-002: The nested app ships weekly

**Status:** Accepted
"""


class TestBoundaryDetection(unittest.TestCase):
    def test_a_holder_of_projects_is_a_boundary(self):
        roots = nested_roots(
            ["apps/web/package.json", "apps/api/pyproject.toml", "engine/run.py"],
            frozenset({"engine"}),
        )
        self.assertEqual(roots, frozenset({"apps/web", "apps/api"}))

    def test_a_project_root_with_its_own_source_is_not_a_boundary(self):
        """`dashboard/` has a package.json and belongs to this repository."""
        roots = nested_roots(
            ["dashboard/package.json", "dashboard/src/app.tsx", "monday/api.py"],
            frozenset({"dashboard", "dashboard/src", "monday"}),
        )
        self.assertEqual(roots, frozenset())

    def test_outside_matches_the_root_and_everything_under_it(self):
        roots = frozenset({"projects/sourcingbot"})
        self.assertTrue(outside("projects/sourcingbot", roots))
        self.assertTrue(outside("projects/sourcingbot/docs/DECISIONS.md", roots))
        self.assertFalse(outside("projects/sourcingbot-notes.md", roots))
        self.assertFalse(outside("docs/DECISIONS.md", roots))


class TestDecisionsAreScopedToTheirProject(unittest.TestCase):
    """A parent with a nested project, both keeping decision logs."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._write("pyproject.toml", "[project]\nname = 'parent'\n")
        self._write("docs/DECISIONS.md", PARENT_ADRS)
        for i in range(5):
            self._write(f"engine/mod{i}.py", "value = 1\n")
        # The nested project: its own marker, its own source, its own decisions.
        self._write("projects/nested/package.json", '{"name": "nested"}\n')
        self._write("projects/nested/docs/DECISIONS.md", NESTED_ADRS)
        for i in range(5):
            self._write(f"projects/nested/src/mod{i}.ts", "export const x = 1;\n")

        self.index = build_index("parent", self.root, cache_root=self.root / ".idx")
        self.graph = build_graph(self.index, tasks=[], knowledge=[])
        self.engine = QuestionEngine(self.index, self.graph, tasks=[])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_the_nested_project_is_detected_as_a_boundary(self):
        self.assertIn("projects/nested", project_boundaries(self.index))

    def test_the_graph_still_holds_both_logs(self):
        """The graph is a map of the repository; scoping is retrieval's job."""
        paths = {n.path for n in self.graph.of_kind(NodeKind.DECISION)}
        self.assertIn("docs/DECISIONS.md", paths)
        self.assertIn("projects/nested/docs/DECISIONS.md", paths)

    def test_retrieval_cannot_reach_the_nested_projects_decisions(self):
        """The load-bearing assertion: unavailable, not merely unmentioned."""
        own = self.engine._own_decisions()
        self.assertTrue(own)
        for node in own:
            self.assertFalse(
                node.path.startswith("projects/"),
                f"{node.path} belongs to another project",
            )

    def test_the_two_projects_adr_001s_do_not_collide(self):
        labels = {n.label for n in self.engine._own_decisions()}
        self.assertTrue(any("stores everything as files" in label for label in labels))
        self.assertFalse(any("renders on the client" in label for label in labels))

    def test_a_question_the_nested_log_answers_returns_no_decision(self):
        """
        "client rendering" is decided only in the nested project.

        The parent must not answer with it. Returning nothing is the correct,
        honest outcome.
        """
        answer = self.engine.ask("Why was client rendering designed this way?")
        cited = [c for c in answer.evidence.citations if c.kind.value == "decision"]
        self.assertEqual(cited, [], [c.label for c in cited])

    def test_a_question_the_parents_log_answers_still_works(self):
        answer = self.engine.ask("Why was the scheduler designed this way?")
        cited = [c for c in answer.evidence.citations if c.kind.value == "decision"]
        self.assertTrue(cited)
        for citation in cited:
            self.assertFalse(citation.path.startswith("projects/"))

    def test_no_citation_resolves_into_another_project(self):
        for question in (
            "Why was caching designed this way?",
            "Where is caching implemented?",
            "What is the scheduler?",
        ):
            with self.subTest(question=question):
                for citation in self.engine.ask(question).evidence.citations:
                    if citation.path:
                        self.assertFalse(
                            citation.path.startswith("projects/nested/"),
                            f"{question} cited {citation.path}",
                        )


if __name__ == "__main__":
    unittest.main()
