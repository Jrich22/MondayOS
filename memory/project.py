"""Cross-session project memory backed by Git-tracked Markdown files."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from memory.base import MemoryRecord


class ProjectMemory:
    """
    Cross-session, Git-tracked memory shared by all agents on a project.

    Persists to memory/project/{key}.md with YAML frontmatter. Every write
    is a new file version, giving us a Git-native change history for free.

    All agents on the same project share this memory. Reads are explicit —
    agents request specific keys, not all project memory at once.

    TODO: Implement read() — parse memory/project/{key}.md frontmatter.
    TODO: Implement write() — serialize MemoryRecord to YAML + Markdown, write file.
    TODO: Add optimistic concurrency: check on-disk version before writing.
    TODO: Implement expire() — set expires_at in frontmatter and rewrite file.
    TODO: Publish MEMORY_WRITTEN / MEMORY_READ events to EventBus.
    TODO: Add checkpoint support for session continuity.
    """

    def __init__(self, project_dir: Path) -> None:
        self._dir = project_dir / "project"

    def read(self, key: str) -> MemoryRecord | None:
        """
        Load and return the project memory record for key.

        Returns None if the key file does not exist or the record has expired.
        """
        raise NotImplementedError("TODO: load memory/project/{key}.md, parse YAML frontmatter")

    def write(self, key: str, value: Any, written_by: str, reason: str = "") -> None:
        """
        Persist a value to memory/project/{key}.md.

        Raises MemoryVersionConflictError if the on-disk version has advanced
        past what was last read (optimistic concurrency protection).
        """
        raise NotImplementedError("TODO: serialize and write memory/project/{key}.md")

    def expire(self, key: str) -> None:
        """Mark a project memory key as expired in its persisted file."""
        raise NotImplementedError

    def invalidate(self, key: str, reason: str) -> None:
        """Invalidate a project memory key and log the reason."""
        raise NotImplementedError

    def keys(self) -> list[str]:
        """
        Return all non-expired keys by scanning memory/project/.

        TODO: scan self._dir for *.md files, parse frontmatter, filter expired.
        """
        raise NotImplementedError
