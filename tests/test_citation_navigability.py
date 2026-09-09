"""
A citation should land a reader where the evidence is.

"workspace/service.py" is a hint. "workspace/service.py:82" is checkable, and the
difference decides whether anyone bothers to look. Before this, only symbols and
decisions carried a line; every file citation named a path and stopped, which on
MondayOS meant roughly one citation in nine could be opened at the right place.

The constraint that shapes the whole design: **a line must be real**. Two sources
are allowed, both of them evidence that already exists — a symbol the index
recorded, or the line where a subject term actually occurs. When neither applies
the citation stays file-only, because a number nobody can check is worse than no
number at all. It looks like precision.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from intelligence.graph import build as build_graph
from intelligence.index import build as build_index
from intelligence.questions import QuestionEngine

MODULE = '''"""Billing."""


def unrelated_helper():
    return 1


class InvoiceLedger:
    """Records charges."""

    def append(self, charge):
        return charge
'''

PROSE = """# Overview

Some preamble that mentions nothing in particular.

The invoice ledger is written before the charge is sent.

More text.
"""


class TestCitationsCarryRealLines(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._write("pyproject.toml", "[project]\nname = 'demo'\n")
        self._write("billing/ledger.py", MODULE)
        for i in range(4):
            self._write(f"billing/mod{i}.py", "value = 1\n")
        self._write("docs/OVERVIEW.md", PROSE)
        self.index = build_index("demo", self.root, cache_root=self.root / ".idx")
        self.graph = build_graph(self.index, tasks=[], knowledge=[])
        self.engine = QuestionEngine(self.index, self.graph, tasks=[])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def _citations(self, question: str):
        return self.engine.ask(question).evidence.citations

    def test_every_cited_line_exists_in_the_file(self):
        """The one property that makes any of this worth doing."""
        for question in (
            "Where is InvoiceLedger implemented?",
            "What is the invoice ledger?",
            "Where is the invoice ledger documented?",
        ):
            with self.subTest(question=question):
                for citation in self._citations(question):
                    if not citation.line or not citation.path:
                        continue
                    lines = (self.root / citation.path).read_text().splitlines()
                    self.assertLessEqual(citation.line, len(lines), citation.path)
                    self.assertGreaterEqual(citation.line, 1)

    def test_a_symbol_is_cited_at_its_definition(self):
        cited = [c for c in self._citations("Where is InvoiceLedger implemented?") if c.line]
        self.assertTrue(cited)
        for citation in cited:
            if citation.path.endswith("ledger.py"):
                text = (self.root / citation.path).read_text().splitlines()
                self.assertIn("InvoiceLedger", text[citation.line - 1])
                break
        else:
            self.fail("the defining file was never cited with a line")

    def test_a_prose_file_is_cited_at_the_line_the_term_appears_on(self):
        cited = [
            c
            for c in self._citations("Where is the invoice ledger documented?")
            if c.path.endswith("OVERVIEW.md") and c.line
        ]
        self.assertTrue(cited, "the document was cited without a line")
        text = (self.root / "docs/OVERVIEW.md").read_text().splitlines()
        self.assertIn("invoice ledger", text[cited[0].line - 1].lower())

    def test_a_file_with_no_matching_line_stays_file_only(self):
        """
        No line is a legitimate answer.

        `_line_for` with terms that appear nowhere must return 0 rather than
        defaulting to 1, which would be a fabricated citation that happens to
        look plausible.
        """
        entry = self.index.files.get("docs/OVERVIEW.md")
        self.assertEqual(self.engine._line_for("docs/OVERVIEW.md", entry, ["zeppelin"]), 0)
        self.assertEqual(self.engine._line_for("docs/OVERVIEW.md", entry, []), 0)

    def test_a_missing_file_yields_no_line_rather_than_an_error(self):
        self.assertEqual(self.engine._line_for("nope/gone.py", None, ["anything"]), 0)

    def test_commits_are_never_given_a_source_line(self):
        """A commit has no file location; inventing one would be nonsense."""
        for citation in self._citations("What changed recently?"):
            if citation.kind.value in ("commit", "pull-request"):
                self.assertFalse(citation.line)

    def test_citations_stay_inside_the_project(self):
        for question in ("Where is InvoiceLedger implemented?", "What is the invoice ledger?"):
            with self.subTest(question=question):
                for citation in self._citations(question):
                    if citation.path:
                        self.assertFalse(citation.path.startswith("/"))
                        self.assertNotIn("..", citation.path)

    def test_lines_are_deterministic(self):
        first = [(c.path, c.line) for c in self._citations("What is the invoice ledger?")]
        second = [(c.path, c.line) for c in self._citations("What is the invoice ledger?")]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
