"""
Marketing Memory - what one project has learned, and how sure it is.

This is not general long-term memory. It holds project-specific claims about what
works for *this* brand and *this* audience: that carousels outperform image posts
here, that Tuesday morning beats Friday afternoon here. A claim that is true for
one project is frequently false for another, which is why memory is scoped to a
workspace and never shared (ADR-011).

Every entry carries its status, and the status governs how it may be used:

    tentative    a pattern the data suggests but nothing has confirmed
    validated    an experiment or a large sample upheld it
    invalidated  later evidence contradicted it

The Brain may cite validated memory freely. Tentative memory is always rendered
with its marker, because a tentative claim quoted as a finding is how a system
talks itself into a strategy nobody tested. Invalidated memory is kept rather
than deleted: knowing a thing was believed and then disproved is worth more than
a gap where it used to be.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

_MEMORY_DIRNAME = "memory"
_MEMORY_FILENAME = "memory.jsonl"

# A tentative claim needs at least this many contributing observations before the
# Brain will even record it. Below that it is an anecdote.
MIN_SAMPLE_FOR_MEMORY = 3

# Below this, a claim may never be promoted to validated regardless of what an
# experiment says: a "confirmed" pattern resting on four data points is exactly
# the overconfidence this layer exists to prevent.
MIN_SAMPLE_FOR_VALIDATION = 10


class MemoryStatus(Enum):
    """How much weight a remembered claim carries."""

    TENTATIVE = "tentative"
    VALIDATED = "validated"
    INVALIDATED = "invalidated"


class MemoryCategory(Enum):
    """What kind of thing was learned."""

    CAMPAIGN_OUTCOME = "campaign-outcome"
    CONTENT_OUTCOME = "content-outcome"
    PLATFORM_OUTCOME = "platform-outcome"
    AUDIENCE_OUTCOME = "audience-outcome"
    EXPERIMENT_OUTCOME = "experiment-outcome"
    SEASONAL_OBSERVATION = "seasonal-observation"
    RECURRING_PATTERN = "recurring-pattern"
    HISTORICAL_RECOMMENDATION = "historical-recommendation"


class InsufficientSampleError(ValueError):
    """Raised when a claim is recorded or promoted on too little data."""


@dataclass
class MemoryEntry:
    """One thing a project has learned."""

    id: str
    project: str
    category: MemoryCategory
    statement: str
    evidence: dict[str, Any] = field(default_factory=dict)
    source_campaigns: list[str] = field(default_factory=list)
    source_content: list[str] = field(default_factory=list)
    sample_size: int = 0
    confidence: str = "low"
    date_range: str = ""
    metric_affected: str = ""
    status: MemoryStatus = MemoryStatus.TENTATIVE
    synthetic: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_citable_as_fact(self) -> bool:
        """Only validated memory may be quoted without a caveat."""
        return self.status is MemoryStatus.VALIDATED

    def render(self) -> str:
        """
        A one-line rendering that can never pass a tentative claim off as settled.

        The marker is part of the string rather than a flag a caller might drop.
        """
        if self.status is MemoryStatus.TENTATIVE:
            prefix = "[TENTATIVE - unconfirmed pattern] "
        elif self.status is MemoryStatus.INVALIDATED:
            prefix = "[INVALIDATED - contradicted by later evidence] "
        else:
            prefix = "[VALIDATED] "
        suffix = f" (n={self.sample_size})"
        if self.synthetic:
            suffix += " [synthetic data]"
        return f"{prefix}{self.statement}{suffix}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project": self.project,
            "category": self.category.value,
            "statement": self.statement,
            "rendered": self.render(),
            "evidence": dict(self.evidence),
            "source_campaigns": list(self.source_campaigns),
            "source_content": list(self.source_content),
            "sample_size": self.sample_size,
            "confidence": self.confidence,
            "date_range": self.date_range,
            "metric_affected": self.metric_affected,
            "status": self.status.value,
            "synthetic": self.synthetic,
            "created_at": _fmt(self.created_at) if self.created_at else "",
            "updated_at": _fmt(self.updated_at) if self.updated_at else "",
            "history": list(self.history),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryEntry:
        created = data.get("created_at")
        updated = data.get("updated_at")
        entry = cls(
            id=str(data["id"]),
            project=str(data.get("project", "")),
            category=MemoryCategory(str(data.get("category", "recurring-pattern"))),
            statement=str(data.get("statement", "")),
            evidence=dict(data.get("evidence") or {}),
            source_campaigns=[str(c) for c in (data.get("source_campaigns") or [])],
            source_content=[str(c) for c in (data.get("source_content") or [])],
            sample_size=int(data.get("sample_size", 0)),
            confidence=str(data.get("confidence", "low")),
            date_range=str(data.get("date_range", "")),
            metric_affected=str(data.get("metric_affected", "")),
            status=MemoryStatus(str(data.get("status", "tentative"))),
            synthetic=bool(data.get("synthetic", False)),
            created_at=_parse(created) if created else None,
            updated_at=_parse(updated) if updated else None,
        )
        entry.history = list(data.get("history") or [])
        return entry


class MarketingMemory:
    """
    Persistent, workspace-scoped marketing knowledge.

    Constructed from one workspace directory, so it can only read and write the
    project it was opened for. Entries are append-only JSONL; an update appends a
    new revision and the reader takes the latest per id, so the history of what a
    project believed stays intact.
    """

    def __init__(self, workspace_dir: Path, project: str) -> None:
        self._dir = Path(workspace_dir) / _MEMORY_DIRNAME
        self._path = self._dir / _MEMORY_FILENAME
        self._project = project

    @property
    def path(self) -> Path:
        return self._path

    @property
    def project(self) -> str:
        return self._project

    def record(
        self,
        category: MemoryCategory,
        statement: str,
        sample_size: int,
        now: datetime,
        evidence: dict[str, Any] | None = None,
        source_campaigns: list[str] | None = None,
        source_content: list[str] | None = None,
        confidence: str = "low",
        date_range: str = "",
        metric_affected: str = "",
        synthetic: bool = False,
    ) -> MemoryEntry:
        """
        Remember a tentative claim. Always tentative; nothing is born validated.

        Refuses a sample below MIN_SAMPLE_FOR_MEMORY: remembering an anecdote as a
        pattern is how a brain accumulates confident folklore.
        """
        if sample_size < MIN_SAMPLE_FOR_MEMORY:
            raise InsufficientSampleError(
                f"Refusing to remember {statement!r} on {sample_size} observation(s); "
                f"{MIN_SAMPLE_FOR_MEMORY} is the minimum. Below that it is an anecdote."
            )
        entry = MemoryEntry(
            id=_entry_id(self._project, category.value, statement),
            project=self._project,
            category=category,
            statement=statement,
            evidence=dict(evidence or {}),
            source_campaigns=list(source_campaigns or []),
            source_content=list(source_content or []),
            sample_size=sample_size,
            confidence=confidence,
            date_range=date_range,
            metric_affected=metric_affected,
            status=MemoryStatus.TENTATIVE,
            synthetic=synthetic,
            created_at=now,
            updated_at=now,
        )
        self._append(entry)
        return entry

    def validate(
        self, entry_id: str, now: datetime, by: str, reason: str, sample_size: int | None = None
    ) -> MemoryEntry:
        """
        Promote a tentative claim to validated.

        Refuses below MIN_SAMPLE_FOR_VALIDATION. A pattern confirmed on a handful
        of posts is not confirmed; it is a small sample that happened to agree.
        """
        entry = self.get(entry_id)
        effective = sample_size if sample_size is not None else entry.sample_size
        if effective < MIN_SAMPLE_FOR_VALIDATION:
            raise InsufficientSampleError(
                f"Refusing to validate {entry_id!r} on {effective} observation(s); "
                f"{MIN_SAMPLE_FOR_VALIDATION} is the minimum for a validated claim."
            )
        return self._transition(
            entry, MemoryStatus.VALIDATED, now, by, reason, sample_size=effective
        )

    def invalidate(self, entry_id: str, now: datetime, by: str, reason: str) -> MemoryEntry:
        """Mark a claim contradicted. Kept, never deleted."""
        return self._transition(self.get(entry_id), MemoryStatus.INVALIDATED, now, by, reason)

    def get(self, entry_id: str) -> MemoryEntry:
        """Latest revision of one entry."""
        for entry in self.all():
            if entry.id == entry_id:
                return entry
        raise KeyError(f"No memory entry {entry_id!r} in project {self._project!r}.")

    def all(self) -> list[MemoryEntry]:
        """
        Latest revision of every entry, oldest id first.

        A malformed line is skipped rather than aborting the read.
        """
        latest: dict[str, MemoryEntry] = {}
        if not self._path.exists():
            return []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, dict):
                continue
            try:
                entry = MemoryEntry.from_dict(parsed)
            except (KeyError, ValueError):
                continue
            latest[entry.id] = entry
        return [latest[k] for k in sorted(latest)]

    def query(
        self,
        category: MemoryCategory | None = None,
        status: MemoryStatus | None = None,
        metric: str = "",
    ) -> list[MemoryEntry]:
        """Filter memory. Every argument narrows."""
        entries = self.all()
        if category is not None:
            entries = [e for e in entries if e.category is category]
        if status is not None:
            entries = [e for e in entries if e.status is status]
        if metric:
            entries = [e for e in entries if e.metric_affected == metric]
        return entries

    def validated(self) -> list[MemoryEntry]:
        """Only the claims that may be cited without a caveat."""
        return self.query(status=MemoryStatus.VALIDATED)

    def tentative(self) -> list[MemoryEntry]:
        """Claims the data suggests but nothing has confirmed."""
        return self.query(status=MemoryStatus.TENTATIVE)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _transition(
        self,
        entry: MemoryEntry,
        status: MemoryStatus,
        now: datetime,
        by: str,
        reason: str,
        sample_size: int | None = None,
    ) -> MemoryEntry:
        entry.history.append(
            {
                "from_status": entry.status.value,
                "to_status": status.value,
                "changed_by": by,
                "reason": reason,
                "at": _fmt(now),
            }
        )
        entry.status = status
        entry.updated_at = now
        if sample_size is not None:
            entry.sample_size = sample_size
        self._append(entry)
        return entry

    def _append(self, entry: MemoryEntry) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.to_dict(), sort_keys=True) + "\n")


def _entry_id(project: str, category: str, statement: str) -> str:
    """
    Stable id derived from what is remembered.

    Recording the same claim twice updates one entry rather than accumulating
    near-duplicates the Brain would then cite as independent support.
    """
    import hashlib

    material = "|".join((project, category, statement.strip().lower()))
    return f"MEM-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:12]}"


def _parse(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.rstrip("Z")).replace(tzinfo=UTC)
    return datetime.now(tz=UTC)


def _fmt(value: datetime) -> str:
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.strftime("%Y-%m-%dT%H:%M:%SZ")
