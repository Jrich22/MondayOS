"""
Discovery across five projects, asserted as invariants rather than as answers.

The temptation after fixing overfitting is to write down what discovery now
returns for each corpus and assert that forever. That would move the overfitting
rather than remove it: the next honest improvement would fail a test for being an
improvement, and the pressure would be to keep the old answer.

So nothing here asserts a count or an exact set. What it asserts is the
properties that made the old behaviour wrong -- a layout convention reported as a
capability, a plan document reported as the work it plans, a real capability
deleted for being small -- plus the handful of capabilities each project so
plainly has that losing one would mean something broke.

Four of the corpora are the developer's own repositories and are skipped cleanly
when absent. The fifth is built from scratch in a temporary directory, so at
least one case always runs and it is the one no rule could have been tuned to.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from benchmark.corpus import discover as discover_corpora
from initiatives.discover import discover
from initiatives.models import Initiative
from intelligence.graph import build as build_graph
from intelligence.index import build as build_index
from intelligence.models import NodeKind
from tests.synthetic_corpus import CAPABILITIES, LAYERS, PLAN_DOCUMENTS, build

REPO = Path(__file__).resolve().parent.parent

# Words that mark a name as the title of a plan rather than a capability. If one
# of these reaches the roster, a document has been mistaken for the work.
PLAN_WORDS = ("roadmap", "plan", "vision", "implementation", "proposal", "checklist")

# An identifier-shaped filename: TASK-0020, DEC-0001, RES-0138. The same shape
# discovery uses to tell a filed record from a capability document.
ARTEFACT = re.compile(r"^[a-z]{2,12}[-_ ]?\d{2,}$", re.I)

# Layout conventions. None of these is a capability in any project.
CONTAINERS = ("src", "source", "app", "lib", "packages", "components", "pages")


def initiatives_for(project: str, root: Path, cache: Path) -> list[Initiative]:
    index = build_index(project, root, cache_root=cache)
    return discover(index, build_graph(index, tasks=[], knowledge=[]), [])


class DiscoveryInvariants:
    """The properties every project's roster must have, whatever is in it."""

    def assert_no_container(self, names: set[str], where: str) -> None:
        for container in CONTAINERS:
            self.assertNotIn(container, {n.lower() for n in names}, where)  # type: ignore[attr-defined]

    def assert_no_plan_titles(self, names: set[str], where: str) -> None:
        # Whole words only. Cue App's `planning` is event planning -- a real
        # capability that happens to contain "plan", and a substring test would
        # report it as a document title.
        for name in names:
            words = {w for w in re.split(r"[^a-z]+", name.lower()) if w}
            offending = words & set(PLAN_WORDS)
            self.assertFalse(  # type: ignore[attr-defined]
                offending, f"{where}: '{name}' reads as a document title"
            )

    def assert_every_initiative_has_work(self, found: list[Initiative], where: str) -> None:
        """
        A capability nobody is building is a document.

        Checked through members rather than through the seed that produced them,
        because members are what a user is shown and what they would dispute.
        """
        for initiative in found:
            if initiative.declared:
                continue
            work = [
                m
                for m in initiative.members
                if m.kind in (NodeKind.TASK, NodeKind.DECISION, NodeKind.TEST)
                or (m.kind is NodeKind.FILE and not m.label.lower().endswith(".md"))
            ]
            self.assertTrue(work, f"{where}: '{initiative.name}' rests on documents alone")  # type: ignore[attr-defined]


class TestSyntheticCorpus(unittest.TestCase, DiscoveryInvariants):
    """
    The corpus no rule could have been tuned to.

    A Python service under `source/`, which is the load-bearing detail: if the
    container is recognised here, it was recognised by shape.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = TemporaryDirectory()
        root = build(Path(cls._tmp.name) / "service")
        cls.found = initiatives_for("synthetic", root, Path(cls._tmp.name) / "cache")
        cls.names = {i.name for i in cls.found}

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_every_planted_capability_is_recovered(self):
        for capability in CAPABILITIES:
            self.assertIn(capability, {n.lower() for n in self.names}, capability)

    def test_the_container_is_not_a_capability(self):
        self.assert_no_container(self.names, "synthetic")

    def test_architectural_layers_are_not_capabilities(self):
        for layer in LAYERS:
            self.assertNotIn(layer, {n.lower() for n in self.names}, layer)

    def test_plan_documents_are_not_capabilities(self):
        for document in PLAN_DOCUMENTS:
            self.assertNotIn(document, self.names)
        self.assert_no_plan_titles(self.names, "synthetic")

    def test_every_initiative_rests_on_work(self):
        self.assert_every_initiative_has_work(self.found, "synthetic")

    def test_the_set_is_exactly_the_three_planted_capabilities(self):
        """
        Pinned exactly, unlike the real corpora.

        This one is safe to pin because the fixture is the specification: it
        plants three capabilities, so anything else appearing is a defect rather
        than a discovery.
        """
        self.assertEqual({n.lower() for n in self.names}, set(CAPABILITIES))

    def test_nothing_is_named_after_a_nested_directory(self):
        """Bounded recursion: depth must not turn leaf directories into capabilities."""
        for leaf in ("deep", "nested", "deeper", "thing", "other"):
            self.assertNotIn(leaf, {n.lower() for n in self.names}, leaf)


class TestRealCorpora(unittest.TestCase, DiscoveryInvariants):
    """
    The four registered projects, skipped cleanly when they are not on this
    machine.

    Assertions are minimums and prohibitions. "At least three capabilities" says
    the layout is being read; "exactly twelve" would only say that today's answer
    was today's answer.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = TemporaryDirectory()
        cls.corpora = {c.slug: c for c in discover_corpora(REPO / "config")}

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _for(self, slug: str) -> list[Initiative]:
        corpus = self.corpora.get(slug)
        if corpus is None or not corpus.available or corpus.root is None:
            self.skipTest(f"{slug} is not available on this machine")
        return initiatives_for(slug, corpus.root, Path(self._tmp.name) / slug)

    def test_every_corpus_holds_the_invariants(self):
        for slug in sorted(self.corpora):
            corpus = self.corpora[slug]
            if not corpus.available or corpus.root is None:
                continue
            with self.subTest(corpus=slug):
                found = initiatives_for(slug, corpus.root, Path(self._tmp.name) / f"inv-{slug}")
                names = {i.name for i in found}
                self.assert_no_container(names, slug)
                self.assert_no_plan_titles(names, slug)
                self.assert_every_initiative_has_work(found, slug)

    def test_mondayos_keeps_its_established_capabilities(self):
        """
        The ones whose absence would mean something broke.

        Deliberately not the whole roster: MondayOS gains capabilities as it
        grows, and a test that pinned the full set would fail on every new
        package.
        """
        names = {i.name for i in self._for("mondayos")}
        for capability in (
            "AI Workspace",
            "Growth BOT",
            "Reasoning Engine",
            "Memory System",
            "Workflows",
            "dashboard",
        ):
            self.assertIn(capability, names, capability)

    def test_cue_app_has_capabilities_rather_than_one_container(self):
        found = self._for("cue-app")
        self.assertGreaterEqual(len(found), 3, [i.name for i in found])

    def test_sourcingbot_has_capabilities_rather_than_one_container(self):
        found = self._for("sourcingbot")
        self.assertGreaterEqual(len(found), 3, [i.name for i in found])

    def test_no_record_becomes_a_member_on_any_corpus(self):
        """
        A filed record is evidence about one unit of work, not implementation.

        `Task System` once reported 89 members, 79 of them the tasks it tracks.
        Member counts order the roster and feed health and progress, so a store
        beside the code made its manager the biggest capability in the project.
        """
        for slug in sorted(self.corpora):
            corpus = self.corpora[slug]
            if not corpus.available or corpus.root is None:
                continue
            with self.subTest(corpus=slug):
                found = initiatives_for(slug, corpus.root, Path(self._tmp.name) / f"rec-{slug}")
                for initiative in found:
                    for member in initiative.members:
                        if not member.node_id.startswith("file:"):
                            continue
                        stem = member.label.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                        self.assertIsNone(
                            ARTEFACT.match(stem.replace("_", "-")),
                            f"{slug}: '{initiative.name}' counts the record {member.label}",
                        )

    def test_no_member_resolves_outside_its_project_root(self):
        for slug in sorted(self.corpora):
            corpus = self.corpora[slug]
            if not corpus.available or corpus.root is None:
                continue
            with self.subTest(corpus=slug):
                found = initiatives_for(slug, corpus.root, Path(self._tmp.name) / f"out-{slug}")
                for initiative in found:
                    for member in initiative.members:
                        if member.node_id.startswith("file:"):
                            self.assertFalse(member.label.startswith("/"), member.label)
                            self.assertNotIn("..", member.label)

    def test_weatherbot_reports_code_capabilities_not_document_titles(self):
        """
        `safety/` is sixteen files of real code that was reported as "Safety
        Implementation Plan", and `archive/` and `ops/` were deleted for having
        six files each.
        """
        names = {i.name.lower() for i in self._for("weatherbot")}
        self.assertIn("safety", names)
        self.assertTrue({"archive", "ops"} & names, names)


if __name__ == "__main__":
    unittest.main()
