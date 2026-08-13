"""Knowledge sources the memory-first pipeline searches before a model call.

The pipeline distinguishes two searches (§1):

    * project memory        — retained ``KnowledgeRecord``s for this project
    * organizational memory — the curated ``KnowledgeStore`` (MKS entries)

Both are exposed through the ``KnowledgeSource`` protocol so the pipeline stays
decoupled from storage. This increment ships two adapters:

    RecordSource       — over an in-memory list of ``KnowledgeRecord`` (project
                         memory / newly retained knowledge). This is the seam a
                         persistent retention store (§6) plugs into later.
    OrgKnowledgeSource — over the existing ``knowledge.store.KnowledgeStore``,
                         adapting MKS ``KnowledgeEntry`` records into scored
                         ``KnowledgeRecord``s so both searches speak one type.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from core.types import Timestamp
from retention.record import (
    Durability,
    KnowledgeKind,
    KnowledgeRecord,
    KnowledgeStatus,
    VerificationStatus,
)
from retention.scoring import ScoredMatch, score

if TYPE_CHECKING:
    from knowledge.entry import KnowledgeEntry
    from knowledge.store import KnowledgeStore


class KnowledgeSource(Protocol):
    """A searchable pool of knowledge records scoped to a project."""

    def search(
        self,
        query: str,
        project: str,
        now: Timestamp | None = None,
        limit: int = 10,
    ) -> list[ScoredMatch]:
        """Return scored, usable matches for the query within ``project``."""
        ...


class RecordSource:
    """In-memory ``KnowledgeSource`` over retained ``KnowledgeRecord``s.

    Enforces project isolation (§10) and never returns REJECTED or SUPERSEDED
    records (a rejected candidate must never be used, §10).
    """

    def __init__(self, records: list[KnowledgeRecord] | None = None) -> None:
        self._records: list[KnowledgeRecord] = list(records or [])

    def add(self, record: KnowledgeRecord) -> None:
        self._records.append(record)

    def all(self) -> list[KnowledgeRecord]:
        return list(self._records)

    def search(
        self,
        query: str,
        project: str,
        now: Timestamp | None = None,
        limit: int = 10,
    ) -> list[ScoredMatch]:
        matches: list[ScoredMatch] = []
        for record in self._records:
            if record.project != project:  # project isolation
                continue
            if not record.is_usable():      # exclude rejected / superseded
                continue
            scored = score(record, query, now)
            if scored.relevance > 0.0:
                matches.append(scored)
        matches.sort(key=lambda m: m.composite, reverse=True)
        return matches[:limit]


# Map MKS knowledge types to retention kinds so org entries score uniformly.
_MKS_KIND_MAP = {
    "decision": KnowledgeKind.DECISION,
    "lesson": KnowledgeKind.LESSON,
    "pattern": KnowledgeKind.PATTERN,
    "runbook": KnowledgeKind.RUNBOOK,
    "research": KnowledgeKind.CLAIM,
    "documentation": KnowledgeKind.CLAIM,
    "feature": KnowledgeKind.REQUIREMENT,
    "bug": KnowledgeKind.RISK,
}


def _entry_to_record(entry: "KnowledgeEntry", project: str) -> KnowledgeRecord:
    """Adapt a curated MKS ``KnowledgeEntry`` into a ``KnowledgeRecord``.

    Curated, on-disk entries are treated as accepted, human-approved, durable
    knowledge — they are the org's reviewed knowledge base. Confidence carries
    over from the entry.
    """
    kind = _MKS_KIND_MAP.get(entry.entry_type.value, KnowledgeKind.CLAIM)
    return KnowledgeRecord(
        knowledge_id=entry.id,
        kind=kind,
        title=entry.title,
        canonical_statement=entry.summary or entry.title,
        summary=entry.summary,
        project=project,
        tags=list(entry.tags),
        source_provider="human",
        source_run="",
        source_task="",
        confidence=entry.confidence,
        verification=VerificationStatus.HUMAN_APPROVED,
        status=KnowledgeStatus.ACCEPTED,
        durability=Durability.DURABLE,
        created_at=entry.created_at,
        last_verified_at=entry.updated_at or entry.created_at,
        metadata={"origin": "mks", "component": list(entry.components)},
    )


class OrgKnowledgeSource:
    """``KnowledgeSource`` adapter over the curated ``KnowledgeStore``."""

    def __init__(self, store: "KnowledgeStore", project: str = "mondayos") -> None:
        self._store = store
        self._project = project

    def search(
        self,
        query: str,
        project: str,
        now: Timestamp | None = None,
        limit: int = 10,
    ) -> list[ScoredMatch]:
        # The curated store is the org knowledge base; treat any request's
        # project as able to read it, but tag records with their own project.
        entries = self._store.search(query, limit=limit)
        matches: list[ScoredMatch] = []
        for entry in entries:
            record = _entry_to_record(entry, self._project)
            scored = score(record, query, now)
            if scored.relevance > 0.0:
                matches.append(scored)
        matches.sort(key=lambda m: m.composite, reverse=True)
        return matches[:limit]
