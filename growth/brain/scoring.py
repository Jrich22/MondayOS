"""
Deterministic scoring - health and priority as arithmetic, not judgement.

Every score here is a pure function of numbers already measured, computed the
same way every time. No weighting is learned, no threshold is tuned at runtime,
and nothing consults a model. A caller can re-derive any score by hand from the
components returned alongside it, which is the property that makes a score worth
showing at all.

Scores are 0-100 and always arrive with their components, so "62" is never the
whole answer. A score computed from too little data reports ``None`` and says
why, for the same reason a rate with an empty denominator does: a health score
of 0 for a project nobody has measured would read as failure rather than
absence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Below this many contributing observations a score is not computed at all.
MIN_OBSERVATIONS_FOR_SCORE = 1

# Component weights. Explicit and summing to 1.0 so a reader can check the
# arithmetic rather than trust it.
CAMPAIGN_HEALTH_WEIGHTS: dict[str, float] = {
    "objective_progress": 0.40,
    "conversion_rate": 0.25,
    "engagement_rate": 0.20,
    "delivery": 0.15,
}

CHANNEL_HEALTH_WEIGHTS: dict[str, float] = {
    "conversion_rate": 0.40,
    "engagement_rate": 0.35,
    "ctr": 0.25,
}

WORKSPACE_HEALTH_WEIGHTS: dict[str, float] = {
    "publishing_success_rate": 0.30,
    "approval_rate": 0.20,
    "conversion_rate": 0.30,
    "engagement_rate": 0.20,
}

CONTENT_QUALITY_WEIGHTS: dict[str, float] = {
    "engagement_rate": 0.35,
    "ctr": 0.35,
    "conversion_rate": 0.30,
}

# Reference ceilings that map a rate onto 0-100. These are conventions, not
# discoveries: they exist so scores are comparable between projects, and they are
# stated here so nobody mistakes them for measured benchmarks.
RATE_CEILINGS: dict[str, float] = {
    "engagement_rate": 0.05,
    "ctr": 0.02,
    "conversion_rate": 0.05,
    "approval_rate": 1.0,
    "publishing_success_rate": 1.0,
}


@dataclass
class Score:
    """A 0-100 score with the components that produced it."""

    name: str
    value: float | None
    components: dict[str, float | None] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)
    synthetic: bool = False
    sample_size: int = 0
    reason: str = ""

    @property
    def band(self) -> str:
        """A coarse label. Unknown when the score could not be computed."""
        if self.value is None:
            return "unknown"
        if self.value >= 75:
            return "healthy"
        if self.value >= 50:
            return "fair"
        if self.value >= 25:
            return "weak"
        return "poor"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "band": self.band,
            "components": dict(self.components),
            "weights": dict(self.weights),
            "synthetic": self.synthetic,
            "sample_size": self.sample_size,
            "reason": self.reason,
        }


def normalize_rate(value: float | None, metric: str) -> float | None:
    """
    Map a rate onto 0-100 against its reference ceiling.

    A rate at or above the ceiling scores 100; the mapping is linear below it.
    ``None`` in, ``None`` out - an unmeasured rate must not become a zero score.
    """
    if value is None:
        return None
    ceiling = RATE_CEILINGS.get(metric, 1.0)
    if ceiling <= 0:
        return None
    return max(0.0, min(100.0, (value / ceiling) * 100.0))


def _weighted(
    name: str,
    components: dict[str, float | None],
    weights: dict[str, float],
    synthetic: bool,
    sample_size: int,
) -> Score:
    """
    Combine components, renormalising over only those that are defined.

    A missing component is skipped rather than counted as zero: treating "not
    measured" as "scored zero" would drag a healthy campaign's score down for
    having a metric nobody has collected yet.
    """
    available = {k: v for k, v in components.items() if v is not None}
    if not available:
        return Score(
            name=name,
            value=None,
            components=components,
            weights=weights,
            synthetic=synthetic,
            sample_size=sample_size,
            reason="undefined: none of the component metrics are measured",
        )
    total_weight = sum(weights.get(k, 0.0) for k in available)
    if total_weight <= 0:
        return Score(
            name=name,
            value=None,
            components=components,
            weights=weights,
            synthetic=synthetic,
            sample_size=sample_size,
            reason="undefined: no weight assigned to the available components",
        )
    score = sum(available[k] * weights.get(k, 0.0) for k in available) / total_weight
    return Score(
        name=name,
        value=round(score, 2),
        components=components,
        weights=weights,
        synthetic=synthetic,
        sample_size=sample_size,
        reason=(
            ""
            if len(available) == len(components)
            else f"computed from {len(available)} of {len(components)} components"
        ),
    )


def campaign_health(campaign_analytics: dict[str, Any]) -> Score:
    """Health of one campaign: progress, conversion, engagement, delivery."""
    metrics = campaign_analytics.get("metrics", {})
    progress = campaign_analytics.get("objective_progress", {}) or {}

    percent = progress.get("percent")
    created = int(campaign_analytics.get("content_created", 0) or 0)
    published = int(campaign_analytics.get("content_published", 0) or 0)
    observed = _sample(metrics)

    # Delivery is only meaningful once a campaign has actually run. A campaign
    # with content drafted, nothing published and nothing measured has not
    # under-delivered - it has not started, and scoring it 0 would push an
    # operator to act on a campaign that is simply still in draft.
    has_run = published > 0 or observed > 0
    delivery = (published / created * 100.0) if (created > 0 and has_run) else None

    components: dict[str, float | None] = {
        "objective_progress": (
            max(0.0, min(100.0, float(percent))) if isinstance(percent, (int, float)) else None
        ),
        "conversion_rate": normalize_rate(_metric(metrics, "conversion_rate"), "conversion_rate"),
        "engagement_rate": normalize_rate(_metric(metrics, "engagement_rate"), "engagement_rate"),
        "delivery": delivery,
    }
    return _weighted(
        "campaign_health",
        components,
        CAMPAIGN_HEALTH_WEIGHTS,
        bool(campaign_analytics.get("synthetic")),
        _sample(metrics),
    )


def channel_health(platform_analytics: dict[str, Any]) -> Score:
    """Health of one platform: conversion, engagement, click-through."""
    metrics = platform_analytics.get("metrics", {})
    components: dict[str, float | None] = {
        "conversion_rate": normalize_rate(_metric(metrics, "conversion_rate"), "conversion_rate"),
        "engagement_rate": normalize_rate(_metric(metrics, "engagement_rate"), "engagement_rate"),
        "ctr": normalize_rate(_metric(metrics, "ctr"), "ctr"),
    }
    return _weighted(
        "channel_health",
        components,
        CHANNEL_HEALTH_WEIGHTS,
        bool(platform_analytics.get("synthetic")),
        _sample(metrics),
    )


def workspace_health(workspace_analytics: dict[str, Any]) -> Score:
    """Health of a whole project: operational reliability plus performance."""
    metrics = workspace_analytics.get("metrics", {})
    components: dict[str, float | None] = {
        "publishing_success_rate": normalize_rate(
            _metric(metrics, "publishing_success_rate"), "publishing_success_rate"
        ),
        "approval_rate": normalize_rate(_metric(metrics, "approval_rate"), "approval_rate"),
        "conversion_rate": normalize_rate(_metric(metrics, "conversion_rate"), "conversion_rate"),
        "engagement_rate": normalize_rate(_metric(metrics, "engagement_rate"), "engagement_rate"),
    }
    return _weighted(
        "workspace_health",
        components,
        WORKSPACE_HEALTH_WEIGHTS,
        bool(workspace_analytics.get("synthetic")),
        _sample(metrics),
    )


def content_quality(content_analytics: dict[str, Any]) -> Score:
    """
    Quality of one content item, measured by how people responded to it.

    Named quality rather than performance because that is what a reader will take
    it for - but it is entirely behavioural. It says nothing about the writing,
    only about what happened after it was published.
    """
    metrics = content_analytics.get("metrics", {})
    components: dict[str, float | None] = {
        "engagement_rate": normalize_rate(_metric(metrics, "engagement_rate"), "engagement_rate"),
        "ctr": normalize_rate(_metric(metrics, "ctr"), "ctr"),
        "conversion_rate": normalize_rate(_metric(metrics, "conversion_rate"), "conversion_rate"),
    }
    return _weighted(
        "content_quality",
        components,
        CONTENT_QUALITY_WEIGHTS,
        bool(content_analytics.get("synthetic")),
        _sample(metrics),
    )


def recommendation_priority(
    severity: str, confidence: str, sample_size: int, synthetic: bool
) -> str:
    """
    Map an opportunity onto a recommendation priority. Pure lookup, no judgement.

    Synthetic evidence caps priority at P2: demo data must never produce a P0 that
    an operator feels obliged to act on this morning.
    """
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(severity, 3)
    confidence_bump = {"high": 0, "medium": 1, "low": 2}.get(confidence, 2)
    sample_bump = 0 if sample_size >= 10 else (1 if sample_size >= 3 else 2)

    index = min(3, severity_rank + confidence_bump + sample_bump)
    priority = ["P0", "P1", "P2", "P3"][index]
    if synthetic and priority in ("P0", "P1"):
        return "P2"
    return priority


def _metric(metrics: dict[str, Any], key: str) -> float | None:
    entry = metrics.get(key) or {}
    value = entry.get("value")
    return float(value) if isinstance(value, (int, float)) else None


def _sample(metrics: dict[str, Any]) -> int:
    return max(
        (int((m or {}).get("sample_size", 0)) for m in metrics.values() if isinstance(m, dict)),
        default=0,
    )
