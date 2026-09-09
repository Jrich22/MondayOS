"""
Reaching the reasoning a decision records, not only the words in its heading.

Decision retrieval matched ADR *titles* and nothing else. A decision can settle a
question its title never names — "why do we retry this way" may be argued at
length inside an ADR called something about scheduling — and that reasoning was
unreachable. It is a retrieval gap, not a wording problem, so it is fixed by
looking in the right place rather than by asking the model to try harder.

The ordering guarantee is the load-bearing part. Titles are tier 1 and body
matches are appended after every one of them, so a title match can never be
displaced. That is a property of the construction, not a ranking heuristic that
usually comes out right.

The fixture is the synthetic corpus, whose ADR-002 is titled for scheduling and
argues idempotency — a word appearing in no title anywhere.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from intelligence.graph import build as build_graph
from intelligence.index import build as build_index
from intelligence.questions import QuestionEngine
from tests.synthetic_corpus import BODY_ONLY_TOPIC, build


class TestTwoTierDecisionRetrieval(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = TemporaryDirectory()
        root = build(Path(cls._tmp.name) / "service")
        index = build_index("synthetic", root, cache_root=Path(cls._tmp.name) / "cache")
        graph = build_graph(index, tasks=[], knowledge=[])
        cls.engine = QuestionEngine(index, graph, tasks=[])

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _decisions(self, question: str):
        return [
            c for c in self.engine.ask(question).evidence.citations if c.kind.value == "decision"
        ]

    def test_a_title_match_is_found(self):
        cited = self._decisions("Why was scheduling designed this way?")
        self.assertTrue(cited)
        self.assertIn("title mentions", cited[0].because)

    def test_a_body_only_topic_is_reachable(self):
        """`idempotent` appears in no ADR title. Title matching alone found nothing."""
        cited = self._decisions(f"Why was {BODY_ONLY_TOPIC} retry designed this way?")
        self.assertTrue(cited, "body-level decision retrieval found nothing")
        self.assertIn("body discusses", cited[0].because)

    def test_a_body_match_cites_the_decisions_own_heading_line(self):
        """
        A decision log holds many ADRs in one file.

        Citing the top of the file would be true and useless; the line must be
        where the decision that actually matched begins.
        """
        cited = self._decisions(f"Why was {BODY_ONLY_TOPIC} retry designed this way?")
        citation = cited[0]
        self.assertGreater(citation.line, 1)
        text = (Path(self._tmp.name) / "service" / citation.path).read_text().splitlines()
        heading = text[citation.line - 1]
        self.assertTrue(heading.startswith("## ADR-"), heading)
        self.assertIn(citation.label.split(":")[0], heading)

    def test_a_title_match_outranks_a_body_match_for_the_same_query(self):
        """
        The guarantee. `scheduling` is in ADR-002's title and also appears in
        ADR-001's neighbourhood; whatever else is found, the title match leads.
        """
        cited = self._decisions("Why was scheduling designed this way?")
        self.assertIn("title mentions", cited[0].because)
        body_positions = [i for i, c in enumerate(cited) if "body discusses" in c.because]
        title_positions = [i for i, c in enumerate(cited) if "title mentions" in c.because]
        if body_positions and title_positions:
            self.assertLess(max(title_positions), min(body_positions))

    def test_no_decision_is_cited_twice(self):
        cited = self._decisions("Why was scheduling designed this way?")
        references = [c.reference for c in cited]
        self.assertEqual(len(references), len(set(references)))

    def test_a_topic_no_decision_covers_returns_no_decision(self):
        """Silence is the honest answer when nothing was written down."""
        self.assertEqual(self._decisions("Why was the mango pipeline designed this way?"), [])

    def test_retrieval_is_deterministic(self):
        first = [
            (c.reference, c.line) for c in self._decisions("Why was scheduling designed this way?")
        ]
        second = [
            (c.reference, c.line) for c in self._decisions("Why was scheduling designed this way?")
        ]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
