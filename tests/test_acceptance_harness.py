"""
Tests for the acceptance harness itself, run offline.

None of these calls a provider. The harness is the thing that decides whether
MondayOS is releasable, so its own judgement has to be checkable without a
network and without a bill -- and a gate that has never been seen to fail is a
gate nobody should trust. Every one is fired at a synthetic violation and at a
clean run.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from acceptance.gates import GATES, Verdict, evaluate, overall
from acceptance.journeys import observe_stream
from acceptance.observe import (
    audit_scores,
    check_citations,
    cited_decisions,
    named_initiatives,
    quoted_scores,
)
from acceptance.pacing import Outcome, Pacing, classify
from acceptance.report import AcceptanceReport, Defect
from acceptance.session import ProjectSession, TurnRecord, isolated_root

CAPABLE = {"reports_stop_reason": True}
SILENT = {"reports_stop_reason": False}


def _validated(
    *, claims: int = 1, unsupported: int = 0, values: list[float] | None = None, **kw
) -> dict:
    """One product `score_validation` block, as `WorkspaceService` reports it."""
    bad = [{"concept": "confidence", "value": v, "text": f"confidence {v}"} for v in values or []]
    seen = bad + [
        {"concept": "confidence", "value": 0.47, "text": "confidence 47%"}
        for _ in range(max(0, claims - len(bad)))
    ]
    return {
        "checked": claims > 0,
        "claims_found": claims,
        "verified": claims - unsupported,
        "unsupported": unsupported,
        "claims": seen,
        "unsupported_claims": bad,
        "authoritative": [0.47, 0.94, 0.43],
        "correction_attempted": False,
        "correction_succeeded": False,
        **kw,
    }


def _turn(**kw) -> TurnRecord:
    base = dict(
        project="demo",
        turn_id="B.next",
        question="What should we build next?",
        expect_register="executive",
        observed_register="executive",
        outcome="ok",
        citations={
            "total": 0,
            "resolved": 0,
            "with_line": 0,
            "unresolved": [],
            "outside_boundary": [],
            "invalid_lines": [],
        },
        initiatives={"named": [], "invented": []},
    )
    base.update(kw)
    return TurnRecord(**base)


class TestProviderClassification(unittest.TestCase):
    """An overloaded API is not a defect. Saying otherwise measures someone else's queue."""

    def test_overload_and_rate_limits_are_transient(self):
        for message in (
            "Error code: 529 - overloaded_error",
            "429 Too Many Requests",
            "The service is temporarily unavailable",
            "Read timed out",
            "Connection reset by peer",
        ):
            with self.subTest(message=message):
                self.assertIs(classify(message), Outcome.PROVIDER_TRANSIENT)

    def test_authentication_and_bad_models_are_fatal(self):
        for message in ("401 Unauthorized", "invalid_api_key", "model not found: gpt-9"):
            with self.subTest(message=message):
                self.assertIs(classify(message), Outcome.PROVIDER_FATAL)

    def test_no_error_is_ok(self):
        self.assertIs(classify(""), Outcome.OK)

    def test_an_unrecognised_provider_error_is_not_blamed_on_the_product(self):
        """Guessing "this must be MondayOS" would manufacture defects from vendor prose."""
        self.assertIs(classify("Something nobody has seen before"), Outcome.PROVIDER_FATAL)

    def test_backoff_is_bounded(self):
        waits: list[float] = []
        pacing = Pacing(sleep=waits.append)
        for attempt in range(6):
            pacing.wait_before_retry(attempt)
        self.assertEqual(len(waits), 6)
        self.assertLessEqual(max(waits), max(pacing.backoff))


class TestCitationChecking(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "billing").mkdir()
        (self.root / "billing" / "ledger.py").write_text("a\nb\nc\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_real_path_with_a_real_line_resolves(self):
        check = check_citations("See billing/ledger.py:2 for this.", self.root, frozenset())
        self.assertEqual((check.total, check.resolved, check.with_line), (1, 1, 1))
        self.assertEqual(check.invalid_lines, [])

    def test_a_line_past_the_end_of_the_file_is_caught(self):
        check = check_citations("See billing/ledger.py:900.", self.root, frozenset())
        self.assertTrue(check.invalid_lines)

    def test_a_path_in_a_nested_project_is_a_boundary_breach(self):
        check = check_citations(
            "See projects/other/src/app.ts:4.", self.root, frozenset({"projects/other"})
        )
        self.assertEqual(check.outside_boundary, ["projects/other/src/app.ts"])

    def test_prose_is_not_mistaken_for_a_citation(self):
        check = check_citations("The router decides, e.g. for lookups.", self.root, frozenset())
        self.assertEqual(check.total, 0)

    def test_a_nonexistent_file_is_unresolved_not_a_breach(self):
        check = check_citations("See billing/ghost.py.", self.root, frozenset())
        self.assertEqual(check.unresolved, ["billing/ghost.py"])
        self.assertEqual(check.outside_boundary, [])


class TestAnswerReading(unittest.TestCase):
    def test_adr_references_are_normalised(self):
        self.assertEqual(cited_decisions("per ADR-017 and adr 002"), ["ADR-002", "ADR-017"])

    def test_the_adr_width_matches_the_index(self):
        """
        RC1/ACC-008. The harness accepted `ADR-1`; the index requires three
        digits, so the harness recognised references the index could never
        resolve and gate 10 was marginally over-eager.
        """
        self.assertEqual(cited_decisions("per ADR-1 and ADR-17"), [])

    def test_quoted_scores_are_read_as_fractions(self):
        found = quoted_scores("Confidence is 72%, and execution risk is 0.30.")
        self.assertEqual(found["confidence"], 0.72)
        self.assertEqual(found["execution_risk"], 0.3)

    def test_an_answer_quoting_nothing_yields_nothing(self):
        """Not reciting the numbers is good writing, not a failure."""
        self.assertEqual(quoted_scores("We should harden the scheduler first."), {})

    def test_a_sentence_fragment_is_never_an_invented_initiative(self):
        """
        RC1/H-8. Gate 2 accused the model of inventing an initiative called
        "and reduce the risk of future changes".

        A capability is named "Billing" or "AI Workspace". Anything carrying a
        conjunction, an article or a verb is prose, and reporting prose as
        fabrication is worse than missing a real one.
        """
        answer = 'the "and reduce the risk of future changes" initiative'
        self.assertEqual(named_initiatives(answer, ["Billing"])["invented"], [])

    def test_a_known_multi_word_initiative_is_recognised(self):
        found = named_initiatives('the "AI Workspace" initiative', ["AI Workspace"])
        self.assertEqual(found["named"], ["AI Workspace"])
        self.assertEqual(found["invented"], [])

    def test_a_fragment_without_stopwords_is_still_not_a_name(self):
        """
        RC1/H-8 second pass. "took priority", "directly" and "s maturity" carry
        no conjunctions or articles and are still fragments; gate 2 reported all
        three as invented capabilities. A name looks like a name.
        """
        for fragment in ("took priority", "directly", "immediately", "s maturity"):
            with self.subTest(fragment=fragment):
                answer = f'the "{fragment}" initiative'
                self.assertEqual(named_initiatives(answer, ["Billing"])["invented"], [])

    def test_a_capitalised_two_word_invention_is_still_caught(self):
        found = named_initiatives('The "Quantum Ledger" capability', ["Billing"])
        self.assertEqual(found["invented"], ["Quantum Ledger"])

    def test_a_short_plausible_name_is_still_caught(self):
        """The gate must keep its teeth: a real invention is still reported."""
        found = named_initiatives('The "Telepathy" initiative is at risk.', ["Billing"])
        self.assertEqual(found["invented"], ["Telepathy"])

    def test_invention_is_only_claimed_for_a_phrase_presented_as_one(self):
        known = ["Billing", "Scheduling"]
        self.assertEqual(named_initiatives("Billing is going well.", known)["invented"], [])
        found = named_initiatives('The "Telepathy" initiative is at risk.', known)
        self.assertEqual(found["invented"], ["Telepathy"])

    def test_a_known_initiative_named_as_one_is_not_invention(self):
        found = named_initiatives('The "Billing" capability is healthy.', ["Billing"])
        self.assertEqual(found["invented"], [])


class TestGates(unittest.TestCase):
    """
    Every gate, shown capable of failing and incapable of passing on nothing.

    The rule these enforce: a gate that evaluated zero observations reports
    INCONCLUSIVE, never PASS. Three gates violated it in the RC1 run — 5 compared
    no keys, 12 inspected no state, 9 read a field nothing ever wrote — and each
    reported PASS in the document a release decision rested on.
    """

    # A project that got all the way through: a decision was shown and persisted.
    LIVE = {
        "demo": {
            "available": True,
            "completed": True,
            "recommendation_key": "k1",
            "strategy_persisted": True,
            "hidden_reasoning_keys": [],
            "determinism": {"recommendation_key": ("k1", "k1")},
        }
    }
    # A project whose strategic turn never completed.
    STALLED = {
        "demo": {
            "available": True,
            "completed": True,
            "recommendation_key": "",
            "strategy_persisted": False,
            "hidden_reasoning_keys": [],
            "determinism": {},
        }
    }

    def _gate(self, number: int, turns, projects, caps=CAPABLE):
        return next(g for g in evaluate(turns, projects, caps) if g.number == number)

    def test_all_fourteen_gates_are_reported(self):
        results = evaluate([_turn()], self.LIVE, CAPABLE)
        self.assertEqual({g.number for g in results}, {n for n, _ in GATES})

    # ------------------------------------------------ zero observations rule

    def test_no_gate_can_pass_without_exercising_anything(self):
        """The general form of the defect, asserted once over every gate."""
        for gate in evaluate([], {}, CAPABLE):
            with self.subTest(gate=gate.number):
                if gate.exercised == 0:
                    self.assertIsNot(gate.verdict, Verdict.PASS, gate.name)

    def test_gate_5_cannot_pass_with_zero_key_comparisons(self):
        follow = _turn(turn_id="C.evidence", persisted_key="")
        gate = self._gate(5, [follow], self.STALLED)
        self.assertIs(gate.verdict, Verdict.INCONCLUSIVE)
        self.assertEqual(gate.exercised, 0)

    def test_gate_5_reads_the_persisted_key_not_the_turns_own(self):
        """
        RC1/H-9. A continuation returns no fresh recommendation by design.

        Reading the transient field found nothing on every follow-up, so the gate
        exercised zero observations across twelve opportunities while appearing to
        check continuity. What survives the follow-up is the *stored* decision.
        """
        follow = _turn(
            turn_id="C.evidence", recommendation_key="", persisted_key="k1", continuation=True
        )
        gate = self._gate(5, [follow], self.LIVE)
        self.assertIs(gate.verdict, Verdict.PASS)
        self.assertEqual(gate.exercised, 1)

    def test_gate_5_anchors_to_the_decision_current_at_the_follow_up(self):
        """
        RC1/H-10. A fresh executive question legitimately re-decides mid-thread.

        Cue App's stored decision moved at `E.risk` -- "What is the biggest
        risk?" -- and the follow-ups after it correctly continued the *new*
        decision. Comparing them against the conversation's first recommendation
        reported correct behaviour as a silent re-decision.
        """
        follow = _turn(
            turn_id="I.say-more-warm", persisted_key="k2", anchor_at_turn="k2", continuation=True
        )
        self.assertIs(self._gate(5, [follow], self.LIVE).verdict, Verdict.PASS)

    def test_gate_5_fails_when_the_stored_decision_changed(self):
        follow = _turn(turn_id="C.evidence", persisted_key="k2", continuation=True)
        self.assertIs(self._gate(5, [follow], self.LIVE).verdict, Verdict.FAIL)

    def test_gate_5_allows_a_change_when_reassessment_was_requested(self):
        follow = _turn(turn_id="D.change-mind", persisted_key="k2", reassessed=True)
        self.assertIs(self._gate(5, [follow], self.LIVE).verdict, Verdict.PASS)

    def test_gate_8_cannot_pass_without_persisted_strategy(self):
        warm = _turn(turn_id="I.say-more-warm", observed_register="grounded")
        gate = self._gate(8, [warm], self.STALLED)
        self.assertIs(gate.verdict, Verdict.INCONCLUSIVE)
        self.assertIn("nothing to continue", gate.reason + gate.note)

    def test_gate_8_fails_when_state_exists_and_the_turn_grounds(self):
        warm = _turn(turn_id="I.say-more-warm", observed_register="grounded")
        self.assertIs(self._gate(8, [warm], self.LIVE).verdict, Verdict.FAIL)

    def test_gate_8_passes_when_state_exists_and_the_turn_continues(self):
        warm = _turn(turn_id="I.say-more-warm", observed_register="continuation")
        self.assertIs(self._gate(8, [warm], self.LIVE).verdict, Verdict.PASS)

    def test_gate_12_cannot_pass_when_nothing_was_persisted(self):
        gate = self._gate(12, [_turn()], self.STALLED)
        self.assertIs(gate.verdict, Verdict.INCONCLUSIVE)

    def test_gate_12_fails_on_a_planted_hidden_field(self):
        projects = {"demo": {**self.LIVE["demo"], "hidden_reasoning_keys": ["prompt"]}}
        gate = self._gate(12, [_turn()], projects)
        self.assertIs(gate.verdict, Verdict.FAIL)
        self.assertIn("prompt", gate.failures[0])

    def test_gate_12_passes_when_state_was_persisted_and_clean(self):
        self.assertIs(self._gate(12, [_turn()], self.LIVE).verdict, Verdict.PASS)

    def test_gate_9_fails_on_a_planted_foreign_commit(self):
        """The gate that could not fail at all before."""
        turn = _turn(cited_commits=["abc1234"], foreign_history=["abc1234"])
        gate = self._gate(9, [turn], self.LIVE)
        self.assertIs(gate.verdict, Verdict.FAIL)
        self.assertIn("abc1234", gate.failures[0])

    def test_gate_9_passes_only_when_commits_were_actually_cited(self):
        cited = _turn(cited_commits=["5c44663"], foreign_history=[])
        self.assertIs(self._gate(9, [cited], self.LIVE).verdict, Verdict.PASS)
        silent = _turn(cited_commits=[], foreign_history=[])
        self.assertIs(self._gate(9, [silent], self.LIVE).verdict, Verdict.INCONCLUSIVE)

    def test_gate_14_cannot_pass_on_partial_coverage(self):
        projects = {
            "a": {**self.LIVE["demo"], "determinism": {"recommendation_key": ("k", "k")}},
            "b": {**self.LIVE["demo"], "determinism": {}},
            "c": {**self.LIVE["demo"], "determinism": {}},
            "d": {**self.LIVE["demo"], "determinism": {}},
        }
        gate = self._gate(14, [_turn()], projects)
        self.assertIs(gate.verdict, Verdict.INCONCLUSIVE)
        self.assertEqual((gate.exercised, gate.opportunities), (1, 4))
        self.assertIn("partial coverage", gate.reason)

    def test_gate_14_fails_on_any_mismatch(self):
        projects = {"demo": {**self.LIVE["demo"], "determinism": {"confidence": (0.5, 0.9)}}}
        self.assertIs(self._gate(14, [_turn()], projects).verdict, Verdict.FAIL)

    def test_gate_14_passes_on_full_matched_coverage(self):
        self.assertIs(self._gate(14, [_turn()], self.LIVE).verdict, Verdict.PASS)

    # -------------------------------------------------------- other gates

    def test_gate_1_fails_on_a_cross_project_citation(self):
        bad = _turn(
            citations={**_turn().citations, "total": 1, "outside_boundary": ["projects/x/a.ts"]}
        )
        self.assertIs(self._gate(1, [bad], self.LIVE).verdict, Verdict.FAIL)

    def test_gate_2_fails_on_an_invented_initiative(self):
        bad = _turn(initiatives={"named": ["Telepathy"], "invented": ["Telepathy"]})
        self.assertIs(self._gate(2, [bad], self.LIVE).verdict, Verdict.FAIL)

    def test_gate_3_fails_when_a_lookup_answers_strategically(self):
        bad = _turn(turn_id="F.where", expect_register="grounded", observed_register="executive")
        self.assertIs(self._gate(3, [bad], self.LIVE).verdict, Verdict.FAIL)

    # ---------------------------------------------------------------- gate 6
    #
    # Superseded by D-7. These used to build a turn out of `computed_values` and
    # `quoted_scores` and let the gate re-derive the judgement from prose. The
    # gate now reads the product's own `score_validation`, which is produced by
    # the code that decides whether an answer may be delivered at all. The
    # scenarios are unchanged; the authority for the verdict is not.

    def test_gate_6_fails_when_an_unsupported_score_was_delivered(self):
        bad = _turn(score_validation=_validated(claims=3, unsupported=1, values=[0.12]))
        self.assertIs(self._gate(6, [bad], self.LIVE).verdict, Verdict.FAIL)

    def test_gate_6_names_the_number_that_failed(self):
        bad = _turn(score_validation=_validated(claims=3, unsupported=1, values=[0.12]))
        self.assertIn("0.12", " ".join(self._gate(6, [bad], self.LIVE).failures))

    def test_gate_6_passes_a_turn_whose_every_claim_was_verified(self):
        good = _turn(score_validation=_validated(claims=2))
        gate = self._gate(6, [good], self.LIVE)
        self.assertIs(gate.verdict, Verdict.PASS)
        self.assertEqual(gate.exercised, 1)

    def test_gate_6_passes_when_a_correction_cleaned_the_answer(self):
        """
        The 76% scenario, as the run sees it. The model generated an invented
        score, MondayOS corrected once, and the delivered answer is clean.

        That is a pass, and the correction stays visible in the metadata: the
        gate is about what reached the user, and the report is about what it took
        to get there.
        """
        turn = _turn(
            score_validation=_validated(
                claims=3, correction_attempted=True, correction_succeeded=True
            )
        )
        gate = self._gate(6, [turn], self.LIVE)
        self.assertIs(gate.verdict, Verdict.PASS)
        self.assertTrue(turn.score_validation["correction_attempted"])
        self.assertTrue(turn.score_validation["correction_succeeded"])

    def test_gate_6_does_not_score_a_turn_that_failed_closed(self):
        """
        A refusal is not a delivered answer. The invented number did not reach
        anyone, and counting it either way would say something untrue -- as a
        pass it credits an answer that was never shown, as a failure it blames
        the product for working.
        """
        refused = _turn(
            outcome="product_fail_closed",
            score_validation=_validated(claims=2, unsupported=1, values=[0.76]),
        )
        gate = self._gate(6, [refused], self.LIVE)
        self.assertIsNot(gate.verdict, Verdict.FAIL)
        self.assertEqual(gate.exercised, 0)

    def test_gate_6_passes_a_continuation_reciting_the_stored_decision(self):
        """
        RC1/H-7, re-expressed. Which values a continuation may quote is the
        product's question now -- `_authoritative_scores` adds the persisted
        decision -- so the harness no longer needs to model the rule to observe
        that it held.
        """
        turn = _turn(
            turn_id="D.change-mind",
            continuation=True,
            score_validation=_validated(claims=2),
        )
        self.assertIs(self._gate(6, [turn], self.LIVE).verdict, Verdict.PASS)

    def test_gate_6_still_fails_a_continuation_that_invents_a_number(self):
        """The gate keeps its teeth on continuations too."""
        turn = _turn(
            turn_id="D.change-mind",
            continuation=True,
            score_validation=_validated(claims=2, unsupported=1, values=[0.11]),
        )
        self.assertIs(self._gate(6, [turn], self.LIVE).verdict, Verdict.FAIL)

    def test_gate_6_is_one_observation_per_turn_not_per_number(self):
        """
        An answer is delivered or it is not. Scoring each number separately let a
        turn with four good quotes and one invention read as 80% healthy, when
        what actually happened is that an invented score reached a user.
        """
        good = _turn(score_validation=_validated(claims=6))
        self.assertEqual(self._gate(6, [good], self.LIVE).exercised, 1)

    def test_gate_6_does_not_exercise_on_an_answer_with_no_scores(self):
        """A turn stating no number has nothing to get right."""
        quiet = _turn(score_validation=_validated(claims=0))
        gate = self._gate(6, [quiet], self.LIVE)
        self.assertEqual(gate.exercised, 0)
        self.assertEqual(gate.opportunities, 1)
        self.assertIs(gate.verdict, Verdict.INCONCLUSIVE)

    def test_gate_6_never_passes_on_zero_observations(self):
        quiet = _turn(score_validation=_validated(claims=0))
        self.assertIsNot(self._gate(6, [quiet, quiet], self.LIVE).verdict, Verdict.PASS)

    def test_gate_6_ignores_a_turn_the_product_never_validated(self):
        """No metadata is not an observation. It is a turn the gate cannot see."""
        gate = self._gate(6, [_turn()], self.LIVE)
        self.assertEqual(gate.opportunities, 0)
        self.assertIs(gate.verdict, Verdict.INCONCLUSIVE)

    def test_gate_7_fails_when_a_cold_elaboration_goes_executive(self):
        bad = _turn(
            turn_id="I.say-more-cold", expect_register="grounded", observed_register="executive"
        )
        self.assertIs(self._gate(7, [bad], self.LIVE).verdict, Verdict.FAIL)

    def test_gate_10_fails_on_an_invented_decision(self):
        bad = _turn(cited_decisions=["ADR-999"], invented_decisions=["ADR-999"])
        self.assertIs(self._gate(10, [bad], self.LIVE).verdict, Verdict.FAIL)

    def test_gate_11_fails_on_a_line_past_the_end_of_a_file(self):
        bad = _turn(citations={**_turn().citations, "with_line": 1, "invalid_lines": ["a.py:900"]})
        self.assertIs(self._gate(11, [bad], self.LIVE).verdict, Verdict.FAIL)

    def test_gate_13_is_inconclusive_when_a_corpus_did_not_finish(self):
        """
        Superseded: this asserted FAIL for a corpus that merely did not finish.

        Under the hardened model an unfinished flow with no product error is
        missing evidence rather than a defect -- the thing that stopped it was
        outside MondayOS. A product error still fails, which the test below pins.
        """
        projects = {"demo": {**self.LIVE["demo"], "completed": False}}
        self.assertIs(self._gate(13, [_turn()], projects).verdict, Verdict.INCONCLUSIVE)

    # --------------------------------------------- unverifiable vs inconclusive

    def test_gate_4_is_unverifiable_on_a_silent_provider(self):
        gate = self._gate(4, [_turn()], self.LIVE, SILENT)
        self.assertIs(gate.verdict, Verdict.UNVERIFIABLE)
        self.assertIn("stop reason", gate.reason)

    def test_unverifiable_is_distinct_from_inconclusive(self):
        """
        Different claims. UNVERIFIABLE says the environment cannot show this;
        INCONCLUSIVE says this run did not. Collapsing them would hide the
        difference between "we cannot look" and "we did not look".
        """
        capable = self._gate(4, [_turn()], self.LIVE, CAPABLE)
        silent = self._gate(4, [_turn()], self.LIVE, SILENT)
        self.assertIs(silent.verdict, Verdict.UNVERIFIABLE)
        self.assertIs(capable.verdict, Verdict.INCONCLUSIVE)
        self.assertNotEqual(silent.reason, capable.reason)

    def test_gate_4_fails_when_truncation_was_hidden(self):
        bad = _turn(stop_reason="max_tokens", incomplete=False)
        self.assertIs(self._gate(4, [bad], self.LIVE).verdict, Verdict.FAIL)

    def test_gate_4_passes_when_truncation_was_reported(self):
        good = _turn(stop_reason="max_tokens", incomplete=True)
        self.assertIs(self._gate(4, [good], self.LIVE).verdict, Verdict.PASS)

    # ------------------------------------------------- provider vs product

    def test_a_provider_failure_never_fails_a_gate(self):
        """Someone else's outage is not a defect here."""
        broken = _turn(
            outcome="provider_transient",
            error="529 overloaded",
            observed_register="",
            initiatives={"named": ["Ghost"], "invented": ["Ghost"]},
            cited_commits=["deadbee"],
            foreign_history=["deadbee"],
        )
        for gate in evaluate([broken], self.LIVE, CAPABLE):
            with self.subTest(gate=gate.number):
                self.assertIsNot(gate.verdict, Verdict.FAIL, gate.name)

    def test_a_provider_failure_is_still_counted_as_an_opportunity(self):
        """The report must show what the run could not reach."""
        broken = _turn(outcome="provider_transient", observed_register="")
        gate = self._gate(1, [broken], self.LIVE)
        self.assertEqual(gate.opportunities, 1)
        self.assertEqual(gate.exercised, 0)

    def test_a_product_error_can_still_fail_a_gate(self):
        """Only MondayOS raising is MondayOS's fault -- and it must count."""
        crash = _turn(outcome="product_error", error="TypeError: boom")
        projects = {"demo": {**self.LIVE["demo"], "completed": False}}
        self.assertIs(self._gate(13, [crash], projects).verdict, Verdict.FAIL)

    # --------------------------------------------------------------- overall

    def test_a_fully_exercised_clean_run_passes_every_applicable_gate(self):
        turns = [
            _turn(
                turn_id="A.overview",
                expect_register="grounded",
                observed_register="grounded",
                citations={
                    "total": 3,
                    "resolved": 3,
                    "with_line": 2,
                    "unresolved": [],
                    "outside_boundary": [],
                    "invalid_lines": [],
                },
                cited_commits=["5c44663"],
                cited_decisions=["ADR-017"],
                initiatives={"named": ["Billing"], "invented": []},
            ),
            _turn(
                turn_id="B.next",
                scores={"confidence": 0.6},
                computed_values=[0.6, 0.9],
                quoted_scores={"confidence": 0.47},
                score_validation=_validated(claims=1),
                recommendation_key="k1",
            ),
            _turn(turn_id="C.evidence", persisted_key="k1", continuation=True),
            _turn(
                turn_id="I.say-more-cold", expect_register="grounded", observed_register="grounded"
            ),
            _turn(turn_id="I.say-more-warm", observed_register="continuation"),
            _turn(turn_id="X.truncated", stop_reason="max_tokens", incomplete=True),
        ]
        results = evaluate(turns, self.LIVE, CAPABLE)
        for gate in results:
            with self.subTest(gate=gate.number):
                self.assertIsNot(gate.verdict, Verdict.FAIL, gate.name)
                self.assertIsNot(gate.verdict, Verdict.UNVERIFIABLE, gate.name)
        self.assertEqual(overall(results), "READY")

    def test_overall_verdicts(self):
        clean = evaluate([_turn()], self.LIVE, CAPABLE)
        self.assertIn(overall(clean), ("READY", "READY WITH KNOWN LIMITATIONS"))
        silent = evaluate([_turn()], self.LIVE, SILENT)
        self.assertEqual(overall(silent), "READY WITH KNOWN LIMITATIONS")
        bad = _turn(cited_decisions=["ADR-999"], invented_decisions=["ADR-999"])
        self.assertEqual(overall(evaluate([bad], self.LIVE, CAPABLE)), "NOT READY")


class TestReport(unittest.TestCase):
    def _report(self, caps=CAPABLE) -> AcceptanceReport:
        report = AcceptanceReport(provider={"name": "test", "model": "m"}, started_at="now")
        report.projects = dict(TestGates.LIVE)
        report.turns = [_turn(answer_excerpt="We should harden the scheduler.")]
        report.gates = evaluate(report.turns, report.projects, caps)
        return report

    def test_the_json_round_trips(self):
        data = json.loads(self._report().to_json())
        self.assertEqual(data["kind"], "acceptance")
        self.assertIn("gates", data)
        self.assertIn("provider_incidents", data)

    def test_there_is_no_quality_score_anywhere(self):
        """A number standing in for judgement would be the one people quote."""
        blob = self._report().to_json().lower()
        for banned in ("quality_score", "reasoning_score", "answer_score", "grade"):
            self.assertNotIn(banned, blob)

    def test_the_review_package_shows_the_question_and_an_excerpt(self):
        markdown = self._report().review_markdown()
        self.assertIn("What should we build next?", markdown)
        self.assertIn("harden the scheduler", markdown)

    def test_readiness_names_its_verdict_and_its_limitations(self):
        readiness = self._report(SILENT).readiness_markdown()
        self.assertIn("READY WITH KNOWN LIMITATIONS", readiness)
        self.assertIn("unverifiable", readiness.lower())

    def test_defects_appear_on_the_punch_list(self):
        report = self._report()
        report.defects = [
            Defect(
                severity="major",
                title="Something broke",
                projects=["demo"],
                reproducibility="every run",
                root_cause="a bug",
                proposed_fix="fix it",
                benchmark_impact="none",
            )
        ]
        readiness = report.readiness_markdown()
        self.assertIn("Something broke", readiness)
        self.assertIn("every run", readiness)


class TestIsolation(unittest.TestCase):
    def test_a_run_gets_its_own_root_and_copies_the_registry(self):
        with TemporaryDirectory() as tmp:
            registry = Path(tmp) / "projects.json"
            registry.write_text('{"demo": {"name": "demo", "source_path": "/abs/demo"}}')
            root = isolated_root(registry, Path(tmp) / "run")
            copied = json.loads((root / "config" / "projects.json").read_text())
            self.assertEqual(copied["demo"]["source_path"], "/abs/demo")
            self.assertNotEqual(root, registry.parent)


if __name__ == "__main__":
    unittest.main()


class TestStrategyIsReadFromTheStore(unittest.TestCase):
    """
    RC1/H-6. Continuity state is read through MondayOS's own store.

    Two wrong assumptions preceded this, and the second is the instructive one.
    The first read `payload["conversation"]["strategy"]`; the API response
    carries no such key. The second read the record directly but globbed for
    `*.json` -- and conversations are Markdown with YAML frontmatter (ADR-003),
    so it found nothing either. The fix looked like it had worked while changing
    nothing, and four gates stayed unexercised for a second run.

    Using `ConversationStore` removes the guesswork: the product's own reader
    knows where records live and how they are shaped.
    """

    def _session(self, root: Path, project: str = "demo"):
        from acceptance.pacing import Pacing
        from acceptance.session import ProjectSession

        return ProjectSession(
            monday=None,
            project=project,
            corpus_root=root,
            monday_root=root,
            boundaries=frozenset(),
            discovered=[],
            own_decisions=[],
            own_commits=frozenset(),
            pacing=Pacing(sleep=lambda _s: None),
        )

    def _conversation(self, root: Path, with_strategy: bool):
        from datetime import UTC, datetime

        from workspace.models import Conversation, StrategicState
        from workspace.store import ConversationStore

        store = ConversationStore(root)
        now = datetime(2026, 9, 10, tzinfo=UTC)
        conversation = Conversation(
            id="CONV-0001", project="demo", title="t", created_at=now, updated_at=now
        )
        if with_strategy:
            conversation.strategy = StrategicState(
                question="What should we build next?",
                topic="next-work",
                project="demo",
                fingerprint="FP-1",
                recommendation="Harden the scheduler",
                recommendation_key="k1",
            )
        store.save(conversation)
        return store

    def test_persisted_strategy_is_read_from_the_record(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._conversation(root, with_strategy=True)
            strategy = self._session(root)._persisted_strategy("CONV-0001")
            self.assertEqual(strategy["recommendation_key"], "k1")
            self.assertEqual(strategy["fingerprint"], "FP-1")

    def test_a_conversation_without_strategy_reads_empty(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._conversation(root, with_strategy=False)
            self.assertEqual(self._session(root)._persisted_strategy("CONV-0001"), {})

    def test_an_unknown_conversation_reads_empty_rather_than_raising(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(self._session(root)._persisted_strategy("nope"), {})
            self.assertEqual(self._session(root)._persisted_strategy(""), {})

    def test_the_stale_scenario_can_actually_move_the_fingerprint(self):
        """
        The scenario that had never once run.

        It used the same `*.json` glob, so every stale run silently did nothing
        and reported `exercised: False`.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self._conversation(root, with_strategy=True)
            session = self._session(root)
            self.assertTrue(session._move_fingerprint("CONV-0001"))
            moved = store.get("demo", "CONV-0001")
            self.assertEqual(moved.strategy.fingerprint, "acceptance-moved-world")

    def test_moving_a_fingerprint_that_does_not_exist_reports_false(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._conversation(root, with_strategy=False)
            self.assertFalse(self._session(root)._move_fingerprint("CONV-0001"))

    def test_records_are_markdown_not_json(self):
        """
        The assumption that broke this, asserted so it cannot return.

        A harness globbing `*.json` under the conversation directory finds only
        `.sequences.json` and concludes nothing was ever persisted.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._conversation(root, with_strategy=True)
            directory = root / "workspace" / "conversations" / "demo"
            self.assertTrue(list(directory.glob("CONV-*.md")))
            self.assertEqual([p.name for p in directory.glob("CONV-*.json")], [])

    def test_the_api_response_shape_carries_no_strategy(self):
        """The first wrong assumption, also asserted."""
        from datetime import UTC, datetime

        from workspace.models import Conversation

        now = datetime(2026, 9, 10, tzinfo=UTC)
        keys = set(
            Conversation(id="C", project="p", title="t", created_at=now, updated_at=now).to_dict()
        )
        self.assertNotIn("strategy", keys)


class Gate13CompletionTests(unittest.TestCase):
    """
    Completing the journey means answering it, not iterating it.

    Gate 13 reported PASS on a hosted run where every one of fifty-two turns
    failed with `provider_fatal` and not one was answered. Every other gate went
    inconclusive under the non-vacuous rules; this one turned a provider outage
    into a green tick. These tests pin each of the four outcomes, starting with
    the run that exposed it.
    """

    CORPORA = ("mondayos", "cue-app", "sourcingbot", "weatherbot")

    def _projects(self, completed: bool = True, only: tuple[str, ...] = ()) -> dict:
        names = only or self.CORPORA
        return {
            name: {"available": True, "completed": completed and name in names}
            for name in self.CORPORA
        }

    def _gate13(self, projects, turns):
        results = {e.number: e for e in evaluate(turns, projects, {})}
        return results[13]

    def test_the_hosted_outage_is_inconclusive_not_pass(self):
        """
        The exact run: 52 turns, all provider_fatal, zero answered.

        Thirteen turns per corpus across four corpora, none answered. The gate
        must not claim the flow completed.
        """
        turns = [
            _turn(project=name, turn_id=f"T{i}", outcome="provider_fatal")
            for name in self.CORPORA
            for i in range(13)
        ]
        self.assertEqual(len(turns), 52)
        gate = self._gate13(self._projects(completed=True), turns)
        self.assertIs(gate.verdict, Verdict.INCONCLUSIVE)
        self.assertEqual(gate.exercised, 0)
        self.assertEqual(gate.opportunities, 4)
        self.assertIn("provider", gate.note)

    def test_all_four_completing_on_real_answers_passes(self):
        turns = [
            _turn(project=name, turn_id=f"T{i}", outcome="ok")
            for name in self.CORPORA
            for i in range(13)
        ]
        gate = self._gate13(self._projects(completed=True), turns)
        self.assertIs(gate.verdict, Verdict.PASS)
        self.assertEqual(gate.exercised, 4)
        self.assertEqual(gate.passed, 4)

    def test_one_corpus_incomplete_is_inconclusive(self):
        """Partial coverage without a product error is missing evidence."""
        turns = [
            _turn(project=name, turn_id=f"T{i}", outcome="ok")
            for name in self.CORPORA
            for i in range(13)
        ]
        projects = self._projects(only=("mondayos", "cue-app", "sourcingbot"))
        gate = self._gate13(projects, turns)
        self.assertIs(gate.verdict, Verdict.INCONCLUSIVE)
        self.assertEqual(gate.exercised, 3)
        self.assertIn("weatherbot", gate.note)

    def test_a_product_error_fails(self):
        """The only class of failure that is MondayOS's."""
        turns = [
            _turn(project=name, turn_id=f"T{i}", outcome="ok")
            for name in self.CORPORA
            for i in range(13)
        ]
        turns.append(_turn(project="weatherbot", turn_id="E.risk", outcome="product_error"))
        gate = self._gate13(self._projects(), turns)
        self.assertIs(gate.verdict, Verdict.FAIL)
        self.assertTrue(any("E.risk" in f for f in gate.failures))

    def test_a_product_error_outranks_a_provider_outage(self):
        turns = [
            _turn(project=name, turn_id=f"T{i}", outcome="provider_fatal")
            for name in self.CORPORA
            for i in range(13)
        ]
        turns.append(_turn(project="mondayos", turn_id="B.next", outcome="product_error"))
        gate = self._gate13(self._projects(), turns)
        self.assertIs(gate.verdict, Verdict.FAIL)

    def test_no_other_gate_changed_for_a_fully_answered_run(self):
        """This fix must not loosen or tighten anything else."""
        turns = [
            _turn(project=name, turn_id=f"T{i}", outcome="ok")
            for name in self.CORPORA
            for i in range(13)
        ]
        verdicts = {e.number: e.verdict for e in evaluate(turns, self._projects(), {})}
        self.assertIs(verdicts[13], Verdict.PASS)
        for number in (1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 14):
            with self.subTest(gate=number):
                self.assertIsNot(verdicts[number], Verdict.FAIL, "an unrelated gate began failing")


class FailClosedClassificationTests(unittest.TestCase):
    """
    MondayOS refusing its own output is not the provider's fault.

    A hosted Anthropic run recorded fourteen turns as `provider_fatal`, every one
    carrying the same message: `unverified evidence in generated answer`. That is
    MondayOS's fail-closed refusal — the integrity guarantee firing — and the
    harness had no word for it, so `classify()` fell through to "unrecognised
    provider error". The run then reported fourteen outages that never happened
    and dropped those turns from the gates they should have informed.
    """

    ANTHROPIC_RUN_ERROR = "unverified evidence in generated answer"

    def test_the_exact_hosted_error_is_a_product_refusal(self):
        self.assertIs(classify(self.ANTHROPIC_RUN_ERROR), Outcome.PRODUCT_FAIL_CLOSED)

    def test_the_user_facing_refusal_text_is_also_recognised(self):
        body = (
            "MondayOS could not verify the evidence cited in this answer, so it has not been shown."
        )
        self.assertIs(classify(body), Outcome.PRODUCT_FAIL_CLOSED)

    def test_a_refusal_is_not_a_provider_outage(self):
        for outcome in (Outcome.PROVIDER_FATAL, Outcome.PROVIDER_TRANSIENT):
            self.assertIsNot(classify(self.ANTHROPIC_RUN_ERROR), outcome)

    def test_a_refusal_is_not_a_product_error(self):
        """A defect fails a gate; a principled refusal must not."""
        self.assertIsNot(classify(self.ANTHROPIC_RUN_ERROR), Outcome.PRODUCT)

    def test_provider_classification_is_unchanged(self):
        cases = {
            "": Outcome.OK,
            "429 Too Many Requests": Outcome.PROVIDER_TRANSIENT,
            "overloaded_error": Outcome.PROVIDER_TRANSIENT,
            "invalid api key": Outcome.PROVIDER_FATAL,
            "model not found": Outcome.PROVIDER_FATAL,
            "some unknown vendor message": Outcome.PROVIDER_FATAL,
        }
        for error, expected in cases.items():
            with self.subTest(error=error):
                self.assertIs(classify(error), expected)

    def test_a_refusal_mentioning_a_number_is_not_read_as_a_status_code(self):
        """The fail-closed pattern is matched before any provider pattern."""
        error = "unverified evidence in generated answer (429 identifiers checked)"
        self.assertIs(classify(error), Outcome.PRODUCT_FAIL_CLOSED)

    def test_a_refused_turn_still_completes_the_flow(self):
        """Declining to answer is a finished turn, not a gap in the journey."""
        turns = [_turn(project="mondayos", turn_id=f"T{i}", outcome="ok") for i in range(12)] + [
            _turn(project="mondayos", turn_id="F.where", outcome="product_fail_closed")
        ]
        projects = {"mondayos": {"available": True, "completed": True}}
        gate = {e.number: e for e in evaluate(turns, projects, {})}[13]
        self.assertIs(gate.verdict, Verdict.PASS)

    def test_a_refused_turn_is_not_scored_but_is_still_an_opportunity(self):
        """
        It must not be silently removed from the count.

        A refusal produces no answer, so there is nothing to score — but the
        opportunity existed, and hiding it would make a run that refused half its
        turns look identical to one that answered them all.
        """
        turns = [
            _turn(project="mondayos", turn_id=f"T{i}", outcome="product_fail_closed")
            for i in range(4)
        ]
        gate = {e.number: e for e in evaluate(turns, {"mondayos": {"available": True}}, {})}[1]
        self.assertEqual(gate.opportunities, 4)
        self.assertEqual(gate.exercised, 0)
        self.assertIs(gate.verdict, Verdict.INCONCLUSIVE)


class StreamingJourneyTests(unittest.TestCase):
    """
    The streaming path, observed through the production entry point.

    The conversational journey drives `send-message`, which does not stream, so
    the last hosted run could not say anything about buffering. These exercise
    `observe_stream` against a fake Monday so the observation model itself is
    trustworthy before it is pointed at a real provider.
    """

    REAL = "5c44663"
    FAKE = "6d23456"

    class _Monday:
        """A Monday-shaped stub that yields the events `stream_message` yields."""

        def __init__(self, events, persisted=None, stop_reason=""):
            self._events = events
            self._persisted = persisted
            self._stop_reason = stop_reason

        def workspace(self, action, **kw):
            class R:
                pass

            r = R()
            if action == "create-conversation":
                r.data = {"id": "CONV-0001"}
            else:
                r.data = {
                    "messages": [
                        {
                            "role": "assistant",
                            "content": self._persisted or "",
                            "stop_reason": self._stop_reason,
                        }
                    ]
                }
            return r

        def workspace_stream(self, project, conversation_id, content):
            yield from self._events

    def _done(self, content, stop_reason="end_turn", incomplete=False, error=""):
        return {
            "type": "done",
            "message": {
                "content": content,
                "stop_reason": stop_reason,
                "incomplete": incomplete,
                "error": error,
            },
        }

    def test_a_progressive_stream_is_recorded_as_progressive(self):
        events = [
            {"type": "user", "message": {}},
            {"type": "delta", "text": "Recent "},
            {"type": "delta", "text": "work "},
            {"type": "delta", "text": "landed."},
            self._done("Recent work landed."),
        ]
        obs = observe_stream(self._Monday(events, persisted="Recent work landed."), "p")
        self.assertTrue(obs.streamed_progressively)
        self.assertEqual(len(obs.deltas), 3)
        self.assertEqual(obs.visible_before_validation, "Recent work landed.")

    def test_a_buffered_turn_releases_one_delta_after_validation(self):
        """The guarantee: an evidence-bearing answer arrives whole, once."""
        body = f"Recent work landed in `{self.REAL}`."
        events = [
            {"type": "user", "message": {}},
            {"type": "delta", "text": body},
            self._done(body),
        ]
        obs = observe_stream(self._Monday(events, persisted=body), "p")
        self.assertFalse(obs.streamed_progressively)
        self.assertEqual(obs.visible_before_validation, body)
        self.assertTrue(obs.persisted_matches_shown)

    def test_no_unverified_identifier_appears_in_any_delta(self):
        """A fail-closed stream shows the refusal, never the fabrication."""
        refusal = "MondayOS could not verify the evidence cited in this answer."
        events = [
            {"type": "user", "message": {}},
            {"type": "delta", "text": refusal},
            self._done(refusal, incomplete=True, error="unverified evidence in generated answer"),
        ]
        obs = observe_stream(self._Monday(events, persisted=refusal), "p")
        self.assertNotIn(self.FAKE, obs.visible_before_validation)
        self.assertNotIn(self.FAKE, obs.final_content)
        self.assertTrue(obs.incomplete)
        self.assertIs(classify(obs.error), Outcome.PRODUCT_FAIL_CLOSED)

    def test_stop_reason_survives_the_streaming_path(self):
        events = [
            {"type": "delta", "text": "x"},
            self._done("x", stop_reason="max_tokens", incomplete=True),
        ]
        obs = observe_stream(self._Monday(events, persisted="x"), "p")
        self.assertEqual(obs.stop_reason, "max_tokens")
        self.assertTrue(obs.incomplete)

    def test_persisted_answer_must_match_what_was_shown(self):
        events = [{"type": "delta", "text": "shown"}, self._done("shown")]
        good = observe_stream(self._Monday(events, persisted="shown"), "p")
        self.assertTrue(good.persisted_matches_shown)
        bad = observe_stream(self._Monday(events, persisted="something else"), "p")
        self.assertFalse(bad.persisted_matches_shown)

    def test_the_observation_records_no_hidden_reasoning(self):
        events = [{"type": "delta", "text": "x"}, self._done("x")]
        keys = observe_stream(self._Monday(events, persisted="x"), "p").to_dict()
        for forbidden in ("prompt", "snapshot", "reasoning", "candidates"):
            self.assertNotIn(forbidden, keys)


class ScoreExtractionTests(unittest.TestCase):
    """
    A number near a score's name is not necessarily that score.

    A hosted run produced twelve gate-6 violations. With the full answer text
    retained, every one adjudicated as a harness artifact rather than a model
    claim. The clearest: MondayOS computed `execution_risk=0.43` and the model
    wrote

        **Projects concentrates risk** — holding 36% of the codebase

    which put `36%` within twenty-four characters of "risk", so the harness
    recorded an execution risk of 0.36 the model never asserted.

    The discriminator is what follows the number. A share says what it is a share
    *of*; a score does not.
    """

    def test_a_share_of_the_codebase_is_not_an_execution_risk(self):
        text = "**Projects concentrates risk** — holding 36% of the codebase, a regression there"
        self.assertEqual(quoted_scores(text), {})

    def test_shares_of_other_things_are_also_ignored(self):
        for text in (
            "risk covers 15% of the tests",
            "confidence in 80% of the modules",
            "evidence from 20% of the files",
        ):
            with self.subTest(text=text):
                self.assertEqual(quoted_scores(text), {})

    def test_genuine_scores_are_still_extracted(self):
        cases = {
            "Execution Risk **Moderate (43%)** for integrations": ("execution_risk", 0.43),
            "the evidence strength: **94%, high**": ("evidence_strength", 0.94),
            "Confidence: 66% (medium)": ("confidence", 0.66),
            "confidence is 0.47 overall": ("confidence", 0.47),
        }
        for text, (name, value) in cases.items():
            with self.subTest(text=text):
                self.assertEqual(quoted_scores(text).get(name), value)

    def test_an_unsupported_score_still_fails_gate_6(self):
        """
        The failure condition must survive both fixes.

        A model asserting a reasoning score MondayOS never computed is a real
        product-integrity problem. Narrowing the extractor must not make gate 6
        incapable of saying so -- and neither must moving the verdict to the
        product, which is what actually decides it now.
        """
        turn = _turn(
            turn_id="B.next",
            score_validation=_validated(claims=3, unsupported=1, values=[0.99]),
        )
        gate = {e.number: e for e in evaluate([turn], {"demo": {"available": True}}, {})}[6]
        self.assertIs(gate.verdict, Verdict.FAIL)
        self.assertTrue(any("0.99" in f for f in gate.failures))

    def test_a_score_the_product_verified_passes_gate_6(self):
        turn = _turn(turn_id="B.next", score_validation=_validated(claims=3))
        gate = {e.number: e for e in evaluate([turn], {"demo": {"available": True}}, {})}[6]
        self.assertIsNot(gate.verdict, Verdict.FAIL)


class TestScoreAuditCrossCheck(unittest.TestCase):
    """
    The adversarial half of D-7.

    Gate 6's verdict comes from the product. That is right -- one authority for
    the decision -- and it has one blind spot: a validator that silently stops
    seeing claims reports a clean answer, and nothing downstream can tell that
    apart from an answer with no scores in it.

    So the old prose reader is kept, pointed at exactly that question, and its
    disagreements are surfaced rather than resolved. It is not allowed to
    overrule the product, and the product is not allowed to silence it.
    """

    LIVE = {"demo": {"available": True}}

    def _gate6(self, turn) -> Any:
        return {e.number: e for e in evaluate([turn], self.LIVE, CAPABLE)}[6]

    def test_agreement_produces_no_finding(self):
        audit = audit_scores(_validated(claims=1), {"confidence": 0.47})
        self.assertEqual(audit["disagreement"], [])

    def test_a_claim_the_product_never_saw_is_a_disagreement(self):
        audit = audit_scores(_validated(claims=1), {"execution_risk": 0.99})
        self.assertEqual(audit["disagreement"], [{"concept": "execution_risk", "value": 0.99}])

    def test_a_claim_the_product_never_saw_fails_gate_6(self):
        """It is not the model that failed here. It is the instrument."""
        turn = _turn(
            score_validation=_validated(claims=1),
            score_audit=audit_scores(_validated(claims=1), {"execution_risk": 0.99}),
        )
        gate = self._gate6(turn)
        self.assertIs(gate.verdict, Verdict.FAIL)
        self.assertIn("AUDIT DISAGREEMENT", " ".join(gate.failures))

    def test_a_product_that_validated_nothing_still_gets_audited(self):
        """No metadata is not a licence. An unchecked number is the worst case."""
        audit = audit_scores({}, {"confidence": 0.76})
        self.assertEqual(audit["disagreement"], [{"concept": "confidence", "value": 0.76}])
        self.assertFalse(audit["product_checked"])

    def test_the_d7_sentence_does_not_override_the_product(self):
        """
        `## Confidence and Evidence` put the word "Evidence" within the old
        window of the *confidence* value, so the independent reader calls 0.61 an
        evidence strength. The product saw 0.61 and cleared it.

        Matching is by value, because the question is whether the product saw the
        number -- not whether both readers labelled it the same way. A
        disagreement about the label is not evidence that a claim was missed, and
        raising one would put D-7's own defect back in the gate.
        """
        answer = "## Confidence and Evidence\n\n**Confidence:** 0.61\n**Evidence Strength:** 0.88"
        independent = quoted_scores(answer)
        self.assertEqual(independent.get("evidence_strength"), 0.61)

        product = {
            "checked": True,
            "claims_found": 2,
            "verified": 2,
            "unsupported": 0,
            "claims": [
                {"concept": "confidence", "value": 0.61, "text": "Confidence:** 0.61"},
                {
                    "concept": "evidence_strength",
                    "value": 0.88,
                    "text": "Evidence Strength:** 0.88",
                },
            ],
            "unsupported_claims": [],
        }
        audit = audit_scores(product, independent)
        self.assertEqual(audit["disagreement"], [])
        self.assertIs(
            self._gate6(_turn(score_validation=product, score_audit=audit)).verdict, Verdict.PASS
        )

    def test_the_d4_sentence_is_not_a_score_to_either_reader(self):
        answer = "**Projects concentrates risk** -- holding 36% of the codebase in one package."
        self.assertEqual(quoted_scores(answer), {})
        self.assertEqual(
            audit_scores({"checked": True, "claims": []}, quoted_scores(answer))["disagreement"], []
        )

    def test_the_audit_never_resolves_the_disagreement(self):
        """Both readings are reported. Picking a winner is the defect this replaces."""
        audit = audit_scores(_validated(claims=1), {"execution_risk": 0.99})
        self.assertEqual(audit["product_claims"], [0.47])
        self.assertEqual(
            audit["independent_claims"], [{"concept": "execution_risk", "value": 0.99}]
        )


class TestStopReasonIsReadFromTheAssistantMessage(unittest.TestCase):
    """
    D-5. Gate 4 was reading a key off the wrong object.

    `stop_reason` is recorded on the assistant message, and this harness looked
    for it on the reasoning assessment -- which has no such key. Every turn
    therefore recorded `""`, gate 4 reported UNVERIFIABLE on a provider that
    reports a stop reason perfectly well, and two hosted runs could not exercise
    the one gate that catches a truncated answer shown as a finished one.

    Measurement, not product: nothing here changes what MondayOS means by
    `stop_reason`.
    """

    LIVE = {"demo": {"available": True}}

    def _session(self) -> ProjectSession:
        return ProjectSession(
            monday=None,
            project="demo",
            corpus_root=Path("."),
            monday_root=Path("."),
            boundaries=frozenset(),
            discovered=[],
            own_decisions=[],
            own_commits=frozenset(),
            pacing=Pacing(),
        )

    def _record(self, **message) -> TurnRecord:
        record = TurnRecord(
            project="demo", turn_id="B.next", question="q", expect_register="executive"
        )
        payload = {"assistant_message": {"content": "An answer.", **message}, "assessment": {}}
        self._session()._measure(record, payload)
        return record

    def test_a_normal_completion_is_observed(self):
        self.assertEqual(self._record(stop_reason="end_turn").stop_reason, "end_turn")

    def test_truncation_is_observed(self):
        record = self._record(stop_reason="max_tokens", incomplete=True)
        self.assertEqual(record.stop_reason, "max_tokens")
        self.assertTrue(record.incomplete)

    def test_a_provider_that_reports_nothing_records_nothing(self):
        self.assertEqual(self._record().stop_reason, "")

    def test_the_assessment_is_not_where_it_is_looked_for(self):
        """The exact defect: a stop reason on the assessment must not be read."""
        record = TurnRecord(
            project="demo", turn_id="B.next", question="q", expect_register="executive"
        )
        self._session()._measure(
            record,
            {
                "assistant_message": {"content": "a", "stop_reason": "max_tokens"},
                "assessment": {"metadata": {"stop_reason": "end_turn"}},
            },
        )
        self.assertEqual(record.stop_reason, "max_tokens")

    def test_an_observed_truncation_reaches_gate_4(self):
        """The join. Recording it is only useful if the gate then scores it."""
        hidden = _turn(stop_reason=self._record(stop_reason="max_tokens").stop_reason)
        gate = next(g for g in evaluate([hidden], self.LIVE, CAPABLE) if g.number == 4)
        self.assertIs(gate.verdict, Verdict.FAIL, "truncation reported as complete")

    def test_zero_observations_never_pass(self):
        gate = next(g for g in evaluate([_turn()], self.LIVE, CAPABLE) if g.number == 4)
        self.assertIsNot(gate.verdict, Verdict.PASS)


class TestHeadingsAreNotInitiativeClaims(unittest.TestCase):
    """
    Gate 2's false positive. `## Capability Health Assessment` is a section
    label, and the detector read "Health Assessment" out of it as an invented
    capability -- the heading word sits immediately before the title, which is
    exactly the shape a claim has.

    The rule this file has followed throughout: prefer a false negative to a
    false accusation. A heading is never an accusation; a genuinely invented
    initiative still has to be caught.
    """

    KNOWN = ["workspace", "acceptance", "Growth BOT"]

    def _invented(self, answer: str) -> list[str]:
        return named_initiatives(answer, self.KNOWN)["invented"]

    def test_the_exact_hosted_false_positive(self):
        self.assertEqual(self._invented("## Capability Health Assessment\n\nAll healthy."), [])

    def test_headings_at_every_level_are_ignored(self):
        for prefix in ("#", "##", "###", "######"):
            with self.subTest(prefix=prefix):
                self.assertEqual(self._invented(f"{prefix} Capability Health Assessment\n"), [])

    def test_a_bold_section_label_is_ignored(self):
        self.assertEqual(self._invented("**Capability Health Assessment**\n\nAll healthy."), [])

    def test_a_genuine_invented_initiative_is_still_caught(self):
        self.assertEqual(
            self._invented("Quantum Ledger should be the next initiative."), ["Quantum Ledger"]
        )

    def test_a_genuine_invention_below_a_heading_is_still_caught(self):
        answer = "## Capability Health Assessment\n\nQuantum Ledger should be the next initiative."
        self.assertEqual(self._invented(answer), ["Quantum Ledger"])

    def test_an_ordinary_noun_phrase_is_not_an_accusation(self):
        """The cost of the name-leading form: it needs an assertion, not a noun."""
        self.assertEqual(self._invented("Delivery capability improved after the refactor."), [])

    def test_a_quoted_invention_is_still_caught(self):
        self.assertEqual(self._invented('The "Telepathy" capability is at risk.'), ["Telepathy"])

    def test_a_discovered_capability_is_never_invented(self):
        self.assertEqual(self._invented("The workspace capability is healthy."), [])
        self.assertEqual(self._invented("The Growth BOT capability ships next."), [])

    def test_ordinary_prose_claims_nothing(self):
        for answer in (
            "this is the next initiative we discussed",
            "The capability is in good shape.",
            "Delivery capability improved after the refactor.",
        ):
            with self.subTest(answer=answer):
                self.assertEqual(self._invented(answer), [])
