"""In-memory index over knowledge entries for fast lookups."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from knowledge.entry import KnowledgeEntry, KnowledgeType, LifecycleStatus


class KnowledgeIndex:
    """
    In-memory index of all knowledge entries for fast, structured lookups.

    The index is derived from entry files — it is always rebuilt from source,
    never edited manually. _by_id holds ALL entries (all lifecycle statuses).
    The secondary indexes (_by_type, _by_tag, _by_component) hold only ACTIVE
    entries, matching the MKS rule that only ACTIVE entries appear in default
    search results.

    add() supports incremental updates after a single entry is written,
    so a full rebuild is not required on every write.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, KnowledgeEntry] = {}
        self._by_type: dict[KnowledgeType, list[KnowledgeEntry]] = defaultdict(list)
        self._by_tag: dict[str, list[KnowledgeEntry]] = defaultdict(list)
        self._by_component: dict[str, list[KnowledgeEntry]] = defaultdict(list)

    def build(self, entries: list[KnowledgeEntry]) -> None:
        """Rebuild the entire index from a list of entries."""
        self._by_id = {}
        self._by_type = defaultdict(list)
        self._by_tag = defaultdict(list)
        self._by_component = defaultdict(list)
        for entry in entries:
            self._index_entry(entry)

    def add(self, entry: KnowledgeEntry) -> None:
        """
        Add or update a single entry in the index.

        If an entry with the same ID already exists, it is removed from all
        secondary indexes before the new version is indexed.
        """
        existing = self._by_id.get(entry.id)
        if existing is not None:
            self._remove_from_secondary(existing)
        self._index_entry(entry)

    def lookup(self, entry_id: str) -> KnowledgeEntry | None:
        """Return an entry by ID, or None if not found."""
        return self._by_id.get(entry_id)

    def by_type(self, entry_type: KnowledgeType) -> list[KnowledgeEntry]:
        """Return all ACTIVE entries of the given type."""
        return list(self._by_type[entry_type])

    def by_tag(self, tag: str) -> list[KnowledgeEntry]:
        """Return all ACTIVE entries with the given tag (case-insensitive)."""
        return list(self._by_tag[tag.lower()])

    def by_component(self, component: str) -> list[KnowledgeEntry]:
        """Return all ACTIVE entries for the given component."""
        return list(self._by_component[component])

    def all_active(self) -> list[KnowledgeEntry]:
        """Return all ACTIVE entries."""
        return [e for e in self._by_id.values() if e.is_active()]

    def write_markdown_index(self, output_path: Path) -> None:
        """Write a human-readable index.md summarising all active entries."""
        grouped: dict[str, list[KnowledgeEntry]] = defaultdict(list)
        for entry in self._by_id.values():
            if entry.is_active():
                grouped[entry.entry_type.value].append(entry)

        lines = ["# Knowledge Index\n\n"]
        for type_name in sorted(grouped):
            lines.append(f"## {type_name.upper()}\n\n")
            lines.append("| ID | Title | Tags |\n|---|---|---|\n")
            for entry in sorted(grouped[type_name], key=lambda e: e.id):
                tags = ", ".join(entry.tags)
                lines.append(f"| {entry.id} | {entry.title} | {tags} |\n")
            lines.append("\n")

        output_path.write_text("".join(lines), encoding="utf-8")

    @property
    def size(self) -> int:
        """Total number of entries in the index (all lifecycle statuses)."""
        return len(self._by_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _index_entry(self, entry: KnowledgeEntry) -> None:
        self._by_id[entry.id] = entry
        if entry.status == LifecycleStatus.ACTIVE:
            self._by_type[entry.entry_type].append(entry)
            for tag in entry.tags:
                self._by_tag[tag.lower()].append(entry)
            for component in entry.components:
                self._by_component[component].append(entry)

    def _remove_from_secondary(self, entry: KnowledgeEntry) -> None:
        et = entry.entry_type
        if et in self._by_type:
            self._by_type[et] = [e for e in self._by_type[et] if e.id != entry.id]
        for tag in entry.tags:
            key = tag.lower()
            if key in self._by_tag:
                self._by_tag[key] = [e for e in self._by_tag[key] if e.id != entry.id]
        for component in entry.components:
            if component in self._by_component:
                self._by_component[component] = [
                    e for e in self._by_component[component] if e.id != entry.id
                ]
