"""Typed error classes for the knowledge module."""
from __future__ import annotations


class KnowledgeError(Exception):
    """Base class for all knowledge module errors."""


class KnowledgeParseError(KnowledgeError):
    """Raised when a Markdown file cannot be parsed as a KnowledgeEntry."""

    def __init__(self, message: str, source_path: str = "<string>", field: str = "") -> None:
        self.source_path = source_path
        self.field = field
        super().__init__(message)


class KnowledgeNotFoundError(KnowledgeError):
    """Raised when a knowledge entry cannot be found by ID."""

    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id
        super().__init__(f"Knowledge entry not found: {entry_id}")


class KnowledgeConflictError(KnowledgeError):
    """Raised when attempting to create an entry with a duplicate ID."""

    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id
        super().__init__(f"Knowledge entry already exists: {entry_id}")


class KnowledgeValidationError(KnowledgeError):
    """Raised when a KnowledgeEntry fails MKS validation rules."""

    def __init__(self, message: str, entry_id: str = "") -> None:
        self.entry_id = entry_id
        super().__init__(message)
