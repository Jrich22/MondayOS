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


class TestStrategicPhrasingsS4(unittest.TestCase):
    """
    The five phrasings the S2 benchmark recorded as routing grounded.

    Four were plain gaps: no pattern covered "what is blocking us", codebase
    health, or a choice between two courses of action, and the priorities pattern
    required a literal space where people write a hyphen. None of them was caught
    by a guard — they simply fell through — which is why widening the patterns
    cannot weaken the lookup override.
    """

    def test_blockers_route_executive(self):
        for question in (
            "What is blocking us?",
            "What are the blockers?",
            "What's blocking the release?",
            "What is holding us back?",
            "What's in our way?",
        ):
            with self.subTest(question=question):
                self.assertTrue(ROUTER.route(question).executive, question)

    def test_codebase_health_routes_executive(self):
        for question in (
            "How healthy is this codebase?",
            "What is the overall health of the project?",
            "How is the repo doing?",
        ):
            with self.subTest(question=question):
                self.assertTrue(ROUTER.route(question).executive, question)

    def test_a_choice_between_two_courses_routes_executive(self):
        for question in (
            "Should we refactor or ship?",
            "Should we harden this or start the next feature?",
        ):
            with self.subTest(question=question):
                self.assertTrue(ROUTER.route(question).executive, question)

    def test_highest_leverage_survives_a_hyphen(self):
        """The whole defect was one character."""
        self.assertTrue(ROUTER.route("What is the highest-leverage thing to do?").executive)
        self.assertTrue(ROUTER.route("What is the highest leverage thing to do?").executive)


class TestElaborationIsContinuationOnly(unittest.TestCase):
    """
    "Say more" must never open Executive Mode.

    It has no subject of its own. With a strategic decision in play it can only
    mean "say more about that decision"; in a fresh or grounded thread it has no
    referent at all, and answering it with a memo would be the router inventing a
    conversation that never happened.
    """

    ELABORATIONS = (
        "Say more about that.",
        "Tell me more.",
        "Go on.",
        "Elaborate.",
        "Expand on that.",
    )

    def test_grounded_without_prior_strategic_state(self):
        for question in self.ELABORATIONS:
            with self.subTest(question=question):
                route = ROUTER.route(question)
                self.assertIs(route.register, Register.GROUNDED, question)
                self.assertFalse(route.executive, question)

    def test_continuation_with_prior_strategic_state(self):
        for question in self.ELABORATIONS:
            with self.subTest(question=question):
                route = ROUTER.route(question, strategy=_state(), fingerprint="FP-1")
                self.assertIs(route.register, Register.CONTINUATION, question)

    def test_elaboration_on_a_stale_decision_still_describes_rather_than_acts(self):
        """
        Describing a past recommendation is always safe; acting on a stale one is
        not. "Say more" asks to describe, so it stays a continuation even when the
        world has moved.
        """
        route = ROUTER.route("Say more about that.", strategy=_state(), fingerprint="FP-2")
        self.assertIs(route.register, Register.CONTINUATION)
        self.assertTrue(route.stale)
        self.assertFalse(route.current_action)


class TestStrategicWideningDidNotLeak(unittest.TestCase):
    """
    Adversarial near-misses: code questions that share a word with a strategic
    pattern.

    Each of these contains vocabulary the S4 patterns now match — blocker,
    health, leverage — while asking for a file and a line. Answering any of them
    with a memo would bury the answer, which is the failure the lookup override
    exists to prevent.
    """

    MUST_STAY_GROUNDED = (
        "Where is the blocker defined?",
        "Show me the health check module.",
        "How does the blocking queue work?",
        "What file owns the health endpoint?",
        "What changed in blocking.py?",
        "Explain the highest-leverage module.",
    )

    def test_code_questions_sharing_strategic_vocabulary_stay_grounded(self):
        for question in self.MUST_STAY_GROUNDED:
            with self.subTest(question=question):
                self.assertIs(ROUTER.route(question).register, Register.GROUNDED, question)

    def test_they_stay_grounded_inside_a_strategic_thread_too(self):
        """The lookup override is unconditional; a live decision must not change it."""
        for question in self.MUST_STAY_GROUNDED:
            with self.subTest(question=question):
                route = ROUTER.route(question, strategy=_state(), fingerprint="FP-1")
                self.assertIs(route.register, Register.GROUNDED, question)

    def test_explain_is_a_lookup_by_rule_rather_than_by_accident(self):
        """
        `Explain X` used to reach grounded only by matching no strategic pattern.

        That held until a strategic pattern grew wide enough to catch one, which
        S4's priorities fix did: "Explain the highest-leverage module" contains a
        priorities phrase. The guard now says so explicitly.
        """
        route = ROUTER.route("Explain the retention package.")
        self.assertIs(route.register, Register.GROUNDED)
        self.assertIn("lookup", route.reason)

    def test_an_or_question_phrased_as_a_lookup_stays_grounded(self):
        route = ROUTER.route("Show me whether we should use Redis or Postgres.")
        self.assertIs(route.register, Register.GROUNDED)
