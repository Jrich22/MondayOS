"""Knowledge entry data models — aligned with MKS 1.0 (docs/MKS.md)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.types import EntityId, Timestamp


class KnowledgeType(Enum):
    """12 knowledge types defined in MKS 1.0 Section 9."""

    BUG = "bug"
    DECISION = "decision"
    TASK = "task"
    SPRINT = "sprint"
    FEATURE = "feature"
    LESSON = "lesson"
    PATTERN = "pattern"
    RUNBOOK = "runbook"
    DOCUMENTATION = "documentation"
    RESEARCH = "research"
    WEATHER = "weather"
    EXPERIMENT = "experiment"


class LifecycleStatus(Enum):
    """Lifecycle states from MKS 1.0 Section 7."""

    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class RelationType(Enum):
    """13 typed relationship directions from MKS 1.0 Section 6.3."""

    CAUSED_BY = "CAUSED_BY"
    RESOLVED_BY = "RESOLVED_BY"
    SPAWNED = "SPAWNED"
    PART_OF = "PART_OF"
    SUPERSEDES = "SUPERSEDES"
    DEPENDS_ON = "DEPENDS_ON"
    IMPLEMENTS = "IMPLEMENTS"
    DOCUMENTS = "DOCUMENTS"
    REFERENCES = "REFERENCES"
    RELATED_TO = "RELATED_TO"
    LED_TO = "LED_TO"
    VALIDATES = "VALIDATES"
    CONTRADICTS = "CONTRADICTS"


@dataclass
class Relationship:
    """A typed, directional link from a source entry to a target entry."""

    relation: RelationType
    target_id: EntityId
    created_at: Timestamp
    created_by: str
    note: str = ""


# Backward-compatible aliases for code written against the Sprint 1 schema.
EntryType = KnowledgeType
EntryStatus = LifecycleStatus


@dataclass
class KnowledgeEntry:
    """
    A single entry in the MondayOS knowledge base.

    Conforms to the Canonical Knowledge Object (CKO) defined in MKS 1.0.
    All unrecognised frontmatter fields are stored in `metadata` for
    forward compatibility.

    IDs are assigned by KnowledgeStore at creation time. Callers pass
    id="" when creating new entries; the store fills it in before writing.
    """

    # CKO required fields — no defaults, must be provided at construction
    id: EntityId
    entry_type: KnowledgeType
    title: str
    status: LifecycleStatus
    created_at: Timestamp
    components: list[str]
    tags: list[str]
    body: str

    # CKO optional fields — safe defaults
    version: int = 1
    summary: str = ""
    created_by: str = "human"
    updated_at: Timestamp | None = None
    updated_by: str = ""
    authored_by: str = "human"
    confidence: float = 1.0
    relationships: list[Relationship] = field(default_factory=list)
    type_fields: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    superseded_by: EntityId | None = None

    def is_active(self) -> bool:
        return self.status == LifecycleStatus.ACTIVE

    def supersedes(self, other_id: EntityId) -> bool:
        """True if this entry was explicitly written to replace another."""
        return self.metadata.get("supersedes") == other_id
