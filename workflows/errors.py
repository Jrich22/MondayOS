"""Workflow error hierarchy."""
from __future__ import annotations


class WorkflowError(Exception):
    """Base error for all workflow failures."""


class WorkflowNotFoundError(WorkflowError):
    """A workflow definition was requested but does not exist."""


class WorkflowValidationError(WorkflowError):
    """A workflow YAML definition is structurally invalid."""


class StepExecutionError(WorkflowError):
    """A workflow step failed during execution."""

    def __init__(self, step_id: str, message: str) -> None:
        self.step_id = step_id
        super().__init__(f"Step '{step_id}' failed: {message}")


class ApprovalDenied(WorkflowError):
    """A human_approval step was rejected by the human operator."""

    def __init__(self, step_id: str) -> None:
        self.step_id = step_id
        super().__init__(f"Approval denied at step '{step_id}'")
