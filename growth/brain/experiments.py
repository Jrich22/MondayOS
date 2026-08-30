"""
The experiment engine - how a hypothesis earns the right to be called a finding.

An experiment is the only route from Hypothesis to ConfirmedLearning. Everything
else the Brain produces is a candidate explanation, and this module exists so a
candidate can be tested rather than assumed.

Three rules do the real work.

**A result below the minimum sample is inconclusive, never a winner.** Organic
social rarely reaches statistical significance, so rather than compute a p-value
this layer cannot honestly support, an experiment declares its required sample up
front and refuses to name a winner without it. "We do not know yet" is a
legitimate outcome and the most common honest one.

**A difference smaller than the minimum effect is inconclusive too.** Two numbers
that differ by 2% on a few hundred impressions are the same number wearing
different clothes.

**An experiment that changes published content needs explicit human approval.**
Running a variation means putting real content in front of real people; the Brain
may propose it, and only a human may start it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from growth.brain.evidence import Evidence, standing_assumptions
from growth.brain.models import Confidence, deterministic_id

# Below this many observations per variation, no winner is declared.
DEFAULT_MINIMUM_SAMPLE = 30

# Relative difference below which two variations are treated as indistinguishable.
MINIMUM_DETECTABLE_EFFECT = 0.10

# How long an experiment runs by default, in days.
DEFAULT_MEASUREMENT_DAYS = 14


class ExperimentVariable(Enum):
    """What an experiment varies. One variable at a time, always."""

    POSTING_TIME = "posting-time"
    POSTING_FREQUENCY = "posting-frequency"
    CONTENT_LENGTH = "content-length"
    CONTENT_FORMAT = "content-format"
    PLATFORM_CHOICE = "platform-choice"
    CTA = "cta"
    AUDIENCE = "audience"
    THEME = "theme"


class ExperimentStatus(Enum):
    """Lifecycle of an experiment."""

    PROPOSED = "proposed"
    APPROVED = "approved"
    RUNNING = "running"
    COMPLETED = "completed"
    INCONCLUSIVE = "inconclusive"
    CANCELLED = "cancelled"


_TRANSITIONS: dict[ExperimentStatus, set[ExperimentStatus]] = {
    ExperimentStatus.PROPOSED: {ExperimentStatus.APPROVED, ExperimentStatus.CANCELLED},
    ExperimentStatus.APPROVED: {ExperimentStatus.RUNNING, ExperimentStatus.CANCELLED},
    ExperimentStatus.RUNNING: {
        ExperimentStatus.COMPLETED,
        ExperimentStatus.INCONCLUSIVE,
        ExperimentStatus.CANCELLED,
    },
    ExperimentStatus.COMPLETED: set(),
    ExperimentStatus.INCONCLUSIVE: set(),
    ExperimentStatus.CANCELLED: set(),
}


class ExperimentApprovalRequiredError(PermissionError):
    """Raised when an experiment that changes published content is started unapproved."""

    def __init__(self, experiment_id: str) -> None:
        super().__init__(
            f"Experiment {experiment_id!r} changes published content and has not been "
            "approved by a human. The Brain may propose an experiment; only a person "
            "may start one that puts content in front of an audience."
        )


class InvalidExperimentTransitionError(ValueError):
    """Raised when an experiment is moved along an illegal edge."""

    def __init__(self, from_status: str, to_status: str) -> None:
        super().__init__(f"Illegal experiment transition {from_status} -> {to_status}.")


@dataclass
class Variation:
    """One arm of an experiment."""

    label: str
    description: str
    content_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "description": self.description,
            "content_ids": list(self.content_ids),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Variation:
        return cls(
            label=str(data.get("label", "")),
            description=str(data.get("description", "")),
            content_ids=[str(c) for c in (data.get("content_ids") or [])],
        )


@dataclass
class ExperimentResult:
    """What an experiment measured, and whether it settled anything."""

    metric: str
    value_a: float | None = None
    value_b: float | None = None
    sample_a: int = 0
    sample_b: int = 0
    winner: str = ""
    conclusive: bool = False
    reason: str = ""
    relative_difference: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "value_a": self.value_a,
            "value_b": self.value_b,
            "sample_a": self.sample_a,
            "sample_b": self.sample_b,
            "winner": self.winner,
            "conclusive": self.conclusive,
            "reason": self.reason,
            "relative_difference": self.relative_difference,
        }


@dataclass
class Experiment:
    """One controlled comparison within one project."""

    id: str
    project: str
    hypothesis: str
    variable: ExperimentVariable
    variation_a: Variation
    variation_b: Variation
    metric: str
    reason: str = ""
    campaign: str = ""
    minimum_sample: int = DEFAULT_MINIMUM_SAMPLE
    measurement_days: int = DEFAULT_MEASUREMENT_DAYS
    confidence: Confidence = Confidence.LOW
    status: ExperimentStatus = ExperimentStatus.PROPOSED
    changes_published_content: bool = True
    approved_by: str = ""
    start: datetime | None = None
    end: datetime | None = None
    result: ExperimentResult | None = None
    conclusion: str = ""
    evidence: Evidence | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def can_transition_to(self, status: ExperimentStatus) -> bool:
        return status in _TRANSITIONS.get(self.status, set())

    def approve(self, by: str, now: datetime, reason: str = "") -> None:
        """Record human approval. The only way an experiment becomes startable."""
        self._transition(ExperimentStatus.APPROVED, by, now, reason or "approved")
        self.approved_by = by

    def start_run(self, now: datetime, by: str = "system") -> None:
        """
        Begin the experiment.

        Refuses when it changes published content and no human has approved it.
        """
        if self.changes_published_content and not self.approved_by:
            raise ExperimentApprovalRequiredError(self.id)
        self._transition(ExperimentStatus.RUNNING, by, now, "started")
        self.start = now
        self.end = now + _days(self.measurement_days)

    def cancel(self, by: str, now: datetime, reason: str = "") -> None:
        self._transition(ExperimentStatus.CANCELLED, by, now, reason or "cancelled")

    def conclude(
        self,
        value_a: float | None,
        value_b: float | None,
        sample_a: int,
        sample_b: int,
        now: datetime,
        by: str = "system",
    ) -> ExperimentResult:
        """
        Settle the experiment from measured values.

        Resolves to COMPLETED with a winner only when both arms cleared the
        minimum sample AND the difference exceeds the minimum detectable effect.
        Anything else is INCONCLUSIVE, which is a real answer and not a failure.
        """
        result = evaluate_result(
            metric=self.metric,
            value_a=value_a,
            value_b=value_b,
            sample_a=sample_a,
            sample_b=sample_b,
            minimum_sample=self.minimum_sample,
        )
        self.result = result
        self.conclusion = result.reason
        target = ExperimentStatus.COMPLETED if result.conclusive else ExperimentStatus.INCONCLUSIVE
        self._transition(target, by, now, result.reason)
        return result

    def _transition(self, status: ExperimentStatus, by: str, now: datetime, reason: str) -> None:
        if not self.can_transition_to(status):
            raise InvalidExperimentTransitionError(self.status.value, status.value)
        self.history.append(
            {
                "from_status": self.status.value,
                "to_status": status.value,
                "changed_by": by,
                "reason": reason,
                "at": _fmt(now),
            }
        )
        self.status = status

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project": self.project,
            "campaign": self.campaign,
            "hypothesis": self.hypothesis,
            "variable": self.variable.value,
            "variation_a": self.variation_a.to_dict(),
            "variation_b": self.variation_b.to_dict(),
            "metric": self.metric,
            "reason": self.reason,
            "minimum_sample": self.minimum_sample,
            "measurement_days": self.measurement_days,
            "confidence": self.confidence.value,
            "status": self.status.value,
            "changes_published_content": self.changes_published_content,
            "approved_by": self.approved_by,
            "start": _fmt(self.start) if self.start else "",
            "end": _fmt(self.end) if self.end else "",
            "result": self.result.to_dict() if self.result else None,
            "conclusion": self.conclusion,
            "evidence": self.evidence.to_dict() if self.evidence else None,
            "history": list(self.history),
            "created_at": _fmt(self.created_at) if self.created_at else "",
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Experiment:
        result = data.get("result")
        evidence = data.get("evidence")
        experiment = cls(
            id=str(data["id"]),
            project=str(data.get("project", "")),
            hypothesis=str(data.get("hypothesis", "")),
            variable=ExperimentVariable(str(data.get("variable", "content-format"))),
            variation_a=Variation.from_dict(data.get("variation_a") or {}),
            variation_b=Variation.from_dict(data.get("variation_b") or {}),
            metric=str(data.get("metric", "")),
            reason=str(data.get("reason", "")),
            campaign=str(data.get("campaign", "")),
            minimum_sample=int(data.get("minimum_sample", DEFAULT_MINIMUM_SAMPLE)),
            measurement_days=int(data.get("measurement_days", DEFAULT_MEASUREMENT_DAYS)),
            confidence=Confidence(str(data.get("confidence", "low"))),
            status=ExperimentStatus(str(data.get("status", "proposed"))),
            changes_published_content=bool(data.get("changes_published_content", True)),
            approved_by=str(data.get("approved_by", "")),
            start=_parse(data["start"]) if data.get("start") else None,
            end=_parse(data["end"]) if data.get("end") else None,
            conclusion=str(data.get("conclusion", "")),
            evidence=Evidence.from_dict(evidence) if evidence else None,
            created_at=_parse(data["created_at"]) if data.get("created_at") else None,
        )
        if isinstance(result, dict):
            experiment.result = ExperimentResult(
                metric=str(result.get("metric", "")),
                value_a=result.get("value_a"),
                value_b=result.get("value_b"),
                sample_a=int(result.get("sample_a", 0)),
                sample_b=int(result.get("sample_b", 0)),
                winner=str(result.get("winner", "")),
                conclusive=bool(result.get("conclusive", False)),
                reason=str(result.get("reason", "")),
                relative_difference=result.get("relative_difference"),
            )
        experiment.history = list(data.get("history") or [])
        return experiment


def evaluate_result(
    metric: str,
    value_a: float | None,
    value_b: float | None,
    sample_a: int,
    sample_b: int,
    minimum_sample: int = DEFAULT_MINIMUM_SAMPLE,
) -> ExperimentResult:
    """
    Decide whether a comparison settled anything. Pure function, no side effects.

    Never names a winner on an under-powered sample or an effect smaller than
    MINIMUM_DETECTABLE_EFFECT.
    """
    base = ExperimentResult(
        metric=metric,
        value_a=value_a,
        value_b=value_b,
        sample_a=sample_a,
        sample_b=sample_b,
    )
    if value_a is None or value_b is None:
        base.reason = "inconclusive: one variation has no measured value"
        return base
    if sample_a < minimum_sample or sample_b < minimum_sample:
        base.reason = (
            f"inconclusive: samples {sample_a}/{sample_b} are below the required "
            f"{minimum_sample} per variation"
        )
        return base

    larger = max(abs(value_a), abs(value_b))
    if larger == 0:
        base.reason = "inconclusive: both variations measured zero"
        return base

    difference = abs(value_a - value_b) / larger
    base.relative_difference = round(difference, 4)
    if difference < MINIMUM_DETECTABLE_EFFECT:
        base.reason = (
            f"inconclusive: {difference * 100:.1f}% difference is below the "
            f"{MINIMUM_DETECTABLE_EFFECT * 100:.0f}% minimum detectable effect"
        )
        return base

    base.conclusive = True
    base.winner = "a" if value_a > value_b else "b"
    base.reason = (
        f"variation {base.winner} won on {metric} by {difference * 100:.1f}% "
        f"({sample_a}/{sample_b} observations)"
    )
    return base


def propose_experiment(
    project: str,
    hypothesis: str,
    variable: ExperimentVariable,
    metric: str,
    variation_a: Variation,
    variation_b: Variation,
    now: datetime,
    reason: str = "",
    campaign: str = "",
    minimum_sample: int = DEFAULT_MINIMUM_SAMPLE,
    measurement_days: int = DEFAULT_MEASUREMENT_DAYS,
    confidence: Confidence = Confidence.LOW,
    evidence: Evidence | None = None,
) -> Experiment:
    """Build a proposed experiment. Always PROPOSED; only a human approves it."""
    return Experiment(
        id=deterministic_id("EXP", project, variable.value, hypothesis),
        project=project,
        hypothesis=hypothesis,
        variable=variable,
        variation_a=variation_a,
        variation_b=variation_b,
        metric=metric,
        reason=reason,
        campaign=campaign,
        minimum_sample=minimum_sample,
        measurement_days=measurement_days,
        confidence=confidence,
        evidence=evidence
        or Evidence(
            assumptions=standing_assumptions(),
            falsifier=f"The two variations show no material difference in {metric}.",
        ),
        created_at=now,
    )


def suggest_experiments(project: str, opportunities: list[Any], now: datetime) -> list[Experiment]:
    """
    Turn detected opportunities into concrete experiment proposals.

    Every suggestion inherits the opportunity's evidence, so an experiment can
    always be traced to the measurement that motivated it.
    """
    suggestions: list[Experiment] = []
    for opportunity in opportunities:
        variable = _variable_for(opportunity.rule)
        if variable is None:
            continue
        suggestions.append(
            propose_experiment(
                project=project,
                hypothesis=(
                    f"Changing {variable.value.replace('-', ' ')} will improve "
                    f"{opportunity.success_metric or 'conversions'} "
                    f"({opportunity.title})."
                ),
                variable=variable,
                metric=opportunity.success_metric or "conversions",
                variation_a=Variation("a", "Current approach (control)"),
                variation_b=Variation("b", opportunity.proposed_experiment or "Varied approach"),
                now=now,
                reason=opportunity.detail,
                campaign=(
                    opportunity.affected_campaigns[0] if opportunity.affected_campaigns else ""
                ),
                confidence=Confidence(opportunity.confidence)
                if opportunity.confidence in ("low", "medium", "high")
                else Confidence.LOW,
                evidence=opportunity.evidence,
            )
        )
    return sorted(suggestions, key=lambda e: e.id)


def _variable_for(rule: str) -> ExperimentVariable | None:
    """Map a detector rule onto the variable an experiment should vary."""
    mapping = {
        "engagement-trend/decline": ExperimentVariable.CONTENT_FORMAT,
        "engagement-trend/surge": ExperimentVariable.CONTENT_FORMAT,
        "campaign/behind": ExperimentVariable.CTA,
        "campaign/ahead": ExperimentVariable.POSTING_FREQUENCY,
        "platform/outperform": ExperimentVariable.PLATFORM_CHOICE,
        "platform/underperform": ExperimentVariable.CTA,
        "theme/strong": ExperimentVariable.THEME,
        "theme/weak": ExperimentVariable.THEME,
        "reuse/backlog": ExperimentVariable.CONTENT_FORMAT,
        "conversion/weakest-step": ExperimentVariable.CTA,
    }
    return mapping.get(rule)


def _days(count: int) -> Any:
    from datetime import timedelta

    return timedelta(days=count)


def _parse(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.rstrip("Z")).replace(tzinfo=UTC)
    return datetime.now(tz=UTC)


def _fmt(value: datetime) -> str:
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.strftime("%Y-%m-%dT%H:%M:%SZ")
