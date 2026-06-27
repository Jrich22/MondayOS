"""Tests for the knowledge module."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from knowledge import (
    EntryStatus,
    EntryType,
    KnowledgeEntry,
    KnowledgeIndex,
    KnowledgeLoader,
    KnowledgeNotFoundError,
    KnowledgeParseError,
    KnowledgeStore,
    KnowledgeType,
    LifecycleStatus,
    Relationship,
    RelationType,
)
from knowledge.parser import KnowledgeParser


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_entry(
    entry_id: str = "BUG-0001",
    entry_type: KnowledgeType = KnowledgeType.BUG,
    status: LifecycleStatus = LifecycleStatus.ACTIVE,
) -> KnowledgeEntry:
    return KnowledgeEntry(
        id=entry_id,
        entry_type=entry_type,
        title="Sample entry",
        status=status,
        created_at=_now(),
        components=["core"],
        tags=["test", "sample"],
        body="## Symptom\nSomething broke.",
    )


# ---------------------------------------------------------------------------
# TestKnowledgeEntry
# ---------------------------------------------------------------------------

class TestKnowledgeEntry:
    def test_active_entry_is_active(self) -> None:
        assert _make_entry(status=LifecycleStatus.ACTIVE).is_active()

    def test_deprecated_entry_is_not_active(self) -> None:
        assert not _make_entry(status=LifecycleStatus.DEPRECATED).is_active()

    def test_superseded_entry_is_not_active(self) -> None:
        assert not _make_entry(status=LifecycleStatus.SUPERSEDED).is_active()

    def test_draft_entry_is_not_active(self) -> None:
        assert not _make_entry(status=LifecycleStatus.DRAFT).is_active()

    def test_archived_entry_is_not_active(self) -> None:
        assert not _make_entry(status=LifecycleStatus.ARCHIVED).is_active()

    def test_supersedes_checks_metadata(self) -> None:
        entry = _make_entry()
        entry.metadata["supersedes"] = "BUG-0000"
        assert entry.supersedes("BUG-0000")
        assert not entry.supersedes("BUG-9999")

    def test_all_12_knowledge_types_defined(self) -> None:
        expected = {
            "bug", "decision", "task", "sprint", "feature", "lesson",
            "pattern", "runbook", "documentation", "research", "weather",
            "experiment",
        }
        assert {t.value for t in KnowledgeType} == expected

    def test_original_four_type_values_preserved(self) -> None:
        assert KnowledgeType.BUG.value == "bug"
        assert KnowledgeType.DECISION.value == "decision"
        assert KnowledgeType.PATTERN.value == "pattern"
        assert KnowledgeType.RUNBOOK.value == "runbook"

    def test_all_5_lifecycle_statuses_defined(self) -> None:
        values = {s.value for s in LifecycleStatus}
        assert values == {"draft", "active", "deprecated", "superseded", "archived"}

    def test_default_confidence_is_1_0(self) -> None:
        assert _make_entry().confidence == 1.0

    def test_default_authored_by_is_human(self) -> None:
        assert _make_entry().authored_by == "human"

    def test_default_version_is_1(self) -> None:
        assert _make_entry().version == 1

    def test_default_summary_is_empty(self) -> None:
        assert _make_entry().summary == ""

    def test_relationships_default_to_empty_list(self) -> None:
        assert _make_entry().relationships == []

    def test_entry_type_is_backward_compat_alias(self) -> None:
        assert EntryType is KnowledgeType

    def test_entry_status_is_backward_compat_alias(self) -> None:
        assert EntryStatus is LifecycleStatus

    def test_entry_type_alias_values_match(self) -> None:
        assert EntryType.BUG.value == "bug"
        assert EntryType.DECISION.value == "decision"
        assert EntryType.PATTERN.value == "pattern"
        assert EntryType.RUNBOOK.value == "runbook"


# ---------------------------------------------------------------------------
# TestRelationship
# ---------------------------------------------------------------------------

class TestRelationship:
    def test_relationship_stores_all_fields(self) -> None:
        rel = Relationship(
            relation=RelationType.RESOLVED_BY,
            target_id="TASK-0042",
            created_at=_now(),
            created_by="human:jrich",
            note="Fixed in this task",
        )
        assert rel.relation == RelationType.RESOLVED_BY
        assert rel.target_id == "TASK-0042"
        assert rel.note == "Fixed in this task"

    def test_relationship_note_defaults_to_empty(self) -> None:
        rel = Relationship(
            relation=RelationType.PART_OF,
            target_id="SPR-0001",
            created_at=_now(),
            created_by="human",
        )
        assert rel.note == ""

    def test_all_13_relation_types_defined(self) -> None:
        assert len(RelationType) == 13


# ---------------------------------------------------------------------------
# TestKnowledgeIndex
# ---------------------------------------------------------------------------

class TestKnowledgeIndex:
    def test_initial_size_is_zero(self) -> None:
        assert KnowledgeIndex().size == 0

    def test_build_populates_index(self) -> None:
        index = KnowledgeIndex()
        index.build([_make_entry("BUG-0001"), _make_entry("BUG-0002")])
        assert index.size == 2

    def test_lookup_by_id_returns_entry(self) -> None:
        index = KnowledgeIndex()
        entry = _make_entry("BUG-0001")
        index.build([entry])
        result = index.lookup("BUG-0001")
        assert result is not None
        assert result.id == "BUG-0001"

    def test_lookup_missing_id_returns_none(self) -> None:
        index = KnowledgeIndex()
        index.build([])
        assert index.lookup("MISSING-9999") is None

    def test_by_type_returns_only_matching_type(self) -> None:
        index = KnowledgeIndex()
        index.build([
            _make_entry("BUG-0001", KnowledgeType.BUG),
            _make_entry("PAT-0001", KnowledgeType.PATTERN),
        ])
        bugs = index.by_type(KnowledgeType.BUG)
        assert len(bugs) == 1
        assert bugs[0].id == "BUG-0001"

    def test_by_tag_returns_tagged_entries(self) -> None:
        index = KnowledgeIndex()
        e1 = _make_entry("BUG-0001")   # tags: ["test", "sample"]
        e2 = _make_entry("BUG-0002")
        e2.tags = ["other"]
        index.build([e1, e2])
        results = index.by_tag("test")
        assert len(results) == 1
        assert results[0].id == "BUG-0001"

    def test_by_tag_is_case_insensitive(self) -> None:
        index = KnowledgeIndex()
        e = _make_entry("BUG-0001")
        e.tags = ["RateLimit"]
        index.build([e])
        assert len(index.by_tag("ratelimit")) == 1
        assert len(index.by_tag("RATELIMIT")) == 1

    def test_by_component_returns_relevant_entries(self) -> None:
        index = KnowledgeIndex()
        index.build([_make_entry("BUG-0001")])  # components: ["core"]
        results = index.by_component("core")
        assert len(results) == 1

    def test_non_active_entries_excluded_from_secondary_indexes(self) -> None:
        index = KnowledgeIndex()
        inactive = _make_entry("BUG-0001", status=LifecycleStatus.SUPERSEDED)
        index.build([inactive])
        assert index.size == 1               # still in _by_id
        assert index.by_type(KnowledgeType.BUG) == []   # not in secondary

    def test_all_active_returns_only_active(self) -> None:
        index = KnowledgeIndex()
        active = _make_entry("BUG-0001", status=LifecycleStatus.ACTIVE)
        old = _make_entry("BUG-0000", status=LifecycleStatus.SUPERSEDED)
        index.build([active, old])
        actives = index.all_active()
        assert len(actives) == 1
        assert actives[0].id == "BUG-0001"

    def test_add_incremental_updates_index(self) -> None:
        index = KnowledgeIndex()
        index.build([])
        entry = _make_entry("PAT-0001", KnowledgeType.PATTERN)
        index.add(entry)
        assert index.lookup("PAT-0001") is not None
        assert index.size == 1

    def test_add_replaces_existing_entry(self) -> None:
        index = KnowledgeIndex()
        old = _make_entry("BUG-0001")
        index.build([old])
        updated = _make_entry("BUG-0001")
        updated.title = "Updated title"
        index.add(updated)
        assert index.size == 1  # no duplicate
        assert index.lookup("BUG-0001").title == "Updated title"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# TestKnowledgeParser
# ---------------------------------------------------------------------------

_VALID_FRONTMATTER = """\
---
authored_by: human
components:
- integrations
confidence: 1.0
created_at: '2026-06-27T14:00:00Z'
created_by: human:jrich
entry_type: bug
id: BUG-0001
relationships: []
status: active
summary: Rate limit responses caused crashes.
tags:
- api
- rate-limit
title: Claude API 429 not retried
type_fields: {}
updated_at: '2026-06-27T14:00:00Z'
updated_by: human:jrich
version: 1
---

## Symptom
RuntimeError on HTTP 429.
"""


class TestKnowledgeParser:
    def setup_method(self) -> None:
        self.parser = KnowledgeParser()

    def test_parse_valid_frontmatter_produces_entry(self) -> None:
        entry = self.parser.parse(_VALID_FRONTMATTER)
        assert entry.id == "BUG-0001"
        assert entry.entry_type == KnowledgeType.BUG
        assert entry.status == LifecycleStatus.ACTIVE
        assert entry.title == "Claude API 429 not retried"
        assert entry.tags == ["api", "rate-limit"]

    def test_parse_missing_id_raises(self) -> None:
        raw = "---\nentry_type: bug\ntitle: x\nstatus: active\ncreated_at: '2026-01-01T00:00:00Z'\n---\n\n## Body\n"
        with pytest.raises(KnowledgeParseError) as exc_info:
            self.parser.parse(raw)
        assert "id" in str(exc_info.value)

    def test_parse_unknown_entry_type_raises(self) -> None:
        raw = "---\nid: X-0001\nentry_type: UNKNOWN\ntitle: x\nstatus: active\ncreated_at: '2026-01-01T00:00:00Z'\n---\n\n## Body\n"
        with pytest.raises(KnowledgeParseError):
            self.parser.parse(raw)

    def test_parse_unknown_status_raises(self) -> None:
        raw = "---\nid: BUG-0001\nentry_type: bug\ntitle: x\nstatus: invalid\ncreated_at: '2026-01-01T00:00:00Z'\n---\n\n## Body\n"
        with pytest.raises(KnowledgeParseError):
            self.parser.parse(raw)

    def test_parse_missing_frontmatter_raises(self) -> None:
        with pytest.raises(KnowledgeParseError):
            self.parser.parse("# Just a heading\nNo frontmatter here.")

    def test_parse_body_is_extracted(self) -> None:
        entry = self.parser.parse(_VALID_FRONTMATTER)
        assert "## Symptom" in entry.body
        assert "RuntimeError" in entry.body

    def test_parse_extra_frontmatter_goes_to_metadata(self) -> None:
        raw = (
            "---\nid: BUG-0001\nentry_type: bug\ntitle: x\nstatus: active\n"
            "created_at: '2026-01-01T00:00:00Z'\ncustom_field: custom_value\n---\n\n## Body\n"
        )
        entry = self.parser.parse(raw)
        assert entry.metadata.get("custom_field") == "custom_value"

    def test_serialize_produces_frontmatter_block(self) -> None:
        entry = _make_entry("BUG-0001")
        entry.created_at = datetime(2026, 6, 27, 14, 0, 0, tzinfo=timezone.utc)
        serialized = self.parser.serialize(entry)
        assert serialized.startswith("---\n")
        assert "\n---\n" in serialized

    def test_serialize_parse_roundtrip(self) -> None:
        original = self.parser.parse(_VALID_FRONTMATTER)
        serialized = self.parser.serialize(original)
        restored = self.parser.parse(serialized)
        assert restored.id == original.id
        assert restored.entry_type == original.entry_type
        assert restored.title == original.title
        assert restored.status == original.status
        assert restored.tags == original.tags
        assert restored.body == original.body

    def test_parse_relationships(self) -> None:
        raw = (
            "---\nid: BUG-0001\nentry_type: bug\ntitle: x\nstatus: active\n"
            "created_at: '2026-01-01T00:00:00Z'\n"
            "relationships:\n"
            "  - relation: RESOLVED_BY\n"
            "    target_id: TASK-0042\n"
            "    note: Fixed\n"
            "    created_at: '2026-01-01T00:00:00Z'\n"
            "    created_by: human\n"
            "---\n\n## Body\n"
        )
        entry = self.parser.parse(raw)
        assert len(entry.relationships) == 1
        assert entry.relationships[0].relation == RelationType.RESOLVED_BY
        assert entry.relationships[0].target_id == "TASK-0042"


# ---------------------------------------------------------------------------
# TestKnowledgeStore — requires tmp_path (writes to disk)
# ---------------------------------------------------------------------------

class TestKnowledgeStore:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path) -> None:
        self.store = KnowledgeStore(project_root=tmp_path)
        self.tmp_path = tmp_path

    def _new_entry(
        self,
        entry_type: KnowledgeType = KnowledgeType.BUG,
        title: str = "Sample entry",
    ) -> KnowledgeEntry:
        return KnowledgeEntry(
            id="",
            entry_type=entry_type,
            title=title,
            status=LifecycleStatus.ACTIVE,
            created_at=_now(),
            components=["core"],
            tags=["test", "sample"],
            body="## Symptom\nSomething broke.",
            summary="Something broke.",
        )

    def test_add_returns_entry_id(self) -> None:
        entry_id = self.store.add(self._new_entry())
        assert entry_id != ""

    def test_add_assigns_id_with_bug_prefix(self) -> None:
        entry_id = self.store.add(self._new_entry(KnowledgeType.BUG))
        assert entry_id.startswith("BUG-")

    def test_add_assigns_id_with_pattern_prefix(self) -> None:
        entry_id = self.store.add(self._new_entry(KnowledgeType.PATTERN))
        assert entry_id.startswith("PAT-")

    def test_add_id_matches_mks_format(self) -> None:
        import re
        entry_id = self.store.add(self._new_entry())
        assert re.match(r"^[A-Z]{2,7}-\d{4,}$", entry_id)

    def test_add_writes_file_to_disk(self) -> None:
        entry_id = self.store.add(self._new_entry())
        file_path = self.tmp_path / "knowledge" / "bugs" / f"{entry_id}.md"
        assert file_path.exists()

    def test_add_increments_sequence(self) -> None:
        id1 = self.store.add(self._new_entry())
        id2 = self.store.add(self._new_entry())
        assert id1 != id2
        assert int(id1.split("-")[1]) < int(id2.split("-")[1])

    def test_get_retrieves_stored_entry(self) -> None:
        entry = self._new_entry(title="Specific title for retrieval")
        entry_id = self.store.add(entry)
        retrieved = self.store.get(entry_id)
        assert retrieved.title == "Specific title for retrieval"

    def test_get_missing_id_raises(self) -> None:
        with pytest.raises(KnowledgeNotFoundError):
            self.store.get("BUG-9999")

    def test_search_returns_matching_entries(self) -> None:
        self.store.add(self._new_entry(title="Homebrew PATH fix"))
        results = self.store.search("Homebrew")
        assert len(results) == 1
        assert results[0].title == "Homebrew PATH fix"

    def test_search_is_case_insensitive(self) -> None:
        self.store.add(self._new_entry(title="Homebrew PATH fix"))
        assert len(self.store.search("homebrew")) == 1
        assert len(self.store.search("HOMEBREW")) == 1

    def test_search_returns_empty_for_no_match(self) -> None:
        self.store.add(self._new_entry(title="Something unrelated"))
        assert self.store.search("zzznomatch") == []

    def test_search_respects_limit(self) -> None:
        for i in range(5):
            self.store.add(self._new_entry(title=f"Test entry {i}"))
        results = self.store.search("Test", limit=3)
        assert len(results) <= 3

    def test_search_only_returns_active_entries(self) -> None:
        entry = self._new_entry(title="Active entry")
        entry.status = LifecycleStatus.DEPRECATED
        self.store.add(entry)
        assert self.store.search("Active entry") == []

    def test_search_scores_title_matches_higher(self) -> None:
        self.store.add(self._new_entry(title="Exact title match"))
        body_only = self._new_entry(title="Unrelated title")
        body_only.body = "## Content\nExact title match is mentioned in body."
        body_only.summary = "unrelated"
        self.store.add(body_only)
        results = self.store.search("Exact title match")
        assert results[0].title == "Exact title match"

    def test_supersede_marks_old_entry_superseded(self) -> None:
        old_id = self.store.add(self._new_entry(title="Old entry"))
        new_entry = self._new_entry(title="Replacement entry")
        self.store.supersede(old_id, new_entry)
        old = self.store.get(old_id)
        assert old.status == LifecycleStatus.SUPERSEDED

    def test_supersede_sets_superseded_by(self) -> None:
        old_id = self.store.add(self._new_entry())
        new_entry = self._new_entry(title="Replacement")
        new_id = self.store.supersede(old_id, new_entry)
        old = self.store.get(old_id)
        assert old.superseded_by == new_id

    def test_supersede_returns_new_entry_id(self) -> None:
        old_id = self.store.add(self._new_entry())
        new_id = self.store.supersede(old_id, self._new_entry(title="New version"))
        assert new_id != old_id

    def test_superseded_entry_excluded_from_search(self) -> None:
        old_id = self.store.add(self._new_entry(title="Rate limit bug"))
        self.store.supersede(old_id, self._new_entry(title="Rate limit bug v2"))
        results = self.store.search("Rate limit bug")
        assert all(r.status == LifecycleStatus.ACTIVE for r in results)

    def test_list_all_returns_active_entries(self) -> None:
        self.store.add(self._new_entry())
        self.store.add(self._new_entry())
        entries = self.store.list_all()
        assert len(entries) == 2

    def test_list_all_filters_by_type(self) -> None:
        self.store.add(self._new_entry(KnowledgeType.BUG))
        self.store.add(self._new_entry(KnowledgeType.PATTERN))
        bugs = self.store.list_all(KnowledgeType.BUG)
        assert len(bugs) == 1
        assert bugs[0].entry_type == KnowledgeType.BUG

    def test_sequence_persists_across_store_instances(self) -> None:
        id1 = self.store.add(self._new_entry())
        # Create a new store instance pointing at the same directory
        store2 = KnowledgeStore(project_root=self.tmp_path)
        id2 = store2.add(self._new_entry())
        # Sequence should not reset — id2's number > id1's number
        seq1 = int(id1.split("-")[1])
        seq2 = int(id2.split("-")[1])
        assert seq2 > seq1

    def test_load_existing_entries_on_boot(self) -> None:
        entry_id = self.store.add(self._new_entry(title="Persisted entry"))
        # New instance should load the entry from disk
        store2 = KnowledgeStore(project_root=self.tmp_path)
        loaded = store2.get(entry_id)
        assert loaded.title == "Persisted entry"
