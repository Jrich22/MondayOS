"""BaseParser and SourceInfo — shared infrastructure for all source parsers."""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from migrate.candidate import KnowledgeCandidate, _extract_summary, _fingerprint, slugify


@dataclass
class SourceInfo:
    """Metadata about a registered source, returned by MigrationEngine.list_sources()."""

    name: str
    source_file: str
    description: str
    entry_types: list[str]


class BaseParser(ABC):
    """
    Abstract base for all source-document parsers.

    Subclasses implement parse() and set the SOURCE_NAME and SOURCE_FILE
    class variables. The engine discovers parsers via the SOURCE_REGISTRY
    in migrate/engine.py.

    Parsers must:
    - Never raise on malformed input; return partial results with lower confidence.
    - Set source_ref to a stable, unique, human-readable string.
    - Set fingerprint via _fingerprint(content) for change detection.
    - Produce one candidate per logically distinct knowledge unit.
    """

    SOURCE_NAME: ClassVar[str]
    SOURCE_FILE: ClassVar[str]     # relative to project root
    DESCRIPTION: ClassVar[str]
    ENTRY_TYPES: ClassVar[list[str]]

    @abstractmethod
    def parse(self, text: str) -> list[KnowledgeCandidate]:
        """
        Parse the source document text into knowledge candidates.

        Args:
            text: Full content of the source file.

        Returns:
            List of KnowledgeCandidates. May be empty if nothing parseable found.
            Never raises — returns whatever can be extracted.
        """

    def source_info(self) -> SourceInfo:
        return SourceInfo(
            name=self.SOURCE_NAME,
            source_file=self.SOURCE_FILE,
            description=self.DESCRIPTION,
            entry_types=self.ENTRY_TYPES,
        )

    # ------------------------------------------------------------------
    # Shared helpers available to all parsers
    # ------------------------------------------------------------------

    @staticmethod
    def split_sections(text: str, pattern: str) -> list[str]:
        """Split text into sections where each starts with a match for `pattern`."""
        parts = re.split(f"(?={pattern})", text, flags=re.MULTILINE)
        return [p.strip() for p in parts if p.strip()]

    @staticmethod
    def extract_summary(content: str) -> str:
        return _extract_summary(content)

    @staticmethod
    def fingerprint(content: str) -> str:
        return _fingerprint(content)

    @staticmethod
    def slug(text: str) -> str:
        return slugify(text)

    @staticmethod
    def strip_header(text: str) -> str:
        """Remove the leading markdown header line(s) from a section."""
        lines = text.splitlines()
        i = 0
        while i < len(lines) and lines[i].startswith("#"):
            i += 1
        return "\n".join(lines[i:]).strip()
