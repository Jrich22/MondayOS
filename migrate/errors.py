"""Migration error hierarchy."""
from __future__ import annotations


class MigrationError(Exception):
    """Base error for all migration failures."""


class SourceNotFoundError(MigrationError):
    """The source document file does not exist."""

    def __init__(self, source: str, path: str) -> None:
        self.source = source
        self.path = path
        super().__init__(f"Source '{source}' not found at: {path}")


class UnknownSourceError(MigrationError):
    """A source name was requested that is not registered."""

    def __init__(self, source: str) -> None:
        self.source = source
        super().__init__(f"Unknown source '{source}'. Run 'monday migrate list' to see available sources.")


class ParseError(MigrationError):
    """A parser failed to extract candidates from a source document."""

    def __init__(self, source: str, reason: str) -> None:
        self.source = source
        super().__init__(f"Failed to parse '{source}': {reason}")


class RollbackError(MigrationError):
    """A rollback operation failed."""

    def __init__(self, run_id: str, reason: str) -> None:
        self.run_id = run_id
        super().__init__(f"Rollback of run '{run_id}' failed: {reason}")
