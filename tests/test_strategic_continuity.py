"""
Tests for continuing a strategic conversation.

The failure this closes: "What evidence supports that recommendation?" was
classified as a fresh grounded lookup, so it lost the register, lost the
recommendation it was asking about, and inherited the smaller budget — which is
what truncated it. The fix is conversational state, not a bigger budget, and
these tests assert the state rather than the symptom.

Two properties matter most.

**The stored winner is stable.** A continuation must not re-rank. Recomputing
risks producing a different winner and then discussing it as though it were the
one the user asked about, which is worse than not answering at all.

**Stale state cannot drive current action.** Describing a past recommendation
accurately is always safe; acting on one computed against a repository that has
since moved is how a system gives confidently obsolete advice.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from initiatives.models import Health, Initiative, Progress
from intelligence.evidence import Citation, CitationKind, Evidence
from reasoning.confidence import score
from reasoning.engine import ReasoningEngine
from reasoning.models import Assessment, ClaimKind, Confidence, Mode, Recommendation
from reasoning.options import Alternative, Risk
from workspace.models import (
    AlternativeRef,
    Conversation,
    EvidenceRef,
    Message,
    MessageRole,
    Score,
    StrategicState,
    recommendation_key,
)
from workspace.responder import (
    CONTINUATION_INSTRUCTION,
    DEFAULT_MAX_TOKENS,
    EXECUTIVE_INSTRUCTION,
    EXECUTIVE_MAX_TOKENS,
    WorkspaceRequest,
)
from workspace.service import _capture_strategy

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def _evidence() -> Evidence:
    evidence = Evidence()
    evidence.add(
        Citation(
            kind=CitationKind.FILE,
            reference="integrations/",
            path="integrations/",
            because="11 source files, no tests",
        )
    )
    return evidence


def _assessment(continuation: bool = False) -> Assessment:
    assessment = Assessment(
        question="What should we build next?",
        mode=Mode.EXECUTIVE,
        mode_reason="matched next-work",
        continuation=continuation,
    )
    initiative = Initiative(slug="integrations", name="integrations")
    initiative.health = Health.AT_RISK
    initiative.progress = Progress(has_code=True)
    assessment.initiatives = [initiative]
    assessment.recommendations = [
        Recommendation(
            statement="Add a test suite for integrations",
            rationale="integrations is at-risk: implementation with no tests",
            confidence=Confidence(0.47, because=("test",)),
            evidence_strength=score(ClaimKind.FACT, _evidence()),
            execution_risk=Risk(0.43, because=("test",)),
            tradeoffs=("regressions are silent",),
            alternatives=(
                Alternative("Address workspace under-testing", "integrations unblocks more first"),
                Alternative("Write a testing strategy", "narrower payoff"),
            ),
            effort="scoped to one capability",
            evidence=_evidence(),
            initiative="integrations",
        )
    ]
    return assessment


def _message(incomplete: bool = False, error: str = "") -> Message:
    return Message(
        id="MSG-0002",
        role=MessageRole.ASSISTANT,
        content="...",
        created_at=NOW,
        incomplete=incomplete,
        error=error,
    )


class _Snapshot:
    """Minimal stand-in: capture reads only these two fields."""

    def __init__(self, fingerprint: str = "FP-1", snapshot_id: str = "SNAP-1") -> None:
        self.fingerprint = fingerprint
        self.id = snapshot_id


def _conversation(project: str = "mondayos") -> Conversation:
    return Conversation(
        id="CONV-0001",
        project=project,
        title="Strategy",
        created_at=NOW,
        updated_at=NOW,
    )


class TestCapture(unittest.TestCase):
    def test_a_completed_executive_turn_persists_state(self):
        conversation = _conversation()
        _capture_strategy(conversation, _assessment(), _message(), _Snapshot(), NOW)
        state = conversation.strategy
        self.assertIsNotNone(state)
        self.assertEqual(state.recommendation, "Add a test suite for integrations")
        self.assertEqual(state.initiative_slug, "integrations")
        self.assertEqual(state.fingerprint, "FP-1")
        self.assertEqual(state.source_message_id, "MSG-0002")
        self.assertEqual(state.confidence.band, "medium")
        self.assertEqual(state.execution_risk.band, "moderate")
        self.assertEqual(len(state.alternatives), 2)

    def test_the_recommendation_key_is_derived_from_content(self):
        conversation = _conversation()
        _capture_strategy(conversation, _assessment(), _message(), _Snapshot(), NOW)
        self.assertEqual(
            conversation.strategy.recommendation_key,
            recommendation_key("Add a test suite for integrations", "integrations"),
        )

    def test_an_incomplete_answer_never_becomes_strategic_state(self):
        """
        A truncated narration may have stopped before the recommendation was
        shown. Persisting it would record something the user never saw, breaking
        the one guarantee this state rests on.
        """
        conversation = _conversation()
        _capture_strategy(conversation, _assessment(), _message(incomplete=True), _Snapshot(), NOW)
        self.assertIsNone(conversation.strategy)

    def test_a_failed_answer_never_becomes_strategic_state(self):
        conversation = _conversation()
        _capture_strategy(conversation, _assessment(), _message(error="boom"), _Snapshot(), NOW)
        self.assertIsNone(conversation.strategy)

    def test_a_grounded_turn_persists_nothing(self):
        conversation = _conversation()
        grounded = Assessment(question="where is x", mode=Mode.GROUNDED)
        _capture_strategy(conversation, grounded, _message(), _Snapshot(), NOW)
        self.assertIsNone(conversation.strategy)

    def test_a_continuation_does_not_overwrite_the_decision_it_continues(self):
        """
        Otherwise the recommendation drifts turn by turn while appearing stable.
        """
        conversation = _conversation()
        _capture_strategy(conversation, _assessment(), _message(), _Snapshot(), NOW)
        original = conversation.strategy.recommendation

        follow_up = _assessment(continuation=True)
        follow_up.recommendations[0].statement = "Something else entirely"
        _capture_strategy(conversation, follow_up, _message(), _Snapshot(), NOW)
        self.assertEqual(conversation.strategy.recommendation, original)

    def test_a_new_executive_question_replaces_prior_state(self):
        conversation = _conversation()
        _capture_strategy(conversation, _assessment(), _message(), _Snapshot(), NOW)

        fresh = _assessment()
        fresh.question = "What are we neglecting?"
        fresh.recommendations[0].statement = "Write a testing strategy"
        _capture_strategy(conversation, fresh, _message(), _Snapshot("FP-2"), NOW)

        self.assertEqual(conversation.strategy.recommendation, "Write a testing strategy")
        self.assertEqual(conversation.strategy.fingerprint, "FP-2")

    def test_an_executive_turn_that_ranked_nothing_persists_nothing(self):
        conversation = _conversation()
        empty = _assessment()
        empty.recommendations = []
        _capture_strategy(conversation, empty, _message(), _Snapshot(), NOW)
        self.assertIsNone(conversation.strategy)


class TestBudgetFollowsRegister(unittest.TestCase):
    def test_a_continuation_receives_the_executive_budget(self):
        """
        The original failure. "What evidence supports that recommendation?" got
        the grounded budget and truncated.
        """
        request = WorkspaceRequest(
            project="p",
            message="What evidence supports that recommendation?",
            assessment=_assessment(continuation=True),
        )
        self.assertTrue(request.executive)
        self.assertTrue(request.continuation)
        self.assertEqual(request.token_budget(), EXECUTIVE_MAX_TOKENS)

    def test_a_grounded_turn_keeps_the_grounded_budget(self):
        request = WorkspaceRequest(
            project="p",
            message="Where is WorkspaceService implemented?",
            assessment=Assessment(question="q", mode=Mode.GROUNDED),
        )
        self.assertEqual(request.token_budget(), DEFAULT_MAX_TOKENS)

    def test_no_global_limit_was_raised(self):
        self.assertEqual(DEFAULT_MAX_TOKENS, 2000)
        self.assertEqual(EXECUTIVE_MAX_TOKENS, 6000)

    def test_a_continuation_uses_its_own_instruction(self):
        continuation = WorkspaceRequest(
            project="p", message="why?", assessment=_assessment(continuation=True)
        )
        fresh = WorkspaceRequest(project="p", message="what next?", assessment=_assessment())
        self.assertEqual(continuation.instruction(), CONTINUATION_INSTRUCTION)
        self.assertEqual(fresh.instruction(), EXECUTIVE_INSTRUCTION)

    def test_the_continuation_instruction_forbids_re_ranking_and_asking(self):
        lowered = CONTINUATION_INSTRUCTION.lower()
        self.assertIn("do not re-rank", lowered)
        self.assertIn("do not ask which recommendation", lowered)
        self.assertIn("do not invent confidence or risk", lowered)
        self.assertIn("do not create tasks", lowered)


class TestContinuationReasoning(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "integrations").mkdir()
        for i in range(6):
            (root / "integrations" / f"m{i}.py").write_text(f"class C{i}:\n    pass\n")
        (root / "docs").mkdir()
        (root / "docs" / "INTEGRATIONS.md").write_text("# Integrations\n")
        from intelligence.graph import build as build_graph
        from intelligence.index import build as build_index

        self.index = build_index("demo", root, cache_root=root / ".idx")
        self.graph = build_graph(self.index, tasks=[], knowledge=[])
        self.engine = ReasoningEngine(self.index, self.graph, [])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _state(self, fingerprint: str = "FP-1", slugs: list[str] | None = None) -> StrategicState:
        return StrategicState(
            question="What should we build next?",
            topic="next-work",
            project="demo",
            fingerprint=fingerprint,
            recommendation="Add a test suite for integrations",
            rationale="no tests",
            initiative_slug="integrations",
            initiative_slugs=slugs if slugs is not None else ["integrations"],
            alternatives=[AlternativeRef("Write a testing strategy", "narrower payoff")],
            evidence_refs=[EvidenceRef("file", "integrations/", "integrations/", 0, "no tests")],
            evidence_strength=Score(0.94, "high"),
            confidence=Score(0.47, "medium"),
            execution_risk=Score(0.43, "moderate"),
        )

    def test_a_continuation_keeps_the_stored_recommendation(self):
        assessment = self.engine.continue_from(
            self._state(), "What evidence supports that?", "FP-1"
        )
        self.assertTrue(assessment.continuation)
        self.assertIs(assessment.mode, Mode.EXECUTIVE)
        self.assertEqual(assessment.prior.recommendation, "Add a test suite for integrations")

    def test_a_continuation_does_not_re_rank(self):
        """
        The load-bearing test. Re-ranking risks a different winner, which would
        then be discussed as though it were the one the user asked about.
        """
        assessment = self.engine.continue_from(self._state(), "Why that one?", "FP-1")
        self.assertEqual(assessment.recommendations, [])

    def test_matching_fingerprints_are_not_stale(self):
        assessment = self.engine.continue_from(self._state(), "Why?", "FP-1")
        self.assertFalse(assessment.stale)
        self.assertIn("STATUS: current", assessment.render())

    def test_a_changed_fingerprint_marks_the_decision_stale(self):
        assessment = self.engine.continue_from(self._state(), "What was the runner-up?", "FP-2")
        self.assertTrue(assessment.continuation)
        self.assertTrue(assessment.stale)
        rendered = assessment.render()
        self.assertIn("STATUS: stale", rendered)
        self.assertIn("Do not present it as current advice", rendered)

    def test_a_stale_current_action_question_is_routed_away_from_the_record(self):
        """
        Case C, asserted where the decision now lives.

        Acting on an outdated recommendation is a different failure from
        describing one, and only the first is dangerous. The router refuses to
        route such a question to the stored decision at all, so `continue_from`
        is never reached — which is why this asserts on the route rather than on
        the assessment.
        """
        from reasoning.router import ROUTER, Register

        decision = ROUTER.route(
            "Is that still the best option?",
            strategy=self._state(fingerprint="FP-1"),
            fingerprint="FP-2",
        )
        self.assertIs(decision.register, Register.EXECUTIVE)
        self.assertFalse(decision.continuation)
        self.assertTrue(decision.stale)
        self.assertTrue(decision.current_action)

    def test_a_current_action_question_on_fresh_state_continues_normally(self):
        assessment = self.engine.continue_from(
            self._state(), "Is that still the best option?", "FP-1"
        )
        self.assertTrue(assessment.continuation)
        self.assertFalse(assessment.stale)

    def test_a_removed_capability_marks_the_recommendation_obsolete(self):
        state = self._state(slugs=["integrations", "billing"])
        assessment = self.engine.continue_from(state, "What was the runner-up?", "FP-1")
        self.assertTrue(assessment.obsolete)
        self.assertIn("billing", assessment.stale_because)
        self.assertIn("STATUS: obsolete", assessment.render())
        # Still inspectable rather than discarded.
        self.assertEqual(assessment.prior.recommendation, "Add a test suite for integrations")

    def test_the_prior_decision_leads_the_rendered_assessment(self):
        rendered = self.engine.continue_from(self._state(), "Why?", "FP-1").render()
        self.assertIn("Prior recommendation", rendered)
        self.assertLess(rendered.index("Prior recommendation"), len(rendered) // 2)

    def test_the_stored_scores_are_reported_not_recomputed(self):
        rendered = self.engine.continue_from(
            self._state(), "How confident are you?", "FP-1"
        ).render()
        self.assertIn("evidence strength high (94%)", rendered)
        self.assertIn("recommendation confidence medium (47%)", rendered)
        self.assertIn("execution risk moderate (43%)", rendered)

    def test_alternatives_are_rendered_in_the_order_shown(self):
        rendered = self.engine.continue_from(
            self._state(), "What was the runner-up?", "FP-1"
        ).render()
        self.assertIn("Alternatives shown, in the order presented", rendered)
        self.assertIn("1. Write a testing strategy", rendered)


class TestStaleReassessmentIsDisclosed(unittest.TestCase):
    """
    Found in the live acceptance run.

    A stale current-action question correctly triggered a fresh assessment, and
    the answer opened with "Status: Current — the project has not changed" — on
    the very turn we reassessed *because* it had. The status line was not in the
    assessment; the model had seen one earlier in the transcript and repeated the
    shape. A fresh answer that silently replaces a stale one is indistinguishable
    from one that ignored the staleness, so it now says which it is.
    """

    def test_a_stale_triggered_reassessment_says_so(self):
        assessment = Assessment(question="q", mode=Mode.EXECUTIVE, replaced_stale=True)
        rendered = assessment.render()
        self.assertIn("# Reassessed", rendered)
        self.assertIn("was NOT reused", rendered)
        self.assertIn("Do not claim the project is unchanged", rendered)

    def test_an_ordinary_fresh_assessment_carries_no_such_note(self):
        self.assertNotIn("# Reassessed", Assessment(question="q", mode=Mode.EXECUTIVE).render())

    def test_the_flag_is_serialised_for_inspection(self):
        data = Assessment(question="q", mode=Mode.EXECUTIVE, replaced_stale=True).to_dict()
        self.assertTrue(data["replaced_stale"])


class TestProjectIsolation(unittest.TestCase):
    def test_strategic_state_cannot_cross_projects(self):
        """
        Structural: state lives on a conversation, and a conversation's project
        is fixed at construction and is a path segment in storage.
        """
        from workspace.store import ConversationStore

        with TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp))
            one = store.create("mondayos", "Strategy")
            _capture_strategy(one, _assessment(), _message(), _Snapshot(), NOW)
            store.save(one)

            other = store.create("cue-app", "Other")
            store.save(other)

            self.assertIsNotNone(store.get("mondayos", one.id).strategy)
            self.assertIsNone(store.get("cue-app", other.id).strategy)

    def test_a_new_conversation_inherits_nothing(self):
        from workspace.store import ConversationStore

        with TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp))
            first = store.create("mondayos", "First")
            _capture_strategy(first, _assessment(), _message(), _Snapshot(), NOW)
            store.save(first)

            second = store.create("mondayos", "Second")
            store.save(second)
            self.assertIsNone(store.get("mondayos", second.id).strategy)


class TestRestart(unittest.TestCase):
    def test_state_survives_a_restart_and_continuation_still_works(self):
        from reasoning.executive import route
        from workspace.store import ConversationStore

        with TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp))
            conversation = store.create("mondayos", "Strategy")
            _capture_strategy(conversation, _assessment(), _message(), _Snapshot(), NOW)
            store.save(conversation)

            reloaded = ConversationStore(Path(tmp)).get("mondayos", conversation.id)
            routing = route("What evidence supports that recommendation?", reloaded.strategy)
            self.assertTrue(routing.continuation)
            self.assertEqual(reloaded.strategy.recommendation, "Add a test suite for integrations")


class TestNoHiddenReasoning(unittest.TestCase):
    def test_the_continuation_prompt_material_is_only_what_was_shown(self):
        """
        The rendered prior state is what reaches the model. It must contain the
        decision and nothing about how it was reached internally.
        """
        state = StrategicState(
            question="What should we build next?",
            recommendation="Add a test suite for integrations",
            initiative_slug="integrations",
        )
        rendered = state.render().lower()
        for forbidden in ("leverage", "candidate", "scratch", "chain of thought", "prompt"):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
