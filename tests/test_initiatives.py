"""
Tests for the initiative domain model and declaration.

Two properties carry the weight.

**A declared initiative survives having nothing in it.** That is the entire reason
roadmap reasoning is possible: a capability agreed in planning and not yet started
is invisible to every repository signal, and "we committed to this and have not
begun" is the most valuable sentence this layer produces.

**Progress refuses to invent a denominator.** With tasks, completed-over-total is
a real ratio. Without them there is nothing honest to divide, so an initiative
reports maturity signals instead of a percentage. A missing number with a stated
basis beats a confident wrong one.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from initiatives.declare import declare, load, save
from initiatives.models import (
    Basis,
    Health,
    Initiative,
    Member,
    Milestone,
    Progress,
    Seed,
    slugify,
)
from intelligence.models import NodeKind


def _member(kind: NodeKind, label: str) -> Member:
    return Member(node_id=f"{kind.value}:{label}", kind=kind, label=label, because="test")


class TestSlugs(unittest.TestCase):
    def test_slugs_are_stable_and_filesystem_safe(self):
        self.assertEqual(slugify("AI Workspace"), "ai-workspace")
        self.assertEqual(slugify("  Cue App / RSVP "), "cue-app-rsvp")
        self.assertEqual(slugify("Growth BOT"), "growth-bot")

    def test_punctuation_never_produces_a_trailing_separator(self):
        self.assertEqual(slugify("Billing!!!"), "billing")


class TestProgress(unittest.TestCase):
    def test_tasks_give_a_real_percentage(self):
        progress = Progress(basis=Basis.TASKS, completed=2, total=3)
        self.assertEqual(progress.percent, 67)
        self.assertIn("2/3 tasks", progress.display())

    def test_without_tasks_there_is_no_percentage(self):
        """
        Refusing to invent a denominator.

        Counting files would produce a number that moves when someone splits a
        module in two.
        """
        progress = Progress(basis=Basis.SIGNALS, has_code=True, has_tests=True)
        self.assertIsNone(progress.percent)
        self.assertIn("maturity", progress.display())
        self.assertEqual(progress.signal_count, 2)

    def test_nothing_measured_reports_not_started(self):
        self.assertEqual(Progress().display(), "not started")
        self.assertIsNone(Progress().percent)

    def test_a_zero_denominator_never_divides(self):
        self.assertIsNone(Progress(basis=Basis.TASKS, completed=0, total=0).percent)


class TestInitiativeModel(unittest.TestCase):
    def test_membership_is_queryable_by_kind(self):
        initiative = Initiative(slug="x", name="X")
        initiative.members = [
            _member(NodeKind.FILE, "x/a.py"),
            _member(NodeKind.TASK, "TASK-1 [backlog] a"),
        ]
        self.assertEqual(len(initiative.of_kind(NodeKind.FILE)), 1)
        self.assertEqual(len(initiative.of_kind(NodeKind.TASK)), 1)
        self.assertFalse(initiative.empty)

    def test_every_member_states_why_it_belongs(self):
        """
        A grouping that cannot say why a file belongs to it is a cluster, and a
        cluster is something nobody can correct.
        """
        member = _member(NodeKind.FILE, "x/a.py")
        self.assertTrue(member.because)
        self.assertIn("because", member.to_dict())

    def test_a_declared_initiative_with_no_members_is_still_an_initiative(self):
        initiative = Initiative(slug="billing", name="Billing", declared=True)
        self.assertTrue(initiative.empty)
        self.assertTrue(initiative.declared)

    def test_render_states_the_roadmap_case_explicitly(self):
        initiative = Initiative(slug="billing", name="Billing", declared=True)
        initiative.health_because = "declared on the roadmap"
        self.assertIn("no implementing work found", initiative.render())

    def test_render_carries_health_progress_and_milestone(self):
        initiative = Initiative(slug="x", name="X")
        initiative.health = Health.AT_RISK
        initiative.health_because = "no tests"
        initiative.progress = Progress(basis=Basis.TASKS, completed=1, total=2)
        initiative.next_milestone = Milestone(statement="Do the thing", rationale="because")
        rendered = initiative.render()
        self.assertIn("at-risk", rendered)
        self.assertIn("50%", rendered)
        self.assertIn("Do the thing", rendered)

    def test_health_bands_mark_what_needs_attention(self):
        """
        The bands answer "should I worry", which is the question actually asked.
        """
        self.assertTrue(Health.BLOCKED.needs_attention)
        self.assertTrue(Health.AT_RISK.needs_attention)
        self.assertTrue(Health.STALLED.needs_attention)
        self.assertFalse(Health.HEALTHY.needs_attention)
        self.assertFalse(Health.NOT_STARTED.needs_attention)

    def test_serialisation_round_trips_the_shape_an_api_returns(self):
        initiative = Initiative(slug="x", name="X", declared=True)
        initiative.members = [_member(NodeKind.FILE, "x/a.py")]
        data = initiative.to_dict()
        self.assertEqual(data["slug"], "x")
        self.assertTrue(data["declared"])
        self.assertEqual(data["member_count"], 1)
        self.assertEqual(data["members"][0]["because"], "test")


class TestDeclaration(unittest.TestCase):
    def test_declarations_round_trip(self):
        with TemporaryDirectory() as tmp:
            config = Path(tmp)
            declare(config, "Billing", summary="Take money", keywords=["invoice"])
            seeds = load(config)
            self.assertEqual([s.name for s in seeds], ["Billing"])
            self.assertIn("invoice", seeds[0].keywords)
            # The name is always a keyword without anyone repeating it.
            self.assertIn("billing", seeds[0].keywords)
            self.assertTrue(seeds[0].declared)

    def test_a_declaration_outranks_every_derived_signal(self):
        """A human naming a capability is the strongest statement available."""
        with TemporaryDirectory() as tmp:
            declare(Path(tmp), "Billing")
            self.assertGreater(load(Path(tmp))[0].authority, Seed(name="x", because="y").authority)

    def test_redeclaring_replaces_rather_than_duplicates(self):
        with TemporaryDirectory() as tmp:
            config = Path(tmp)
            declare(config, "Billing", summary="first")
            declare(config, "billing", summary="second")
            self.assertEqual(len(load(config)), 1)

    def test_a_malformed_file_does_not_break_reasoning(self):
        """A typo in one initiative must not take down the other nine."""
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "initiatives.json").write_text("{ not json")
            self.assertEqual(load(Path(tmp)), [])

    def test_a_malformed_entry_is_skipped_not_fatal(self):
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "initiatives.json").write_text(
                json.dumps({"initiatives": [{"nope": 1}, "junk", {"name": "Billing"}]})
            )
            self.assertEqual([s.name for s in load(Path(tmp))], ["Billing"])

    def test_a_missing_file_is_the_normal_case(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(load(Path(tmp)), [])

    def test_saving_does_not_grow_the_file_on_every_round_trip(self):
        with TemporaryDirectory() as tmp:
            config = Path(tmp)
            declare(config, "Billing", keywords=["invoice"])
            first = (config / "initiatives.json").read_text()
            save(config, load(config))
            self.assertEqual((config / "initiatives.json").read_text(), first)


if __name__ == "__main__":
    unittest.main()
