"""Typed error classes for the tasks module."""
from __future__ import annotations


class TaskError(Exception):
    """Base class for all task module errors."""


class TaskNotFoundError(TaskError):
    """Raised when a task cannot be found by ID."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"Task not found: {task_id}")


class TaskValidationError(TaskError):
    """Raised when a task fails validation (missing required fields, etc.)."""

    def __init__(self, message: str, field: str = "") -> None:
        self.field = field
        super().__init__(message)


class InvalidTransitionError(TaskError):
    """Raised when a requested status transition is not permitted."""

    def __init__(self, from_status: str, to_status: str) -> None:
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"Cannot transition from {from_status!r} to {to_status!r}"
        )


class TaskParseError(TaskError):
    """Raised when a task file cannot be parsed."""

    def __init__(self, message: str, source_path: str = "<string>") -> None:
        self.source_path = source_path
        super().__init__(message)
