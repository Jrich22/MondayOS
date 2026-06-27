"""Markdown + YAML frontmatter parser for knowledge entries."""
from __future__ import annotations

from knowledge.entry import KnowledgeEntry


class KnowledgeParser:
    """
    Parses raw Markdown (with YAML frontmatter) into a KnowledgeEntry.

    File format:
        ---
        id: BUG-0001
        type: bug
        title: Short description
        ... (other frontmatter fields)
        ---

        ## Symptom
        ...

    The parser is strict: missing required fields raise KnowledgeParseError.
    Unknown frontmatter fields are stored in entry.metadata (forward compat).

    TODO: Use PyYAML to parse the frontmatter block.
    TODO: Validate all required fields (id, type, title, status, created, components, tags).
    TODO: Convert `created` string to UTC datetime.
    TODO: Map the `type` string to EntryType enum.
    TODO: Return structured KnowledgeParseError with field-level detail.
    """

    def parse(self, raw: str, source_path: str = "<string>") -> KnowledgeEntry:
        """
        Parse a raw Markdown string into a KnowledgeEntry.

        Args:
            raw:         Full file content including the --- frontmatter block.
            source_path: File path for error messages.

        Raises KnowledgeParseError if frontmatter is missing or malformed.
        """
        raise NotImplementedError("TODO: split frontmatter from body, parse YAML, build KnowledgeEntry")

    def serialize(self, entry: KnowledgeEntry) -> str:
        """
        Serialize a KnowledgeEntry to the Markdown + YAML frontmatter format.

        The output of serialize(parse(raw)) must round-trip cleanly.

        TODO: Use PyYAML dumper with explicit field ordering.
        TODO: Preserve unknown metadata fields in frontmatter.
        """
        raise NotImplementedError("TODO: build YAML frontmatter block + Markdown body")
