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

from acceptance.gates import GATES, Verdict, evaluate, overall
from acceptance.observe import check_citations, cited_decisions, named_initiatives, quoted_scores
from acceptance.pacing import Outcome, Pacing, classify
from acceptance.report import AcceptanceReport, Defect
from acceptance.session import TurnRecord, isolated_root

CAPABLE = {"reports_stop_reason": True}
SILENT = {"reports_stop_reason": False}


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

    def test_gate_6_fails_on_a_number_the_assessment_never_computed(self):
        bad = _turn(computed_values=[0.64, 0.91], quoted_scores={"confidence": 0.12})
        self.assertIs(self._gate(6, [bad], self.LIVE).verdict, Verdict.FAIL)

    def test_gate_6_compares_a_continuation_against_the_decision_it_continues(self):
        """
        RC1/H-7. The responder hands a continuation the prior decision's scores
        "exactly as they were shown to the user" and forbids inventing others.

        So the persisted record is authoritative, not the thin assessment a
        continuation computes. Comparing against the latter reported eight exact
        recitations as inventions -- MondayOS quoting its own numbers back.
        """
        turn = _turn(
            turn_id="D.change-mind",
            continuation=True,
            computed_values=[0.88],
            persisted_values=[0.47, 0.94, 0.43],
            quoted_scores={"confidence": 0.47, "evidence_strength": 0.94},
        )
        gate = self._gate(6, [turn], self.LIVE)
        self.assertIs(gate.verdict, Verdict.PASS)
        self.assertEqual(gate.exercised, 2)

    def test_gate_6_still_fails_a_continuation_that_invents_a_number(self):
        """The gate keeps its teeth on continuations too."""
        turn = _turn(
            turn_id="D.change-mind",
            continuation=True,
            computed_values=[0.88],
            persisted_values=[0.47, 0.94],
            quoted_scores={"confidence": 0.11},
        )
        self.assertIs(self._gate(6, [turn], self.LIVE).verdict, Verdict.FAIL)

    def test_gate_6_does_not_use_persisted_scores_on_a_fresh_turn(self):
        """A fresh executive turn computes its own; the stored decision is not licence."""
        turn = _turn(
            turn_id="B.next",
            continuation=False,
            computed_values=[0.60],
            persisted_values=[0.11],
            quoted_scores={"confidence": 0.11},
        )
        self.assertIs(self._gate(6, [turn], self.LIVE).verdict, Verdict.FAIL)

    def test_gate_6_accepts_any_value_the_assessment_computed(self):
        """
        RC1/H-7. An answer may quote the confidence of an inference it is
        explaining, not only the winning recommendation's.

        sourcingBOT's assessment computed eight distinct values; the harness knew
        one of them and reported eleven faithful recitations as inventions.
        """
        good = _turn(
            computed_values=[0.10, 0.34, 0.52, 0.66, 0.94],
            quoted_scores={"confidence": 0.66, "evidence_strength": 0.94},
        )
        gate = self._gate(6, [good], self.LIVE)
        self.assertIs(gate.verdict, Verdict.PASS)
        self.assertEqual(gate.exercised, 2)

    def test_gate_6_never_compares_against_an_assessment_that_computed_nothing(self):
        """A grounded turn has no recommendation, so 0.0 is not a value to disagree with."""
        grounded = _turn(computed_values=[], quoted_scores={"confidence": 0.92})
        gate = self._gate(6, [grounded], self.LIVE)
        self.assertEqual(gate.exercised, 0)
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

    def test_gate_13_fails_when_a_corpus_did_not_finish(self):
        projects = {"demo": {**self.LIVE["demo"], "completed": False}}
        self.assertIs(self._gate(13, [_turn()], projects).verdict, Verdict.FAIL)

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
                quoted_scores={"confidence": 0.6},
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
