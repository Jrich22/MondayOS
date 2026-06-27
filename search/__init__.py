"""
MondayOS Search — unified full-text retrieval across all data sources.

The search engine is the retrieval backbone of MondayOS. Before any agent
takes action, it searches for prior work: resolved bugs, prior decisions,
patterns, in-progress tasks. Retrieval is how the system avoids repeating
itself.

All data sources — knowledge, tasks, memory — are queryable through a
single SearchEngine call. Callers do not need to know how each source
stores its data.

Phase 1: Keyword-based search over Markdown files.
Phase 2: Embedding-based semantic search (local model, no external API required).

Public interface:
    SearchEngine  — the single search interface
    SearchQuery   — structured query (text, sources, tags, components, limit)
    SearchResult  — a single ranked result (source, id, title, snippet, score)
"""
from __future__ import annotations

from search.engine import SearchEngine
from search.query import SearchQuery, SearchResult

__all__ = [
    "SearchEngine",
    "SearchQuery",
    "SearchResult",
]
