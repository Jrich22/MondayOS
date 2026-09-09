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
        self.assertEqual(cited_decisions("per ADR-17 and adr 002"), ["ADR-002", "ADR-017"])

    def test_quoted_scores_are_read_as_fractions(self):
        found = quoted_scores("Confidence is 72%, and execution risk is 0.30.")
        self.assertEqual(found["confidence"], 0.72)
        self.assertEqual(found["execution_risk"], 0.3)

    def test_an_answer_quoting_nothing_yields_nothing(self):
        """Not reciting the numbers is good writing, not a failure."""
        self.assertEqual(quoted_scores("We should harden the scheduler first."), {})

    def test_invention_is_only_claimed_for_a_phrase_presented_as_one(self):
        known = ["Billing", "Scheduling"]
        self.assertEqual(named_initiatives("Billing is going well.", known)["invented"], [])
        found = named_initiatives('The "Telepathy" initiative is at risk.', known)
        self.assertEqual(found["invented"], ["Telepathy"])

    def test_a_known_initiative_named_as_one_is_not_invention(self):
        found = named_initiatives('The "Billing" capability is healthy.', ["Billing"])
        self.assertEqual(found["invented"], [])


class TestGates(unittest.TestCase):
    """Every gate, fired at a violation and at a clean run."""

    CLEAN_PROJECTS = {
        "demo": {
            "available": True,
            "completed": True,
            "recommendation_key": "k1",
            "hidden_reasoning_keys": [],
            "determinism": {},
        }
    }

    def _verdict(self, number: int, turns, projects, caps=CAPABLE):
        return next(g for g in evaluate(turns, projects, caps) if g.number == number)

    def test_all_fourteen_gates_are_reported(self):
        results = evaluate([_turn()], self.CLEAN_PROJECTS, CAPABLE)
        self.assertEqual({g.number for g in results}, {n for n, _ in GATES})

    def test_gate_1_cross_project_citation(self):
        bad = _turn(citations={**_turn().citations, "outside_boundary": ["projects/x/a.ts"]})
        self.assertIs(self._verdict(1, [bad], self.CLEAN_PROJECTS).verdict, Verdict.FAILED)
        self.assertIs(self._verdict(1, [_turn()], self.CLEAN_PROJECTS).verdict, Verdict.PASSED)

    def test_gate_2_invented_initiative(self):
        bad = _turn(initiatives={"named": ["Telepathy"], "invented": ["Telepathy"]})
        self.assertIs(self._verdict(2, [bad], self.CLEAN_PROJECTS).verdict, Verdict.FAILED)

    def test_gate_3_lookup_answered_strategically(self):
        bad = _turn(turn_id="F.where", expect_register="grounded", observed_register="executive")
        self.assertIs(self._verdict(3, [bad], self.CLEAN_PROJECTS).verdict, Verdict.FAILED)

    def test_gate_4_is_unverifiable_on_a_silent_provider(self):
        """The point of the whole capability design: unanswerable is not passed."""
        result = self._verdict(4, [_turn()], self.CLEAN_PROJECTS, SILENT)
        self.assertIs(result.verdict, Verdict.UNVERIFIABLE)
        self.assertIn("stop reason", result.reason)

    def test_gate_4_fails_when_truncation_was_hidden(self):
        bad = _turn(stop_reason="max_tokens", incomplete=False)
        self.assertIs(self._verdict(4, [bad], self.CLEAN_PROJECTS).verdict, Verdict.FAILED)

    def test_gate_4_passes_when_truncation_was_reported(self):
        good = _turn(stop_reason="max_tokens", incomplete=True)
        self.assertIs(self._verdict(4, [good], self.CLEAN_PROJECTS).verdict, Verdict.PASSED)

    def test_gate_5_a_follow_up_that_re_decided(self):
        bad = _turn(turn_id="C.evidence", observed_register="continuation", recommendation_key="k2")
        self.assertIs(self._verdict(5, [bad], self.CLEAN_PROJECTS).verdict, Verdict.FAILED)

    def test_gate_6_a_quoted_score_that_disagrees(self):
        bad = _turn(scores={"confidence": 0.64}, quoted_scores={"confidence": 0.95})
        self.assertIs(self._verdict(6, [bad], self.CLEAN_PROJECTS).verdict, Verdict.FAILED)

    def test_gate_6_tolerates_rounding(self):
        good = _turn(scores={"confidence": 0.64}, quoted_scores={"confidence": 0.65})
        self.assertIs(self._verdict(6, [good], self.CLEAN_PROJECTS).verdict, Verdict.PASSED)

    def test_gates_7_and_8_are_the_two_halves_of_say_more(self):
        cold_bad = _turn(
            turn_id="I.say-more-cold", expect_register="grounded", observed_register="executive"
        )
        self.assertIs(self._verdict(7, [cold_bad], self.CLEAN_PROJECTS).verdict, Verdict.FAILED)
        warm_bad = _turn(
            turn_id="I.say-more-warm", expect_register="continuation", observed_register="grounded"
        )
        self.assertIs(self._verdict(8, [warm_bad], self.CLEAN_PROJECTS).verdict, Verdict.FAILED)

    def test_gate_9_foreign_history(self):
        bad = _turn(foreign_history=["abc123 another project's commit"])
        self.assertIs(self._verdict(9, [bad], self.CLEAN_PROJECTS).verdict, Verdict.FAILED)

    def test_gate_10_invented_decision(self):
        bad = _turn(invented_decisions=["ADR-999"])
        self.assertIs(self._verdict(10, [bad], self.CLEAN_PROJECTS).verdict, Verdict.FAILED)

    def test_gate_11_invalid_line(self):
        bad = _turn(citations={**_turn().citations, "invalid_lines": ["a.py:900"]})
        self.assertIs(self._verdict(11, [bad], self.CLEAN_PROJECTS).verdict, Verdict.FAILED)

    def test_gate_12_hidden_reasoning_persisted(self):
        projects = {"demo": {**self.CLEAN_PROJECTS["demo"], "hidden_reasoning_keys": ["prompt"]}}
        self.assertIs(self._verdict(12, [_turn()], projects).verdict, Verdict.FAILED)

    def test_gate_13_incomplete_corpus(self):
        projects = {"demo": {**self.CLEAN_PROJECTS["demo"], "completed": False}}
        self.assertIs(self._verdict(13, [_turn()], projects).verdict, Verdict.FAILED)

    def test_gate_14_recommendation_identity_must_be_stable(self):
        projects = {
            "demo": {
                **self.CLEAN_PROJECTS["demo"],
                "determinism": {"recommendation_key": ("k1", "k2")},
            }
        }
        self.assertIs(self._verdict(14, [_turn()], projects).verdict, Verdict.FAILED)
        same = {
            "demo": {
                **self.CLEAN_PROJECTS["demo"],
                "determinism": {"recommendation_key": ("k1", "k1")},
            }
        }
        self.assertIs(self._verdict(14, [_turn()], same).verdict, Verdict.PASSED)

    def test_a_provider_failure_never_fails_a_gate(self):
        """The load-bearing rule: someone else's outage is not a defect here."""
        broken = _turn(
            outcome="provider_transient",
            error="529 overloaded",
            observed_register="",
            initiatives={"named": [], "invented": ["Ghost"]},
        )
        results = evaluate([broken], self.CLEAN_PROJECTS, CAPABLE)
        self.assertFalse([g for g in results if g.verdict is Verdict.FAILED])

    def test_overall_verdicts(self):
        clean = evaluate([_turn()], self.CLEAN_PROJECTS, CAPABLE)
        self.assertIn(overall(clean), ("READY", "READY WITH KNOWN LIMITATIONS"))
        silent = evaluate([_turn()], self.CLEAN_PROJECTS, SILENT)
        self.assertEqual(overall(silent), "READY WITH KNOWN LIMITATIONS")
        bad = _turn(invented_decisions=["ADR-999"])
        self.assertEqual(overall(evaluate([bad], self.CLEAN_PROJECTS, CAPABLE)), "NOT READY")


class TestReport(unittest.TestCase):
    def _report(self, caps=CAPABLE) -> AcceptanceReport:
        report = AcceptanceReport(provider={"name": "test", "model": "m"}, started_at="now")
        report.projects = dict(TestGates.CLEAN_PROJECTS)
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
