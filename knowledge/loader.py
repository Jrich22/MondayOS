"""Knowledge file discovery and loading."""
from __future__ import annotations

from pathlib import Path

from knowledge.entry import KnowledgeEntry


class KnowledgeLoader:
    """
    Discovers and loads knowledge entry files from the filesystem.

    Walks the knowledge/ directory, identifies valid entry files (*.md with
    YAML frontmatter), and delegates parsing to KnowledgeParser. Files that
    fail to parse emit a warning and are skipped — one bad file does not
    prevent the rest of the knowledge base from loading.

    TODO: Implement load_all() — walk knowledge/ with pathlib, call load_file().
    TODO: Validate that filename matches the `id` field in frontmatter.
    TODO: Emit structured warning (not exception) for unparseable files.
    TODO: Track which files were skipped and why for diagnostics.
    """

    def __init__(self, knowledge_dir: Path) -> None:
        self._dir = knowledge_dir

    def load_all(self) -> list[KnowledgeEntry]:
        """Load all valid knowledge entries from the knowledge directory."""
        raise NotImplementedError("TODO: walk self._dir for *.md, parse each via KnowledgeParser")

    def load_file(self, path: Path) -> KnowledgeEntry:
        """
        Load a single knowledge entry from a Markdown file.

        Raises KnowledgeParseError if the file is missing required frontmatter.
        """
        raise NotImplementedError("TODO: read file, delegate to KnowledgeParser.parse()")
