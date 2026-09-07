"""
Tests that MondayOS cannot cite itself.

A system that treats its own prior answers as evidence for its next one is not
grounded, it is circular — and the citation trail looks identical either way,
which is exactly what makes it worth excluding structurally rather than
filtering at answer time. Before this boundary existed, 13 of the 103 files
matching "recommendation" in this repository were MondayOS's own transcripts.

The tests come in pairs. Every exclusion has a matching test that legitimate
material of the same kind is still indexed, because the failure mode of an
over-broad filter — a project that quietly stops being searchable — is harder to
notice than the loop it was meant to close.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from intelligence.index import build as build_index
from intelligence.scanner import is_self_generated, walk


def _project(root: Path) -> None:
    """A project containing both real material and MondayOS's own output."""
    # --- legitimate project material ---
    (root / "src").mkdir(parents=True)
    (root / "src" / "billing.py").write_text("class BillingEngine:\n    '''Charges customers.'''\n")
    (root / "docs").mkdir()
    (root / "docs" / "ARCHITECTURE.md").write_text(
        "# Architecture\n\nOur recommendation is to keep billing separate.\n"
    )
    (root / "docs" / "DECISIONS.md").write_text(
        "## ADR-001: Separate billing\n\n**Status:** Accepted\n"
    )
    (root / "tasks" / "active").mkdir(parents=True)
    (root / "tasks" / "active" / "TASK-0001.md").write_text(
        "---\nid: TASK-0001\n---\n\nObjective: ship the billing recommendation.\n"
    )
    (root / "knowledge" / "decisions").mkdir(parents=True)
    (root / "knowledge" / "decisions" / "DEC-0001.md").write_text(
        "---\nid: DEC-0001\nauthored_by: human\n---\n\nA recommendation we authored.\n"
    )

    # --- MondayOS's own output ---
    (root / "workspace" / "conversations" / "demo").mkdir(parents=True)
    (root / "workspace" / "conversations" / "demo" / "CONV-0001.md").write_text(
        "---\nid: CONV-0001\n---\n\n# Strategy\n\n"
        "My recommendation is to add a test suite for integrations. "
        "Evidence strength high, execution risk moderate.\n"
    )
    (root / "knowledge" / "runtime" / "research").mkdir(parents=True)
    (root / "knowledge" / "runtime" / "research" / "RES-0106.md").write_text(
        "---\nid: RES-0106\nauthored_by: human\n---\n\n"
        "Execution result: a generated recommendation.\n"
    )
    (root / "agents" / "active").mkdir(parents=True)
    (root / "agents" / "active" / "AGENT-0001.md").write_text("---\nid: AGENT-0001\n---\n")
    (root / "knowledge" / "GENERATED_INDEX.md").write_text("# Generated index\n")
    (root / "knowledge" / ".sequences.json").write_text('{"DEC": 1}\n')
    (root / "config").mkdir()
    (root / "config" / "projects.json").write_text('{"demo": {}}\n')


class TestSelfGeneratedExclusion(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _project(self.root)
        self.index = build_index("demo", self.root, cache_root=self.root / ".idx")
        self.paths = set(self.index.files)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ------------------------------------------------------------- negative

    def test_a_conversation_cannot_become_evidence(self):
        """
        The load-bearing test. A conversation containing "recommendation" must
        not be retrievable for a later question about the project.
        """
        hits = self.index.files_with("recommendation")
        self.assertTrue(hits, "the term should still match real project material")
        self.assertFalse(
            [h for h in hits if h.startswith("workspace/conversations/")],
            "MondayOS cited its own conversation as project evidence",
        )

    def test_no_conversation_file_is_indexed_at_all(self):
        self.assertFalse([p for p in self.paths if p.startswith("workspace/conversations/")])

    def test_machine_generated_knowledge_is_excluded(self):
        self.assertFalse([p for p in self.paths if p.startswith("knowledge/runtime/")])

    def test_agent_runtime_records_are_excluded(self):
        self.assertFalse([p for p in self.paths if p.startswith("agents/active/")])

    def test_generated_indexes_and_registries_are_excluded(self):
        for path in (
            "knowledge/GENERATED_INDEX.md",
            "knowledge/.sequences.json",
            "config/projects.json",
        ):
            with self.subTest(path=path):
                self.assertNotIn(path, self.paths)

    # ------------------------------------------------------------- positive

    def test_authored_documentation_stays_searchable(self):
        self.assertIn("docs/ARCHITECTURE.md", self.paths)
        self.assertIn("docs/ARCHITECTURE.md", self.index.files_with("recommendation"))

    def test_decisions_stay_searchable(self):
        self.assertIn("docs/DECISIONS.md", self.paths)

    def test_tasks_stay_searchable(self):
        """
        Tasks are human-authored intent. "What did we decide about X" legitimately
        reaches a task objective, so they are deliberately kept.
        """
        self.assertIn("tasks/active/TASK-0001.md", self.paths)
        self.assertIn("tasks/active/TASK-0001.md", self.index.files_with("recommendation"))

    def test_human_authored_knowledge_stays_searchable(self):
        """
        The distinction is provenance, not the word "knowledge". Excluding the
        whole tree would throw away decisions a human wrote.
        """
        self.assertIn("knowledge/decisions/DEC-0001.md", self.paths)
        self.assertIn("knowledge/decisions/DEC-0001.md", self.index.files_with("recommendation"))

    def test_source_code_stays_searchable(self):
        self.assertIn("src/billing.py", self.paths)

    def test_the_workspace_package_source_is_not_confused_with_conversations(self):
        """
        `workspace/conversations/` is output; `workspace/*.py` is the code that
        produces it. Only the first is excluded.
        """
        (self.root / "workspace" / "service.py").write_text("class WorkspaceService:\n    pass\n")
        index = build_index("demo", self.root, cache_root=self.root / ".idx2")
        self.assertIn("workspace/service.py", set(index.files))


class TestPredicate(unittest.TestCase):
    def test_it_matches_on_path_prefix_not_substring(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertTrue(is_self_generated(root / "workspace" / "conversations" / "a.md", root))
            self.assertFalse(is_self_generated(root / "workspace" / "service.py", root))
            # A directory that merely contains the word must not match.
            self.assertFalse(is_self_generated(root / "src" / "conversations.py", root))

    def test_a_path_outside_the_root_is_not_ours_to_judge(self):
        with TemporaryDirectory() as tmp:
            self.assertFalse(is_self_generated(Path("/etc/passwd"), Path(tmp)))

    def test_walk_never_yields_self_generated_files(self):
        with TemporaryDirectory() as tmp:
            # walk() resolves the root, and on macOS /var is a symlink to
            # /private/var — so the comparison base must be resolved too.
            root = Path(tmp).resolve()
            _project(root)
            for path in walk(root):
                relative = path.resolve().relative_to(root).as_posix()
                with self.subTest(path=relative):
                    self.assertFalse(relative.startswith("workspace/conversations/"))
                    self.assertFalse(relative.startswith("knowledge/runtime/"))


if __name__ == "__main__":
    unittest.main()
