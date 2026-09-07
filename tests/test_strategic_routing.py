"""
Tests for routing precedence with strategic state in play.

Precedence is the design here, and the order is load-bearing:

    1. grounded lookup override   (unconditional)
    2. fresh executive question
    3. strategic continuation
    4. grounded

The lookup guard runs first so that no amount of widening the strategic patterns
can make "where is X implemented?" answer with a memo. That property is asserted
directly rather than left to follow from the implementation.

Continuation requires strong evidence because **false positives are worse than
misses**: a strategic answer to a code question is jarring and buries the answer,
while a missed follow-up merely returns the grounded answer MondayOS already gave.
"""

from __future__ import annotations

import unittest

from reasoning.executive import Topic, is_continuation, is_current_action, route
from workspace.models import StrategicState


def _state(**kw) -> StrategicState:
    base = dict(
        question="What should we build next?",
        topic="next-work",
        project="mondayos",
        recommendation="Add a test suite for integrations",
        initiative_slug="integrations",
        initiative_slugs=["integrations", "workspace"],
    )
    base.update(kw)
    return StrategicState(**base)


class TestGroundedLookupOverride(unittest.TestCase):
    """Pinned regressions: these must never become executive."""

    NAMED = [
        "Where is ContextEngine implemented?",
        "Find every place ContextEngine is used.",
        "Show me every reference to WorkspaceService.",
        "What file owns provider routing?",
    ]

    def test_named_lookups_stay_grounded_with_active_strategic_state(self):
        for question in self.NAMED:
            with self.subTest(question=question):
                routing = route(question, _state())
                self.assertFalse(routing.executive, question)
                self.assertFalse(routing.continuation, question)

    def test_they_stay_grounded_with_no_state_too(self):
        for question in self.NAMED:
            with self.subTest(question=question):
                self.assertFalse(route(question, None).executive, question)

    def test_the_override_beats_strategic_vocabulary_in_the_same_sentence(self):
        """
        The reason the guard runs first.

        A question can contain a strategic word and still plainly want a file.
        """
        routing = route("Where is the risk assessment implemented?", _state())
        self.assertFalse(routing.executive)
        self.assertIn("retrieval lookup", routing.reason)

    def test_other_retrieval_shapes_stay_grounded(self):
        for question in (
            "What changed in the last three commits?",
            "Open the file.",
            "List all the providers.",
            "Which file defines the budget?",
            "Turn on the debug flag.",
        ):
            with self.subTest(question=question):
                self.assertFalse(route(question, _state()).executive, question)


class TestFreshExecutive(unittest.TestCase):
    def test_an_explicit_strategic_question_starts_fresh(self):
        """
        Precedence 2 beats 3 deliberately: a new strategic question replaces the
        stored decision rather than being read as a comment on it.
        """
        routing = route("What should we build next?", _state())
        self.assertTrue(routing.executive)
        self.assertFalse(routing.continuation)
        self.assertIs(routing.topic, Topic.NEXT_WORK)

    def test_a_new_executive_question_on_a_different_topic_is_still_fresh(self):
        routing = route("What are we neglecting?", _state())
        self.assertTrue(routing.executive)
        self.assertFalse(routing.continuation)
        self.assertIs(routing.topic, Topic.GAPS)


class TestContinuation(unittest.TestCase):
    MUST_INHERIT = [
        "Why?",
        "Why that one?",
        "What evidence supports that recommendation?",
        "What would make you change your mind?",
        "What was the runner-up?",
        "Compare it to the second option.",
        "How confident are you?",
        "Turn that into a plan.",
    ]

    def test_follow_ups_inherit_the_executive_register(self):
        for question in self.MUST_INHERIT:
            with self.subTest(question=question):
                routing = route(question, _state())
                self.assertTrue(routing.executive, question)
                self.assertTrue(routing.continuation, question)

    def test_a_continuation_carries_the_stored_topic(self):
        self.assertIs(route("Why?", _state()).topic, Topic.NEXT_WORK)

    def test_nothing_continues_without_stored_state(self):
        """
        Test 7 in shape: a new conversation has no state, so nothing to inherit.
        """
        for question in self.MUST_INHERIT:
            with self.subTest(question=question):
                self.assertFalse(route(question, None).executive, question)

    def test_a_bare_why_is_accepted_alone(self):
        """It has no subject to supply and cannot mean anything else."""
        for question in ("Why?", "why", "So why?", "Why that?"):
            with self.subTest(question=question):
                self.assertTrue(is_continuation(question, _state())[0], question)

    def test_why_with_its_own_subject_does_not_continue(self):
        """
        The distinction that keeps "why" from swallowing every question.
        """
        for question in (
            "Why does the budget work that way?",
            "Why did the test fail?",
            "Why is ContextEngine cached?",
        ):
            with self.subTest(question=question):
                self.assertFalse(route(question, _state()).executive, question)

    def test_a_back_reference_alone_is_not_enough(self):
        """
        False positives are worse than misses: "that" appears constantly in code
        conversations.
        """
        continues, why = is_continuation("Is that cached?", _state())
        self.assertFalse(continues)
        self.assertIn("without strategic vocabulary", why)

    def test_strategic_vocabulary_alone_is_not_enough(self):
        continues, why = is_continuation("What are the risks of caching?", _state())
        self.assertFalse(continues)
        self.assertIn("without a back-reference", why)

    def test_naming_a_stored_capability_counts_as_strategic(self):
        """ "Is integrations still the right call" is unambiguous."""
        self.assertTrue(is_continuation("Is that integrations option right?", _state())[0])

    def test_naming_an_unrelated_capability_does_not(self):
        self.assertFalse(is_continuation("Is that billing thing cached?", _state())[0])

    def test_every_routing_decision_explains_itself(self):
        for question in self.MUST_INHERIT + [
            "Where is X implemented?",
            "What should we build next?",
        ]:
            with self.subTest(question=question):
                self.assertTrue(route(question, _state()).reason)


class TestCurrentActionVersusHistorical(unittest.TestCase):
    """
    The split that decides what a stale record may be used for.

    Describing a past recommendation accurately is always safe. Acting on one
    computed against a repository that has since moved is how a system gives
    confidently obsolete advice, so the two are separated deterministically.
    """

    HISTORICAL = [
        "What evidence supported that recommendation?",
        "Why did you pick that one?",
        "What was the runner-up?",
        "How confident were you?",
    ]

    CURRENT = [
        "Should we still build that?",
        "Is that still the best option?",
        "What should I do first?",
        "What should we do now?",
    ]

    def test_historical_follow_ups_are_not_current_action(self):
        for question in self.HISTORICAL:
            with self.subTest(question=question):
                self.assertFalse(is_current_action(question), question)

    def test_current_action_follow_ups_are_detected(self):
        for question in self.CURRENT:
            with self.subTest(question=question):
                self.assertTrue(is_current_action(question), question)

    def test_routing_marks_current_action_on_continuations(self):
        self.assertTrue(route("Is that still the best option?", _state()).current_action)
        self.assertTrue(route("Should we still build that?", _state()).current_action)
        self.assertFalse(route("What was the runner-up?", _state()).current_action)

    def test_a_question_that_can_only_be_a_follow_up_is_never_fresh(self):
        """
        Regression, caught by this suite.

        "Is that still the best option?" matched the *fresh* next-work pattern on
        "best ... option" and was answered as a brand-new prioritisation, silently
        discarding the decision it was asking about. A back-reference settles it:
        nothing referring to a prior answer can be starting a new one.
        """
        routing = route("Is that still the best option?", _state())
        self.assertTrue(routing.continuation)
        self.assertTrue(routing.executive)

    def test_the_same_phrasing_without_a_back_reference_stays_fresh(self):
        routing = route("What is the safest high-value thing we could do next?", _state())
        self.assertTrue(routing.executive)
        self.assertFalse(routing.continuation)

    def test_still_alone_does_not_make_a_code_question_strategic(self):
        """Precision: "still" is a common word in ordinary engineering talk."""
        for question in ("Is that still cached?", "Is the build still running?"):
            with self.subTest(question=question):
                self.assertFalse(route(question, _state()).executive, question)


class TestSubjectCarryOverUnchanged(unittest.TestCase):
    def test_grounded_carry_over_is_untouched_by_this_change(self):
        """
        Requirement 20. Strategic state is additive: the lexical subject
        mechanism the project index relies on must behave exactly as before.
        """
        from intelligence.questions import _points_back, subject_terms

        self.assertTrue(_points_back("find every place it is used"))
        self.assertFalse(_points_back("where is ContextEngine defined"))
        self.assertEqual(subject_terms("where is ContextEngine implemented"), ["contextengine"])


if __name__ == "__main__":
    unittest.main()
