"""In-memory index over knowledge entries for fast lookups."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from knowledge.entry import EntryStatus, EntryType, KnowledgeEntry


class KnowledgeIndex:
    """
    In-memory index of all knowledge entries for fast, structured lookups.

    The index is derived from entry files — it is always rebuilt from source,
    never edited manually. It enables O(1) ID lookups and O(n-matching) tag
    and component queries without scanning all files on every search.

    Phase 1: In-memory dict, rebuilt on startup or after any write.
    Phase 2: SQLite-backed persistent index for full-text search performance.

    TODO: Implement build() — populate _by_id, _by_type, _by_tag, _by_component.
    TODO: Implement write_markdown_index() — write knowledge/index.md.
    TODO: Add incremental update (add/remove single entry) to avoid full rebuilds.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, KnowledgeEntry] = {}
        self._by_type: dict[EntryType, list[KnowledgeEntry]] = defaultdict(list)
        self._by_tag: dict[str, list[KnowledgeEntry]] = defaultdict(list)
        self._by_component: dict[str, list[KnowledgeEntry]] = defaultdict(list)

    def build(self, entries: list[KnowledgeEntry]) -> None:
        """Rebuild the entire index from a list of entries."""
        raise NotImplementedError("TODO: populate all index dicts; skip non-ACTIVE entries")

    def lookup(self, entry_id: str) -> KnowledgeEntry | None:
        """Return an entry by ID, or None if not found."""
        raise NotImplementedError

    def by_type(self, entry_type: EntryType) -> list[KnowledgeEntry]:
        """Return all active entries of a given type."""
        raise NotImplementedError

    def by_tag(self, tag: str) -> list[KnowledgeEntry]:
        """Return all active entries that have the given tag."""
        raise NotImplementedError

    def by_component(self, component: str) -> list[KnowledgeEntry]:
        """Return all active entries affecting the given component."""
        raise NotImplementedError

    def write_markdown_index(self, output_path: Path) -> None:
        """Write a human-readable index.md summarizing all entries."""
        raise NotImplementedError("TODO: render a Markdown table of all entries grouped by type")

    @property
    def size(self) -> int:
        """Total number of entries in the index (all statuses)."""
        return len(self._by_id)
