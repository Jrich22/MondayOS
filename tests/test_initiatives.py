"""
Tests for initiative intelligence.

The load-bearing property is **precision of membership**. A missing member is a
gap a user can point out; a wrong member is Monday confidently misdescribing
someone's product, and it poisons every number computed from it. Most of what
follows checks that things which are not capabilities do not become capabilities.

The second property is that a **declared initiative survives having nothing in
it**. That case is the entire reason roadmap reasoning is possible: a capability
agreed in planning and not yet started is invisible to every repository signal,
and reporting "we committed to this and have not begun" is the most valuable
sentence this layer produces.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import initiatives as initiative_intelligence
from initiatives.declare import declare, load
from initiatives.discover import _doc_name, discover, seeds_from_tasks
from initiatives.drift import DriftKind, detect, detect_all
from initiatives.health import assess, link
from initiatives.models import Basis, Health, Initiative, Member, Seed, slugify
from intelligence.graph import build as build_graph
from intelligence.index import build as build_index
from intelligence.models import NodeKind


def _member(kind: NodeKind, label: str, node_id: str = "") -> Member:
    return Member(
        node_id=node_id or f"{kind.value}:{label}", kind=kind, label=label, because="test"
    )


class TestNaming(unittest.TestCase):
    def test_acronyms_survive(self):
        """
        "Ai Workspace" is not what anyone calls it.

        Regression: title-casing every token mangled the derived name in the exact
        place a user first sees it.
        """
        self.assertEqual(_doc_name("docs/AI_WORKSPACE.md"), "AI Workspace")
        self.assertEqual(_doc_name("docs/GROWTH_BOT.md"), "Growth BOT")
        self.assertEqual(_doc_name("docs/MEMORY_SYSTEM.md"), "Memory System")

    def test_slugs_are_stable(self):
        self.assertEqual(slugify("AI Workspace"), "ai-workspace")
        self.assertEqual(slugify("  Cue App / RSVP "), "cue-app-rsvp")


class TestTaskPrefixDiscovery(unittest.TestCase):
    def test_a_repeated_prefix_names_a_capability(self):
        tasks = [
            {"id": "T-1", "title": "Cue App: Roll Call"},
            {"id": "T-2", "title": "Cue App: Multi-Organization"},
        ]
        seeds = seeds_from_tasks(tasks)
        self.assertEqual([s.name for s in seeds], ["Cue App"])

    def test_a_single_use_prefix_is_a_phrasing_habit(self):
        """One task titled "Fix: something" is not a capability."""
        self.assertEqual(seeds_from_tasks([{"id": "T-1", "title": "Fix: a bug"}]), [])

    def test_a_sentence_containing_a_colon_is_not_a_prefix(self):
        tasks = [
            {"id": "T-1", "title": "Investigate why the thing broke: it was DNS"},
            {"id": "T-2", "title": "Investigate why the other thing broke: also DNS"},
        ]
        self.assertEqual(seeds_from_tasks(tasks), [])


class TestDiscoveryPrecision(unittest.TestCase):
    """A wrong grouping is worse than a missing one."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "billing").mkdir()
        for i in range(6):
            (self.root / "billing" / f"mod{i}.py").write_text(f"class B{i}:\n    pass\n")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_billing.py").write_text("def test_x():\n    pass\n")
        (self.root / "docs").mkdir()
        (self.root / "docs" / "BILLING.md").write_text("# Billing\n")
        (self.root / "docs" / "ENGINEERING_STANDARDS.md").write_text("# Standards\n")
        (self.root / "tasks").mkdir(parents=True)
        (self.root / "tasks" / "TASK-0051.md").write_text("# A task\n")
        (self.root / "small").mkdir()
        (self.root / "small" / "one.py").write_text("x = 1\n")
        self.index = build_index("demo", self.root, cache_root=self.root / ".idx")
        self.graph = build_graph(self.index, tasks=[], knowledge=[])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _names(self, tasks=None) -> list[str]:
        return [i.name for i in discover(self.index, self.graph, tasks=tasks or [])]

    def test_a_package_with_a_document_becomes_one_initiative(self):
        """`billing/` and `BILLING.md` are one capability seen twice."""
        names = self._names()
        self.assertIn("Billing", names)
        self.assertEqual(sum(1 for n in names if n.lower() == "billing"), 1)

    def test_process_documents_are_not_capabilities(self):
        self.assertNotIn("Engineering Standards", self._names())

    def test_artefact_records_are_not_capabilities(self):
        """
        Regression: every file under tasks/ is documentation by file kind, so
        each one became an initiative named after a task id.
        """
        self.assertFalse([n for n in self._names() if n.lower().startswith("task 00")])

    def test_a_small_uncorroborated_package_is_a_module(self):
        """Without this every directory is an initiative and the roster is noise."""
        self.assertNotIn("small", self._names())

    def test_central_tests_are_attributed_to_what_they_test(self):
        """
        Regression: tests living in tests/ were invisible, so every package with
        external tests reported as untested — the exact signal health leans on.
        """
        billing = next(i for i in discover(self.index, self.graph) if i.name == "Billing")
        tests = billing.of_kind(NodeKind.TEST)
        self.assertTrue(any("test_billing" in m.label for m in tests))

    def test_every_member_states_why_it_belongs(self):
        """A grouping nobody can correct is a grouping nobody can trust."""
        for initiative in discover(self.index, self.graph):
            for member in initiative.members:
                self.assertTrue(member.because, f"{initiative.name}/{member.label}")

    def test_discovery_is_deterministic(self):
        self.assertEqual(self._names(), self._names())


class TestDeclaration(unittest.TestCase):
    def test_a_declared_initiative_survives_having_no_work(self):
        """
        The whole reason roadmap reasoning is possible.

        Discovery sees only what exists. A capability agreed in planning and not
        started is invisible to every repository signal, and saying so is the most
        useful thing this layer does.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "readme.md").write_text("# hi\n")
            index = build_index("demo", root, cache_root=root / ".idx")
            graph = build_graph(index, tasks=[], knowledge=[])
            seed = Seed(name="Billing", because="declared", declared=True, keywords=["billing"])
            found = discover(index, graph, declared=[seed])
            billing = next(i for i in found if i.name == "Billing")
            self.assertTrue(billing.empty)
            self.assertTrue(billing.declared)

    def test_a_declared_initiative_reports_not_started_with_a_first_step(self):
        initiative = assess(Initiative(slug="billing", name="Billing", declared=True))
        self.assertIs(initiative.health, Health.NOT_STARTED)
        self.assertIsNotNone(initiative.next_milestone)
        self.assertIn("first increment", initiative.next_milestone.statement.lower())

    def test_declarations_round_trip(self):
        with TemporaryDirectory() as tmp:
            config = Path(tmp)
            declare(config, "Billing", summary="Take money", keywords=["invoice"])
            seeds = load(config)
            self.assertEqual([s.name for s in seeds], ["Billing"])
            self.assertIn("invoice", seeds[0].keywords)
            # The name is always a keyword without anyone repeating it.
            self.assertIn("billing", seeds[0].keywords)

    def test_a_malformed_declaration_file_does_not_break_reasoning(self):
        """A typo in one initiative must not take down the other nine."""
        with TemporaryDirectory() as tmp:
            config = Path(tmp)
            (config / "initiatives.json").write_text("{ not json")
            self.assertEqual(load(config), [])

    def test_a_malformed_entry_is_skipped_not_fatal(self):
        with TemporaryDirectory() as tmp:
            config = Path(tmp)
            (config / "initiatives.json").write_text(
                json.dumps({"initiatives": [{"nope": 1}, {"name": "Billing"}]})
            )
            self.assertEqual([s.name for s in load(config)], ["Billing"])

    def test_a_missing_file_is_the_normal_case(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(load(Path(tmp)), [])


class TestProgress(unittest.TestCase):
    def test_tasks_give_a_real_percentage(self):
        initiative = Initiative(slug="x", name="X")
        initiative.members = [
            _member(NodeKind.TASK, "TASK-1 [completed] a"),
            _member(NodeKind.TASK, "TASK-2 [completed] b"),
            _member(NodeKind.TASK, "TASK-3 [backlog] c"),
        ]
        assess(initiative)
        self.assertIs(initiative.progress.basis, Basis.TASKS)
        self.assertEqual(initiative.progress.percent, 67)

    def test_without_tasks_there_is_no_percentage(self):
        """
        Refusing to invent a denominator.

        Counting files would produce a number that moves when someone splits a
        module in two. A missing percentage with a stated basis beats a confident
        wrong one.
        """
        initiative = Initiative(slug="x", name="X")
        initiative.members = [_member(NodeKind.FILE, "x/a.py")]
        assess(initiative)
        self.assertIs(initiative.progress.basis, Basis.SIGNALS)
        self.assertIsNone(initiative.progress.percent)
        self.assertIn("maturity", initiative.progress.display())


class TestHealth(unittest.TestCase):
    def _with(self, *members: Member) -> Initiative:
        initiative = Initiative(slug="x", name="X")
        initiative.members = list(members)
        return assess(initiative)

    def test_a_blocker_outranks_progress(self):
        """
        80% with a blocker needs attention more than 20% moving steadily, and a
        status sorted by percentage would bury it.
        """
        initiative = self._with(
            _member(NodeKind.TASK, "TASK-1 [completed] a"),
            _member(NodeKind.TASK, "TASK-2 [completed] b"),
            _member(NodeKind.TASK, "TASK-3 [completed] c"),
            _member(NodeKind.TASK, "TASK-4 [completed] d"),
            _member(NodeKind.TASK, "TASK-5 [blocked] e"),
        )
        self.assertIs(initiative.health, Health.BLOCKED)
        self.assertTrue(initiative.health.needs_attention)

    def test_code_without_tests_is_at_risk(self):
        initiative = self._with(_member(NodeKind.FILE, "x/a.py"))
        self.assertIs(initiative.health, Health.AT_RISK)
        self.assertIn("no tests", initiative.health_because)

    def test_code_with_tests_is_healthy(self):
        initiative = self._with(
            _member(NodeKind.FILE, "x/a.py"), _member(NodeKind.TEST, "tests/test_x.py")
        )
        self.assertIs(initiative.health, Health.HEALTHY)

    def test_in_progress_with_no_commits_is_stalled(self):
        initiative = self._with(
            _member(NodeKind.FILE, "x/a.py"),
            _member(NodeKind.TEST, "tests/test_x.py"),
            _member(NodeKind.TASK, "TASK-1 [in-progress] a"),
        )
        self.assertIs(initiative.health, Health.STALLED)

    def test_every_health_verdict_explains_itself(self):
        for members in (
            (_member(NodeKind.FILE, "x/a.py"),),
            (_member(NodeKind.TASK, "TASK-1 [blocked] a"),),
            (),
        ):
            self.assertTrue(self._with(*members).health_because)

    def test_the_next_milestone_leads_on_the_blocker(self):
        initiative = self._with(
            _member(NodeKind.FILE, "x/a.py"), _member(NodeKind.TASK, "TASK-1 [blocked] a")
        )
        self.assertIn("blocker", initiative.next_milestone.statement.lower())


class TestDependencies(unittest.TestCase):
    def test_a_shared_artefact_is_a_dependency(self):
        shared = _member(NodeKind.FILE, "common/util.py")
        one = Initiative(slug="one", name="One", members=[shared])
        two = Initiative(slug="two", name="Two", members=[shared])
        link([one, two])
        self.assertEqual([d.on for d in one.dependencies], ["two"])
        self.assertIn("shares", one.dependencies[0].because)

    def test_unrelated_initiatives_have_no_dependency(self):
        """
        Nothing is inferred from similarity: an invented constraint would be
        acted on as though the project had agreed to it.
        """
        one = Initiative(slug="one", name="One", members=[_member(NodeKind.FILE, "a/x.py")])
        two = Initiative(slug="two", name="Two", members=[_member(NodeKind.FILE, "b/y.py")])
        link([one, two])
        self.assertEqual(one.dependencies, [])


class TestBuildEndToEnd(unittest.TestCase):
    def test_build_discovers_assesses_and_links(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "billing").mkdir()
            for i in range(6):
                (root / "billing" / f"m{i}.py").write_text(f"class B{i}:\n    pass\n")
            (root / "docs").mkdir()
            (root / "docs" / "BILLING.md").write_text("# Billing\n")
            index = build_index("demo", root, cache_root=root / ".idx")
            graph = build_graph(index, tasks=[], knowledge=[])
            found = initiative_intelligence.build(
                index, graph, tasks=[], config_dir=root / "config"
            )
            self.assertTrue(found)
            billing = next(i for i in found if i.name == "Billing")
            self.assertIsNot(billing.health, Health.NOT_STARTED)
            self.assertTrue(billing.render())
            self.assertIn("health", billing.render())


class TestRecordRealityDrift(unittest.TestCase):
    """
    Where the code and the project record disagree.

    The case that prompted this: Growth BOT reported 0% against 8 tasks while its
    code was present, tested, and named in nineteen merged commits. The percentage
    was faithful to the task store and wrong about the world.

    The second property tested here matters as much as the first: **nothing is
    repaired**. Closing a task because commits exist would be MondayOS overwriting
    a human's record on a heuristic, and the first wrong guess would erase the fact
    that somebody deliberately reopened something.
    """

    def _initiative(self, *members: Member, name: str = "Growth BOT") -> Initiative:
        initiative = Initiative(slug=slugify(name), name=name)
        initiative.members = list(members)
        return assess(initiative)

    def _shipped_but_backlog(self) -> Initiative:
        return self._initiative(
            _member(NodeKind.FILE, "growth/a.py"),
            _member(NodeKind.TEST, "tests/test_growth.py"),
            _member(NodeKind.COMMIT, "abc1234 Growth Bot: publishing connector"),
            _member(NodeKind.PULL_REQUEST, "PR #37 Growth Bot"),
            _member(NodeKind.TASK, "TASK-0065 [backlog] Growth Increment 5"),
            _member(NodeKind.TASK, "TASK-0066 [backlog] Growth Increment 6"),
        )

    def test_shipped_code_with_a_backlog_record_is_drift(self):
        drifts = detect(self._shipped_but_backlog())
        self.assertEqual([d.kind for d in drifts], [DriftKind.SHIPPED_BUT_BACKLOG])
        self.assertIn("shipped", drifts[0].implementation)
        self.assertIn("backlog", drifts[0].record)

    def test_drift_states_both_sides_without_picking_a_winner(self):
        """
        MondayOS does not know which source is right. Presenting a verdict would
        be the automatic repair this deliberately does not perform.
        """
        drift = detect(self._shipped_but_backlog())[0]
        self.assertTrue(drift.implementation)
        self.assertTrue(drift.record)
        self.assertTrue(drift.consequence)

    def test_nothing_is_repaired(self):
        initiative = self._shipped_but_backlog()
        before = [m.label for m in initiative.of_kind(NodeKind.TASK)]
        drifts = detect(initiative)
        after = [m.label for m in initiative.of_kind(NodeKind.TASK)]
        self.assertEqual(before, after)
        self.assertTrue(all(d.to_dict()["repaired"] is False for d in drifts))

    def test_completed_tasks_with_no_implementation_is_the_other_direction(self):
        """Opposite correction, so it must not collapse into one flag."""
        initiative = self._initiative(
            _member(NodeKind.TASK, "TASK-1 [completed] a"),
            _member(NodeKind.TASK, "TASK-2 [completed] b"),
        )
        drifts = detect(initiative)
        self.assertEqual([d.kind for d in drifts], [DriftKind.COMPLETE_BUT_ABSENT])

    def test_a_blocked_task_with_landing_commits_is_drift(self):
        initiative = self._initiative(
            _member(NodeKind.FILE, "x/a.py"),
            _member(NodeKind.COMMIT, "aaa Work on x"),
            _member(NodeKind.COMMIT, "bbb More work on x"),
            _member(NodeKind.TASK, "TASK-1 [blocked] a"),
        )
        self.assertIn(DriftKind.BLOCKED_BUT_MOVING, [d.kind for d in detect(initiative)])

    def test_agreement_produces_no_drift(self):
        """The overwhelmingly common case must stay silent."""
        initiative = self._initiative(
            _member(NodeKind.FILE, "x/a.py"),
            _member(NodeKind.TEST, "tests/test_x.py"),
            _member(NodeKind.COMMIT, "aaa did the thing"),
            _member(NodeKind.COMMIT, "bbb did more"),
            _member(NodeKind.TASK, "TASK-1 [completed] a"),
        )
        self.assertEqual(detect(initiative), [])

    def test_a_thin_record_is_not_drift(self):
        """
        Under-recorded is a different observation from contradicted, and firing
        on it would bury the real disagreements in noise.
        """
        initiative = self._initiative(
            _member(NodeKind.FILE, "x/a.py"), _member(NodeKind.TEST, "tests/test_x.py")
        )
        self.assertEqual(detect(initiative), [])

    def test_one_passing_mention_is_not_a_shipped_capability(self):
        initiative = self._initiative(
            _member(NodeKind.FILE, "x/a.py"),
            _member(NodeKind.TEST, "tests/test_x.py"),
            _member(NodeKind.COMMIT, "aaa mentioned it once"),
            _member(NodeKind.TASK, "TASK-1 [backlog] a"),
        )
        self.assertEqual(detect(initiative), [])

    def test_detect_all_attaches_drift_and_ranks_stale_records_first(self):
        shipped = self._shipped_but_backlog()
        absent = self._initiative(_member(NodeKind.TASK, "TASK-9 [completed] x"), name="Ghost")
        drifts = detect_all([absent, shipped])
        self.assertIs(drifts[0].kind, DriftKind.SHIPPED_BUT_BACKLOG)
        self.assertTrue(shipped.drift)

    def test_drift_reaches_the_rendered_initiative(self):
        initiative = self._shipped_but_backlog()
        detect_all([initiative])
        self.assertIn("record/reality drift", initiative.render())


if __name__ == "__main__":
    unittest.main()


class TestEvidenceModel(unittest.TestCase):
    """
    What may create a capability, and what may only describe one.

    Discovery used to let the highest-authority *name* win a merge, which meant a
    document outranked the package it was written about. `safety/` -- sixteen
    files of real code -- was reported as "Safety Implementation Plan", and
    `research/` as "Research Roadmap". Both are plans about work, presented as
    the work itself.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, *paths: str) -> None:
        for path in paths:
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# generated\nvalue = 1\n" if path.endswith(".py") else "# doc\n")

    def _discover(self, project: str = "demo") -> list[Initiative]:
        index = build_index(project, self.root, cache_root=self.root / ".idx")
        return discover(index, build_graph(index, tasks=[], knowledge=[]), [])

    def _names(self, project: str = "demo") -> set[str]:
        return {i.name for i in self._discover(project)}

    def test_a_document_alone_creates_nothing(self):
        self._write("safety/rails.py", "safety/limits.py", "safety/caps.py", "safety/halt.py")
        self._write("docs/ROADMAP.md", "docs/DELIVERY_PLAN.md")
        names = self._names()
        self.assertIn("safety", names)
        self.assertNotIn("Roadmap", names)
        self.assertNotIn("Delivery Plan", names)

    def test_a_plan_document_does_not_rename_the_work_it_plans(self):
        """The exact WeatherBot regression: `safety/` is not a plan."""
        self._write("safety/rails.py", "safety/limits.py", "safety/caps.py", "safety/halt.py")
        self._write("docs/SAFETY_IMPLEMENTATION_PLAN.md")
        self.assertIn("safety", self._names())

    def test_a_scope_word_does_not_rename_the_work_it_scopes(self):
        self._write(
            "workflows/run.py", "workflows/step.py", "workflows/gate.py", "workflows/state.py"
        )
        self._write("docs/TEAM_WORKFLOW.md")
        self.assertIn("workflows", self._names())

    def test_a_document_may_still_improve_a_name(self):
        """`reasoning/` plus REASONING_ENGINE.md is what people call it."""
        self._write(
            "reasoning/engine.py", "reasoning/claim.py", "reasoning/score.py", "reasoning/rank.py"
        )
        self._write("docs/REASONING_ENGINE.md")
        self.assertIn("Reasoning Engine", self._names())

    def test_two_added_qualifiers_are_a_document_title_not_a_name(self):
        self._write("safety/rails.py", "safety/limits.py", "safety/caps.py", "safety/halt.py")
        self._write("docs/TRADING_SAFETY_RAILS.md")
        self.assertIn("safety", self._names())

    def test_the_document_that_supplies_the_name_is_the_one_cited(self):
        """
        Provenance must be inspectable and exact.

        Two documents describe `knowledge/`; only one names it, and citing the
        other would leave the name unverifiable at the moment someone checks.
        """
        self._write(
            "knowledge/store.py", "knowledge/query.py", "knowledge/index.py", "knowledge/tag.py"
        )
        self._write("docs/KNOWLEDGE_RUNTIME_POLICY.md", "docs/KNOWLEDGE_SYSTEM.md")
        found = {i.name: i for i in self._discover()}
        self.assertIn("Knowledge System", found)
        because = found["Knowledge System"].because
        self.assertIn("named by docs/KNOWLEDGE_SYSTEM.md", because)
        self.assertNotIn("named by docs/KNOWLEDGE_RUNTIME_POLICY.md", because)

    def test_a_modest_directory_is_a_capability(self):
        """
        Six files was below the old threshold of ten.

        That number was measured on this repository's packages and silently
        deleted WeatherBot's `archive/` and `ops/`.
        """
        self._write(*[f"archive/mod{i}.py" for i in range(6)])
        self.assertIn("archive", self._names())

    def test_a_container_never_becomes_a_capability(self):
        for container in ("src", "source", "app"):
            with self.subTest(container=container):
                self._tmp.cleanup()
                self._tmp = TemporaryDirectory()
                self.root = Path(self._tmp.name)
                self._write(
                    *[f"{container}/billing/mod{i}.py" for i in range(5)],
                    *[f"{container}/checkin/mod{i}.py" for i in range(5)],
                )
                names = self._names()
                self.assertNotIn(container, names)
                self.assertIn("billing", names)

    def test_a_transport_is_folded_into_the_capability_it_serves(self):
        self._write(*[f"dashboard/mod{i}.py" for i in range(6)])
        self._write(*[f"dashboard_api/route{i}.py" for i in range(5)])
        names = self._names()
        self.assertIn("dashboard", names)
        self.assertNotIn("dashboard_api", names)

    def test_the_projects_own_namespace_is_not_one_of_its_capabilities(self):
        self._write(*[f"monday/mod{i}.py" for i in range(6)])
        self._write(*[f"growth/mod{i}.py" for i in range(6)])
        names = self._names(project="mondayos")
        self.assertNotIn("monday", names)
        self.assertIn("growth", names)

    def test_a_nested_project_contributes_no_capabilities(self):
        self._write(*[f"growth/mod{i}.py" for i in range(6)])
        self._write("projects/other-app/package.json")
        self._write(*[f"projects/other-app/src/checkin/mod{i}.ts" for i in range(6)])
        names = self._names()
        self.assertIn("growth", names)
        self.assertNotIn("checkin", names)
        self.assertNotIn("projects", names)

    def test_a_declared_initiative_survives_with_no_code_at_all(self):
        """Unchanged by S3, and the reason the evidence gate exempts declarations."""
        self._write("growth/mod0.py")
        index = build_index("demo", self.root, cache_root=self.root / ".idx")
        seed = Seed(name="Planned Thing", because="declared", declared=True)
        found = discover(index, build_graph(index, tasks=[], knowledge=[]), [], [seed])
        self.assertIn("Planned Thing", {i.name for i in found})
