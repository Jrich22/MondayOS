"""Knowledge file discovery and loading."""
from __future__ import annotations

import warnings
from pathlib import Path

from knowledge.entry import KnowledgeEntry
from knowledge.errors import KnowledgeParseError
from knowledge.parser import KnowledgeParser

# Files to skip unconditionally — not knowledge entries
_SKIP_NAMES = frozenset({"index.md", "README.md", "CHANGELOG.md"})


class KnowledgeLoader:
    """
    Discovers and loads knowledge entry files from the filesystem.

    Walks the knowledge directory recursively, identifies valid entry files
    (*.md files with YAML frontmatter), and delegates parsing to
    KnowledgeParser. Files that fail to parse emit a warning and are skipped
    so one corrupt file never prevents the rest of the base from loading.
    """

    def __init__(self, knowledge_dir: Path) -> None:
        self._dir = knowledge_dir
        self._parser = KnowledgeParser()

    def load_all(self) -> list[KnowledgeEntry]:
        """Load all valid knowledge entries from the knowledge directory."""
        if not self._dir.exists():
            return []

        entries: list[KnowledgeEntry] = []
        for path in sorted(self._dir.rglob("*.md")):
            if path.name in _SKIP_NAMES:
                continue
            # Skip non-entry files without emitting a warning
            content = path.read_text(encoding="utf-8")
            if not content.startswith("---"):
                continue
            try:
                entry = self._parser.parse(content, source_path=str(path))
                entries.append(entry)
            except KnowledgeParseError as exc:
                warnings.warn(
                    f"Skipping {path.name}: {exc}",
                    stacklevel=2,
                )
        return entries

    def load_file(self, path: Path) -> KnowledgeEntry:
        """
        Load a single knowledge entry from a Markdown file.

        Raises KnowledgeParseError if the file has missing or malformed
        frontmatter.
        """
        raw = path.read_text(encoding="utf-8")
        return self._parser.parse(raw, source_path=str(path))
