"""Structured knowledge records for the v4.0 Knowledge Retention initiative.

A ``KnowledgeRecord`` is the durable, structured form of something MondayOS
learned — from a provider (OpenAI / Anthropic / local), a research tool, a
completed workflow, or a human approval. It is deliberately distinct from
``knowledge.entry.KnowledgeEntry`` (the MKS 1.0 Canonical Knowledge Object):

    * ``KnowledgeEntry`` is the curated, human-facing knowledge base on disk.
    * ``KnowledgeRecord`` is a retention record with its own lifecycle
      (candidate → reviewed → accepted → …), provenance about which model
      produced it, verification state, and freshness metadata. It is what the
      memory-first pipeline searches *before* deciding to call a model.

This module defines the record and its enums only — extraction (§2), conflict
detection (§5), and persistence/retrieval services (§6) are separate concerns
built on top of this model in later increments.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from core.types import EntityId, Timestamp


class KnowledgeKind(Enum):
    """The *type* of a knowledge record — the shapes worth extracting (§2)."""

    CLAIM = "claim"
    DECISION = "decision"
    LESSON = "lesson"
    PATTERN = "pattern"
    RUNBOOK = "runbook"
    REQUIREMENT = "requirement"
    RISK = "risk"
    QUESTION = "question"  # an unresolved question — knowledge of a gap


class KnowledgeStatus(Enum):
    """Retention lifecycle (§3). Distinct from MKS ``LifecycleStatus``."""

    CANDIDATE = "candidate"    # extracted, not yet reviewed
    REVIEWED = "reviewed"      # a human/workflow looked at it, not yet trusted
    ACCEPTED = "accepted"      # trusted; may answer future requests from memory
    STALE = "stale"            # was accepted, now past its review window
    REJECTED = "rejected"      # judged wrong/unsafe; must never be used
    SUPERSEDED = "superseded"  # replaced by a newer record


class VerificationStatus(Enum):
    """How a record's truth was established (drives source quality, §1)."""

    UNVERIFIED = "unverified"              # only a model asserted it
    TEST_BACKED = "test_backed"            # supported by passing tests
    WORKFLOW_APPROVED = "workflow_approved"  # cleared a team workflow
    HUMAN_APPROVED = "human_approved"      # a human explicitly approved it
    CONTRADICTED = "contradicted"          # conflicting evidence found


class Durability(Enum):
    """Expiration class (§9) — how a record ages and when it needs re-checking."""

    DURABLE = "durable"              # true until superseded (e.g. an ADR)
    VERSION_BOUND = "version_bound"  # valid for a project/software version
    TIME_SENSITIVE = "time_sensitive"  # decays quickly (e.g. API pricing)


# Default review windows per durability class (§9). None = no scheduled review;
# such records are re-verified only when superseded or contradicted.
_DEFAULT_REVIEW_WINDOW: dict[Durability, timedelta | None] = {
    Durability.DURABLE: None,
    Durability.VERSION_BOUND: timedelta(days=90),
    Durability.TIME_SENSITIVE: timedelta(days=14),
}

# Statuses a record may hold while still being usable to answer from memory.
# CANDIDATE/REVIEWED are not yet trusted; STALE needs verification; REJECTED and
# SUPERSEDED must never answer. Only ACCEPTED is directly answerable.
_ANSWERABLE_STATUSES = frozenset({KnowledgeStatus.ACCEPTED})


def suggest_review_after(
    created_at: Timestamp,
    durability: Durability,
) -> Timestamp | None:
    """Return the default ``review_after`` for a record of this durability."""
    window = _DEFAULT_REVIEW_WINDOW[durability]
    return None if window is None else created_at + window


@dataclass
class Citation:
    """A single piece of supporting evidence or provenance for a record."""

    source: str            # e.g. "pytest", "workflow:review-9", "human:justin"
    detail: str = ""       # short human-readable note
    url: str = ""          # optional link (doc, PR, run)

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "detail": self.detail, "url": self.url}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Citation":
        return cls(source=d.get("source", ""), detail=d.get("detail", ""), url=d.get("url", ""))


@dataclass
class KnowledgeRecord:
    """A single retained, structured knowledge record (§3).

    Field set is the §3 knowledge-record contract. IDs are assigned by the
    persistence layer (later increment); callers building a candidate pass
    ``knowledge_id=""`` and let the store fill it in, mirroring ``KnowledgeStore``.
    """

    # Identity & content
    knowledge_id: EntityId
    kind: KnowledgeKind
    title: str
    canonical_statement: str          # the single-sentence durable claim
    summary: str                      # concise rationale — NOT raw model output
    project: str                      # project scope; enforces isolation (§10)
    tags: list[str] = field(default_factory=list)

    # Provenance (§2: preserve final answer + rationale, never chain-of-thought)
    source_provider: str = ""         # "anthropic" | "openai" | "ollama" | "human" | "workflow"
    source_model: str = ""            # specific model id, if any
    source_run: str = ""              # run id that produced it
    source_task: str = ""             # task id it was learned during
    citations: list[Citation] = field(default_factory=list)

    # Trust & lifecycle
    confidence: float = 0.5           # [0,1]
    verification: VerificationStatus = VerificationStatus.UNVERIFIED
    status: KnowledgeStatus = KnowledgeStatus.CANDIDATE
    durability: Durability = Durability.DURABLE

    # Freshness (§9)
    created_at: Timestamp = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    last_verified_at: Timestamp | None = None
    review_after: Timestamp | None = None

    # Usage learning (§7) & conflict tracking (§5)
    usage_count: int = 0
    conflicts_with: list[EntityId] = field(default_factory=list)
    supersedes: EntityId | None = None

    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Default the review window from durability when not explicitly set.
        if self.review_after is None:
            self.review_after = suggest_review_after(self.created_at, self.durability)

    # ------------------------------------------------------------------
    # Lifecycle predicates
    # ------------------------------------------------------------------

    def is_answerable(self) -> bool:
        """True if this record may directly answer a request (ACCEPTED only)."""
        return self.status in _ANSWERABLE_STATUSES

    def is_usable(self) -> bool:
        """True if this record may be *considered* at all.

        REJECTED and SUPERSEDED records are never usable — a rejected candidate
        must never influence a decision (§10).
        """
        return self.status not in (KnowledgeStatus.REJECTED, KnowledgeStatus.SUPERSEDED)

    def needs_review(self, now: Timestamp | None = None) -> bool:
        """True if this record is past its scheduled review window (§9)."""
        if self.review_after is None:
            return False
        return (now or datetime.now(tz=timezone.utc)) >= self.review_after

    def is_stale(self, now: Timestamp | None = None) -> bool:
        """True if the record is stale by status or by an elapsed review window."""
        if self.status == KnowledgeStatus.STALE:
            return True
        # An accepted record whose review window elapsed is effectively stale
        # even before a sweep flips its status.
        if self.status == KnowledgeStatus.ACCEPTED and self.needs_review(now):
            return True
        return False

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def record_use(self) -> None:
        """Increment usage count when this record is retrieved for a task (§7)."""
        self.usage_count += 1

    def mark_verified(
        self,
        verification: VerificationStatus,
        now: Timestamp | None = None,
    ) -> None:
        """Stamp a fresh verification and reset the review window (§9)."""
        ts = now or datetime.now(tz=timezone.utc)
        self.verification = verification
        self.last_verified_at = ts
        self.review_after = suggest_review_after(ts, self.durability)
        if self.status == KnowledgeStatus.STALE:
            self.status = KnowledgeStatus.ACCEPTED

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "knowledge_id": self.knowledge_id,
            "kind": self.kind.value,
            "title": self.title,
            "canonical_statement": self.canonical_statement,
            "summary": self.summary,
            "project": self.project,
            "tags": list(self.tags),
            "source_provider": self.source_provider,
            "source_model": self.source_model,
            "source_run": self.source_run,
            "source_task": self.source_task,
            "citations": [c.to_dict() for c in self.citations],
            "confidence": self.confidence,
            "verification": self.verification.value,
            "status": self.status.value,
            "durability": self.durability.value,
            "created_at": self.created_at.isoformat(),
            "last_verified_at": self.last_verified_at.isoformat() if self.last_verified_at else None,
            "review_after": self.review_after.isoformat() if self.review_after else None,
            "usage_count": self.usage_count,
            "conflicts_with": list(self.conflicts_with),
            "supersedes": self.supersedes,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "KnowledgeRecord":
        def _ts(v: Any) -> Timestamp | None:
            return datetime.fromisoformat(v) if v else None

        return cls(
            knowledge_id=d["knowledge_id"],
            kind=KnowledgeKind(d["kind"]),
            title=d["title"],
            canonical_statement=d["canonical_statement"],
            summary=d.get("summary", ""),
            project=d["project"],
            tags=list(d.get("tags", [])),
            source_provider=d.get("source_provider", ""),
            source_model=d.get("source_model", ""),
            source_run=d.get("source_run", ""),
            source_task=d.get("source_task", ""),
            citations=[Citation.from_dict(c) for c in d.get("citations", [])],
            confidence=d.get("confidence", 0.5),
            verification=VerificationStatus(d.get("verification", "unverified")),
            status=KnowledgeStatus(d.get("status", "candidate")),
            durability=Durability(d.get("durability", "durable")),
            created_at=_ts(d.get("created_at")) or datetime.now(tz=timezone.utc),
            last_verified_at=_ts(d.get("last_verified_at")),
            review_after=_ts(d.get("review_after")),
            usage_count=d.get("usage_count", 0),
            conflicts_with=list(d.get("conflicts_with", [])),
            supersedes=d.get("supersedes"),
            metadata=dict(d.get("metadata", {})),
        )
