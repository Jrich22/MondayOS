"""
intelligence — deterministic project understanding.

Lets MondayOS answer questions *from the project*: what is being built, why a
decision was made, where something is implemented, what changed. Four pieces:

    scanner + index   what exists, and what words are in it
    symbols           what is defined, and at which line
    graph             how tasks, decisions, code, tests, commits and PRs relate
    questions         what a question retrieves, and the evidence for it

No vector database, no embeddings, no model in the indexing or retrieval path.
Identical project state produces an identical index and identical answers.
Retrieval that cannot be explained cannot be trusted, and an answer that cannot
name its source is indistinguishable from one that was invented.

The index is a **derived cache**, never a system of record. Tasks live in the
TaskManager, knowledge in the KnowledgeStore, history in git; this package reads
them and builds no second copy that could disagree.
"""

from intelligence.evidence import Citation, CitationKind, Evidence
from intelligence.graph import RelationshipGraph
from intelligence.graph import build as build_graph
from intelligence.index import IndexStats, ProjectIndex
from intelligence.index import build as build_index
from intelligence.models import (
    Edge,
    EdgeKind,
    FileKind,
    IndexedFile,
    Node,
    NodeKind,
    Symbol,
    SymbolKind,
)
from intelligence.questions import Answer, Intent, QuestionEngine

__all__ = [
    "Answer",
    "Citation",
    "CitationKind",
    "Edge",
    "EdgeKind",
    "Evidence",
    "FileKind",
    "IndexStats",
    "IndexedFile",
    "Intent",
    "Node",
    "NodeKind",
    "ProjectIndex",
    "QuestionEngine",
    "RelationshipGraph",
    "Symbol",
    "SymbolKind",
    "build_graph",
    "build_index",
]
