"""
MondayOS Knowledge — structured engineering knowledge base.

The knowledge system accumulates everything MondayOS learns over its lifetime:
bugs resolved, decisions made, patterns discovered, procedures documented.
AI agents query this system before starting any task to avoid repeating
work that has already been done.

Knowledge types and ID prefixes (MKS 1.0):
    BUG          (BUG-NNNN)  — symptom, root cause, resolution, prevention
    DECISION     (DEC-NNNN)  — context, decision, alternatives, consequences
    TASK         (TASK-NNNN) — completed work record
    SPRINT       (SPR-NNNN)  — sprint retrospective
    FEATURE      (FEA-NNNN)  — product capability definition
    LESSON       (LES-NNNN)  — generalised insight from experience
    PATTERN      (PAT-NNNN)  — reusable solution to a recurring problem
    RUNBOOK      (RUN-NNNN)  — step-by-step operational procedure
    DOCUMENTATION(DOC-NNNN)  — structured reference document
    RESEARCH     (RES-NNNN)  — investigation results
    WEATHER      (WEA-NNNN)  — environmental observation
    EXPERIMENT   (EXP-NNNN)  — hypothesis + method + result

See docs/MKS.md for the full Canonical Knowledge Object schema.

Public interface:
    KnowledgeStore   — primary read/write interface (the only path to persistence)
    KnowledgeEntry   — Canonical Knowledge Object data model
    KnowledgeType    — enum of all 12 entry types
    LifecycleStatus  — enum of lifecycle states
    RelationType     — enum of typed relationship directions
    Relationship     — directional typed link between entries
    EntryType        — backward-compat alias for KnowledgeType
    EntryStatus      — backward-compat alias for LifecycleStatus
    KnowledgeLoader  — discovers and loads entry files from disk
    KnowledgeParser  — parses Markdown + YAML frontmatter into KnowledgeEntry
    KnowledgeIndex   — fast in-memory lookup index
"""
from __future__ import annotations

from knowledge.entry import (
    EntryStatus,
    EntryType,
    KnowledgeEntry,
    KnowledgeType,
    LifecycleStatus,
    Relationship,
    RelationType,
)
from knowledge.errors import (
    KnowledgeConflictError,
    KnowledgeError,
    KnowledgeNotFoundError,
    KnowledgeParseError,
    KnowledgeValidationError,
)
from knowledge.index import KnowledgeIndex
from knowledge.loader import KnowledgeLoader
from knowledge.parser import KnowledgeParser
from knowledge.store import KnowledgeStore

__all__ = [
    # Core data model
    "KnowledgeStore",
    "KnowledgeEntry",
    "KnowledgeType",
    "LifecycleStatus",
    "Relationship",
    "RelationType",
    # Backward-compat aliases
    "EntryType",
    "EntryStatus",
    # Errors
    "KnowledgeError",
    "KnowledgeParseError",
    "KnowledgeNotFoundError",
    "KnowledgeConflictError",
    "KnowledgeValidationError",
    # Sub-systems
    "KnowledgeLoader",
    "KnowledgeParser",
    "KnowledgeIndex",
]
