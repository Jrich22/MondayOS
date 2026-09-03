"""
Tests for deterministic project intelligence.

Three properties carry the weight here, and each is the kind that fails quietly:

**Determinism.** The same project must produce the same index and the same
answers. A retrieval layer that drifts is one whose answers cannot be compared
between runs, and the whole promise of this subsystem is that its output is
checkable.

**Grounding.** Every answer must cite what it was built from, and every citation
must be navigable. An uncited answer and an invented one look identical.

**Isolation and secrets.** The index is read to build prompts, so it inherits the
Context Engine's rules: one project, and nothing credential-shaped ever enters it.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from intelligence import build_graph, build_index
from intelligence.evidence import Citation, CitationKind, Evidence
from intelligence.index import extract_references, extract_terms
from intelligence.models import EdgeKind, FileKind, NodeKind, SymbolKind
from intelligence.questions import Intent, QuestionEngine, classify, subject_terms
from intelligence.scanner import classify as classify_file
from intelligence.symbols import from_python, from_typescript

SOURCE = '''
"""Module docstring. Implements ADR-017."""
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

MAX_RETRIES = 3


@dataclass
class Snapshot:
    """A point in time."""

    id: str


class Colour(Enum):
    RED = "red"


class Writer(Protocol):
    def write(self, text: str) -> None: ...


class Engine:
    """Does the work. See TASK-0073."""

    def build(self, project: str) -> str:
        """Assemble."""
        return project


def helper(value: int) -> int:
    return value
'''

TSX = """
export interface WorkspaceState { project: string }
export type Mode = "live" | "demo";
export enum Kind { A, B }
export class Engine {}
export function render(value: string) { return value; }
export const MAX_ITEMS = 40;
export const handler = (a: number) => a;
"""


def _project(root: Path, name: str = "alpha") -> Path:
    """A small but realistic project: source, test, docs, ADRs, config."""
    path = root / name
    (path / "src").mkdir(parents=True, exist_ok=True)
    (path / "tests").mkdir(parents=True, exist_ok=True)
    (path / "docs").mkdir(parents=True, exist_ok=True)
    (path / "src" / "engine.py").write_text(SOURCE)
    (path / "src" / "ui.tsx").write_text(TSX)
    (path / "tests" / "test_engine.py").write_text("def test_engine_builds():\n    assert True\n")
    (path / "docs" / "ARCHITECTURE.md").write_text("# Architecture\n\nThe engine streams output.\n")
    (path / "docs" / "DECISIONS.md").write_text(
        "# Decisions\n\n"
        "## ADR-017: Streaming Is Deterministic\n\n**Status:** Accepted\n\nBecause.\n\n"
        "## ADR-018 — Providers Are Injected\n\n**Status:** Accepted\n\nBecause.\n"
    )
    (path / "pyproject.toml").write_text("[tool.x]\nname = 'alpha'\n")
    return path


class TestScanner(unittest.TestCase):
    def test_classifies_by_role_not_extension(self):
        with TemporaryDirectory() as tmp:
            root = _project(Path(tmp))
            self.assertIs(classify_file(root / "src" / "engine.py", root), FileKind.SOURCE)
            self.assertIs(classify_file(root / "tests" / "test_engine.py", root), FileKind.TEST)
            self.assertIs(
                classify_file(root / "docs" / "ARCHITECTURE.md", root), FileKind.DOCUMENTATION
            )
            self.assertIs(classify_file(root / "docs" / "DECISIONS.md", root), FileKind.DECISION)
            self.assertIs(classify_file(root / "pyproject.toml", root), FileKind.CONFIG)

    def test_a_decision_log_outranks_documentation(self):
        """ "Why did we decide" must reach decisions, not prose."""
        with TemporaryDirectory() as tmp:
            root = _project(Path(tmp))
            index = build_index("alpha", root, use_cache=False)
            self.assertEqual(len(index.files_of(FileKind.DECISION)), 1)

    def test_secrets_are_never_indexed(self):
        with TemporaryDirectory() as tmp:
            root = _project(Path(tmp))
            (root / ".env").write_text("OPENAI_API_KEY=sk-live-must-never-appear\n")
            (root / "src" / "aws.credentials.json").write_text('{"key": "AKIAXXXXXXXXXXXXXXXX"}')
            index = build_index("alpha", root, use_cache=False)

            self.assertNotIn(".env", index.files)
            self.assertFalse([p for p in index.files if "credential" in p])
            # And no trace in the searchable surface either.
            self.assertNotIn("sk", index.terms.get("live", set()))

    def test_generated_files_are_skipped(self):
        with TemporaryDirectory() as tmp:
            root = _project(Path(tmp))
            (root / "package-lock.json").write_text('{"lockfileVersion": 3}')
            index = build_index("alpha", root, use_cache=False)
            self.assertNotIn("package-lock.json", index.files)


class TestSymbols(unittest.TestCase):
    def test_python_kinds_are_distinguished(self):
        symbols = {s.name: s for s in from_python(SOURCE, "engine.py")}
        self.assertIs(symbols["Snapshot"].kind, SymbolKind.DATACLASS)
        self.assertIs(symbols["Colour"].kind, SymbolKind.ENUM)
        self.assertIs(symbols["Writer"].kind, SymbolKind.PROTOCOL)
        self.assertIs(symbols["Engine"].kind, SymbolKind.CLASS)
        self.assertIs(symbols["helper"].kind, SymbolKind.FUNCTION)
        self.assertIs(symbols["MAX_RETRIES"].kind, SymbolKind.CONSTANT)

    def test_methods_record_their_class(self):
        build = next(s for s in from_python(SOURCE, "engine.py") if s.name == "build")
        self.assertIs(build.kind, SymbolKind.METHOD)
        self.assertEqual(build.parent, "Engine")
        self.assertEqual(build.qualified, "Engine.build")

    def test_definitions_carry_a_navigable_line_range(self):
        engine = next(s for s in from_python(SOURCE, "engine.py") if s.name == "Engine")
        self.assertGreater(engine.line, 0)
        self.assertGreater(engine.end_line, engine.line)

    def test_a_file_that_does_not_parse_yields_nothing(self):
        """An index that fails on broken syntax is useless during a refactor."""
        self.assertEqual(from_python("def (:::", "broken.py"), [])

    def test_typescript_exports(self):
        kinds = {s.name: s.kind for s in from_typescript(TSX, "ui.tsx")}
        self.assertIs(kinds["WorkspaceState"], SymbolKind.INTERFACE)
        self.assertIs(kinds["Mode"], SymbolKind.TYPE)
        self.assertIs(kinds["Kind"], SymbolKind.ENUM)
        self.assertIs(kinds["Engine"], SymbolKind.CLASS)
        self.assertIs(kinds["render"], SymbolKind.FUNCTION)
        self.assertIs(kinds["MAX_ITEMS"], SymbolKind.CONSTANT)


class TestTerms(unittest.TestCase):
    def test_identifiers_are_split_and_kept_whole(self):
        terms = extract_terms("def render_context(self): pass")
        self.assertIn("render_context", terms)
        self.assertIn("render", terms)
        self.assertIn("context", terms)

    def test_camel_case_is_split(self):
        terms = extract_terms("class ContextEngine: pass")
        self.assertIn("contextengine", terms)
        self.assertIn("context", terms)
        self.assertIn("engine", terms)

    def test_the_path_contributes_terms(self):
        terms = extract_terms("x = 1", "growth/generation/planner.py")
        self.assertIn("growth", terms)
        self.assertIn("planner", terms)

    def test_artefact_references_are_extracted(self):
        refs = extract_references("Implements ADR-017 for TASK-0073, merged in PR #39.")
        self.assertIn("ADR-017", refs)
        self.assertIn("TASK-0073", refs)
        self.assertIn("PR#39", refs)


class TestIndex(unittest.TestCase):
    def test_building_twice_yields_an_identical_index(self):
        with TemporaryDirectory() as tmp:
            root = _project(Path(tmp))
            first = build_index("alpha", root, use_cache=False)
            second = build_index("alpha", root, use_cache=False)
            self.assertEqual(sorted(first.files), sorted(second.files))
            self.assertEqual(first.to_dict()["files"], second.to_dict()["files"])

    def test_the_cache_makes_a_rebuild_reuse_unchanged_files(self):
        with TemporaryDirectory() as tmp:
            root = _project(Path(tmp))
            cache = Path(tmp) / "cache"
            first = build_index("alpha", root, cache_root=cache)
            self.assertEqual(first.stats.reused, 0)

            second = build_index("alpha", root, cache_root=cache)
            self.assertEqual(second.stats.reparsed, 0)
            self.assertGreater(second.stats.reused, 0)

    def test_a_changed_file_is_reparsed(self):
        with TemporaryDirectory() as tmp:
            root = _project(Path(tmp))
            cache = Path(tmp) / "cache"
            build_index("alpha", root, cache_root=cache)

            target = root / "src" / "engine.py"
            target.write_text(SOURCE + "\n\ndef added() -> None:\n    pass\n")
            import os
            import time

            os.utime(target, (time.time() + 10, time.time() + 10))

            second = build_index("alpha", root, cache_root=cache)
            self.assertEqual(second.stats.reparsed, 1)
            self.assertTrue(second.find_symbol("added"))

    def test_a_corrupt_cache_rebuilds_rather_than_failing(self):
        """Stale context is worse than slow context."""
        with TemporaryDirectory() as tmp:
            root = _project(Path(tmp))
            cache = Path(tmp) / "cache"
            build_index("alpha", root, cache_root=cache)
            (cache / "alpha.json").write_text("{not json")
            rebuilt = build_index("alpha", root, cache_root=cache)
            self.assertGreater(rebuilt.stats.reparsed, 0)
            self.assertTrue(rebuilt.files)

    def test_symbol_lookup_resolves_to_a_definition(self):
        with TemporaryDirectory() as tmp:
            root = _project(Path(tmp))
            index = build_index("alpha", root, use_cache=False)
            found = index.find_symbol("Engine")
            self.assertTrue(found)
            self.assertTrue(any(s.path.endswith("engine.py") for s in found))

    def test_term_search_finds_content_not_filenames(self):
        with TemporaryDirectory() as tmp:
            root = _project(Path(tmp))
            index = build_index("alpha", root, use_cache=False)
            # "streams" appears only inside ARCHITECTURE.md's prose.
            hits = index.files_with("streams")
            self.assertIn("docs/ARCHITECTURE.md", hits)

    def test_the_index_covers_one_project_only(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = _project(root, "alpha")
            beta = _project(root, "beta")
            (beta / "src" / "secret_feature.py").write_text("BETA_ONLY_SYMBOL = 1\n")

            index = build_index("alpha", alpha, use_cache=False)
            self.assertFalse(index.find_symbol("BETA_ONLY_SYMBOL"))
            self.assertFalse([p for p in index.files if "beta" in p])


class TestGraph(unittest.TestCase):
    def _graph(self, tmp: str):
        root = _project(Path(tmp))
        index = build_index("alpha", root, use_cache=False)
        tasks = [
            {
                "id": "TASK-0073",
                "title": "Streaming",
                "status": "in-progress",
                "objective": "Implement ADR-017",
                "context": "",
                "project": "alpha",
            },
            {
                "id": "TASK-0099",
                "title": "Blocked work",
                "status": "blocked",
                "objective": "",
                "context": "",
                "blocked_reason": "waiting on review",
            },
        ]
        return index, build_graph(index, tasks=tasks), tasks

    def test_a_task_naming_an_adr_links_to_it(self):
        with TemporaryDirectory() as tmp:
            _, graph, _ = self._graph(tmp)
            edges = [e for e in graph.edges if e.kind is EdgeKind.IMPLEMENTS]
            self.assertTrue(edges)
            self.assertEqual(edges[0].source, "task:TASK-0073")
            self.assertIn("names ADR-017", edges[0].because)

    def test_code_naming_an_adr_links_to_it(self):
        with TemporaryDirectory() as tmp:
            _, graph, _ = self._graph(tmp)
            refs = [
                e
                for e in graph.edges
                if e.kind is EdgeKind.REFERENCES and e.source.startswith("file:")
            ]
            self.assertTrue(any("engine.py" in e.source for e in refs))

    def test_tests_link_to_what_they_cover(self):
        with TemporaryDirectory() as tmp:
            _, graph, _ = self._graph(tmp)
            tests = [e for e in graph.edges if e.kind is EdgeKind.TESTS]
            self.assertTrue(tests)
            self.assertIn("test_engine.py", tests[0].source)

    def test_both_adr_heading_styles_are_parsed(self):
        """MondayOS writes "ADR-001: Title"; sourcingBOT writes "ADR-019 — Title"."""
        with TemporaryDirectory() as tmp:
            _, graph, _ = self._graph(tmp)
            labels = [n.label for n in graph.of_kind(NodeKind.DECISION)]
            self.assertTrue(any(label.startswith("ADR-017") for label in labels))
            self.assertTrue(any(label.startswith("ADR-018") for label in labels))

    def test_every_edge_carries_its_evidence(self):
        with TemporaryDirectory() as tmp:
            _, graph, _ = self._graph(tmp)
            for edge in graph.edges:
                self.assertTrue(edge.because, f"{edge.source} -> {edge.target} has no reason")

    def test_an_edge_to_a_missing_node_is_refused(self):
        with TemporaryDirectory() as tmp:
            _, graph, _ = self._graph(tmp)
            before = len(graph.edges)
            from intelligence.models import Edge

            graph.add_edge(Edge("task:GHOST", "file:nowhere", EdgeKind.REFERENCES, "invented"))
            self.assertEqual(len(graph.edges), before)


class TestQuestionEngine(unittest.TestCase):
    def _engine(self, tmp: str) -> QuestionEngine:
        root = _project(Path(tmp))
        index = build_index("alpha", root, use_cache=False)
        tasks = [
            {
                "id": "TASK-0073",
                "title": "Streaming responder",
                "status": "in-progress",
                "objective": "Implement ADR-017 streaming",
                "context": "",
                "project": "alpha",
            },
            {
                "id": "TASK-0099",
                "title": "Blocked work",
                "status": "blocked",
                "objective": "",
                "context": "",
                "blocked_reason": "waiting on review",
            },
        ]
        return QuestionEngine(index, build_graph(index, tasks=tasks), tasks)

    def test_intent_classification(self):
        cases = {
            "What are we currently building?": Intent.CURRENT_WORK,
            "Why did we make this decision about streaming?": Intent.WHY_DECISION,
            "Where is the Engine implemented?": Intent.WHERE_IMPLEMENTED,
            "Show me everything related to providers.": Intent.EVERYTHING_ABOUT,
            "Why is TASK-0099 blocked?": Intent.WHY_BLOCKED,
            "What changed since yesterday?": Intent.WHAT_CHANGED,
            "Where is streaming documented?": Intent.WHERE_DOCUMENTED,
        }
        for question, expected in cases.items():
            self.assertIs(classify(question), expected, question)

    def test_question_words_are_not_treated_as_the_subject(self):
        self.assertEqual(subject_terms("Where is streaming implemented?"), ["streaming"])

    def test_artefact_ids_survive_tokenisation(self):
        """Splitting TASK-0099 would search for "task", which matches everything."""
        self.assertIn("TASK-0099", subject_terms("Why is TASK-0099 blocked?"))

    def test_where_implemented_returns_definitions_with_lines(self):
        with TemporaryDirectory() as tmp:
            answer = self._engine(tmp).ask("Where is the Engine implemented?")
            self.assertIs(answer.intent, Intent.WHERE_IMPLEMENTED)
            symbols = answer.evidence.of(CitationKind.SYMBOL)
            self.assertTrue(symbols)
            self.assertTrue(all(c.line > 0 for c in symbols))
            self.assertTrue(any("engine.py" in c.path for c in symbols))

    def test_why_decision_returns_decision_records(self):
        with TemporaryDirectory() as tmp:
            answer = self._engine(tmp).ask("Why did we decide streaming should work this way?")
            decisions = answer.evidence.of(CitationKind.DECISION)
            self.assertTrue(decisions)
            self.assertTrue(any("ADR-017" in c.label for c in decisions))
            self.assertTrue(all(c.path and c.line for c in decisions))

    def test_why_decision_with_no_adr_still_shows_what_exists(self):
        """
        Silence is the wrong answer to a reasonable question.

        Returning nothing dropped both the answer and — because an ungrounded
        answer produces no context source — the carried subject. "No ADR covers
        this, here is the implementation" is true and useful.
        """
        with TemporaryDirectory() as tmp:
            answer = self._engine(tmp).ask("Why did we design the Snapshot that way?")
            self.assertIs(answer.intent, Intent.WHY_DECISION)
            self.assertIn("No decision record", answer.finding)
            self.assertTrue(answer.grounded)
            self.assertIn("What exists instead", answer.finding)

    def test_design_and_way_are_question_words_not_subjects(self):
        self.assertEqual(subject_terms("Why did we design it that way?"), [])

    def test_why_blocked_answers_the_question_that_was_asked(self):
        """A task that is not blocked must be reported as not blocked."""
        with TemporaryDirectory() as tmp:
            engine = self._engine(tmp)
            blocked = engine.ask("Why is TASK-0099 blocked?")
            self.assertIn("waiting on review", blocked.finding)

            other = engine.ask("Why is TASK-0073 blocked?")
            self.assertIn("not blocked", other.finding)
            self.assertIn("in-progress", other.finding)

    def test_current_work_reports_what_is_in_progress(self):
        with TemporaryDirectory() as tmp:
            answer = self._engine(tmp).ask("What are we currently building?")
            self.assertIn("TASK-0073", answer.finding)
            self.assertTrue(answer.evidence.of(CitationKind.TASK))

    def test_where_documented_returns_prose_and_decisions(self):
        with TemporaryDirectory() as tmp:
            answer = self._engine(tmp).ask("Where is streaming documented?")
            paths = [c.path for c in answer.evidence.citations]
            self.assertTrue(any(p.endswith(".md") for p in paths))

    def test_everything_about_spans_kinds(self):
        with TemporaryDirectory() as tmp:
            answer = self._engine(tmp).ask("Show me everything related to engine.")
            kinds = {c.kind for c in answer.evidence.citations}
            self.assertGreaterEqual(len(kinds), 2)

    def test_an_unmatched_question_says_so_rather_than_inventing(self):
        with TemporaryDirectory() as tmp:
            answer = self._engine(tmp).ask("Where is quantumflux implemented?")
            self.assertFalse(answer.grounded)
            self.assertIn("Nothing", answer.finding)
            self.assertIn("nothing", answer.evidence.summary())

    def test_the_subject_carries_over_when_a_question_omits_one(self):
        with TemporaryDirectory() as tmp:
            engine = self._engine(tmp)
            follow_up = engine.ask("Where is that implemented?", carry="the Engine class")
            self.assertIn("engine", follow_up.subject)
            self.assertTrue(follow_up.evidence.of(CitationKind.SYMBOL))

    def test_a_back_reference_keeps_the_carried_subject(self):
        """
        "Find every place **it** is used" is a follow-up.

        Relying only on a verb stoplist made this fragile: one missing word
        ("used") turned the follow-up into a search for that word. The pronoun is
        the reliable signal, because it is what makes the sentence a follow-up.
        """
        with TemporaryDirectory() as tmp:
            engine = self._engine(tmp)
            answer = engine.ask("Find every place it is used.", carry="the Engine class")
            self.assertIn("engine", answer.subject)
            self.assertTrue(answer.evidence.of(CitationKind.SYMBOL))

    def test_a_follow_up_without_a_pronoun_still_inherits_when_it_has_no_subject(self):
        with TemporaryDirectory() as tmp:
            answer = self._engine(tmp).ask("Where documented?", carry="the Engine class")
            self.assertIn("engine", answer.subject)

    def test_an_explicit_subject_beats_the_carried_one(self):
        """Carry-over must never override what was actually asked."""
        with TemporaryDirectory() as tmp:
            engine = self._engine(tmp)
            answer = engine.ask("Where is Snapshot implemented?", carry="the Engine class")
            self.assertIn("snapshot", answer.subject)

    def test_answers_are_identical_across_runs(self):
        with TemporaryDirectory() as tmp:
            engine = self._engine(tmp)
            first = engine.ask("Where is the Engine implemented?")
            second = engine.ask("Where is the Engine implemented?")
            self.assertEqual(first.finding, second.finding)
            self.assertEqual(first.to_dict()["evidence"], second.to_dict()["evidence"])


class TestEvidence(unittest.TestCase):
    def test_a_citation_with_a_range_displays_it(self):
        citation = Citation(
            kind=CitationKind.SYMBOL,
            reference="Engine.build",
            path="workspace/service.py",
            line=82,
            end_line=140,
        )
        self.assertEqual(citation.display(), "workspace/service.py lines 82-140")

    def test_a_single_line_citation_says_line(self):
        citation = Citation(
            kind=CitationKind.SYMBOL, reference="x", path="a.py", line=5, end_line=5
        )
        self.assertEqual(citation.display(), "a.py line 5")

    def test_duplicate_sources_are_counted_once(self):
        """Listing a file twice overstates how much an answer rests on it."""
        evidence = Evidence()
        for _ in range(3):
            evidence.add(Citation(kind=CitationKind.FILE, reference="a.py", path="a.py"))
        self.assertEqual(len(evidence.citations), 1)

    def test_the_based_on_line_names_the_kinds(self):
        evidence = Evidence()
        evidence.add(Citation(kind=CitationKind.FILE, reference="a.py", path="a.py"))
        evidence.add(Citation(kind=CitationKind.DECISION, reference="ADR-017"))
        summary = evidence.summary()
        self.assertTrue(summary.startswith("Based on:"))
        self.assertIn("decision", summary)
        self.assertIn("file", summary)

    def test_empty_evidence_says_nothing_rather_than_implying_something(self):
        self.assertIn("nothing", Evidence().summary())


if __name__ == "__main__":
    unittest.main()
