"""Knowledge store — primary read/write interface to the knowledge base."""
from __future__ import annotations

from core.types import EntityId
from knowledge.entry import EntryType, KnowledgeEntry


class KnowledgeStore:
    """
    Read/write interface to the MondayOS knowledge base.

    The store is the only path through which knowledge entries are persisted
    or retrieved. It abstracts the storage backend so callers never deal with
    files, parsing, or index management directly.

    Phase 1: Markdown files on disk (knowledge/{type}/ENTRY-ID.md).
    Phase 2: SQLite index alongside the file store for fast full-text search.

    TODO: Accept KnowledgeLoader and KnowledgeIndex as constructor arguments.
    TODO: Implement add() — serialize entry, write file, rebuild index.
    TODO: Implement get() — lookup in index, load file, parse via KnowledgeParser.
    TODO: Implement search() — delegate to SearchEngine with source="knowledge".
    TODO: Implement supersede() — write new entry, update old entry's status.
    TODO: Publish KNOWLEDGE_ENTRY_CREATED / KNOWLEDGE_ENTRY_UPDATED to EventBus.
    """

    def add(self, entry: KnowledgeEntry) -> EntityId:
        """
        Persist a new knowledge entry and rebuild the index.

        Returns the entry's ID.
        Raises KnowledgeConflictError if an entry with the same ID already exists.
        Use supersede() to replace an existing entry.
        """
        raise NotImplementedError("TODO: write entry to knowledge/{type}/{id}.md")

    def get(self, entry_id: EntityId) -> KnowledgeEntry:
        """
        Retrieve an entry by ID.

        Raises KnowledgeNotFoundError if no entry with that ID exists.
        """
        raise NotImplementedError("TODO: load and parse knowledge/{type}/{id}.md")

    def search(
        self,
        query: str,
        entry_type: EntryType | None = None,
        tags: list[str] | None = None,
        components: list[str] | None = None,
    ) -> list[KnowledgeEntry]:
        """
        Search for active entries matching the query.

        Only ACTIVE entries are returned. Results are ranked by relevance.
        Optionally filtered by type, tags, or components.

        TODO: Delegate to search.SearchEngine with source="knowledge".
        """
        raise NotImplementedError("TODO: integrate with search.SearchEngine")

    def supersede(self, old_id: EntityId, new_entry: KnowledgeEntry) -> EntityId:
        """
        Replace an entry: mark the old one SUPERSEDED, persist the new one.

        The old entry remains in storage with status=SUPERSEDED and
        superseded_by set to the new entry's ID.
        """
        raise NotImplementedError

    def list_all(self, entry_type: EntryType | None = None) -> list[KnowledgeEntry]:
        """
        Return all active entries, optionally filtered by type.

        Does not perform ranking. Use search() when relevance order matters.
        """
        raise NotImplementedError
