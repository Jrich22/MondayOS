"""Tests for the knowledge module."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from knowledge import EntryStatus, EntryType, KnowledgeEntry, KnowledgeStore
from knowledge.index import KnowledgeIndex
from knowledge.parser import KnowledgeParser


def _make_entry(
    entry_id: str = "BUG-0001",
    entry_type: EntryType = EntryType.BUG,
    status: EntryStatus = EntryStatus.ACTIVE,
) -> KnowledgeEntry:
    return KnowledgeEntry(
        id=entry_id,
        entry_type=entry_type,
        title="Sample entry",
        status=status,
        created=datetime.now(tz=timezone.utc),
        components=["core"],
        tags=["test", "sample"],
        body="## Symptom\nSomething broke.",
    )


class TestKnowledgeEntry:
    def test_active_entry_is_active(self) -> None:
        assert _make_entry(status=EntryStatus.ACTIVE).is_active()

    def test_deprecated_entry_is_not_active(self) -> None:
        assert not _make_entry(status=EntryStatus.DEPRECATED).is_active()

    def test_superseded_entry_is_not_active(self) -> None:
        assert not _make_entry(status=EntryStatus.SUPERSEDED).is_active()

    def test_supersedes_checks_metadata(self) -> None:
        entry = _make_entry()
        entry.metadata["supersedes"] = "BUG-0000"
        assert entry.supersedes("BUG-0000")
        assert not entry.supersedes("BUG-9999")

    def test_entry_type_enum_values(self) -> None:
        assert EntryType.BUG.value == "bug"
        assert EntryType.DECISION.value == "decision"
        assert EntryType.PATTERN.value == "pattern"
        assert EntryType.RUNBOOK.value == "runbook"

    def test_default_confidence_is_high(self) -> None:
        entry = _make_entry()
        assert entry.confidence == "high"

    def test_default_authored_by_is_human(self) -> None:
        entry = _make_entry()
        assert entry.authored_by == "human"


class TestKnowledgeIndex:
    def test_build_not_yet_implemented(self) -> None:
        index = KnowledgeIndex()
        with pytest.raises(NotImplementedError):
            index.build([_make_entry()])

    def test_initial_size_is_zero(self) -> None:
        index = KnowledgeIndex()
        assert index.size == 0

    # TODO: The tests below define the expected behavior once build() is implemented.

    def test_lookup_by_id_returns_entry(self) -> None:
        pytest.skip("TODO: implement KnowledgeIndex.build() and lookup()")

    def test_lookup_missing_id_returns_none(self) -> None:
        pytest.skip("TODO: implement KnowledgeIndex.lookup()")

    def test_by_type_returns_only_matching_type(self) -> None:
        pytest.skip("TODO: implement KnowledgeIndex.by_type()")

    def test_by_tag_returns_tagged_entries(self) -> None:
        pytest.skip("TODO: implement KnowledgeIndex.by_tag()")

    def test_by_component_returns_relevant_entries(self) -> None:
        pytest.skip("TODO: implement KnowledgeIndex.by_component()")


class TestKnowledgeParser:
    def test_parse_not_yet_implemented(self) -> None:
        parser = KnowledgeParser()
        with pytest.raises(NotImplementedError):
            parser.parse("---\nid: BUG-0001\n---\n## Body")

    def test_serialize_not_yet_implemented(self) -> None:
        parser = KnowledgeParser()
        entry = _make_entry()
        with pytest.raises(NotImplementedError):
            parser.serialize(entry)

    # TODO: The tests below define the expected contract for the completed parser.

    def test_parse_valid_frontmatter_produces_entry(self) -> None:
        pytest.skip("TODO: implement KnowledgeParser.parse()")

    def test_parse_serialize_roundtrip(self) -> None:
        pytest.skip("TODO: verify serialize(parse(raw)) == raw")

    def test_parse_missing_required_field_raises(self) -> None:
        pytest.skip("TODO: implement KnowledgeParseError for missing fields")


class TestKnowledgeStore:
    def test_add_not_yet_implemented(self) -> None:
        store = KnowledgeStore()
        with pytest.raises(NotImplementedError):
            store.add(_make_entry())

    def test_get_not_yet_implemented(self) -> None:
        store = KnowledgeStore()
        with pytest.raises(NotImplementedError):
            store.get("BUG-0001")

    # TODO: The tests below define expected behavior once the store is implemented.

    def test_add_returns_entry_id(self) -> None:
        pytest.skip("TODO: implement KnowledgeStore.add()")

    def test_get_retrieves_stored_entry(self) -> None:
        pytest.skip("TODO: implement KnowledgeStore.get()")

    def test_search_returns_active_entries(self) -> None:
        pytest.skip("TODO: implement KnowledgeStore.search()")

    def test_supersede_marks_old_entry_superseded(self) -> None:
        pytest.skip("TODO: implement KnowledgeStore.supersede()")
