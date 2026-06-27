"""Tests for the memory module."""
from __future__ import annotations

from pathlib import Path

import pytest

from memory import AgentMemory, ProjectMemory, SessionMemory
from memory.base import MemoryRecord, MemoryStore


class TestMemoryRecord:
    def _make_record(self, **kwargs) -> MemoryRecord:
        from datetime import datetime, timezone
        defaults = dict(
            key="k",
            value="v",
            written_by="test",
            written_at=datetime.now(tz=timezone.utc),
        )
        return MemoryRecord(**{**defaults, **kwargs})

    def test_record_not_expired_without_expiry(self) -> None:
        record = self._make_record()
        assert not record.is_expired()

    def test_mark_expired_makes_record_expired(self) -> None:
        record = self._make_record()
        record.mark_expired()
        assert record.is_expired()

    def test_repr_includes_key_and_version(self) -> None:
        record = self._make_record(key="my-key")
        assert "my-key" in repr(record)
        assert "version=1" in repr(record)


class TestMemoryStoreProtocol:
    def test_session_memory_satisfies_protocol(self) -> None:
        mem = SessionMemory(session_id="proto-test")
        assert isinstance(mem, MemoryStore)


class TestSessionMemory:
    def setup_method(self) -> None:
        self.mem = SessionMemory(session_id="test-session")

    # ------------------------------------------------------------------
    # Implemented behavior
    # ------------------------------------------------------------------

    def test_write_then_read_roundtrip(self) -> None:
        self.mem.write("k", "hello", written_by="test")
        record = self.mem.read("k")
        assert record is not None
        assert record.value == "hello"

    def test_read_missing_key_returns_none(self) -> None:
        assert self.mem.read("nonexistent") is None

    def test_write_twice_increments_version(self) -> None:
        self.mem.write("k", "v1", written_by="a")
        self.mem.write("k", "v2", written_by="b")
        record = self.mem.read("k")
        assert record is not None
        assert record.version == 2
        assert record.value == "v2"

    def test_expire_hides_key_from_read(self) -> None:
        self.mem.write("k", "v", written_by="test")
        self.mem.expire("k")
        assert self.mem.read("k") is None

    def test_expire_removes_key_from_keys(self) -> None:
        self.mem.write("alive", "yes", written_by="test")
        self.mem.write("dead", "no", written_by="test")
        self.mem.expire("dead")
        assert "alive" in self.mem.keys()
        assert "dead" not in self.mem.keys()

    def test_expire_nonexistent_key_does_not_raise(self) -> None:
        self.mem.expire("ghost")

    def test_clear_removes_all_keys(self) -> None:
        self.mem.write("a", 1, written_by="test")
        self.mem.write("b", 2, written_by="test")
        self.mem.clear()
        assert self.mem.keys() == []

    def test_write_stores_reason(self) -> None:
        self.mem.write("k", "v", written_by="agent", reason="initial bootstrap")
        assert self.mem.read("k").reason == "initial bootstrap"  # type: ignore[union-attr]

    def test_write_stores_author(self) -> None:
        self.mem.write("k", "v", written_by="agent:claude-sonnet-4-6")
        assert self.mem.read("k").written_by == "agent:claude-sonnet-4-6"  # type: ignore[union-attr]

    def test_snapshot_returns_plain_dict(self) -> None:
        self.mem.write("x", 42, written_by="test")
        snap = self.mem.snapshot()
        assert snap["x"] == 42

    def test_snapshot_excludes_expired_keys(self) -> None:
        self.mem.write("live", 1, written_by="test")
        self.mem.write("dead", 2, written_by="test")
        self.mem.expire("dead")
        snap = self.mem.snapshot()
        assert "live" in snap
        assert "dead" not in snap


class TestProjectMemory:
    # TODO: These tests require file-based backend implementation.

    def test_read_not_implemented(self, tmp_path: Path) -> None:
        mem = ProjectMemory(tmp_path)
        with pytest.raises(NotImplementedError):
            mem.read("any-key")

    def test_write_not_implemented(self, tmp_path: Path) -> None:
        mem = ProjectMemory(tmp_path)
        with pytest.raises(NotImplementedError):
            mem.write("k", "v", written_by="test")


class TestAgentMemory:
    # TODO: These tests require file-based backend implementation.

    def test_read_not_implemented(self, tmp_path: Path) -> None:
        mem = AgentMemory(agent_id="test-agent", memory_dir=tmp_path)
        with pytest.raises(NotImplementedError):
            mem.read("any-key")
