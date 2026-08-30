"""
The recommendation engine - turning findings into proposed actions, or refusing to.

A recommendation is produced only when a detected opportunity clears the minimum
sample. Below that the same finding becomes a Hypothesis, explicitly unconfirmed,
carrying a proposed experiment instead of an instruction. That single branch is
the whole ethic of this module: the Brain is allowed to notice things on thin
data, and is not allowed to tell anyone what to do about them.

Every recommendation carries evidence and a falsifier, enforced by the type
itself. Nothing here writes prose from a model - the wording is assembled from
the measured numbers by fixed templates, so the same workspace state produces
byte-identical recommendations on every run.
"""

from __future__ import annotations

from datetime import datetime

from growth.brain.evidence import Evidence
from growth.brain.memory import MemoryEntry, MemoryStatus
from growth.brain.models import (
    MIN_SAMPLE_FOR_RECOMMENDATION,
    Confidence,
    Hypothesis,
    Priority,
    Recommendation,
    deterministic_id,
)
from growth.brain.opportunity import Opportunity
from growth.brain.scoring import recommendation_priority


def build(
    opportunity: Opportunity, now: datetime, memory: list[MemoryEntry] | None = None
) -> Recommendation | Hypothesis:
    """
    Convert one opportunity into a recommendation, or a hypothesis if it is thin.

    Returns a Hypothesis - never a Recommendation - when the sample is below
    MIN_SAMPLE_FOR_RECOMMENDATION. Callers must handle both, which is deliberate:
    a signature that returned only recommendations would make it easy to forget
    the distinction exists.
    """
    cited = _citable_memory(opportunity, memory or [])
    evidence = _with_memory(opportunity.evidence, cited)

    if opportunity.sample_size < MIN_SAMPLE_FOR_RECOMMENDATION:
        return Hypothesis(
            id=deterministic_id("HYP", opportunity.project, opportunity.rule, opportunity.title),
            project=opportunity.project,
            statement=opportunity.title,
            rationale=(
                f"{opportunity.detail} Sample is {opportunity.sample_size}, below the "
                f"{MIN_SAMPLE_FOR_RECOMMENDATION} observations this engine requires before "
                "proposing an action, so this is a candidate explanation only."
            ),
            evidence=evidence,
            confidence=Confidence.LOW,
            sample_size=opportunity.sample_size,
            proposed_experiment=opportunity.proposed_experiment,
            created_at=now,
        )

    priority = recommendation_priority(
        severity=opportunity.severity.value,
        confidence=opportunity.confidence,
        sample_size=opportunity.sample_size,
        synthetic=opportunity.synthetic,
    )
    impact = _expected_impact(opportunity)

    return Recommendation(
        id=deterministic_id("REC", opportunity.project, opportunity.rule, opportunity.title),
        project=opportunity.project,
        type=opportunity.type.value,
        title=opportunity.title,
        summary=opportunity.detail,
        explanation=_explanation(opportunity, cited),
        evidence=evidence,
        confidence=_confidence(opportunity.confidence),
        expected_impact=impact,
        suggested_action=opportunity.recommended_action,
        success_metric=opportunity.success_metric or "conversions",
        falsifier=opportunity.evidence.falsifier,
        priority=Priority(priority),
        affected_campaigns=list(opportunity.affected_campaigns),
        affected_platforms=list(opportunity.affected_platforms),
        recommended_experiment=opportunity.proposed_experiment,
        supporting_metrics={
            m.name: {"value": m.value, "unit": m.unit, "synthetic": m.synthetic}
            for m in opportunity.evidence.metrics
        },
        created_at=now,
    )


def build_all(
    opportunities: list[Opportunity],
    now: datetime,
    memory: list[MemoryEntry] | None = None,
) -> tuple[list[Recommendation], list[Hypothesis]]:
    """
    Convert every opportunity, separating what may be recommended from what may not.

    Both lists are sorted by id, so the output is stable across runs regardless of
    detector ordering.
    """
    recommendations: list[Recommendation] = []
    hypotheses: list[Hypothesis] = []
    for opportunity in opportunities:
        record = build(opportunity, now, memory)
        if isinstance(record, Recommendation):
            recommendations.append(record)
        else:
            hypotheses.append(record)

    recommendations.sort(key=lambda r: (r.priority.value, r.id))
    hypotheses.sort(key=lambda h: h.id)
    return recommendations, hypotheses


def _confidence(value: str) -> Confidence:
    try:
        return Confidence(value)
    except ValueError:
        return Confidence.LOW


def _expected_impact(opportunity: Opportunity) -> str:
    """
    State the expected impact, or say plainly that it is not known.

    A quantified estimate is emitted only when the detector actually computed one
    from data. Otherwise this returns the reason it could not - never a plausible
    number, which would outlive everyone's memory of it being invented.
    """
    if opportunity.expected_upside is None:
        reason = opportunity.upside_reason or "no measured effect size available"
        return f"Not quantified - {reason}."
    unit = opportunity.upside_unit or "units"
    caveat = " (from synthetic data)" if opportunity.synthetic else ""
    return f"Approximately {opportunity.expected_upside:g} {unit}{caveat}."


def _explanation(opportunity: Opportunity, memory: list[MemoryEntry]) -> str:
    """Assemble the reasoning from measured facts and any citable memory."""
    parts = [opportunity.detail]

    if opportunity.evidence.observations:
        parts.append("Observed: " + "; ".join(opportunity.evidence.observations) + ".")

    parts.append(
        f"Based on {opportunity.sample_size} observation(s) via rule {opportunity.rule!r}."
    )
    if opportunity.synthetic:
        parts.append(
            "All contributing data is synthetic or operator-imported; no platform has "
            "reported anything."
        )
    for entry in memory:
        parts.append(f"Prior learning: {entry.render()}")
    return " ".join(parts)


def _citable_memory(opportunity: Opportunity, memory: list[MemoryEntry]) -> list[MemoryEntry]:
    """
    Memory relevant to this opportunity.

    Validated and tentative entries are both returned, because a tentative
    pattern is worth mentioning - but MemoryEntry.render() puts the marker in the
    string itself, so a tentative claim cannot be quoted as settled no matter
    which caller formats it. Invalidated memory is never cited.
    """
    metric = opportunity.success_metric
    relevant = [
        entry
        for entry in memory
        if entry.status is not MemoryStatus.INVALIDATED
        and (not metric or entry.metric_affected == metric or not entry.metric_affected)
    ]
    return sorted(relevant, key=lambda e: (e.status is not MemoryStatus.VALIDATED, e.id))[:3]


def _with_memory(evidence: Evidence, memory: list[MemoryEntry]) -> Evidence:
    """Attach cited memory ids to a copy of the evidence."""
    if not memory:
        return evidence
    return Evidence(
        metrics=list(evidence.metrics),
        observations=list(evidence.observations),
        campaigns=list(evidence.campaigns),
        platforms=list(evidence.platforms),
        content_ids=list(evidence.content_ids),
        dates=list(evidence.dates),
        assumptions=list(evidence.assumptions),
        falsifier=evidence.falsifier,
        memory_ids=[e.id for e in memory],
        sample_size=evidence.sample_size,
    )
