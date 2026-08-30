"""
Tests for the Growth Brain (increment 6).

The Brain's whole value rests on properties that are easy to lose silently:
determinism, workspace isolation, the refusal to state a conclusion on thin data,
and the separation between a fact, a guess, and an instruction. Those are what
these tests are pointed at.
"""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from growth import GrowthStore
from growth.brain import (
    MIN_SAMPLE_FOR_MEMORY,
    MIN_SAMPLE_FOR_RECOMMENDATION,
    MIN_SAMPLE_FOR_VALIDATION,
    Confidence,
    Evidence,
    Experiment,
    ExperimentApprovalRequiredError,
    ExperimentStatus,
    ExperimentVariable,
    GrowthBrain,
    Hypothesis,
    InsufficientSampleError,
    InvalidRecommendationError,
    InvalidRecommendationTransitionError,
    MarketingMemory,
    MemoryCategory,
    MemoryStatus,
    Recommendation,
    RecommendationStatus,
    Variation,
    evaluate_result,
    propose_experiment,
)
from growth.brain.forecasting import project_at_run_rate
from growth.brain.models import Priority
from growth.brain.scoring import (
    normalize_rate,
    recommendation_priority,
    workspace_health,
)
from growth.events import EventSource, EventType
from monday import Monday, MondayConfig
from monday.project import ProjectRegistry

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


def _root(tmp: str, projects: dict[str, str] | None = None) -> Path:
    root = Path(tmp)
    (root / "config").mkdir(parents=True, exist_ok=True)
    registry = ProjectRegistry(root / "config")
    for name, source in (projects or {"acme": "acme-src"}).items():
        (root / source).mkdir(parents=True, exist_ok=True)
        registry.register(name, root / source, overwrite=True)
    return root


def _evidence(**kw) -> Evidence:
    fields = {
        "observations": ["something was measured"],
        "falsifier": "the measurement does not reproduce",
        "sample_size": 10,
    }
    fields.update(kw)
    return Evidence(**fields)


class BrainCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = _root(self._tmp.name)
        self.store = GrowthStore(self.root)
        self.store.init_workspace("acme")
        self.handle = self.store.open("acme")
        self.events = self.handle.event_store()
        self.brain = GrowthBrain(self.handle, self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _emit(self, event_type: EventType, value: float = 1.0, **kw):
        return self.events.record(
            event_type=event_type,
            source=kw.pop("source", EventSource.SYNTHETIC),
            occurred_at=kw.pop("occurred_at", T0),
            value=value,
            **kw,
        )

    def _populated_campaign(self, achieved: int = 10, target: str = "Generate 50 demos"):
        campaign = self.handle.create_campaign(
            name="Launch",
            objective=target,
            primary_conversion_goal="demo_request",
            start_date=T0 - timedelta(days=10),
            end_date=T0 + timedelta(days=20),
        )
        for _ in range(achieved):
            self._emit(
                EventType.CUSTOM_CONVERSION,
                1,
                campaign=campaign.id,
                platform="linkedin",
                name="demo_request",
            )
        self._emit(EventType.CLICK, 300, campaign=campaign.id, platform="linkedin")
        self._emit(EventType.IMPRESSION, 5000, campaign=campaign.id, platform="linkedin")
        return campaign


# ---------------------------------------------------------------------------
# Record kinds are never conflated
# ---------------------------------------------------------------------------


class TestRecordKinds(BrainCase):
    def test_hypothesis_always_carries_an_unconfirmed_marker(self):
        h = Hypothesis(
            id="HYP-1",
            project="acme",
            statement="carousels do better",
            rationale="thin sample",
            evidence=_evidence(),
        )
        payload = h.to_dict()
        self.assertTrue(payload["unconfirmed"])
        self.assertFalse(payload["confirmed"])
        self.assertIn("UNCONFIRMED", payload["caveat"])
        self.assertEqual(payload["kind"], "hypothesis")

    def test_hypothesis_cannot_be_constructed_as_confirmed(self):
        with self.assertRaises(TypeError):
            Hypothesis(
                id="HYP-1",
                project="acme",
                statement="x",
                rationale="y",
                evidence=_evidence(),
                confirmed=True,
            )

    def test_a_hypothesis_does_not_share_an_observation_shape(self):
        from growth.brain.models import Observation

        obs = Observation(
            id="OBS-1", project="acme", statement="ctr is 3%", metric="ctr", value=0.03
        ).to_dict()
        hyp = Hypothesis(
            id="HYP-1",
            project="acme",
            statement="ctr is 3% because of carousels",
            rationale="r",
            evidence=_evidence(),
        ).to_dict()
        self.assertNotEqual(obs["kind"], hyp["kind"])
        self.assertNotIn("unconfirmed", obs)
        self.assertIn("unconfirmed", hyp)

    def test_confirmed_learning_is_marked_confirmed(self):
        from growth.brain.models import ConfirmedLearning

        payload = ConfirmedLearning(
            id="LRN-1",
            project="acme",
            statement="carousels win",
            evidence=_evidence(),
            experiment_id="EXP-1",
        ).to_dict()
        self.assertTrue(payload["confirmed"])
        self.assertFalse(payload["unconfirmed"])


# ---------------------------------------------------------------------------
# Recommendations require what makes them checkable
# ---------------------------------------------------------------------------


class TestRecommendationIntegrity(unittest.TestCase):
    def _rec(self, **kw) -> Recommendation:
        fields = {
            "id": "REC-1",
            "project": "acme",
            "type": "channel",
            "title": "t",
            "summary": "s",
            "explanation": "e",
            "evidence": _evidence(),
            "confidence": Confidence.MEDIUM,
            "expected_impact": "i",
            "suggested_action": "a",
            "success_metric": "ctr",
            "falsifier": "it does not reproduce",
        }
        fields.update(kw)
        return Recommendation(**fields)

    def test_a_recommendation_without_evidence_is_refused(self):
        with self.assertRaises(InvalidRecommendationError):
            self._rec(evidence=Evidence())

    def test_assumptions_alone_are_not_evidence(self):
        with self.assertRaises(InvalidRecommendationError):
            self._rec(evidence=Evidence(assumptions=["we assumed a lot"]))

    def test_a_recommendation_without_a_falsifier_is_refused(self):
        with self.assertRaises(InvalidRecommendationError):
            self._rec(falsifier="   ")

    def test_a_recommendation_without_a_success_metric_is_refused(self):
        with self.assertRaises(InvalidRecommendationError):
            self._rec(success_metric="")

    def test_a_valid_recommendation_constructs(self):
        self.assertEqual(self._rec().status, RecommendationStatus.PROPOSED)

    def test_lifecycle_transitions(self):
        rec = self._rec()
        rec.apply_transition(RecommendationStatus.ACCEPTED, "human:j", "ok", T0)
        rec.apply_transition(RecommendationStatus.COMPLETED, "human:j", "done", T0)
        self.assertEqual(rec.status, RecommendationStatus.COMPLETED)
        self.assertEqual(len(rec.history), 2)

    def test_illegal_transitions_raise(self):
        rec = self._rec()
        with self.assertRaises(InvalidRecommendationTransitionError):
            rec.apply_transition(RecommendationStatus.COMPLETED, "human:j", "skip", T0)

    def test_a_rejected_recommendation_is_terminal(self):
        rec = self._rec()
        rec.apply_transition(RecommendationStatus.REJECTED, "human:j", "no", T0)
        for target in RecommendationStatus:
            with self.subTest(target=target.value):
                with self.assertRaises(InvalidRecommendationTransitionError):
                    rec.apply_transition(target, "human:j", "again", T0)

    def test_round_trip(self):
        rec = self._rec()
        self.assertEqual(Recommendation.from_dict(rec.to_dict()).to_dict(), rec.to_dict())


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism(BrainCase):
    def test_the_same_state_yields_byte_identical_analysis(self):
        self._populated_campaign()
        first = json.dumps(self.brain.analyze(T0), sort_keys=True)
        second = json.dumps(
            GrowthBrain(self.store.open("acme"), self.root).analyze(T0), sort_keys=True
        )
        self.assertEqual(first, second)

    def test_no_model_is_called(self):
        self._populated_campaign()
        analysis = self.brain.analyze(T0)
        self.assertTrue(analysis["deterministic"])
        self.assertIsNone(analysis["model_used"])

    def test_the_brain_imports_no_provider_or_network_module(self):
        import subprocess

        hits = subprocess.run(
            [
                "grep",
                "-rnE",
                r"^(import|from) (requests|httpx|urllib|socket|subprocess)|"
                r"anthropic|openai|AIProvider",
                "growth/brain/",
            ],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parent.parent,
        ).stdout.strip()
        self.assertEqual(hits, "", f"the Brain reaches outside its workspace: {hits}")

    def test_ids_are_derived_from_content_not_the_clock(self):
        self._populated_campaign()
        early = {o.id for o in self.brain.opportunities(T0)}
        later = {o.id for o in self.brain.opportunities(T0 + timedelta(days=3))}
        self.assertEqual(early, later)

    def test_output_lists_are_sorted(self):
        self._populated_campaign()
        opportunities = self.brain.opportunities(T0)
        self.assertEqual([o.id for o in opportunities], sorted(o.id for o in opportunities))


# ---------------------------------------------------------------------------
# No causal claim without evidence
# ---------------------------------------------------------------------------


class TestEvidenceThreshold(BrainCase):
    def test_a_thin_sample_produces_a_hypothesis_not_a_recommendation(self):
        from growth.brain.opportunity import Opportunity, OpportunityType, Severity
        from growth.brain.recommendations import build

        thin = Opportunity(
            id="OPP-1",
            project="acme",
            type=OpportunityType.CHANNEL,
            severity=Severity.HIGH,
            title="x converts better",
            detail="d",
            evidence=_evidence(sample_size=1),
            sample_size=MIN_SAMPLE_FOR_RECOMMENDATION - 1,
            success_metric="conversion_rate",
            rule="platform/outperform",
        )
        record = build(thin, T0)
        self.assertIsInstance(record, Hypothesis)
        self.assertTrue(record.to_dict()["unconfirmed"])

    def test_a_sufficient_sample_produces_a_recommendation(self):
        from growth.brain.opportunity import Opportunity, OpportunityType, Severity
        from growth.brain.recommendations import build

        solid = Opportunity(
            id="OPP-1",
            project="acme",
            type=OpportunityType.CHANNEL,
            severity=Severity.HIGH,
            title="x converts better",
            detail="d",
            evidence=_evidence(sample_size=20),
            sample_size=20,
            success_metric="conversion_rate",
            recommended_action="do the thing",
            rule="platform/outperform",
        )
        self.assertIsInstance(build(solid, T0), Recommendation)

    def test_every_generated_recommendation_carries_evidence_and_a_falsifier(self):
        self._populated_campaign()
        for rec in self.brain.recommendations(T0):
            with self.subTest(rec=rec.id):
                self.assertFalse(rec.evidence.is_empty())
                self.assertTrue(rec.falsifier.strip())
                self.assertTrue(rec.evidence.assumptions)

    def test_unquantified_upside_is_stated_as_unknown_not_invented(self):
        from growth.brain.opportunity import Opportunity, OpportunityType, Severity
        from growth.brain.recommendations import build

        opportunity = Opportunity(
            id="OPP-1",
            project="acme",
            type=OpportunityType.CHANNEL,
            severity=Severity.MEDIUM,
            title="t",
            detail="d",
            evidence=_evidence(sample_size=20),
            sample_size=20,
            expected_upside=None,
            upside_reason="no measured effect size",
            success_metric="ctr",
            recommended_action="a",
            rule="platform/outperform",
        )
        rec = build(opportunity, T0)
        assert isinstance(rec, Recommendation)
        self.assertIn("Not quantified", rec.expected_impact)
        self.assertIn("no measured effect size", rec.expected_impact)

    def test_synthetic_evidence_caps_priority(self):
        self.assertEqual(recommendation_priority("critical", "high", 100, synthetic=True), "P2")
        self.assertEqual(recommendation_priority("critical", "high", 100, synthetic=False), "P0")

    def test_synthetic_provenance_is_stated_in_the_explanation(self):
        self._populated_campaign()
        for rec in self.brain.recommendations(T0):
            with self.subTest(rec=rec.id):
                self.assertIn("synthetic", rec.explanation.lower())


# ---------------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------------


class TestWorkspaceIsolation(unittest.TestCase):
    def test_a_brain_cannot_read_another_projects_data(self):
        with TemporaryDirectory() as tmp:
            root = _root(tmp, {"alpha": "a", "beta": "b"})
            store = GrowthStore(root)
            store.init_workspace("alpha")
            store.init_workspace("beta")

            alpha = store.open("alpha")
            alpha.create_campaign(name="Alpha launch")
            alpha.create_content(platform="linkedin", copy="alpha only", themes=["secret"])
            for _ in range(20):
                alpha.event_store().record(
                    event_type=EventType.CLICK,
                    source=EventSource.SYNTHETIC,
                    occurred_at=T0,
                    platform="linkedin",
                )

            beta_brain = GrowthBrain(store.open("beta"), root)
            analysis = beta_brain.analyze(T0)
            payload = json.dumps(analysis)

            self.assertEqual(analysis["opportunities"], [])
            self.assertEqual(analysis["recommendations"], [])
            self.assertNotIn("Alpha launch", payload)
            self.assertNotIn("alpha only", payload)
            self.assertNotIn("secret", payload)

    def test_memory_never_crosses_projects(self):
        with TemporaryDirectory() as tmp:
            root = _root(tmp, {"alpha": "a", "beta": "b"})
            store = GrowthStore(root)
            store.init_workspace("alpha")
            store.init_workspace("beta")

            GrowthBrain(store.open("alpha"), root).remember(
                MemoryCategory.CONTENT_OUTCOME, "carousels win here", 12, T0
            )
            beta_memory = GrowthBrain(store.open("beta"), root).memory.all()
            self.assertEqual(beta_memory, [])

    def test_memory_files_are_per_workspace(self):
        with TemporaryDirectory() as tmp:
            root = _root(tmp, {"alpha": "a", "beta": "b"})
            store = GrowthStore(root)
            store.init_workspace("alpha")
            store.init_workspace("beta")
            GrowthBrain(store.open("alpha"), root).remember(
                MemoryCategory.CONTENT_OUTCOME, "x", 5, T0
            )
            self.assertTrue(MarketingMemory(store.open("alpha").path, "alpha").path.exists())
            self.assertFalse(MarketingMemory(store.open("beta").path, "beta").path.exists())


# ---------------------------------------------------------------------------
# Marketing memory
# ---------------------------------------------------------------------------


class TestMarketingMemory(BrainCase):
    def _memory(self) -> MarketingMemory:
        return self.brain.memory

    def test_memory_persists_across_instances(self):
        self._memory().record(MemoryCategory.PLATFORM_OUTCOME, "linkedin converts best", 12, T0)
        reloaded = GrowthBrain(self.store.open("acme"), self.root).memory.all()
        self.assertEqual(len(reloaded), 1)
        self.assertEqual(reloaded[0].statement, "linkedin converts best")

    def test_everything_is_born_tentative(self):
        entry = self._memory().record(MemoryCategory.CONTENT_OUTCOME, "carousels win", 8, T0)
        self.assertIs(entry.status, MemoryStatus.TENTATIVE)
        self.assertFalse(entry.is_citable_as_fact)

    def test_an_anecdote_is_refused(self):
        with self.assertRaises(InsufficientSampleError):
            self._memory().record(
                MemoryCategory.CONTENT_OUTCOME,
                "one post did well",
                MIN_SAMPLE_FOR_MEMORY - 1,
                T0,
            )

    def test_validation_requires_a_real_sample(self):
        entry = self._memory().record(MemoryCategory.CONTENT_OUTCOME, "carousels win", 5, T0)
        with self.assertRaises(InsufficientSampleError):
            self._memory().validate(entry.id, T0, "human:j", "looks right")

    def test_validation_succeeds_with_enough_data(self):
        entry = self._memory().record(
            MemoryCategory.CONTENT_OUTCOME, "carousels win", MIN_SAMPLE_FOR_VALIDATION, T0
        )
        validated = self._memory().validate(entry.id, T0, "human:j", "experiment confirmed")
        self.assertIs(validated.status, MemoryStatus.VALIDATED)
        self.assertTrue(validated.is_citable_as_fact)

    def test_tentative_memory_always_renders_with_its_marker(self):
        entry = self._memory().record(MemoryCategory.CONTENT_OUTCOME, "carousels win", 5, T0)
        self.assertIn("TENTATIVE", entry.render())
        self.assertIn("TENTATIVE", entry.to_dict()["rendered"])

    def test_validated_memory_renders_without_a_caveat(self):
        entry = self._memory().record(
            MemoryCategory.CONTENT_OUTCOME, "carousels win", MIN_SAMPLE_FOR_VALIDATION, T0
        )
        validated = self._memory().validate(entry.id, T0, "human:j", "confirmed")
        self.assertIn("VALIDATED", validated.render())
        self.assertNotIn("TENTATIVE", validated.render())

    def test_invalidated_memory_is_kept_not_deleted(self):
        entry = self._memory().record(MemoryCategory.CONTENT_OUTCOME, "carousels win", 5, T0)
        self._memory().invalidate(entry.id, T0, "human:j", "later data disagreed")
        entries = self._memory().all()
        self.assertEqual(len(entries), 1)
        self.assertIs(entries[0].status, MemoryStatus.INVALIDATED)
        self.assertIn("INVALIDATED", entries[0].render())

    def test_recording_the_same_claim_twice_updates_one_entry(self):
        self._memory().record(MemoryCategory.CONTENT_OUTCOME, "carousels win", 5, T0)
        self._memory().record(MemoryCategory.CONTENT_OUTCOME, "carousels win", 9, T0)
        self.assertEqual(len(self._memory().all()), 1)

    def test_invalidated_memory_is_never_cited(self):
        from growth.brain.opportunity import Opportunity, OpportunityType, Severity
        from growth.brain.recommendations import build

        entry = self._memory().record(
            MemoryCategory.PLATFORM_OUTCOME,
            "linkedin is best",
            5,
            T0,
            metric_affected="conversion_rate",
        )
        self._memory().invalidate(entry.id, T0, "human:j", "contradicted")

        opportunity = Opportunity(
            id="OPP-1",
            project="acme",
            type=OpportunityType.CHANNEL,
            severity=Severity.MEDIUM,
            title="t",
            detail="d",
            evidence=_evidence(sample_size=20),
            sample_size=20,
            success_metric="conversion_rate",
            recommended_action="a",
            rule="platform/outperform",
        )
        rec = build(opportunity, T0, self._memory().all())
        assert isinstance(rec, Recommendation)
        self.assertNotIn(entry.id, rec.evidence.memory_ids)


# ---------------------------------------------------------------------------
# Opportunities
# ---------------------------------------------------------------------------


class TestOpportunities(BrainCase):
    def test_a_behind_campaign_is_detected(self):
        self._populated_campaign(achieved=10, target="Generate 100 demos")
        rules = {o.rule for o in self.brain.opportunities(T0)}
        self.assertIn("campaign/behind", rules)

    def test_an_ahead_campaign_is_detected(self):
        self._populated_campaign(achieved=30, target="Generate 20 demos")
        rules = {o.rule for o in self.brain.opportunities(T0)}
        self.assertIn("campaign/ahead", rules)

    def test_platform_comparison_needs_two_platforms(self):
        from growth.brain.opportunity import detect_platform_performance

        single = [
            {
                "platform": "linkedin",
                "synthetic": True,
                "metrics": {"conversion_rate": {"value": 0.9, "sample_size": 50}},
            }
        ]
        self.assertEqual(detect_platform_performance("acme", single), [])

    def test_platform_outperformance_is_detected(self):
        from growth.brain.opportunity import detect_platform_performance

        rows = [
            {
                "platform": "linkedin",
                "synthetic": False,
                "metrics": {"conversion_rate": {"value": 0.20, "sample_size": 50}},
            },
            {
                "platform": "x",
                "synthetic": False,
                "metrics": {"conversion_rate": {"value": 0.02, "sample_size": 50}},
            },
        ]
        rules = {o.rule for o in detect_platform_performance("acme", rows)}
        self.assertIn("platform/outperform", rules)
        self.assertIn("platform/underperform", rules)

    def test_every_opportunity_carries_the_required_fields(self):
        self._populated_campaign(achieved=5, target="Generate 100 demos")
        found = self.brain.opportunities(T0)
        self.assertTrue(found)
        for o in found:
            with self.subTest(rule=o.rule):
                payload = o.to_dict()
                for key in (
                    "type",
                    "severity",
                    "confidence",
                    "evidence",
                    "recommended_action",
                    "success_metric",
                ):
                    self.assertTrue(payload[key], f"{key} missing on {o.rule}")

    def test_an_opportunity_without_data_reports_unknown_upside(self):
        self._populated_campaign(achieved=30, target="Generate 20 demos")
        ahead = [o for o in self.brain.opportunities(T0) if o.rule == "campaign/ahead"]
        self.assertTrue(ahead)
        self.assertIsNone(ahead[0].expected_upside)
        self.assertIn("unknown", ahead[0].upside_reason)


# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------


class TestExperiments(unittest.TestCase):
    def _experiment(self, **kw) -> Experiment:
        return propose_experiment(
            project="acme",
            hypothesis="carousels beat images",
            variable=ExperimentVariable.CONTENT_FORMAT,
            metric="engagement_rate",
            variation_a=Variation("a", "image"),
            variation_b=Variation("b", "carousel"),
            now=T0,
            **kw,
        )

    def test_an_experiment_starts_proposed(self):
        self.assertIs(self._experiment().status, ExperimentStatus.PROPOSED)

    def test_starting_without_approval_is_refused(self):
        experiment = self._experiment()
        experiment.approve("human:j", T0)
        experiment.approved_by = ""  # simulate a missing approval record
        with self.assertRaises(ExperimentApprovalRequiredError):
            experiment.start_run(T0)

    def test_no_path_starts_a_published_content_experiment_unapproved(self):
        experiment = self._experiment()
        self.assertTrue(experiment.changes_published_content)
        with self.assertRaises(ExperimentApprovalRequiredError):
            experiment.start_run(T0)

    def test_approval_then_start(self):
        experiment = self._experiment()
        experiment.approve("human:j", T0, "reviewed")
        experiment.start_run(T0)
        self.assertIs(experiment.status, ExperimentStatus.RUNNING)
        self.assertEqual(experiment.approved_by, "human:j")

    def test_a_thin_sample_is_inconclusive_never_a_winner(self):
        result = evaluate_result("ctr", 0.10, 0.02, sample_a=5, sample_b=5, minimum_sample=30)
        self.assertFalse(result.conclusive)
        self.assertEqual(result.winner, "")
        self.assertIn("below the required", result.reason)

    def test_a_small_effect_is_inconclusive(self):
        result = evaluate_result("ctr", 0.100, 0.098, sample_a=100, sample_b=100)
        self.assertFalse(result.conclusive)
        self.assertIn("minimum detectable effect", result.reason)

    def test_a_clear_result_names_a_winner(self):
        result = evaluate_result("ctr", 0.20, 0.05, sample_a=100, sample_b=100)
        self.assertTrue(result.conclusive)
        self.assertEqual(result.winner, "a")

    def test_a_missing_value_is_inconclusive(self):
        result = evaluate_result("ctr", None, 0.05, sample_a=100, sample_b=100)
        self.assertFalse(result.conclusive)

    def test_concluding_an_underpowered_experiment_marks_it_inconclusive(self):
        experiment = self._experiment()
        experiment.approve("human:j", T0)
        experiment.start_run(T0)
        experiment.conclude(0.2, 0.05, sample_a=2, sample_b=2, now=T0)
        self.assertIs(experiment.status, ExperimentStatus.INCONCLUSIVE)

    def test_concluding_a_powered_experiment_completes_it(self):
        experiment = self._experiment()
        experiment.approve("human:j", T0)
        experiment.start_run(T0)
        experiment.conclude(0.2, 0.05, sample_a=100, sample_b=100, now=T0)
        self.assertIs(experiment.status, ExperimentStatus.COMPLETED)

    def test_round_trip(self):
        experiment = self._experiment()
        self.assertEqual(Experiment.from_dict(experiment.to_dict()).to_dict(), experiment.to_dict())


class TestConfirmedLearning(BrainCase):
    def test_an_inconclusive_experiment_confirms_nothing(self):
        experiment = propose_experiment(
            project="acme",
            hypothesis="h",
            variable=ExperimentVariable.CTA,
            metric="ctr",
            variation_a=Variation("a", "x"),
            variation_b=Variation("b", "y"),
            now=T0,
        )
        experiment.approve("human:j", T0)
        experiment.start_run(T0)
        experiment.conclude(0.1, 0.1, sample_a=2, sample_b=2, now=T0)
        self.assertIsNone(self.brain.confirm_learning(experiment, T0))

    def test_a_conclusive_experiment_produces_a_confirmed_learning(self):
        experiment = propose_experiment(
            project="acme",
            hypothesis="carousels beat images",
            variable=ExperimentVariable.CONTENT_FORMAT,
            metric="engagement_rate",
            variation_a=Variation("a", "image"),
            variation_b=Variation("b", "carousel"),
            now=T0,
        )
        experiment.approve("human:j", T0)
        experiment.start_run(T0)
        experiment.conclude(0.02, 0.30, sample_a=60, sample_b=60, now=T0)

        learning = self.brain.confirm_learning(experiment, T0)
        self.assertIsNotNone(learning)
        assert learning is not None
        self.assertTrue(learning.to_dict()["confirmed"])
        self.assertEqual(learning.experiment_id, experiment.id)

    def test_a_confirmed_learning_enters_memory(self):
        experiment = propose_experiment(
            project="acme",
            hypothesis="carousels beat images",
            variable=ExperimentVariable.CONTENT_FORMAT,
            metric="engagement_rate",
            variation_a=Variation("a", "image"),
            variation_b=Variation("b", "carousel"),
            now=T0,
        )
        experiment.approve("human:j", T0)
        experiment.start_run(T0)
        experiment.conclude(0.02, 0.30, sample_a=60, sample_b=60, now=T0)
        self.brain.confirm_learning(experiment, T0)

        remembered = self.brain.memory.query(category=MemoryCategory.EXPERIMENT_OUTCOME)
        self.assertEqual(len(remembered), 1)
        self.assertIs(remembered[0].status, MemoryStatus.TENTATIVE)


# ---------------------------------------------------------------------------
# Forecasting and scoring
# ---------------------------------------------------------------------------


class TestForecasting(unittest.TestCase):
    def test_linear_projection(self):
        forecast = project_at_run_rate(
            "x", achieved=10, elapsed_days=10, remaining_days=20, sample_size=10
        )
        self.assertEqual(forecast.value, 30.0)

    def test_no_elapsed_time_is_undefined(self):
        forecast = project_at_run_rate("x", 10, 0, 20, sample_size=10)
        self.assertIsNone(forecast.value)
        self.assertIn("no elapsed time", forecast.reason)

    def test_a_thin_sample_is_not_projected(self):
        forecast = project_at_run_rate("x", 10, 10, 20, sample_size=1)
        self.assertIsNone(forecast.value)
        self.assertIn("minimum to project", forecast.reason)

    def test_every_forecast_states_its_assumptions(self):
        forecast = project_at_run_rate("x", 10, 10, 20, sample_size=10)
        self.assertTrue(forecast.assumptions)
        self.assertIn("Linear projection", forecast.assumptions[0])

    def test_no_remaining_time_reports_the_observed_total(self):
        forecast = project_at_run_rate("x", 42, 10, 0, sample_size=10)
        self.assertEqual(forecast.value, 42)
        self.assertIn("not a projection", forecast.reason)

    def test_goal_verdict_is_unknown_without_a_target(self):
        from growth.brain.forecasting import forecast_goal_completion

        result = forecast_goal_completion(
            {"objective_progress": {"achieved": 5, "target": None}, "metrics": {}},
            T0 - timedelta(days=10),
            T0 + timedelta(days=10),
            T0,
        )
        self.assertEqual(result["verdict"], "unknown")


class TestScoring(BrainCase):
    def test_scores_are_reproducible(self):
        self._populated_campaign()
        self.assertEqual(self.brain.scores(), self.brain.scores())

    def test_a_score_without_components_is_undefined_not_zero(self):
        score = workspace_health({"metrics": {}, "synthetic": False})
        self.assertIsNone(score.value)
        self.assertEqual(score.band, "unknown")
        self.assertIn("undefined", score.reason)

    def test_normalize_rate_passes_none_through(self):
        self.assertIsNone(normalize_rate(None, "ctr"))

    def test_normalize_rate_caps_at_100(self):
        self.assertEqual(normalize_rate(1.0, "ctr"), 100.0)

    def test_a_score_carries_its_components_and_weights(self):
        self._populated_campaign()
        score = self.brain.scores()["workspace_health"]
        self.assertTrue(score["components"])
        self.assertTrue(score["weights"])

    def test_an_unstarted_campaign_is_unscored_not_poor(self):
        """A draft with nothing published has not under-delivered; it has not started."""
        from growth.brain.scoring import campaign_health

        score = campaign_health(
            {
                "content_created": 3,
                "content_published": 0,
                "metrics": {},
                "objective_progress": {},
                "synthetic": False,
            }
        )
        self.assertIsNone(score.value)
        self.assertEqual(score.band, "unknown")

    def test_a_campaign_that_ran_and_published_nothing_scores_zero_delivery(self):
        """Once a campaign has measured activity, 0 published IS a finding."""
        from growth.brain.scoring import campaign_health

        score = campaign_health(
            {
                "content_created": 3,
                "content_published": 0,
                "metrics": {"clicks": {"value": 10.0, "sample_size": 5}},
                "objective_progress": {},
                "synthetic": False,
            }
        )
        self.assertEqual(score.value, 0.0)

    def test_priority_ordering_is_a_pure_lookup(self):
        self.assertEqual(recommendation_priority("low", "low", 1, False), "P3")
        self.assertEqual(recommendation_priority("critical", "high", 50, False), Priority.P0.value)


# ---------------------------------------------------------------------------
# API / CLI
# ---------------------------------------------------------------------------


class TestBrainApi(unittest.TestCase):
    def _monday(self, tmp: str):
        root = _root(tmp)
        monday = Monday(MondayConfig(project_root=root))
        monday.growth("workspace-init", project="acme")
        return root, monday

    def test_analysis_through_the_api(self):
        with TemporaryDirectory() as tmp:
            root, monday = self._monday(tmp)
            monday.growth("seed-demo", project="acme")
            monday.growth(
                "event-import",
                project="acme",
                source="synthetic",
                events=[
                    {"event_type": "impression", "value": 5000, "platform": "linkedin"},
                    {"event_type": "click", "value": 300, "platform": "linkedin"},
                    {"event_type": "signup", "value": 40, "platform": "linkedin"},
                ],
            )
            result = monday.growth("brain-analyze", project="acme")
            self.assertTrue(result.success)
            analysis = result.data["analysis"]
            self.assertTrue(analysis["deterministic"])
            self.assertIsNone(analysis["model_used"])
            self.assertIn("no model was called", result.message)

    def test_recommendations_and_hypotheses_are_separate_actions(self):
        with TemporaryDirectory() as tmp:
            _, monday = self._monday(tmp)
            recs = monday.growth("brain-recommendations", project="acme")
            hyps = monday.growth("brain-hypotheses", project="acme")
            self.assertTrue(recs.success)
            self.assertTrue(hyps.success)
            self.assertIn("UNCONFIRMED", hyps.message)

    def test_memory_flow_through_the_api(self):
        with TemporaryDirectory() as tmp:
            _, monday = self._monday(tmp)
            recorded = monday.growth(
                "memory-record",
                project="acme",
                statement="carousels win here",
                sample_size=12,
                category="content-outcome",
                metric_affected="engagement_rate",
            )
            self.assertTrue(recorded.success)
            self.assertIn("TENTATIVE", recorded.message)
            entry_id = recorded.data["memory"]["id"]

            validated = monday.growth(
                "memory-validate",
                project="acme",
                entry_id=entry_id,
                by="human:j",
                reason="experiment confirmed",
            )
            self.assertIn("VALIDATED", validated.message)

    def test_recording_an_anecdote_returns_a_failed_response(self):
        with TemporaryDirectory() as tmp:
            _, monday = self._monday(tmp)
            result = monday.growth(
                "memory-record", project="acme", statement="one post did well", sample_size=1
            )
            self.assertFalse(result.success)
            self.assertIn("anecdote", result.message)

    def test_experiments_are_all_proposed_and_need_approval(self):
        with TemporaryDirectory() as tmp:
            _, monday = self._monday(tmp)
            result = monday.growth("brain-experiments", project="acme")
            self.assertTrue(result.success)
            for experiment in result.data["experiments"]:
                self.assertEqual(experiment["status"], "proposed")
                self.assertEqual(experiment["approved_by"], "")
            self.assertIn("require human approval", result.message)


if __name__ == "__main__":
    unittest.main()
