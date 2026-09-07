"""
Tests for the reasoning layer.

Two properties carry most of the weight here, and neither is about output quality.

**A claim's kind must survive the whole pipeline.** The layer is only safe because
an inference is labelled as one; if a recommendation could reach a reader wearing a
fact's clothes, every guarantee this package makes would be cosmetic. Several tests
exist purely to assert that separation holds end to end.

**Confidence must be computed, never asserted.** The tests below check the *shape*
of the scoring — that corroboration across independent source kinds raises a score,
that inference chains lower it, that contradiction lowers it hard, that no amount of
evidence lets a judgement outrank a reading. That is what makes the number mean
something. A test that merely pinned specific percentages would lock in today's
constants and check nothing.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from intelligence.evidence import Citation, CitationKind, Evidence
from intelligence.graph import build as build_graph
from intelligence.index import build as build_index
from intelligence.models import NodeKind
from reasoning import gaps as gap_analysis
from reasoning import recommend
from reasoning.confidence import for_recommendation, score
from reasoning.engine import ReasoningEngine
from reasoning.executive import Topic, route
from reasoning.inference import infer
from reasoning.models import Assessment, Band, Claim, ClaimKind, Confidence, Gap, Mode


def _evidence(*kinds: CitationKind) -> Evidence:
    evidence = Evidence()
    for i, kind in enumerate(kinds):
        evidence.add(Citation(kind=kind, reference=f"ref-{i}", path=f"p{i}.py", because="test"))
    return evidence


class TestConfidence(unittest.TestCase):
    def test_no_evidence_scores_near_zero_whatever_the_claim(self):
        """The case the number exists to catch: a confident guess."""
        for kind in ClaimKind:
            self.assertLess(score(kind, Evidence()).score, 0.1)

    def test_independent_source_kinds_raise_the_score(self):
        one = score(ClaimKind.FACT, _evidence(CitationKind.FILE))
        three = score(
            ClaimKind.FACT,
            _evidence(CitationKind.FILE, CitationKind.TEST, CitationKind.DECISION),
        )
        self.assertGreater(three.score, one.score)

    def test_corroboration_beats_volume(self):
        """
        Ten files agreeing is closer to one source than to ten.

        This is the central claim of the scoring module, so it is asserted
        directly rather than left implied by the constants.
        """
        many_same = score(ClaimKind.FACT, _evidence(*([CitationKind.FILE] * 10)))
        few_varied = score(
            ClaimKind.FACT,
            _evidence(CitationKind.FILE, CitationKind.TEST, CitationKind.DECISION),
        )
        self.assertGreater(few_varied.score, many_same.score)

    def test_longer_inference_chains_score_lower(self):
        near = score(ClaimKind.INFERENCE, _evidence(CitationKind.FILE), steps=1)
        far = score(ClaimKind.INFERENCE, _evidence(CitationKind.FILE), steps=4)
        self.assertGreater(near.score, far.score)

    def test_contradiction_is_expensive(self):
        clean = score(ClaimKind.FACT, _evidence(CitationKind.FILE, CitationKind.TEST))
        disputed = score(
            ClaimKind.FACT,
            _evidence(CitationKind.FILE, CitationKind.TEST),
            contradictions=1,
        )
        self.assertGreater(clean.score - disputed.score, 0.2)

    def test_a_judgement_never_outranks_a_reading(self):
        """
        A recommendation is capped below a fact no matter how well evidenced.

        Recommendations weigh evidence against goals, so more evidence does not
        make one true. The cap is explicit rather than emergent.
        """
        rich = _evidence(*list(CitationKind))
        self.assertLessEqual(for_recommendation(rich, inference_count=1).score, 0.82)
        self.assertGreater(score(ClaimKind.FACT, rich).score, 0.82)

    def test_gap_driven_advice_scores_below_evidence_driven(self):
        present = for_recommendation(_evidence(CitationKind.FILE), 1, gap_driven=False)
        absent = for_recommendation(_evidence(CitationKind.FILE), 1, gap_driven=True)
        self.assertGreater(present.score, absent.score)

    def test_every_score_explains_itself(self):
        """A percentage nobody can argue with is worse than no percentage."""
        result = score(ClaimKind.INFERENCE, _evidence(CitationKind.FILE, CitationKind.TEST))
        self.assertTrue(result.because)
        self.assertTrue(all(r.strip() for r in result.because))

    def test_scores_stay_in_range(self):
        wild = score(ClaimKind.INFERENCE, _evidence(CitationKind.FILE), steps=99, contradictions=99)
        self.assertGreaterEqual(wild.score, 0.0)
        self.assertLessEqual(Confidence(score=5.0).score, 1.0)

    def test_bands_track_scores(self):
        self.assertIs(Confidence(0.9).band, Band.HIGH)
        self.assertIs(Confidence(0.5).band, Band.MEDIUM)
        self.assertIs(Confidence(0.1).band, Band.LOW)


class TestExecutiveRouting(unittest.TestCase):
    STRATEGIC = [
        ("What should we build next?", Topic.NEXT_WORK),
        ("What should Increment 4 be?", Topic.NEXT_WORK),
        ("What's missing?", Topic.GAPS),
        ("What is our biggest gap?", Topic.GAPS),
        ("What worries you?", Topic.RISKS),
        ("What is our biggest technical risk?", Topic.RISKS),
        ("Are we ready for a demo?", Topic.READINESS),
        ("What would an investor ask?", Topic.INVESTOR),
        ("How should we prioritise?", Topic.PRIORITIES),
        # Comparative and indirect phrasings. The superlative-adjective patterns
        # miss all of these, and every one is a question a founder actually asks.
        ("Which initiative is most at risk and why?", Topic.RISKS),
        ("What are you least confident about?", Topic.RISKS),
        ("What are we neglecting?", Topic.GAPS),
        ("What is the safest high-value thing we could do next?", Topic.NEXT_WORK),
        ("What are the three most important initiatives right now?", Topic.PRIORITIES),
    ]

    LOOKUPS = [
        "Where is the responder implemented?",
        "What did we build in the last increment?",
        "When was ADR-017 accepted?",
        "Who wrote the context engine?",
        "How does the budget work?",
        # Named explicitly as questions that must never be forced into the
        # executive register. Widening the strategic patterns is exactly the
        # change that would break these, so they are pinned.
        "Where is ContextEngine implemented?",
        "Show every reference to WorkspaceService.",
        "What file owns provider routing?",
        "Where is WorkspaceService implemented?",
    ]

    def test_strategic_questions_route_to_the_right_topic(self):
        for question, topic in self.STRATEGIC:
            with self.subTest(question=question):
                routing = route(question)
                self.assertTrue(routing.executive, question)
                self.assertIs(routing.topic, topic)

    def test_lookups_stay_grounded(self):
        """
        A false positive is worse than a false negative.

        Answering "where is X" with strategic advice is useless; missing a
        strategic question merely returns the grounded answer MondayOS already
        gave, which is survivable.
        """
        for question in self.LOOKUPS:
            with self.subTest(question=question):
                self.assertFalse(route(question).executive, question)

    def test_a_question_that_is_both_routes_strategic(self):
        routing = route("Where should we focus next?")
        self.assertTrue(routing.executive)

    def test_empty_input_is_not_strategic(self):
        self.assertFalse(route("").executive)
        self.assertFalse(route("   ").executive)

    def test_routing_always_states_a_reason(self):
        for question, _ in self.STRATEGIC:
            self.assertTrue(route(question).reason)


class TestAssessment(unittest.TestCase):
    def _claim(self, kind: ClaimKind, statement: str, conf: float) -> Claim:
        return Claim(
            kind=kind,
            statement=statement,
            derivation="test rule",
            confidence=Confidence(conf, because=("test",)),
            evidence=_evidence(CitationKind.FILE),
        )

    def test_overall_is_limited_by_the_weakest_recommendation(self):
        """
        Advice is acted on as a unit, so the weakest link is the real risk.

        Averaging would hide a thinly-supported recommendation behind two solid
        ones — precisely the entry the reader most needs flagged.
        """
        assessment = Assessment(question="q")
        for conf in (0.8, 0.75, 0.3):
            assessment.recommendations.append(
                recommend.Recommendation(
                    statement="do a thing",
                    rationale="because",
                    confidence=Confidence(conf, because=("test",)),
                )
            )
        self.assertAlmostEqual(assessment.overall.score, 0.3, places=6)

    def test_overall_without_recommendations_falls_back_to_facts(self):
        assessment = Assessment(question="q")
        assessment.facts = [
            self._claim(ClaimKind.FACT, "a", 0.9),
            self._claim(ClaimKind.FACT, "b", 0.7),
        ]
        self.assertAlmostEqual(assessment.overall.score, 0.8, places=6)

    def test_an_empty_assessment_is_honest_rather_than_confident(self):
        empty = Assessment(question="q")
        self.assertEqual(empty.overall.score, 0.0)
        self.assertFalse(empty.has_reasoning)
        self.assertEqual(empty.render(), "")

    def test_render_labels_every_claim_kind_distinctly(self):
        """
        The separation must be visible in the text handed to the model.

        A model that simply follows the headings cannot promote an inference into
        a fact, which is the failure this layer is built to prevent.
        """
        assessment = Assessment(question="q", mode=Mode.EXECUTIVE)
        assessment.facts = [self._claim(ClaimKind.FACT, "a fact", 0.9)]
        assessment.inferences = [self._claim(ClaimKind.INFERENCE, "an inference", 0.5)]
        assessment.recommendations = [
            recommend.Recommendation(
                statement="a recommendation",
                rationale="because",
                confidence=Confidence(0.6, because=("test",)),
            )
        ]
        assessment.gaps = [Gap(subject="s", missing="m", why_it_matters="w", suggested_task="t")]
        text = assessment.render()
        self.assertIn("Established facts", text)
        self.assertIn("Inferences", text)
        self.assertIn("Recommendations", text)
        self.assertIn("Gaps", text)
        # Each statement appears under its own heading, in kind order.
        self.assertLess(text.index("a fact"), text.index("an inference"))
        self.assertLess(text.index("an inference"), text.index("a recommendation"))

    def test_render_carries_confidence_for_every_inference(self):
        assessment = Assessment(question="q")
        assessment.inferences = [self._claim(ClaimKind.INFERENCE, "x", 0.5)]
        self.assertIn("confidence:", assessment.render())

    def test_serialisation_preserves_the_kind_distinction(self):
        assessment = Assessment(question="q", mode=Mode.EXECUTIVE)
        assessment.facts = [self._claim(ClaimKind.FACT, "a fact", 0.9)]
        assessment.inferences = [self._claim(ClaimKind.INFERENCE, "an inference", 0.4)]
        data = assessment.to_dict()
        self.assertEqual(data["facts"][0]["kind"], "fact")
        self.assertEqual(data["inferences"][0]["kind"], "inference")
        self.assertEqual(data["mode"], "executive")


class TestGaps(unittest.TestCase):
    def test_a_gap_always_proposes_work(self):
        """
        The behaviour this package replaces is reporting an absence and stopping.

        Enforced on the type rather than left to whoever writes the next rule.
        """
        gap = Gap(subject="s", missing="m", why_it_matters="w", suggested_task="do the thing")
        self.assertTrue(gap.suggested_task)
        self.assertTrue(gap.why_it_matters)

    def test_work_items_are_proposed_and_never_created(self):
        """
        A reasoning layer that silently files tasks is one people stop asking
        questions of. The read half is safe on every turn; the write half needs
        approval and is not in this increment.
        """
        items = gap_analysis.as_work_items(
            [Gap(subject="s", missing="m", why_it_matters="w", suggested_task="t", severity=1)]
        )
        self.assertEqual(items[0]["title"], "t")
        self.assertEqual(items[0]["priority"], "P1")
        self.assertEqual(items[0]["source"], "reasoning.gaps")


class TestRecommendations(unittest.TestCase):
    def test_the_leading_recommendation_records_what_it_beat(self):
        """The alternatives considered are most of the value of the advice."""
        from reasoning.facts import ProjectFacts

        facts = ProjectFacts(project="p")
        facts.blocked = ["TASK-1 blocked thing"]
        facts.in_progress = ["TASK-2 running thing"]
        gaps = [
            Gap(subject=f"g{i}", missing="m", why_it_matters="w", suggested_task=f"task {i}")
            for i in range(4)
        ]
        out = recommend.build(Topic.NEXT_WORK, facts, [], gaps)
        self.assertTrue(out)
        self.assertTrue(out[0].alternatives)
        # Every alternative states why it lost. An option listed without a reason
        # makes the analysis look thorough while answering nothing.
        for alternative in out[0].alternatives:
            self.assertTrue(alternative.why_not)
        # Only the leading recommendation carries the rejected list.
        self.assertFalse(any(r.alternatives for r in out[1:]))

    def test_unblocking_outranks_starting_new_work(self):
        from reasoning.facts import ProjectFacts

        facts = ProjectFacts(project="p")
        facts.blocked = ["TASK-1 blocked thing"]
        gaps = [Gap(subject="g", missing="m", why_it_matters="w", suggested_task="new work")]
        out = recommend.build(Topic.NEXT_WORK, facts, [], gaps)
        self.assertIn("Unblock", out[0].statement)

    def test_every_recommendation_states_a_tradeoff(self):
        from reasoning.facts import ProjectFacts

        facts = ProjectFacts(project="p")
        facts.blocked = ["TASK-1 x"]
        facts.in_progress = ["TASK-2 y"]
        out = recommend.build(Topic.NEXT_WORK, facts, [], [])
        self.assertTrue(out)
        for rec in out:
            self.assertTrue(rec.tradeoffs, rec.statement)

    def test_topics_draw_on_different_analysis(self):
        """
        Collapsing topics would answer "what worries you" with a roadmap.
        """
        from reasoning.facts import ProjectFacts

        facts = ProjectFacts(project="p")
        facts.in_progress = ["TASK-2 running thing"]
        next_work = recommend.build(Topic.NEXT_WORK, facts, [], [])
        risks = recommend.build(Topic.RISKS, facts, [], [])
        self.assertTrue(next_work)
        # RISKS does not draw on in-progress work, so it stays empty here.
        self.assertFalse(risks)

    def test_no_candidates_yields_no_advice(self):
        from reasoning.facts import ProjectFacts

        self.assertEqual(recommend.build(Topic.GAPS, ProjectFacts(project="p"), [], []), [])


class TestEngineAgainstARealProject(unittest.TestCase):
    """End-to-end over a small real tree, indexed the way MondayOS indexes."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "core").mkdir()
        for i in range(6):
            (root / "core" / f"mod{i}.py").write_text(
                f'"""Core module {i}. Implements ADR-001."""\n\n\nclass Thing{i}:\n    pass\n'
            )
        (root / "docs").mkdir()
        (root / "docs" / "DECISIONS.md").write_text(
            "## ADR-001: Use a core package\n\n**Status:** Accepted\n\nBecause.\n"
        )
        (root / "README.md").write_text("# Test project\n")
        self.root = root
        self.index = build_index("demo", root, cache_root=root / ".index")
        self.graph = build_graph(self.index, tasks=[], knowledge=[])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _engine(self, tasks: list[dict] | None = None) -> ReasoningEngine:
        return ReasoningEngine(self.index, self.graph, tasks or [])

    def test_a_strategic_question_produces_ranked_advice(self):
        assessment = self._engine().assess("What should we build next?")
        self.assertIs(assessment.mode, Mode.EXECUTIVE)
        self.assertTrue(assessment.has_reasoning)

    def test_a_lookup_with_good_retrieval_gets_no_memo(self):
        """
        A well-retrieved lookup wants a file path, not strategy.
        """
        assessment = self._engine().assess("Where is Thing1 defined?", thin_retrieval=False)
        self.assertIs(assessment.mode, Mode.GROUNDED)
        self.assertFalse(assessment.recommendations)
        self.assertFalse(assessment.inferences)

    def test_a_lookup_with_thin_retrieval_still_reasons(self):
        """
        The replacement for "the context does not contain that".

        When retrieval finds little, the assessment carries inferences and gaps so
        the responder has something real to work from rather than an apology.
        """
        assessment = self._engine().assess("What about deployment?", thin_retrieval=True)
        self.assertIs(assessment.mode, Mode.GROUNDED)
        self.assertTrue(assessment.inferences or assessment.gaps)
        self.assertIn("thin", assessment.mode_reason)

    def test_areas_attribute_the_decisions_their_code_cites(self):
        """
        Regression: every area once reported zero ADRs, so "no recorded decision"
        fired for areas that plainly had one. An inference that fires universally
        is indistinguishable from a bug.
        """
        area = self._engine().facts().areas["core"]
        self.assertIn("ADR-001", area.decisions)

    def test_an_area_with_a_decision_is_not_flagged_as_undecided(self):
        claims = infer(self._engine().facts())
        undecided = [c for c in claims if "no recorded architecture decision" in c.statement]
        self.assertFalse([c for c in undecided if c.statement.startswith("core")])

    def test_facts_are_gathered_once_and_reused(self):
        engine = self._engine()
        self.assertIs(engine.facts(), engine.facts())

    def test_the_engine_is_scoped_to_one_project(self):
        engine = self._engine()
        self.assertEqual(engine.project, "demo")
        self.assertEqual(engine.facts().project, "demo")

    def test_a_broken_rule_costs_its_own_conclusion_not_the_answer(self):
        """A heuristic that raises must never take down a founder's question."""
        from reasoning.inference import Rule

        def explode(_facts):
            raise RuntimeError("boom")

        claims = infer(self._engine().facts(), rules=(Rule("explodes", explode),))
        self.assertEqual(claims, [])

    def test_reasoning_reads_and_never_writes(self):
        """The engine is pure analysis: nothing on disk changes."""
        before = sorted(p.name for p in self.root.iterdir())
        engine = self._engine()
        engine.assess("What should we build next?")
        engine.assess("What is missing?")
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), before)

    def test_the_same_project_reasons_identically_twice(self):
        """Determinism is what makes this testable at all."""
        first = self._engine().assess("What should we build next?").to_dict()
        second = self._engine().assess("What should we build next?").to_dict()
        self.assertEqual(first, second)

    def test_blocked_work_reaches_the_recommendation(self):
        tasks = [
            {"id": "TASK-1", "title": "stuck thing", "status": "blocked", "project": "demo"},
            {"id": "TASK-2", "title": "moving thing", "status": "in-progress", "project": "demo"},
        ]
        assessment = self._engine(tasks).assess("What should we do next?")
        self.assertTrue(assessment.recommendations)
        self.assertIn("Unblock", assessment.recommendations[0].statement)


class TestResponderSeam(unittest.TestCase):
    """
    The wiring between reasoning and generation.

    These are the tests that catch the failure where the whole layer computes
    correctly and then reaches nothing — an assessment built and never passed is
    indistinguishable, from the outside, from not having built one.
    """

    def _assessment(self, mode: Mode) -> Assessment:
        assessment = Assessment(question="q", mode=mode)
        assessment.facts = [
            Claim(
                kind=ClaimKind.FACT,
                statement="a stated fact",
                derivation="read",
                confidence=Confidence(0.9, because=("test",)),
                evidence=_evidence(CitationKind.FILE),
            )
        ]
        assessment.recommendations = [
            recommend.Recommendation(
                statement="do the thing",
                rationale="because",
                confidence=Confidence(0.6, because=("test",)),
            )
        ]
        return assessment

    def test_a_strategic_turn_selects_the_executive_instruction(self):
        from workspace.responder import (
            EXECUTIVE_INSTRUCTION,
            SYSTEM_INSTRUCTION,
            WorkspaceRequest,
        )

        strategic = WorkspaceRequest(
            project="p", message="what next?", assessment=self._assessment(Mode.EXECUTIVE)
        )
        self.assertTrue(strategic.executive)
        self.assertEqual(strategic.instruction(), EXECUTIVE_INSTRUCTION)

        lookup = WorkspaceRequest(
            project="p", message="where is x?", assessment=self._assessment(Mode.GROUNDED)
        )
        self.assertFalse(lookup.executive)
        self.assertEqual(lookup.instruction(), SYSTEM_INSTRUCTION)

    def test_a_turn_with_no_assessment_keeps_the_grounded_instruction(self):
        """Increments 1-3 behaviour survives untouched when reasoning is absent."""
        from workspace.responder import SYSTEM_INSTRUCTION, WorkspaceRequest

        request = WorkspaceRequest(project="p", message="hello")
        self.assertFalse(request.executive)
        self.assertEqual(request.instruction(), SYSTEM_INSTRUCTION)

    def test_the_assessment_reaches_the_rendered_context(self):
        from workspace.responder import WorkspaceRequest

        request = WorkspaceRequest(
            project="p", message="q", assessment=self._assessment(Mode.EXECUTIVE)
        )
        rendered = request.render_context()
        self.assertIn("MondayOS assessment", rendered)
        self.assertIn("a stated fact", rendered)
        self.assertIn("do the thing", rendered)

    def test_the_executive_instruction_forbids_inventing_confidence(self):
        """
        A model-supplied percentage looks identical to a computed one and means
        nothing. If it could appear, the one signal telling a reader how much to
        trust the answer would be silently destroyed.
        """
        from workspace.responder import EXECUTIVE_INSTRUCTION

        lowered = EXECUTIVE_INSTRUCTION.lower()
        self.assertIn("do not invent additional confidence", lowered)
        self.assertIn("computed by", lowered)

    def test_the_grounded_instruction_no_longer_leads_with_absence(self):
        """
        The regression this increment exists to prevent.

        The old instruction told the model to report what the context lacked,
        which is why Monday read as a search engine. Grounding is kept; leading
        with the absence is not.
        """
        from workspace.responder import SYSTEM_INSTRUCTION

        lowered = SYSTEM_INSTRUCTION.lower()
        self.assertIn("do not open by listing what the context lacks", lowered)
        self.assertIn("do not stop", lowered)
        # Grounding itself must survive: inference stays labelled.
        self.assertIn("never present an inference as a documented fact", lowered)


class TestServiceIntegration(unittest.TestCase):
    def test_thin_retrieval_is_detected_from_the_snapshot(self):
        from workspace.context.snapshot import ContextSnapshot, ContextSource
        from workspace.service import _thin

        self.assertTrue(_thin(None))

        from datetime import datetime

        empty = ContextSnapshot(id="s1", project="p", created_at=datetime(2026, 1, 1))
        self.assertTrue(_thin(empty))

        rich = ContextSnapshot(id="s2", project="p", created_at=datetime(2026, 1, 1))
        rich.sources.append(
            ContextSource(
                name="intelligence",
                label="Project intelligence",
                origin="index",
                items=[f"item {i}" for i in range(12)],
            )
        )
        self.assertFalse(_thin(rich))

    def test_a_snapshot_without_retrieved_evidence_counts_as_thin(self):
        """
        Other sources describe the project in general. A question only they can
        reach is one nothing specific was found for, which is exactly when
        reasoning should step in.
        """
        from datetime import datetime

        from workspace.context.snapshot import ContextSnapshot, ContextSource
        from workspace.service import _thin

        snapshot = ContextSnapshot(id="s", project="p", created_at=datetime(2026, 1, 1))
        snapshot.sources.append(
            ContextSource(
                name="tasks",
                label="Tasks",
                origin="TaskManager",
                items=[f"task {i}" for i in range(20)],
            )
        )
        self.assertTrue(_thin(snapshot))

    def test_a_failing_reasoner_never_breaks_a_turn(self):
        """
        A reasoning layer that can take down a conversation is worse than one
        that occasionally has nothing to add.
        """
        from workspace.service import WorkspaceService

        def explode(*_args):
            raise RuntimeError("boom")

        with TemporaryDirectory() as tmp:
            service = WorkspaceService(root=Path(tmp), assess=explode)
            self.assertIsNone(service._assessment("p", "q", "", None))


class TestOptionsAndRisk(unittest.TestCase):
    """
    The three scores, and the alternatives.

    The claim being defended is that evidence strength, recommendation confidence
    and execution risk are genuinely different measures. If they moved together
    there would be no reason to compute three, and a single number would be
    honest. These tests pin the cases where they come apart.
    """

    def _initiative(self, **kw):
        from initiatives.models import Initiative, Member, Progress

        initiative = Initiative(slug="x", name="X")
        initiative.progress = Progress(
            has_code=kw.get("code", True),
            has_tests=kw.get("tests", True),
            has_docs=kw.get("docs", True),
        )
        initiative.members = [
            Member(node_id=f"file:f{i}.py", kind=NodeKind.FILE, label=f"f{i}.py", because="t")
            for i in range(kw.get("size", 3))
        ]
        return initiative

    def test_untested_code_raises_execution_risk(self):
        from reasoning.options import Option, execution_risk

        safe = execution_risk(Option("do", "why", 0.5, initiative=self._initiative(tests=True)))
        risky = execution_risk(Option("do", "why", 0.5, initiative=self._initiative(tests=False)))
        self.assertGreater(risky.score, safe.score)
        self.assertTrue(any("no tests" in r for r in risky.because))

    def test_a_large_surface_raises_execution_risk(self):
        from reasoning.options import Option, execution_risk

        small = execution_risk(Option("do", "w", 0.5, initiative=self._initiative(size=3)))
        large = execution_risk(Option("do", "w", 0.5, initiative=self._initiative(size=80)))
        self.assertGreater(large.score, small.score)

    def test_new_work_carries_less_risk_than_changing_existing_work(self):
        """Building something new cannot regress what already works."""
        from reasoning.options import Option, execution_risk

        existing = execution_risk(Option("do", "w", 0.5, initiative=self._initiative()))
        greenfield = execution_risk(
            Option("do", "w", 0.5, initiative=self._initiative(), greenfield=True)
        )
        self.assertLess(greenfield.score, existing.score)

    def test_risk_runs_the_opposite_way_from_confidence(self):
        """
        Higher is worse, deliberately. A reader scanning a list should never have
        to remember which direction a number runs.
        """
        from reasoning.options import Risk, RiskBand

        self.assertIs(Risk(0.9).band, RiskBand.HIGH)
        self.assertIs(Risk(0.1).band, RiskBand.LOW)

    def test_every_risk_explains_itself(self):
        from reasoning.options import Option, execution_risk

        risk = execution_risk(Option("do", "w", 0.5, initiative=self._initiative(tests=False)))
        self.assertTrue(risk.because)

    def test_strong_evidence_can_accompany_a_risky_change(self):
        """
        The case a single blended score hides.

        Solid evidence that something needs doing says nothing about whether doing
        it will go smoothly, and reporting them as one number is backwards for
        anyone choosing what to start.
        """
        from reasoning.options import Option, RiskBand, execution_risk

        option = Option("Rewrite it", "w", 0.9, initiative=self._initiative(tests=False, size=90))
        strength = score(ClaimKind.FACT, _evidence(*list(CitationKind)))
        risk = execution_risk(option)
        # Well-evidenced and genuinely dangerous at the same time.
        self.assertIs(strength.band, Band.HIGH)
        self.assertIsNot(risk.band, RiskBand.LOW)

    def test_losing_options_become_alternatives_with_reasons(self):
        from reasoning.options import Option, compare

        options = [Option(f"option {i}", "why", 0.9 - i * 0.2) for i in range(5)]
        chosen, alternatives = compare(options, keep=2)
        self.assertEqual([o.statement for o in chosen], ["option 0", "option 1"])
        self.assertTrue(alternatives)
        for alternative in alternatives:
            self.assertTrue(alternative.why_not)
            self.assertNotEqual(alternative.why_not, "lower priority")

    def test_nothing_reads_as_certain(self):
        """
        A displayed 100% invites a reader to stop checking, which is the one
        behaviour the confidence engine exists to prevent.
        """
        from reasoning.confidence import CEILING

        everything = score(ClaimKind.FACT, _evidence(*list(CitationKind)), recent=True)
        self.assertLessEqual(everything.score, CEILING)
        self.assertLess(everything.percent, 100)


class TestInitiativeFirstReasoning(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "billing").mkdir()
        for i in range(6):
            (root / "billing" / f"m{i}.py").write_text(f"class B{i}:\n    pass\n")
        (root / "docs").mkdir()
        (root / "docs" / "BILLING.md").write_text("# Billing\n")
        self.root = root
        self.index = build_index("demo", root, cache_root=root / ".idx")
        self.graph = build_graph(self.index, tasks=[], knowledge=[])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_capabilities_lead_the_assessment(self):
        """
        A reader who stops after the first block should still know how the
        product is doing, which is not recoverable from a list of files.
        """
        engine = ReasoningEngine(self.index, self.graph, [])
        assessment = engine.assess("What should we build next?")
        self.assertTrue(assessment.initiatives)
        rendered = assessment.render()
        self.assertIn("# Capabilities", rendered)
        self.assertLess(rendered.index("# Capabilities"), rendered.index("# Recommendations"))

    def test_the_top_recommendation_is_not_the_least_confident(self):
        """
        Regression: initiative candidates carried no evidence, so the best-ranked
        recommendation scored 5%. A recommendation ranked first and scored lowest
        is not cautious, it is incoherent.
        """
        engine = ReasoningEngine(self.index, self.graph, [])
        recs = engine.assess("What should we build next?").recommendations
        self.assertTrue(recs)
        self.assertGreater(recs[0].strength.score, 0.3)

    def test_recommendations_carry_all_three_measures(self):
        engine = ReasoningEngine(self.index, self.graph, [])
        for rec in engine.assess("What should we build next?").recommendations:
            self.assertIsNotNone(rec.strength)
            self.assertIsNotNone(rec.confidence)
            self.assertIsNotNone(rec.execution_risk)

    def test_a_broken_initiative_layer_costs_only_the_capability_view(self):
        """Reasoning falls back to where it was before, not to an error."""
        engine = ReasoningEngine(self.index, self.graph, [])
        engine._index = None  # type: ignore[assignment]
        engine._initiatives = None
        self.assertEqual(engine.initiatives(), [])

    def test_the_executive_instruction_names_all_eight_sections(self):
        from workspace.responder import EXECUTIVE_INSTRUCTION

        for heading in (
            "Facts",
            "Inferences",
            "Alternatives Considered",
            "Tradeoffs",
            "Recommendation",
            "Evidence Strength",
            "Recommendation Confidence",
            "Execution Risk",
        ):
            self.assertIn(heading, EXECUTIVE_INSTRUCTION)

    def test_the_executive_instruction_forbids_inventing_risk_numbers(self):
        from workspace.responder import EXECUTIVE_INSTRUCTION

        self.assertIn("do not invent additional confidence or risk", EXECUTIVE_INSTRUCTION.lower())


class TestTruncationHonesty(unittest.TestCase):
    """
    A cut-off answer must never be presented as a finished one.

    Regression: the first live run of the eight-section format stopped
    mid-sentence inside its final heading and reported `incomplete: False`. The
    provider was reporting `stop_reason == "max_tokens"` correctly; nothing
    consumed it. A partial answer that claims to be complete is a correctness
    failure, not a cosmetic one — the sections it drops are the last ones, which
    here are exactly the scores that make the advice auditable.
    """

    def _provider(self, stop_reason: str):
        from brain.providers.base import (
            AIProvider,
            ProviderAvailability,
            ProviderChunk,
            ProviderResponse,
        )

        class Fake(AIProvider):
            @property
            def name(self) -> str:
                return "fake"

            @property
            def supports_streaming(self) -> bool:
                return True

            def availability(self) -> ProviderAvailability:
                return ProviderAvailability(available=True, provider="fake")

            def stream(self, prompt, context="", max_tokens=1024, **kw):
                yield ProviderChunk(text="a partial answer that stops mid-")
                yield ProviderChunk(done=True, model="m", provider="fake", stop_reason=stop_reason)

            def ask(self, prompt, context="", max_tokens=1024, **kw):
                return ProviderResponse(content="x", model="m", provider="fake")

            def plan(self, objective, context="", max_tokens=2048, **kw):
                return self.ask(objective)

            def summarize(self, content, max_words=150, **kw):
                return self.ask(content)

            def review(self, content, criteria="", **kw):
                return self.ask(content)

        return Fake()

    def _reply(self, stop_reason: str):
        from workspace.responder import ProviderWorkspaceResponder, WorkspaceRequest

        responder = ProviderWorkspaceResponder(self._provider(stop_reason))
        chunks = list(responder.respond_stream(WorkspaceRequest(project="p", message="q")))
        return chunks[-1].reply

    def test_a_run_cut_off_at_max_tokens_is_marked_incomplete(self):
        reply = self._reply("max_tokens")
        self.assertTrue(reply.incomplete)
        self.assertEqual(reply.metadata.get("stop_reason"), "max_tokens")

    def test_a_run_that_finished_is_not_marked_incomplete(self):
        reply = self._reply("end_turn")
        self.assertFalse(reply.incomplete)

    def test_the_partial_text_is_preserved_not_discarded(self):
        """Whatever arrived is work the user watched arrive."""
        self.assertIn("partial answer", self._reply("max_tokens").content)


class TestTokenBudget(unittest.TestCase):
    def test_a_strategic_turn_gets_a_larger_budget(self):
        """
        The eight-section format does not fit a conversational budget.

        Raised rather than trimming the format: a truncated executive answer
        drops its final sections, and those are evidence strength, confidence and
        execution risk — the parts that make it auditable.
        """
        from workspace.responder import (
            DEFAULT_MAX_TOKENS,
            EXECUTIVE_MAX_TOKENS,
            WorkspaceRequest,
        )

        strategic = WorkspaceRequest(
            project="p", message="q", assessment=Assessment(question="q", mode=Mode.EXECUTIVE)
        )
        lookup = WorkspaceRequest(project="p", message="q")
        self.assertEqual(strategic.token_budget(), EXECUTIVE_MAX_TOKENS)
        self.assertEqual(lookup.token_budget(), DEFAULT_MAX_TOKENS)
        self.assertGreater(EXECUTIVE_MAX_TOKENS, DEFAULT_MAX_TOKENS * 2)

    def test_an_explicit_budget_still_wins(self):
        from workspace.responder import WorkspaceRequest

        request = WorkspaceRequest(
            project="p",
            message="q",
            max_tokens=99,
            assessment=Assessment(question="q", mode=Mode.EXECUTIVE),
        )
        self.assertEqual(request.token_budget(), 99)


class TestDriftIsReportedNotRepaired(unittest.TestCase):
    def test_the_assessment_surfaces_drift_in_its_own_block(self):
        """
        A claim about the record, not about the product.

        The capability may be perfectly healthy and the number describing it
        simply wrong. Folding drift into risks would send someone to fix code
        when what needs fixing is a task status.
        """
        from initiatives.drift import Drift, DriftKind

        assessment = Assessment(question="q", mode=Mode.EXECUTIVE)

        class FakeInitiative:
            name = "Growth BOT"
            health = __import__("initiatives").models.Health.HEALTHY
            drift = [
                Drift(
                    kind=DriftKind.SHIPPED_BUT_BACKLOG,
                    initiative="Growth BOT",
                    implementation="shipped",
                    record="all 8 tasks are still backlog",
                    consequence="recorded progress is understated",
                )
            ]

            def summary_line(self):
                return "Growth BOT: healthy, 0% (0/8 tasks)"

        assessment.initiatives = [FakeInitiative()]
        rendered = assessment.render()
        self.assertIn("Record/reality drift", rendered)
        self.assertIn("has NOT changed any task", rendered)
        self.assertEqual(len(assessment.drift), 1)

    def test_the_executive_instruction_forbids_implying_a_repair(self):
        from workspace.responder import EXECUTIVE_INSTRUCTION

        lowered = EXECUTIVE_INSTRUCTION.lower()
        self.assertIn("record/reality drift", lowered)
        self.assertIn("has not changed any task", lowered)


if __name__ == "__main__":
    unittest.main()
