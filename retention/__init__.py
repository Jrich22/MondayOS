"""MondayOS Knowledge Retention — memory-first reasoning (v4.0 initiative).

Core rule: search MondayOS memory before every model call; use an external
model only when existing knowledge is missing, weak, stale, conflicting, or
insufficient for the task.

This package currently ships the two foundational pieces:

    * Structured knowledge records (§3)  — ``KnowledgeRecord`` and its enums.
    * The memory-first pipeline (§1)      — ``MemoryFirstPipeline`` and the
      classify → search → score → decide → log flow.

Extraction (§2), validation (§4), conflict detection (§5), a retrieval service
(§6), usage learning (§7), cost dashboards (§8), and the Memory workspace (§11)
build on these in later increments.
"""
from __future__ import annotations

from retention.conflict import (
    ConflictDetector,
    ConflictOutcome,
    ConflictResult,
    DeterministicMatcher,
    MatchScore,
    MergeProposal,
    SimilarityMatcher,
    SupersedeProposal,
)
from retention.cost import CostEstimate, estimate_call, estimate_verification
from retention.extraction import (
    DroppedSegment,
    ExtractionResult,
    KnowledgeExtractor,
)
from retention.pipeline import (
    Decision,
    DecisionLog,
    MemoryFirstPipeline,
    PipelineDecision,
    ReasoningRequest,
    RequestKind,
    classify,
)
from retention.record import (
    Citation,
    Durability,
    KnowledgeKind,
    KnowledgeRecord,
    KnowledgeStatus,
    VerificationStatus,
    suggest_review_after,
)
from retention.scoring import ScoredMatch, freshness, score, source_quality, text_relevance
from retention.validation import (
    Evidence,
    EvidenceType,
    KnowledgeValidator,
    PromotionOutcome,
    ReviewPriority,
    ValidatedCandidate,
    ValidationResult,
    validate_extraction,
)
from retention.sources import (
    KnowledgeSource,
    OrgKnowledgeSource,
    RecordSource,
)

__all__ = [
    # Records (§3)
    "KnowledgeRecord",
    "KnowledgeKind",
    "KnowledgeStatus",
    "VerificationStatus",
    "Durability",
    "Citation",
    "suggest_review_after",
    # Extraction (§2)
    "KnowledgeExtractor",
    "ExtractionResult",
    "DroppedSegment",
    # Validation (§4/§6/§7)
    "KnowledgeValidator",
    "PromotionOutcome",
    "ValidationResult",
    "ValidatedCandidate",
    "Evidence",
    "EvidenceType",
    "ReviewPriority",
    "validate_extraction",
    # Conflict detection (§5)
    "ConflictDetector",
    "ConflictOutcome",
    "ConflictResult",
    "MergeProposal",
    "SupersedeProposal",
    "SimilarityMatcher",
    "DeterministicMatcher",
    "MatchScore",
    # Pipeline (§1)
    "MemoryFirstPipeline",
    "ReasoningRequest",
    "RequestKind",
    "Decision",
    "PipelineDecision",
    "DecisionLog",
    "classify",
    # Scoring
    "ScoredMatch",
    "score",
    "freshness",
    "source_quality",
    "text_relevance",
    # Sources
    "KnowledgeSource",
    "RecordSource",
    "OrgKnowledgeSource",
    # Cost
    "CostEstimate",
    "estimate_call",
    "estimate_verification",
]
