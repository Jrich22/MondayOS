"""Knowledge store — the only path to persisted knowledge entries."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from core.types import EntityId
from knowledge.entry import KnowledgeEntry, KnowledgeType, LifecycleStatus
from knowledge.errors import KnowledgeConflictError, KnowledgeNotFoundError
from knowledge.index import KnowledgeIndex
from knowledge.loader import KnowledgeLoader
from knowledge.parser import KnowledgeParser

# Maps KnowledgeType to the subdirectory name under knowledge/
_TYPE_DIRS: dict[KnowledgeType, str] = {
    KnowledgeType.BUG: "bugs",
    KnowledgeType.DECISION: "decisions",
    KnowledgeType.TASK: "tasks",
    KnowledgeType.SPRINT: "sprints",
    KnowledgeType.FEATURE: "features",
    KnowledgeType.LESSON: "lessons",
    KnowledgeType.PATTERN: "patterns",
    KnowledgeType.RUNBOOK: "runbooks",
    KnowledgeType.DOCUMENTATION: "docs",
    KnowledgeType.RESEARCH: "research",
    KnowledgeType.WEATHER: "weather",
    KnowledgeType.EXPERIMENT: "experiments",
}

# Maps KnowledgeType to the MKS ID prefix
_TYPE_PREFIXES: dict[KnowledgeType, str] = {
    KnowledgeType.BUG: "BUG",
    KnowledgeType.DECISION: "DEC",
    KnowledgeType.TASK: "TASK",
    KnowledgeType.SPRINT: "SPR",
    KnowledgeType.FEATURE: "FEA",
    KnowledgeType.LESSON: "LES",
    KnowledgeType.PATTERN: "PAT",
    KnowledgeType.RUNBOOK: "RUN",
    KnowledgeType.DOCUMENTATION: "DOC",
    KnowledgeType.RESEARCH: "RES",
    KnowledgeType.WEATHER: "WEA",
    KnowledgeType.EXPERIMENT: "EXP",
}

_SEQUENCES_FILENAME = ".sequences.json"


class KnowledgeStore:
    """
    Read/write interface to the MondayOS knowledge base.

    Phase 1 uses a Markdown-on-disk backend: entries are YAML-frontmatter
    Markdown files stored under knowledge/{type}/{ID}.md. An in-memory
    KnowledgeIndex provides O(1) ID lookups and tag/component filtering.

    Sequence numbers (per-type monotonic counters) are persisted to
    knowledge/.sequences.json so IDs are stable across restarts.

    The StorageBackend protocol defined in MKS 1.0 Section 11 is the
    migration path to SQLite, PostgreSQL, or Neo4j — swap the backend
    in __init__ without changing KnowledgeStore's public interface.
    """

    def __init__(self, project_root: Path = Path(".")) -> None:
        self._knowledge_dir = project_root / "knowledge"
        self._parser = KnowledgeParser()
        self._loader = KnowledgeLoader(self._knowledge_dir)
        self._index = KnowledgeIndex()
        self._sequences: dict[str, int] = {}
        self._sequences_path = self._knowledge_dir / _SEQUENCES_FILENAME
        self._boot()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def add(self, entry: KnowledgeEntry) -> EntityId:
        """
        Persist a new knowledge entry, assign its ID, and update the index.

        The caller passes entry.id="" to let the store assign the ID.
        Raises KnowledgeConflictError if a non-empty ID already exists.
        """
        if entry.id and self._index.lookup(entry.id) is not None:
            raise KnowledgeConflictError(entry.id)

        entry.id = self._next_id(entry.entry_type)

        if entry.updated_at is None:
            entry.updated_at = entry.created_at
        if not entry.updated_by:
            entry.updated_by = entry.created_by

        self._write_file(entry)
        self._index.add(entry)
        return entry.id

    def get(self, entry_id: EntityId) -> KnowledgeEntry:
        """
        Return an entry by ID.

        Raises KnowledgeNotFoundError if no entry with that ID exists.
        """
        entry = self._index.lookup(entry_id)
        if entry is None:
            raise KnowledgeNotFoundError(entry_id)
        return entry

    def search(
        self,
        query: str,
        entry_type: KnowledgeType | None = None,
        tags: list[str] | None = None,
        components: list[str] | None = None,
        limit: int = 10,
    ) -> list[KnowledgeEntry]:
        """
        Keyword search across ACTIVE entries. Results are ranked by score.

        Scoring (per query term):
            +3.0  title match
            +2.0  tag match
            +1.0  summary match
            +0.5  body match

        Only ACTIVE entries are returned. Optionally filtered by type, tags,
        or components before scoring.
        """
        candidates = self._index.all_active()

        if entry_type is not None:
            candidates = [e for e in candidates if e.entry_type == entry_type]
        if tags:
            tag_set = {t.lower() for t in tags}
            candidates = [e for e in candidates if tag_set & {t.lower() for t in e.tags}]
        if components:
            comp_set = set(components)
            candidates = [e for e in candidates if comp_set & set(e.components)]

        terms = query.lower().split()
        if not terms:
            return candidates[:limit]

        scored: list[tuple[float, KnowledgeEntry]] = []
        for entry in candidates:
            score = _score_entry(entry, terms)
            if score > 0.0:
                scored.append((score, entry))

        scored.sort(key=lambda t: t[0], reverse=True)
        return [entry for _, entry in scored[:limit]]

    def supersede(self, old_id: EntityId, new_entry: KnowledgeEntry) -> EntityId:
        """
        Replace an entry: mark the old one SUPERSEDED, persist the new one.

        Raises KnowledgeNotFoundError if old_id does not exist.
        """
        old = self.get(old_id)

        new_id = self.add(new_entry)

        # Mark the old entry as superseded
        old.status = LifecycleStatus.SUPERSEDED
        old.superseded_by = new_id
        old.version += 1
        old.updated_at = datetime.now(tz=timezone.utc)

        self._write_file(old)
        self._index.add(old)  # removes from ACTIVE secondary indexes, keeps in _by_id

        return new_id

    def list_all(self, entry_type: KnowledgeType | None = None) -> list[KnowledgeEntry]:
        """Return all ACTIVE entries, optionally filtered by type."""
        entries = self._index.all_active()
        if entry_type is not None:
            entries = [e for e in entries if e.entry_type == entry_type]
        return entries

    def remove(self, entry_id: EntityId) -> None:
        """
        Delete a knowledge entry from disk and rebuild the in-memory index.

        Used by the migration rollback path. Raises KnowledgeNotFoundError
        if entry_id does not exist.
        """
        entry = self.get(entry_id)
        type_dir = self._knowledge_dir / _TYPE_DIRS[entry.entry_type]
        file_path = type_dir / f"{entry_id}.md"
        if file_path.exists():
            file_path.unlink()
        # Rebuild the index from remaining files on disk
        entries = self._loader.load_all()
        self._index = KnowledgeIndex()
        self._index.build(entries)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _boot(self) -> None:
        """Load existing entries from disk and build the in-memory index."""
        self._load_sequences()
        entries = self._loader.load_all()
        self._index.build(entries)

    def _load_sequences(self) -> None:
        if self._sequences_path.exists():
            try:
                self._sequences = json.loads(
                    self._sequences_path.read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, OSError):
                self._sequences = {}
        else:
            self._sequences = {}

    def _save_sequences(self) -> None:
        self._knowledge_dir.mkdir(parents=True, exist_ok=True)
        self._sequences_path.write_text(
            json.dumps(self._sequences, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _next_id(self, entry_type: KnowledgeType) -> EntityId:
        """
        Allocate the next ID for a type, healing a stale counter.

        The counter is the fast path, but it only reflects allocations made
        through this process. Entries written on another branch or checkout
        are invisible to it, so a counter that lags behind disk would reissue
        a live ID — this has produced two duplicate-ID incidents (see
        knowledge/KNOWLEDGE_LEDGER_REPAIR.md and RES-0100). Taking the max of
        the two makes the counter an optimisation rather than the sole
        authority.
        """
        prefix = _TYPE_PREFIXES[entry_type]
        counter = self._sequences.get(prefix, 0)
        next_seq = max(counter, self._highest_id_on_disk(prefix)) + 1
        self._sequences[prefix] = next_seq
        self._save_sequences()
        return f"{prefix}-{next_seq:04d}"

    def _highest_id_on_disk(self, prefix: str) -> int:
        """
        Highest sequence number for `prefix` among entry files on disk.

        Reads filenames rather than file contents: _write_file names every
        entry {ID}.md, so the filename is the record of which IDs are taken,
        and an entry whose body is malformed or unreadable still reserves its
        ID. Scans recursively so entries filed under an unexpected directory
        are still counted.
        """
        if not self._knowledge_dir.exists():
            return 0

        pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
        highest = 0
        try:
            paths = self._knowledge_dir.rglob(f"{prefix}-*.md")
        except OSError:
            return 0

        for path in paths:
            match = pattern.match(path.stem)
            if match:
                highest = max(highest, int(match.group(1)))
        return highest

    def _write_file(self, entry: KnowledgeEntry) -> None:
        type_dir = self._knowledge_dir / _TYPE_DIRS[entry.entry_type]
        type_dir.mkdir(parents=True, exist_ok=True)
        file_path = type_dir / f"{entry.id}.md"
        file_path.write_text(self._parser.serialize(entry), encoding="utf-8")


def _score_entry(entry: KnowledgeEntry, terms: list[str]) -> float:
    """Score an entry against a list of lowercase query terms."""
    score = 0.0
    title_lower = entry.title.lower()
    summary_lower = entry.summary.lower()
    body_lower = entry.body.lower()
    tags_lower = {t.lower() for t in entry.tags}

    for term in terms:
        if term in title_lower:
            score += 3.0
        if term in tags_lower:
            score += 2.0
        if term in summary_lower:
            score += 1.0
        if term in body_lower:
            score += 0.5

    return score
