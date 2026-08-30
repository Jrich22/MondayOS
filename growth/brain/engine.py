"""
GrowthBrain - the reasoning layer, scoped to exactly one workspace.

The Brain reads what has already been measured and applies explicit rules to it.
It calls no model, writes no copy, publishes nothing, and reaches no network. Its
entire input is the workspace it was opened for: content, campaigns, events,
metrics and memory (ADR-011). There is no argument it can be given that would
make it read a second project.

Determinism is the property everything else rests on. ``now`` is injected rather
than read from a clock, ids are derived from what a record is *about*, and every
output list is sorted, so the same workspace state produces byte-identical
analysis on every run. A caller can diff yesterday's output against today's and
see only real change.

The four record kinds stay separate all the way out. A thin sample yields a
Hypothesis carrying its unconfirmed marker; only a sample that clears the
threshold yields a Recommendation; and only an experiment turns a hypothesis into
a ConfirmedLearning.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from growth.analytics import GrowthAnalytics
from growth.brain.evidence import Evidence, standing_assumptions
from growth.brain.experiments import Experiment, suggest_experiments
from growth.brain.forecasting import forecast_goal_completion, forecast_metric
from growth.brain.memory import MarketingMemory, MemoryCategory, MemoryEntry
from growth.brain.models import (
    ConfirmedLearning,
    Hypothesis,
    Observation,
    Recommendation,
    RecommendationStatus,
    deterministic_id,
)
from growth.brain.opportunity import (
    Opportunity,
    detect_campaign_performance,
    detect_conversion_gap,
    detect_engagement_trend,
    detect_platform_performance,
    detect_reuse_opportunity,
    detect_theme_performance,
)
from growth.brain.recommendations import build_all
from growth.brain.scoring import (
    Score,
    campaign_health,
    channel_health,
    content_quality,
    workspace_health,
)
from growth.library import ContentLibrary
from growth.store import WorkspaceHandle

# The period a trend compares, in days.
DEFAULT_TREND_DAYS = 7

# Horizon for forward projections, in days.
DEFAULT_FORECAST_DAYS = 30


class GrowthBrain:
    """Deterministic reasoning over exactly one Growth workspace."""

    def __init__(
        self,
        handle: WorkspaceHandle,
        project_root: Path,
        analytics: GrowthAnalytics | None = None,
    ) -> None:
        self._handle = handle
        self._root = Path(project_root)
        self._analytics = analytics or GrowthAnalytics(handle, handle.event_store())
        self._library = ContentLibrary(handle)
        self._memory = MarketingMemory(handle.path, handle.slug)

    @property
    def project(self) -> str:
        return self._handle.slug

    @property
    def memory(self) -> MarketingMemory:
        return self._memory

    # ------------------------------------------------------------------
    # Observations
    # ------------------------------------------------------------------

    def observations(self) -> list[Observation]:
        """
        Computed facts about this project. Facts only - nothing here explains anything.
        """
        performance = self._analytics.workspace_performance()
        metrics = performance.get("metrics", {})
        found: list[Observation] = []
        for name in sorted(metrics):
            metric = metrics[name]
            if not isinstance(metric, dict):
                continue
            found.append(
                Observation(
                    id=deterministic_id("OBS", self.project, "workspace", name),
                    project=self.project,
                    statement=_describe_metric(name, metric),
                    metric=name,
                    value=metric.get("value"),
                    source="growth.analytics.workspace_performance",
                    synthetic=bool(metric.get("synthetic")),
                    sample_size=int(metric.get("sample_size", 0)),
                    subject="workspace",
                )
            )
        return found

    # ------------------------------------------------------------------
    # Scores
    # ------------------------------------------------------------------

    def scores(self) -> dict[str, Any]:
        """Health scores for the workspace, each campaign, and each platform."""
        workspace = self._analytics.workspace_performance()
        campaigns = {
            campaign.id: campaign_health(
                self._analytics.campaign_performance(campaign.id)
            ).to_dict()
            for campaign in self._handle.list_campaigns()
        }
        platforms = {
            str(row["platform"]): channel_health(row).to_dict()
            for row in self._analytics.platform_performance()
        }
        return {
            "project": self.project,
            "workspace_health": workspace_health(workspace).to_dict(),
            "campaign_health": dict(sorted(campaigns.items())),
            "channel_health": dict(sorted(platforms.items())),
        }

    def content_scores(self, limit: int = 10) -> list[dict[str, Any]]:
        """Quality scores for measured content, best first."""
        scored: list[tuple[float, dict[str, Any]]] = []
        for item in self._handle.list_content():
            analytics = self._analytics.content_performance(item.id)
            if analytics.get("event_count", 0) == 0:
                continue
            score: Score = content_quality(analytics)
            scored.append(
                (
                    score.value if score.value is not None else -1.0,
                    {
                        "content_id": item.id,
                        "title": item.title,
                        "platform": item.platform,
                        "score": score.to_dict(),
                    },
                )
            )
        scored.sort(key=lambda pair: (-pair[0], pair[1]["content_id"]))
        return [row for _, row in scored[:limit]]

    # ------------------------------------------------------------------
    # Opportunities
    # ------------------------------------------------------------------

    def opportunities(self, now: datetime) -> list[Opportunity]:
        """
        Every detected finding, from every rule, in a stable order.

        Detectors run over already-computed analytics, so adding a rule cannot
        change what the existing rules see.
        """
        workspace = self._analytics.workspace_performance()
        metrics = workspace.get("metrics", {})
        found: list[Opportunity] = []

        trend = self._analytics.trend(
            "engagement", period_days=DEFAULT_TREND_DAYS, now=now
        ).to_dict()
        found.extend(detect_engagement_trend(self.project, trend, metrics))

        for campaign in self._handle.list_campaigns():
            found.extend(
                detect_campaign_performance(
                    self.project, self._analytics.campaign_performance(campaign.id)
                )
            )

        found.extend(
            detect_platform_performance(self.project, self._analytics.platform_performance())
        )
        found.extend(detect_theme_performance(self.project, self._theme_stats()))
        found.extend(
            detect_reuse_opportunity(
                self.project, [e.to_dict() for e in self._library.not_reused_since(90, now)]
            )
        )
        found.extend(detect_conversion_gap(self.project, self._analytics.funnel(), metrics))

        return sorted(found, key=lambda o: o.id)

    # ------------------------------------------------------------------
    # Recommendations and hypotheses
    # ------------------------------------------------------------------

    def analyze(self, now: datetime) -> dict[str, Any]:
        """
        The full deterministic pass: observations, scores, findings, forecasts.

        Recommendations and hypotheses are returned as separate lists and never
        merged, so no consumer can render a candidate explanation as an action.
        """
        opportunities = self.opportunities(now)
        recommendations, hypotheses = build_all(opportunities, now, self._memory.all())
        experiments = suggest_experiments(self.project, opportunities, now)

        return {
            "project": self.project,
            "generated_at": _fmt(now),
            "deterministic": True,
            "model_used": None,
            "observations": [o.to_dict() for o in self.observations()],
            "opportunities": [o.to_dict() for o in opportunities],
            "recommendations": [r.to_dict() for r in recommendations],
            "hypotheses": [h.to_dict() for h in hypotheses],
            "suggested_experiments": [e.to_dict() for e in experiments],
            "scores": self.scores(),
            "forecasts": self.forecasts(now),
            "memory": {
                "validated": [e.to_dict() for e in self._memory.validated()],
                "tentative": [e.to_dict() for e in self._memory.tentative()],
            },
            "note": (
                "Produced by explicit rules over measured data. No model was called. "
                "Hypotheses are candidate explanations, not findings."
            ),
        }

    def recommendations(self, now: datetime) -> list[Recommendation]:
        """Only the findings that cleared the sample threshold."""
        recommendations, _ = build_all(self.opportunities(now), now, self._memory.all())
        return recommendations

    def hypotheses(self, now: datetime) -> list[Hypothesis]:
        """Only the candidate explanations that did not."""
        _, hypotheses = build_all(self.opportunities(now), now, self._memory.all())
        return hypotheses

    # ------------------------------------------------------------------
    # Forecasts
    # ------------------------------------------------------------------

    def forecasts(self, now: datetime) -> dict[str, Any]:
        """Rule-based projections for each campaign and for the workspace."""
        campaigns: dict[str, Any] = {}
        for campaign in self._handle.list_campaigns():
            analytics = self._analytics.campaign_performance(campaign.id)
            campaigns[campaign.id] = forecast_goal_completion(
                analytics, campaign.start_date, campaign.end_date, now
            )

        workspace = self._analytics.workspace_performance()
        metrics = workspace.get("metrics", {})
        elapsed = self._elapsed_days(now)
        return {
            "project": self.project,
            "campaigns": dict(sorted(campaigns.items())),
            "expected_conversions": forecast_metric(
                "conversions",
                metrics,
                elapsed,
                DEFAULT_FORECAST_DAYS,
                bool(workspace.get("synthetic")),
            ).to_dict(),
            "expected_engagement": forecast_metric(
                "engagement",
                metrics,
                elapsed,
                DEFAULT_FORECAST_DAYS,
                bool(workspace.get("synthetic")),
            ).to_dict(),
        }

    # ------------------------------------------------------------------
    # Learning
    # ------------------------------------------------------------------

    def confirm_learning(self, experiment: Experiment, now: datetime) -> ConfirmedLearning | None:
        """
        Promote a conclusive experiment into a confirmed learning, and remember it.

        Returns None for an inconclusive experiment: the only route from
        hypothesis to fact runs through a result that actually settled something.
        """
        result = experiment.result
        if result is None or not result.conclusive:
            return None

        statement = (
            f"{experiment.hypothesis} Confirmed: variation {result.winner} won on "
            f"{result.metric} by {(result.relative_difference or 0) * 100:.1f}%."
        )
        sample = result.sample_a + result.sample_b
        evidence = experiment.evidence or Evidence(assumptions=standing_assumptions())
        learning = ConfirmedLearning(
            id=deterministic_id("LRN", self.project, experiment.id),
            project=self.project,
            statement=statement,
            evidence=evidence,
            experiment_id=experiment.id,
            sample_size=sample,
            confirmed_at=now,
        )
        try:
            self._memory.record(
                category=MemoryCategory.EXPERIMENT_OUTCOME,
                statement=statement,
                sample_size=sample,
                now=now,
                evidence=evidence.to_dict(),
                source_campaigns=[experiment.campaign] if experiment.campaign else [],
                confidence=experiment.confidence.value,
                metric_affected=experiment.metric,
                synthetic=evidence.synthetic,
            )
        except ValueError:
            # Sample too thin to remember. The learning is still returned so the
            # caller sees the result; it simply does not enter memory as a pattern.
            pass
        return learning

    def remember(
        self,
        category: MemoryCategory,
        statement: str,
        sample_size: int,
        now: datetime,
        **fields: Any,
    ) -> MemoryEntry:
        """Record a tentative claim in this project's memory."""
        return self._memory.record(
            category=category, statement=statement, sample_size=sample_size, now=now, **fields
        )

    def review_recommendation(
        self,
        recommendation: Recommendation,
        status: RecommendationStatus,
        by: str,
        reason: str,
        now: datetime,
    ) -> Recommendation:
        """Record a human decision on a recommendation."""
        recommendation.apply_transition(status, changed_by=by, reason=reason, at=now)
        return recommendation

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _theme_stats(self) -> dict[str, dict[str, Any]]:
        """Mean conversions per measured item, grouped by theme."""
        totals: dict[str, list[float]] = defaultdict(list)
        ids: dict[str, list[str]] = defaultdict(list)
        synthetic: dict[str, bool] = defaultdict(bool)

        for item in self._handle.list_content():
            if not item.themes:
                continue
            analytics = self._analytics.content_performance(item.id)
            if analytics.get("event_count", 0) == 0:
                continue
            conversions = (analytics.get("metrics", {}).get("conversions") or {}).get("value")
            if not isinstance(conversions, (int, float)):
                continue
            for theme in item.themes:
                totals[theme].append(float(conversions))
                ids[theme].append(item.id)
                synthetic[theme] = synthetic[theme] or bool(analytics.get("synthetic"))

        return {
            theme: {
                "measured_items": len(values),
                "mean_conversions": sum(values) / len(values) if values else 0.0,
                "content_ids": ids[theme],
                "synthetic": synthetic[theme],
            }
            for theme, values in sorted(totals.items())
        }

    def _elapsed_days(self, now: datetime) -> float:
        """Days between the first recorded event and now, floored at 1."""
        events = self._handle.event_store().all()
        if not events:
            return 0.0
        earliest = min(e.occurred_at for e in events)
        elapsed = (_utc(now) - _utc(earliest)).total_seconds() / 86400.0
        return max(1.0, elapsed)


def _describe_metric(name: str, metric: dict[str, Any]) -> str:
    """A plain sentence for one measured metric."""
    value = metric.get("value")
    if value is None:
        return f"{name} is not measured ({metric.get('reason', 'no data')})"
    unit = metric.get("unit", "count")
    rendered = f"{value * 100:.2f}%" if unit == "ratio" else f"{value:g}"
    suffix = " from synthetic or imported data" if metric.get("synthetic") else ""
    return f"{name} is {rendered}{suffix}"


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _fmt(value: datetime) -> str:
    return _utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")
