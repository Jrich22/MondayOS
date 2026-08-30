"""
growth.brain — the deterministic reasoning layer over one project's measurements.

The Brain reads what has already been measured and applies explicit rules. It
calls no model, writes no copy, publishes nothing, and reaches no network. Its
entire input is the workspace it was opened for (ADR-011).

Four record kinds, never conflated:

    Observation        a computed fact, with the source that produced it
    Hypothesis         a candidate explanation, explicitly UNCONFIRMED
    Recommendation     a proposed action, evidence-backed and falsifiable
    ConfirmedLearning  a hypothesis an experiment upheld

A thin sample produces a Hypothesis rather than a Recommendation. The only route
from hypothesis to confirmed learning runs through an experiment that actually
settled something.
"""

from __future__ import annotations

from growth.brain.engine import GrowthBrain
from growth.brain.evidence import Evidence, MetricCitation, standing_assumptions
from growth.brain.experiments import (
    DEFAULT_MINIMUM_SAMPLE,
    MINIMUM_DETECTABLE_EFFECT,
    Experiment,
    ExperimentApprovalRequiredError,
    ExperimentResult,
    ExperimentStatus,
    ExperimentVariable,
    Variation,
    evaluate_result,
    propose_experiment,
    suggest_experiments,
)
from growth.brain.forecasting import Forecast, forecast_goal_completion, project_at_run_rate
from growth.brain.memory import (
    MIN_SAMPLE_FOR_MEMORY,
    MIN_SAMPLE_FOR_VALIDATION,
    InsufficientSampleError,
    MarketingMemory,
    MemoryCategory,
    MemoryEntry,
    MemoryStatus,
)
from growth.brain.models import (
    MIN_SAMPLE_FOR_RECOMMENDATION,
    Confidence,
    ConfirmedLearning,
    Hypothesis,
    InvalidRecommendationError,
    InvalidRecommendationTransitionError,
    Observation,
    Priority,
    Recommendation,
    RecommendationStatus,
    RecordKind,
    deterministic_id,
)
from growth.brain.opportunity import Opportunity, OpportunityType, Severity
from growth.brain.recommendations import build, build_all
from growth.brain.scoring import (
    Score,
    campaign_health,
    channel_health,
    content_quality,
    recommendation_priority,
    workspace_health,
)

__all__ = [
    "DEFAULT_MINIMUM_SAMPLE",
    "MINIMUM_DETECTABLE_EFFECT",
    "MIN_SAMPLE_FOR_MEMORY",
    "MIN_SAMPLE_FOR_RECOMMENDATION",
    "MIN_SAMPLE_FOR_VALIDATION",
    "Confidence",
    "ConfirmedLearning",
    "Evidence",
    "Experiment",
    "ExperimentApprovalRequiredError",
    "ExperimentResult",
    "ExperimentStatus",
    "ExperimentVariable",
    "Forecast",
    "GrowthBrain",
    "Hypothesis",
    "InsufficientSampleError",
    "InvalidRecommendationError",
    "InvalidRecommendationTransitionError",
    "MarketingMemory",
    "MemoryCategory",
    "MemoryEntry",
    "MemoryStatus",
    "MetricCitation",
    "Observation",
    "Opportunity",
    "OpportunityType",
    "Priority",
    "RecordKind",
    "Recommendation",
    "RecommendationStatus",
    "Score",
    "Severity",
    "Variation",
    "build",
    "build_all",
    "campaign_health",
    "channel_health",
    "content_quality",
    "deterministic_id",
    "evaluate_result",
    "forecast_goal_completion",
    "project_at_run_rate",
    "propose_experiment",
    "recommendation_priority",
    "standing_assumptions",
    "suggest_experiments",
    "workspace_health",
]
