"""The memory-first reasoning pipeline (§1).

Core rule: **search MondayOS memory before every model call.** Use an external
model only when existing knowledge is missing, weak, stale, conflicting, or
insufficient.

For every reasoning request the pipeline:

    1. classifies the request
    2. searches project memory (retained records) and org knowledge (MKS)
    3. scores each match on relevance, confidence, freshness, source quality
    4. decides one of five actions
    5. logs the decision and the estimated avoided provider cost

The pipeline decides *what to do*; it does not itself call a provider. A caller
(the Brain, an agent) executes the chosen action. This keeps the gate pure and
testable, and keeps all provider-specific logic inside ``brain.providers``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from brain.router import ModelTier
from core.types import Timestamp
from retention import cost
from retention.scoring import ScoredMatch
from retention.sources import KnowledgeSource

# --------------------------------------------------------------------------
# Request & classification
# --------------------------------------------------------------------------


class RequestKind(Enum):
    """Coarse classification of a reasoning request (§1 step 1)."""

    FACTUAL = "factual"        # look up a fact
    DECISION = "decision"      # architecture / design choice
    HOWTO = "howto"            # procedure / runbook
    CODE = "code"              # write or review code
    RESEARCH = "research"      # external research claim
    SECURITY = "security"      # security conclusion
    VOLATILE = "volatile"      # pricing / versions / current info — ages fast
    GENERAL = "general"


@dataclass
class ReasoningRequest:
    """A single reasoning request entering the pipeline."""

    text: str
    project: str = "mondayos"
    task_id: str = ""
    privacy_sensitive: bool = False    # if set, never route to an external model
    token_estimate: int = cost.DEFAULT_CALL_TOKENS


# Keyword signals per kind. First kind whose signals match wins (ordered).
_KIND_SIGNALS: list[tuple[RequestKind, frozenset[str]]] = [
    (RequestKind.SECURITY, frozenset("security vulnerability exploit auth cve secret credential".split())),
    (RequestKind.VOLATILE, frozenset("price pricing cost version latest current today now release".split())),
    (RequestKind.CODE, frozenset("code function bug refactor implement compile test stack".split())),
    (RequestKind.DECISION, frozenset("decision architecture design choose adr tradeoff should".split())),
    (RequestKind.HOWTO, frozenset("how deploy runbook steps procedure configure setup".split())),
    (RequestKind.RESEARCH, frozenset("research compare benchmark study paper evidence".split())),
    (RequestKind.FACTUAL, frozenset("what who when where define definition value".split())),
]

# Which model tier a request kind would escalate to if a provider is needed.
_KIND_TIER: dict[RequestKind, ModelTier] = {
    RequestKind.SECURITY: ModelTier.HIGH,
    RequestKind.DECISION: ModelTier.HIGH,
    RequestKind.CODE: ModelTier.HIGH,
    RequestKind.RESEARCH: ModelTier.HIGH,
    RequestKind.VOLATILE: ModelTier.STANDARD,
    RequestKind.HOWTO: ModelTier.STANDARD,
    RequestKind.FACTUAL: ModelTier.STANDARD,
    RequestKind.GENERAL: ModelTier.STANDARD,
}

# Kinds that require human/workflow review before being answered from memory
# alone (§4). Even a strong match is verified rather than trusted outright.
_REVIEW_REQUIRED_KINDS = frozenset(
    {RequestKind.SECURITY, RequestKind.DECISION, RequestKind.RESEARCH, RequestKind.VOLATILE}
)


def classify(request: ReasoningRequest) -> RequestKind:
    """Classify a request by keyword signal, defaulting to GENERAL."""
    tokens = set(re.findall(r"[a-z0-9]+", request.text.lower()))
    for kind, signals in _KIND_SIGNALS:
        if tokens & signals:
            return kind
    return RequestKind.GENERAL


# --------------------------------------------------------------------------
# Decision thresholds
# --------------------------------------------------------------------------

RELEVANCE_STRONG = 0.6      # below this, memory is insufficient → call a model
CONFIDENCE_STRONG = 0.7     # below this, verify rather than trust
FRESHNESS_STALE = 0.4       # below this, the record is stale → verify
SOURCE_QUALITY_MIN = 0.5    # below this, provenance is too weak to trust alone


class Decision(Enum):
    """The five actions the pipeline can choose (§1 step 4)."""

    ANSWER_FROM_MEMORY = "answer_from_memory"
    ANSWER_WITH_VERIFICATION = "answer_with_verification"
    CALL_LOCAL_MODEL = "call_local_model"
    CALL_EXTERNAL_MODEL = "call_external_model"
    ESCALATE_MULTI_MODEL = "escalate_multi_model"


# Where the answer ultimately comes from — recorded for cost accounting (§8).
_ANSWER_SOURCE = {
    Decision.ANSWER_FROM_MEMORY: "memory",
    Decision.ANSWER_WITH_VERIFICATION: "memory+verify",
    Decision.CALL_LOCAL_MODEL: "local",
    Decision.CALL_EXTERNAL_MODEL: "external",
    Decision.ESCALATE_MULTI_MODEL: "external",
}


@dataclass
class PipelineDecision:
    """The full, auditable result of routing one request through the gate."""

    request_text: str
    project: str
    classification: RequestKind
    decision: Decision
    rationale: str
    matches: list[ScoredMatch] = field(default_factory=list)
    answer_source: str = ""
    fallback_reason: str = ""          # why a provider was still needed ("" if not)
    tokens_avoided: int = 0
    cost_avoided_usd: float = 0.0
    decided_at: Timestamp | None = None

    @property
    def best_match(self) -> ScoredMatch | None:
        return self.matches[0] if self.matches else None

    @property
    def avoided_provider_call(self) -> bool:
        return self.decision in (
            Decision.ANSWER_FROM_MEMORY,
            Decision.ANSWER_WITH_VERIFICATION,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "request_text": self.request_text,
            "project": self.project,
            "classification": self.classification.value,
            "decision": self.decision.value,
            "rationale": self.rationale,
            "answer_source": self.answer_source,
            "fallback_reason": self.fallback_reason,
            "tokens_avoided": self.tokens_avoided,
            "cost_avoided_usd": round(self.cost_avoided_usd, 6),
            "matches": [m.to_dict() for m in self.matches],
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
        }


@dataclass
class DecisionLog:
    """Append-only log of pipeline decisions with running savings totals (§8).

    A minimal seed of the §8 cost-avoidance metrics — enough to satisfy §1's
    "log the decision and estimated avoided provider cost". The full dashboard
    metrics are a later increment.
    """

    entries: list[PipelineDecision] = field(default_factory=list)

    def record(self, decision: PipelineDecision) -> None:
        self.entries.append(decision)

    @property
    def total_cost_avoided_usd(self) -> float:
        return sum(e.cost_avoided_usd for e in self.entries)

    @property
    def total_tokens_avoided(self) -> int:
        return sum(e.tokens_avoided for e in self.entries)

    @property
    def memory_answer_rate(self) -> float:
        """Fraction of decisions answered wholly from memory."""
        if not self.entries:
            return 0.0
        answered = sum(1 for e in self.entries if e.decision == Decision.ANSWER_FROM_MEMORY)
        return answered / len(self.entries)

    @property
    def provider_avoidance_rate(self) -> float:
        """Fraction of decisions that avoided an external provider call."""
        if not self.entries:
            return 0.0
        avoided = sum(1 for e in self.entries if e.avoided_provider_call)
        return avoided / len(self.entries)


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------


class MemoryFirstPipeline:
    """Routes reasoning requests through memory before any model call (§1)."""

    def __init__(
        self,
        sources: list[KnowledgeSource],
        log: DecisionLog | None = None,
    ) -> None:
        self._sources = list(sources)
        self.log = log or DecisionLog()

    def decide(
        self,
        request: ReasoningRequest,
        now: Timestamp | None = None,
    ) -> PipelineDecision:
        """Classify, search, score, decide, and log — returning the decision."""
        now = now or datetime.now(tz=timezone.utc)
        classification = classify(request)

        matches = self._gather(request, now)
        decision = self._decide(request, classification, matches, now)

        self.log.record(decision)
        return decision

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _gather(self, request: ReasoningRequest, now: Timestamp) -> list[ScoredMatch]:
        """Search every source and merge, de-duplicating by knowledge_id."""
        seen: dict[str, ScoredMatch] = {}
        for source in self._sources:
            for match in source.search(request.text, request.project, now):
                existing = seen.get(match.record.knowledge_id)
                if existing is None or match.composite > existing.composite:
                    seen[match.record.knowledge_id] = match
        merged = sorted(seen.values(), key=lambda m: m.composite, reverse=True)
        return merged

    def _detect_conflict(self, matches: list[ScoredMatch]) -> bool:
        """True if two strongly-relevant answerable matches conflict (§5)."""
        strong = [
            m
            for m in matches
            if m.relevance >= RELEVANCE_STRONG and m.record.is_answerable()
        ]
        ids = {m.record.knowledge_id for m in strong}
        for m in strong:
            if set(m.record.conflicts_with) & ids:
                return True
        return False

    def _provider_decision(
        self,
        request: ReasoningRequest,
        tier: ModelTier,
        fallback_reason: str,
        rationale: str,
        matches: list[ScoredMatch],
        now: Timestamp,
    ) -> PipelineDecision:
        """Build a decision that calls a model, honoring privacy routing."""
        if request.privacy_sensitive:
            decision = Decision.CALL_LOCAL_MODEL
            rationale = f"{rationale} Routed to a local model (privacy-sensitive)."
        else:
            decision = Decision.CALL_EXTERNAL_MODEL
        return PipelineDecision(
            request_text=request.text,
            project=request.project,
            classification=classify(request),
            decision=decision,
            rationale=rationale,
            matches=matches,
            answer_source=_ANSWER_SOURCE[decision],
            fallback_reason=fallback_reason,
            tokens_avoided=0,
            cost_avoided_usd=0.0,
            decided_at=now,
        )

    def _decide(
        self,
        request: ReasoningRequest,
        classification: RequestKind,
        matches: list[ScoredMatch],
        now: Timestamp,
    ) -> PipelineDecision:
        tier = _KIND_TIER[classification]
        full = cost.estimate_call(tier, request.token_estimate)
        verify = cost.estimate_verification(tier, request.token_estimate)

        best = matches[0] if matches else None

        # 1. Nothing relevant, or only weak matches → knowledge is insufficient.
        if best is None or best.relevance < RELEVANCE_STRONG:
            reason = "no relevant knowledge" if best is None else "weak match"
            return self._provider_decision(
                request,
                tier,
                fallback_reason=reason,
                rationale=(
                    "Memory search found nothing usable; a model call is required."
                    if best is None
                    else f"Best match relevance {best.relevance:.2f} < {RELEVANCE_STRONG} "
                    "threshold; memory is insufficient."
                ),
                matches=matches,
                now=now,
            )

        # 2. Conflicting accepted knowledge → escalate to multi-model review (§5).
        if self._detect_conflict(matches):
            return PipelineDecision(
                request_text=request.text,
                project=request.project,
                classification=classification,
                decision=Decision.ESCALATE_MULTI_MODEL,
                rationale="Two strongly-relevant accepted records conflict; "
                "escalating to multi-model review.",
                matches=matches,
                answer_source=_ANSWER_SOURCE[Decision.ESCALATE_MULTI_MODEL],
                fallback_reason="conflicting knowledge",
                decided_at=now,
            )

        # From here the best match is strongly relevant. Decide how much to
        # trust it. Any of: not accepted, stale, low confidence, weak source,
        # or a review-required kind → answer but verify (§4).
        needs_verification = (
            not best.record.is_answerable()
            or best.freshness < FRESHNESS_STALE
            or best.record.is_stale(now)
            or best.confidence < CONFIDENCE_STRONG
            or best.source_quality < SOURCE_QUALITY_MIN
            or classification in _REVIEW_REQUIRED_KINDS
        )

        if needs_verification:
            reasons = []
            if not best.record.is_answerable():
                reasons.append(f"status is {best.record.status.value}")
            if best.freshness < FRESHNESS_STALE or best.record.is_stale(now):
                reasons.append("record is stale")
            if best.confidence < CONFIDENCE_STRONG:
                reasons.append(f"confidence {best.confidence:.2f} below threshold")
            if best.source_quality < SOURCE_QUALITY_MIN:
                reasons.append("weak provenance")
            if classification in _REVIEW_REQUIRED_KINDS:
                reasons.append(f"{classification.value} requires review")
            avoided_tokens = max(0, full.tokens - verify.tokens)
            avoided_usd = max(0.0, full.usd - verify.usd)
            best.record.record_use()
            return PipelineDecision(
                request_text=request.text,
                project=request.project,
                classification=classification,
                decision=Decision.ANSWER_WITH_VERIFICATION,
                rationale="Relevant record found but "
                + "; ".join(reasons)
                + " — answering from memory with a lightweight verification.",
                matches=matches,
                answer_source=_ANSWER_SOURCE[Decision.ANSWER_WITH_VERIFICATION],
                fallback_reason="verification",
                tokens_avoided=avoided_tokens,
                cost_avoided_usd=avoided_usd,
                decided_at=now,
            )

        # 3. Strong, fresh, confident, well-sourced, accepted → answer from memory.
        best.record.record_use()
        return PipelineDecision(
            request_text=request.text,
            project=request.project,
            classification=classification,
            decision=Decision.ANSWER_FROM_MEMORY,
            rationale=f"Accepted record {best.record.knowledge_id} matches "
            f"(relevance {best.relevance:.2f}, confidence {best.confidence:.2f}, "
            f"freshness {best.freshness:.2f}); answering from memory, no model call.",
            matches=matches,
            answer_source=_ANSWER_SOURCE[Decision.ANSWER_FROM_MEMORY],
            fallback_reason="",
            tokens_avoided=full.tokens,
            cost_avoided_usd=full.usd,
            decided_at=now,
        )
