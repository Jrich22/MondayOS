"""
The four record kinds the Growth Brain reasons in, and their state machines.

The whole point of this module is that these four are never conflated:

    Observation        a computed fact, with the source that produced it
    Hypothesis         a candidate explanation, explicitly UNCONFIRMED
    Recommendation     a proposed action, backed by evidence and falsifiable
    ConfirmedLearning  a hypothesis an experiment actually upheld

A system that blurs them produces confident nonsense, which is the single most
expensive failure available to a marketing brain: it costs a human real time to
evaluate, and it is indistinguishable from insight until acted on. So the
distinction is enforced in the types rather than in prose. A Hypothesis carries
``confirmed = False`` permanently and renders with an explicit marker; the only
route from hypothesis to confirmed learning runs through an experiment.

Nothing here calls a model. Every record is constructed from stored measurements
by explicit rules, and ``created_at`` is always supplied by the caller so the
same workspace state produces byte-identical records on every run.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from growth.brain.evidence import Evidence

# Below this many contributing observations, the Brain will not state a
# recommendation - it states a hypothesis instead. Small samples on opaque
# platform distribution will fit any narrative, and a rule that promotes them
# would manufacture exactly the causal claims ADR-014 forbids.
MIN_SAMPLE_FOR_RECOMMENDATION = 3


class RecordKind(Enum):
    """What kind of claim a record makes. Never inferred from shape."""

    OBSERVATION = "observation"
    HYPOTHESIS = "hypothesis"
    RECOMMENDATION = "recommendation"
    CONFIRMED_LEARNING = "confirmed-learning"


class Confidence(Enum):
    """
    How much weight a record carries.

    Deliberately coarse. A percentage would imply a precision this layer does not
    have, and would invite someone to average two numbers that mean nothing
    together.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Priority(Enum):
    """Recommendation urgency, ordered."""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class RecommendationStatus(Enum):
    """Lifecycle of a recommendation."""

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    COMPLETED = "completed"
    INVALIDATED = "invalidated"


# A rejected recommendation stays rejected: re-proposing it would let the Brain
# nag. Invalidated is reachable from anywhere non-terminal, because new evidence
# can undercut a recommendation at any point in its life.
_RECOMMENDATION_TRANSITIONS: dict[RecommendationStatus, set[RecommendationStatus]] = {
    RecommendationStatus.PROPOSED: {
        RecommendationStatus.ACCEPTED,
        RecommendationStatus.REJECTED,
        RecommendationStatus.INVALIDATED,
    },
    RecommendationStatus.ACCEPTED: {
        RecommendationStatus.COMPLETED,
        RecommendationStatus.INVALIDATED,
        RecommendationStatus.REJECTED,
    },
    RecommendationStatus.REJECTED: set(),
    RecommendationStatus.COMPLETED: set(),
    RecommendationStatus.INVALIDATED: set(),
}


class InvalidRecommendationError(ValueError):
    """Raised when a recommendation is constructed without what makes it checkable."""


class InvalidRecommendationTransitionError(ValueError):
    """Raised when a recommendation is moved along an illegal edge."""

    def __init__(self, from_status: str, to_status: str) -> None:
        super().__init__(f"Illegal recommendation transition {from_status} -> {to_status}.")


def deterministic_id(prefix: str, *parts: str) -> str:
    """
    A stable id derived from what a record is *about*, never from a clock.

    Two runs over the same workspace state produce the same ids, which is what
    lets a caller diff yesterday's recommendations against today's and see only
    real change.
    """
    material = "|".join(p.strip().lower() for p in parts)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


@dataclass
class Observation:
    """
    A computed fact. The only record kind that asserts something is *so*.

    An observation never explains anything - it reports a measurement and where
    it came from. Explanation is a Hypothesis.
    """

    id: str
    project: str
    statement: str
    metric: str
    value: float | None
    source: str = "analytics"
    synthetic: bool = False
    sample_size: int = 0
    subject: str = ""
    observed_at: datetime | None = None

    kind: RecordKind = field(default=RecordKind.OBSERVATION, init=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "id": self.id,
            "project": self.project,
            "statement": self.statement,
            "metric": self.metric,
            "value": self.value,
            "source": self.source,
            "synthetic": self.synthetic,
            "sample_size": self.sample_size,
            "subject": self.subject,
            "observed_at": _fmt(self.observed_at) if self.observed_at else "",
        }


@dataclass
class Hypothesis:
    """
    A candidate explanation. Never a fact, and never rendered as one.

    ``confirmed`` is init-false and cannot be set at construction: the only way a
    hypothesis becomes a confirmed learning is for an experiment to uphold it,
    which produces a separate ConfirmedLearning record. The ``unconfirmed``
    marker is part of every serialization so no consumer can render a hypothesis
    with the same shape as an observation.
    """

    id: str
    project: str
    statement: str
    rationale: str
    evidence: Evidence
    confidence: Confidence = Confidence.LOW
    sample_size: int = 0
    proposed_experiment: str = ""
    created_at: datetime | None = None

    kind: RecordKind = field(default=RecordKind.HYPOTHESIS, init=False)
    confirmed: bool = field(default=False, init=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "unconfirmed": True,
            "confirmed": False,
            "id": self.id,
            "project": self.project,
            "statement": self.statement,
            "rationale": self.rationale,
            "confidence": self.confidence.value,
            "sample_size": self.sample_size,
            "proposed_experiment": self.proposed_experiment,
            "evidence": self.evidence.to_dict(),
            "created_at": _fmt(self.created_at) if self.created_at else "",
            "caveat": (
                "UNCONFIRMED HYPOTHESIS - a candidate explanation, not a finding. "
                "Confirm with an experiment before acting on it as fact."
            ),
        }


@dataclass
class ConfirmedLearning:
    """A hypothesis an experiment upheld. The only kind that may be cited freely."""

    id: str
    project: str
    statement: str
    evidence: Evidence
    experiment_id: str
    confidence: Confidence = Confidence.MEDIUM
    sample_size: int = 0
    confirmed_at: datetime | None = None

    kind: RecordKind = field(default=RecordKind.CONFIRMED_LEARNING, init=False)
    confirmed: bool = field(default=True, init=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "unconfirmed": False,
            "confirmed": True,
            "id": self.id,
            "project": self.project,
            "statement": self.statement,
            "experiment_id": self.experiment_id,
            "confidence": self.confidence.value,
            "sample_size": self.sample_size,
            "evidence": self.evidence.to_dict(),
            "confirmed_at": _fmt(self.confirmed_at) if self.confirmed_at else "",
        }


@dataclass
class Recommendation:
    """
    A proposed action, backed by evidence and stated so it can be proven wrong.

    Evidence and a falsifier are required at construction. A recommendation
    without evidence is an opinion, and an opinion produced at machine scale is
    noise a human has to spend time dismissing. One without a falsifier cannot
    be checked, so it can never be retired on the facts - it just accumulates.
    """

    id: str
    project: str
    type: str
    title: str
    summary: str
    explanation: str
    evidence: Evidence
    confidence: Confidence
    expected_impact: str
    suggested_action: str
    success_metric: str
    falsifier: str
    priority: Priority = Priority.P2
    affected_campaigns: list[str] = field(default_factory=list)
    affected_platforms: list[str] = field(default_factory=list)
    recommended_experiment: str = ""
    supporting_metrics: dict[str, Any] = field(default_factory=dict)
    status: RecommendationStatus = RecommendationStatus.PROPOSED
    created_at: datetime | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    kind: RecordKind = field(default=RecordKind.RECOMMENDATION, init=False)

    def __post_init__(self) -> None:
        if self.evidence is None or self.evidence.is_empty():
            raise InvalidRecommendationError(
                f"Recommendation {self.id!r} has no evidence. A recommendation without "
                "evidence is an opinion, and this engine does not emit opinions."
            )
        if not self.falsifier.strip():
            raise InvalidRecommendationError(
                f"Recommendation {self.id!r} has no falsifier. A recommendation that "
                "cannot be proven wrong can never be retired on the facts."
            )
        if not self.success_metric.strip():
            raise InvalidRecommendationError(f"Recommendation {self.id!r} has no success metric.")

    def can_transition_to(self, new_status: RecommendationStatus) -> bool:
        return new_status in _RECOMMENDATION_TRANSITIONS.get(self.status, set())

    def apply_transition(
        self, new_status: RecommendationStatus, changed_by: str, reason: str, at: datetime
    ) -> None:
        """Move along the lifecycle with an audit entry. Raises on an illegal edge."""
        if not self.can_transition_to(new_status):
            raise InvalidRecommendationTransitionError(self.status.value, new_status.value)
        self.history.append(
            {
                "from_status": self.status.value,
                "to_status": new_status.value,
                "changed_by": changed_by,
                "reason": reason,
                "at": _fmt(at),
            }
        )
        self.status = new_status

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "id": self.id,
            "project": self.project,
            "type": self.type,
            "title": self.title,
            "summary": self.summary,
            "explanation": self.explanation,
            "reason": self.explanation,
            "confidence": self.confidence.value,
            "priority": self.priority.value,
            "expected_impact": self.expected_impact,
            "suggested_action": self.suggested_action,
            "success_metric": self.success_metric,
            "falsifier": self.falsifier,
            "affected_campaigns": list(self.affected_campaigns),
            "affected_platforms": list(self.affected_platforms),
            "recommended_experiment": self.recommended_experiment,
            "supporting_metrics": dict(self.supporting_metrics),
            "evidence": self.evidence.to_dict(),
            "status": self.status.value,
            "created_at": _fmt(self.created_at) if self.created_at else "",
            "history": list(self.history),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Recommendation:
        created = data.get("created_at")
        record = cls(
            id=str(data["id"]),
            project=str(data.get("project", "")),
            type=str(data.get("type", "")),
            title=str(data.get("title", "")),
            summary=str(data.get("summary", "")),
            explanation=str(data.get("explanation", "")),
            evidence=Evidence.from_dict(data.get("evidence") or {}),
            confidence=Confidence(str(data.get("confidence", "low"))),
            expected_impact=str(data.get("expected_impact", "")),
            suggested_action=str(data.get("suggested_action", "")),
            success_metric=str(data.get("success_metric", "")),
            falsifier=str(data.get("falsifier", "")),
            priority=Priority(str(data.get("priority", "P2"))),
            affected_campaigns=[str(c) for c in (data.get("affected_campaigns") or [])],
            affected_platforms=[str(p) for p in (data.get("affected_platforms") or [])],
            recommended_experiment=str(data.get("recommended_experiment", "")),
            supporting_metrics=dict(data.get("supporting_metrics") or {}),
            status=RecommendationStatus(str(data.get("status", "proposed"))),
            created_at=_parse(created) if created else None,
        )
        record.history = list(data.get("history") or [])
        return record


def _parse(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.rstrip("Z")).replace(tzinfo=UTC)
    return datetime.now(tz=UTC)


def _fmt(value: datetime) -> str:
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.strftime("%Y-%m-%dT%H:%M:%SZ")
