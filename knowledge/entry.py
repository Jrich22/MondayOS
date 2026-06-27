"""Knowledge entry data models."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.types import EntityId, Timestamp


class EntryType(Enum):
    """The four knowledge entry types defined in KNOWLEDGE_SYSTEM.md."""

    BUG = "bug"          # symptom → root cause → resolution → prevention
    DECISION = "decision" # context → decision → alternatives → consequences
    PATTERN = "pattern"   # problem → solution → when to use / not use
    RUNBOOK = "runbook"   # step-by-step operational procedure


class EntryStatus(Enum):
    """Lifecycle status of a knowledge entry."""

    ACTIVE = "active"
    DEPRECATED = "deprecated"
    SUPERSEDED = "superseded"


@dataclass
class KnowledgeEntry:
    """
    A single entry in the MondayOS knowledge base.

    Entries are never deleted. Updates create a new entry that supersedes
    the old one; the old entry's status changes to SUPERSEDED. This preserves
    the full history of how understanding evolved over time.

    The `body` field holds the raw Markdown content below the frontmatter.
    The `metadata` field holds any frontmatter fields not covered by the
    typed attributes above (forward compatibility).

    See KNOWLEDGE_SYSTEM.md for the full schema and per-type body structure.
    """

    id: EntityId
    entry_type: EntryType
    title: str
    status: EntryStatus
    created: Timestamp
    components: list[str]
    tags: list[str]
    body: str
    metadata: dict[str, Any] = field(default_factory=dict)
    superseded_by: EntityId | None = None
    authored_by: str = "human"       # "human" or "agent:{model-id}"
    confidence: str = "high"         # "high" | "medium" | "low"

    def is_active(self) -> bool:
        return self.status == EntryStatus.ACTIVE

    def supersedes(self, other_id: EntityId) -> bool:
        """True if this entry was explicitly written to replace another."""
        return self.metadata.get("supersedes") == other_id
