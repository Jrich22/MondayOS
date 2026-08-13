"""Scoring for the memory-first pipeline (§1).

Every candidate knowledge match is scored on four independent axes:

    relevance      — how well the record matches the request text
    confidence     — how sure we are the record is correct (from the record)
    freshness      — how current the record is, given its durability (§9)
    source_quality — how trustworthy its provenance is (verification/provider)

Each axis is normalised to [0, 1]. The pipeline (not this module) turns the
axes into a decision, so the thresholds live there; here we only measure.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from core.types import Timestamp
from retention.record import (
    KnowledgeRecord,
    KnowledgeStatus,
    VerificationStatus,
)

# Tokens too generic to carry relevance signal.
_STOPWORDS = frozenset(
    "a an the is are was were be been of to in on for and or how what "
    "which do does did we our i you it this that with about".split()
)

# Source quality by verification status — how the truth was established.
_VERIFICATION_QUALITY: dict[VerificationStatus, float] = {
    VerificationStatus.HUMAN_APPROVED: 1.0,
    VerificationStatus.WORKFLOW_APPROVED: 0.9,
    VerificationStatus.TEST_BACKED: 0.85,
    VerificationStatus.UNVERIFIED: 0.4,
    VerificationStatus.CONTRADICTED: 0.1,
}

# Small multiplier on source quality by originating provider. A human or a
# team workflow is more trustworthy at face value than an unverified model.
_PROVIDER_WEIGHT: dict[str, float] = {
    "human": 1.0,
    "workflow": 1.0,
    "anthropic": 0.85,
    "openai": 0.85,
    "ollama": 0.7,
    "": 0.7,
}


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def text_relevance(query: str, *fields: str) -> float:
    """Jaccard-style overlap of query tokens against a record's text fields.

    Returns a value in [0, 1]: the fraction of query tokens that appear
    anywhere in the record, lightly boosted so a strong partial match is not
    unfairly penalised by a long query.
    """
    q = _tokenize(query)
    if not q:
        return 0.0
    field_tokens: set[str] = set()
    for f in fields:
        field_tokens |= _tokenize(f)
    if not field_tokens:
        return 0.0
    hits = len(q & field_tokens)
    return hits / len(q)


def freshness(record: KnowledgeRecord, now: Timestamp | None = None) -> float:
    """Freshness in [0, 1] given the record's durability and review window (§9)."""
    now = now or datetime.now(tz=timezone.utc)

    if record.status in (KnowledgeStatus.STALE, KnowledgeStatus.SUPERSEDED):
        return 0.15

    anchor = record.last_verified_at or record.created_at

    if record.review_after is None:
        # Durable: no scheduled review. Decays very slowly with age so an
        # ancient-but-durable fact still reads as reasonably fresh.
        age_days = max(0.0, (now - anchor).total_seconds() / 86400.0)
        return max(0.5, 1.0 - age_days / 3650.0)  # floor 0.5 at ~10 years

    if now >= record.review_after:
        # Past its review window — overdue, treat as stale-fresh.
        return 0.2

    # Linear decay from creation/verification to the review deadline.
    total = (record.review_after - anchor).total_seconds()
    if total <= 0:
        return 1.0
    remaining = (record.review_after - now).total_seconds()
    return max(0.0, min(1.0, remaining / total))


def source_quality(record: KnowledgeRecord) -> float:
    """Provenance trust in [0, 1] from verification status and provider."""
    base = _VERIFICATION_QUALITY.get(record.verification, 0.4)
    weight = _PROVIDER_WEIGHT.get(record.source_provider.lower(), 0.7)
    # Evidence citations nudge quality up (capped).
    evidence_bonus = min(0.1, 0.03 * len(record.citations))
    return max(0.0, min(1.0, base * weight + evidence_bonus))


@dataclass
class ScoredMatch:
    """A knowledge record scored against a request on all four axes."""

    record: KnowledgeRecord
    relevance: float
    confidence: float
    freshness: float
    source_quality: float

    @property
    def composite(self) -> float:
        """Weighted blend used only to rank matches against each other.

        Relevance gates everything (an irrelevant record is useless however
        trustworthy), so it carries the most weight.
        """
        return (
            0.40 * self.relevance
            + 0.20 * self.confidence
            + 0.20 * self.freshness
            + 0.20 * self.source_quality
        )

    def to_dict(self) -> dict[str, float | str]:
        return {
            "knowledge_id": self.record.knowledge_id,
            "title": self.record.title,
            "relevance": round(self.relevance, 3),
            "confidence": round(self.confidence, 3),
            "freshness": round(self.freshness, 3),
            "source_quality": round(self.source_quality, 3),
            "composite": round(self.composite, 3),
        }


def score(record: KnowledgeRecord, query: str, now: Timestamp | None = None) -> ScoredMatch:
    """Score a single record against a request across all four axes."""
    rel = text_relevance(
        query,
        record.title,
        record.canonical_statement,
        record.summary,
        " ".join(record.tags),
    )
    return ScoredMatch(
        record=record,
        relevance=rel,
        confidence=max(0.0, min(1.0, record.confidence)),
        freshness=freshness(record, now),
        source_quality=source_quality(record),
    )
