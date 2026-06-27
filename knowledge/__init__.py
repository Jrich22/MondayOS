"""
MondayOS Knowledge — structured engineering knowledge base.

The knowledge system accumulates everything MondayOS learns over its lifetime:
bugs resolved, decisions made, patterns discovered, procedures documented.
AI agents query this system before starting any task to avoid repeating
work that has already been done.

Entry types and ID prefixes:
    BUG      (BUG-NNNN) — symptom, root cause, resolution, prevention
    DECISION (DEC-NNNN) — context, decision, alternatives, consequences
    PATTERN  (PAT-NNNN) — problem, solution, when to use, known applications
    RUNBOOK  (RUN-NNNN) — step-by-step operational procedure

See docs/KNOWLEDGE_SYSTEM.md for the full entry schema and lifecycle policy.

Public interface:
    KnowledgeStore   — primary read/write interface (the only path to persistence)
    KnowledgeEntry   — data model shared by all entry types
    EntryType        — enum of the four entry types
    EntryStatus      — enum of entry lifecycle states
    KnowledgeLoader  — discovers and loads entry files from disk
    KnowledgeParser  — parses Markdown + YAML frontmatter into KnowledgeEntry
    KnowledgeIndex   — fast in-memory lookup index
"""
from __future__ import annotations

from knowledge.entry import EntryStatus, EntryType, KnowledgeEntry
from knowledge.index import KnowledgeIndex
from knowledge.loader import KnowledgeLoader
from knowledge.parser import KnowledgeParser
from knowledge.store import KnowledgeStore

__all__ = [
    "KnowledgeStore",
    "KnowledgeEntry",
    "EntryType",
    "EntryStatus",
    "KnowledgeLoader",
    "KnowledgeParser",
    "KnowledgeIndex",
]
