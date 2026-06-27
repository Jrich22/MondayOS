"""Unified search engine across all MondayOS data sources."""
from __future__ import annotations

from search.query import SearchQuery, SearchResult


class SearchEngine:
    """
    Unified search interface across all MondayOS data sources.

    Agents call search() at the start of any task to retrieve relevant
    context from knowledge, tasks, and memory in a single request.
    The engine handles source routing, result merging, and ranking.

    Phase 1: Keyword-based full-text search over Markdown files.
    Phase 2: Embedding-based semantic search using a local model.
    Phase 3: Hybrid (keyword + semantic) with re-ranking.

    The public interface does not change between phases — only the
    implementation inside each search method changes.

    TODO: Implement keyword search over knowledge entries (index.md + file scan).
    TODO: Implement keyword search over task files.
    TODO: Implement source routing: only query sources named in query.sources.
    TODO: Merge results from multiple sources into a single ranked list.
    TODO: Log every query and result set for retrieval quality analysis.
    TODO: Add register_source() for pluggable source backends.
    """

    def search(self, query: SearchQuery) -> list[SearchResult]:
        """
        Execute a search and return results ranked by relevance, descending.

        Only results with score > 0.0 are returned. The result list is
        bounded by query.limit. Results from multiple sources are interleaved
        by score.

        Raises SearchError if all requested sources are unavailable.
        """
        raise NotImplementedError("TODO: implement keyword search across registered sources")

    def index_source(self, source_name: str, entries: list[dict]) -> None:
        """
        Register or refresh a data source in the search index.

        TODO: Replace list[dict] with a typed SearchableSource protocol.
        TODO: Trigger full reindex of source_name.
        """
        raise NotImplementedError
