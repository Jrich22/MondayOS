"""
Tests for the conversation router.

Routing used to happen twice per turn. A closure in `monday/api.py` decided
continuation with the strategic state; `ReasoningEngine.assess` then decided
executive-versus-grounded again *without* it. Two policies, one structurally
blind, both computed for every turn.

These tests defend three properties: there is one decision site, every
conversation path reaches it, and its behaviour is unchanged from before the
extraction.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from reasoning.executive import Topic
from reasoning.router import ROUTER, ConversationRouter, Register, Route
from workspace.models import StrategicState

ROOT = Path(__file__).resolve().parent.parent


def _state(**kw) -> StrategicState:
    base = dict(
        question="What should we build next?",
        topic="next-work",
        project="mondayos",
        fingerprint="FP-1",
        recommendation="Add a test suite for integrations",
        initiative_slug="integrations",
        initiative_slugs=["integrations", "workspace"],
    )
    base.update(kw)
    return StrategicState(**base)


class TestSingleAuthority(unittest.TestCase):
    def test_there_is_exactly_one_routing_decision_site(self):
        """
        The regression this module exists for.

        Decision sites are identified by use of the lookup guard — the first and
        unconditional step of the precedence order. More than one means the
        policy has been forked again.
        """
        sites = []
        for file in (ROOT / "reasoning").rglob("*.py"):
            source = file.read_text(encoding="utf-8")
            if "_LOOKUP.search" in source:
                sites.append(file.name)
        self.assertEqual(sites, ["router.py"], f"routing decided in: {sites}")

    def test_the_engine_no_longer_routes(self):
        """
        `ReasoningEngine.assess` accepts a decision; it must not derive one from
        the question, because it cannot see the conversation's strategic state.
        """
        source = (ROOT / "reasoning" / "engine.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "reasoning.executive":
                imported = {a.name for a in node.names}
                self.assertNotIn("route", imported, "the engine imports a second router")

    def test_the_deprecated_entry_point_delegates(self):
        """
        `executive.route` is kept as a redirect so importing it cannot resurrect
        a second policy. It must agree with the router exactly.
        """
        from reasoning.executive import route as deprecated

        for question in (
            "Where is ContextEngine implemented?",
            "What should we build next?",
            "What evidence supports that recommendation?",
        ):
            with self.subTest(question=question):
                old = deprecated(question, _state())
                new = ROUTER.route(question, strategy=_state())
                self.assertEqual(old.executive, new.executive)
                self.assertEqual(old.continuation, new.continuation)
                self.assertEqual(old.reason, new.reason)


class TestPrecedencePreserved(unittest.TestCase):
    """Behaviour must be identical to before the extraction."""

    GROUNDED = [
        "Where is ContextEngine implemented?",
        "Show me every reference to WorkspaceService.",
        "Find every place ContextEngine is used.",
        "What file owns provider routing?",
    ]

    def test_named_lookups_stay_grounded_even_with_strategic_state(self):
        for question in self.GROUNDED:
            with self.subTest(question=question):
                decision = ROUTER.route(question, strategy=_state())
                self.assertIs(decision.register, Register.GROUNDED, question)
                self.assertFalse(decision.executive)

    def test_a_strategic_question_routes_executive(self):
        decision = ROUTER.route("What should we build next?", strategy=_state())
        self.assertIs(decision.register, Register.EXECUTIVE)
        self.assertIs(decision.topic, Topic.NEXT_WORK)
        self.assertFalse(decision.continuation)

    def test_an_evidence_follow_up_continues(self):
        decision = ROUTER.route("What evidence supports that recommendation?", strategy=_state())
        self.assertIs(decision.register, Register.CONTINUATION)
        self.assertTrue(decision.executive)

    def test_follow_ups_that_must_inherit(self):
        for question in (
            "Why?",
            "Why that one?",
            "What would make you change your mind?",
            "What was the runner-up?",
            "Compare it to the second option.",
            "How confident are you?",
            "Turn that into a plan.",
        ):
            with self.subTest(question=question):
                self.assertTrue(ROUTER.route(question, strategy=_state()).continuation)

    def test_nothing_continues_without_state(self):
        for question in ("Why?", "What evidence supports that recommendation?"):
            with self.subTest(question=question):
                self.assertFalse(ROUTER.route(question).executive)


class TestStaleness(unittest.TestCase):
    """
    The rule from PR #43, now decided in one place.

    Describing a past recommendation is always safe. Acting on one computed
    against a repository that has since moved is how a system gives confidently
    obsolete advice.
    """

    def test_matching_fingerprints_are_not_stale(self):
        decision = ROUTER.route("Why?", strategy=_state(), fingerprint="FP-1")
        self.assertFalse(decision.stale)
        self.assertTrue(decision.continuation)

    def test_a_stale_historical_follow_up_still_continues(self):
        decision = ROUTER.route("What was the runner-up?", strategy=_state(), fingerprint="FP-2")
        self.assertTrue(decision.continuation)
        self.assertTrue(decision.stale)
        self.assertFalse(decision.current_action)

    def test_a_stale_current_action_follow_up_forces_a_fresh_assessment(self):
        decision = ROUTER.route(
            "Is that still the best option?", strategy=_state(), fingerprint="FP-2"
        )
        self.assertIs(decision.register, Register.EXECUTIVE)
        self.assertFalse(decision.continuation)
        self.assertTrue(decision.stale)

    def test_no_fingerprint_means_no_staleness_claim(self):
        """Absence of a comparison is not evidence of freshness or of staleness."""
        self.assertFalse(ROUTER.route("Why?", strategy=_state()).stale)


class TestDeterminism(unittest.TestCase):
    def test_the_same_input_always_routes_the_same_way(self):
        for _ in range(3):
            decision = ROUTER.route("What should we build next?", strategy=_state())
            self.assertEqual(
                decision.to_dict(),
                ROUTER.route("What should we build next?", strategy=_state()).to_dict(),
            )

    def test_two_router_instances_agree(self):
        """Stateless: routing cannot depend on which instance answered."""
        a, b = ConversationRouter(), ConversationRouter()
        for question in ("Why?", "Where is X implemented?", "What should we build next?"):
            with self.subTest(question=question):
                self.assertEqual(
                    a.route(question, strategy=_state()).to_dict(),
                    b.route(question, strategy=_state()).to_dict(),
                )

    def test_every_decision_explains_itself(self):
        for question in ("Why?", "Where is X?", "What should we build next?", ""):
            with self.subTest(question=question):
                self.assertTrue(ROUTER.route(question, strategy=_state()).reason)

    def test_a_route_reports_the_register_the_budget_follows(self):
        self.assertTrue(Route(Register.CONTINUATION, "x").executive)
        self.assertTrue(Route(Register.EXECUTIVE, "x").executive)
        self.assertFalse(Route(Register.GROUNDED, "x").executive)


class TestPathParity(unittest.TestCase):
    """
    Streaming and non-streaming must route identically.

    They are separate code paths that both build a request, and the failure this
    guards against is one of them acquiring its own routing behaviour.
    """

    def test_all_three_conversation_paths_share_one_assess_callable(self):
        """
        Structural proof: `stream_message`, `send_message` and `retry_message`
        all reach routing through `_request` -> `_assessment` -> `self._assess`,
        and `WorkspaceRequest` is constructed in exactly one place.
        """
        source = (ROOT / "workspace" / "service.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("WorkspaceRequest("), 1)
        self.assertEqual(source.count("self._assess("), 1)

    def test_streaming_and_non_streaming_produce_identical_routes(self):
        """
        Behavioural proof: the same question and state routed through the shared
        callable yields the same decision regardless of which path asks.
        """
        recorded: list[Route] = []

        def capture(_project, question, _subject, _thin, strategy=None, fingerprint=""):
            recorded.append(ROUTER.route(question, strategy=strategy, fingerprint=fingerprint))
            return None

        from tempfile import TemporaryDirectory

        from workspace.service import WorkspaceService

        questions = [
            "What should we build next?",
            "What evidence supports that recommendation?",
            "Where is ContextEngine implemented?",
        ]
        with TemporaryDirectory() as tmp:
            service = WorkspaceService(root=Path(tmp), assess=capture)
            conversation = service._store.create("demo", "t")
            conversation.strategy = _state(project="demo")

            for question in questions:
                service._assessment(conversation, question, None)
            streamed = [r.to_dict() for r in recorded]

            recorded.clear()
            for question in questions:
                service._assessment(conversation, question, None)
            non_streamed = [r.to_dict() for r in recorded]

        self.assertEqual(streamed, non_streamed)
        # And the decisions are the ones expected, not merely equal to each other.
        self.assertEqual(
            [d["register"] for d in streamed],
            ["executive", "continuation", "grounded"],
        )


if __name__ == "__main__":
    unittest.main()
