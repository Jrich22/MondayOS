"""Search query and result types."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.types import EntityId


@dataclass
class SearchQuery:
    """
    A structured query to the MondayOS search engine.

    `sources` constrains which subsystems are searched. Empty means all sources.
    `tags` and `components` are AND-filtered against entry metadata.
    `limit` caps the number of results returned.

    TODO: Add `after` / `before` datetime range filtering.
    TODO: Add `min_score` threshold to exclude low-confidence matches.
    TODO: Add `semantic` flag to trigger embedding-based search (Phase 2).
    """

    text: str
    sources: list[str] = field(default_factory=list)      # e.g. ["knowledge", "tasks"]
    tags: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    limit: int = 10


@dataclass
class SearchResult:
    """
    A single ranked result from the search engine.

    `score` is a float in [0.0, 1.0] where 1.0 is a perfect match.
    `snippet` is a short excerpt showing the matching context.
    `metadata` holds source-specific data (e.g. task status, entry type).
    """

    source: str              # which subsystem this came from: "knowledge", "tasks", "memory"
    entry_id: EntityId
    title: str
    snippet: str
    score: float             # relevance: 1.0 = perfect match, 0.0 = no match
    metadata: dict[str, Any] = field(default_factory=dict)

    def __lt__(self, other: SearchResult) -> bool:
        """Higher score sorts earlier (for sorted() / heapq usage)."""
        return self.score > other.score
