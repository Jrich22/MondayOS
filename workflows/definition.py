"""Workflow definition schema — loaded from YAML files."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from workflows.errors import WorkflowNotFoundError, WorkflowValidationError


class StepType(Enum):
    ASK = "ask"
    SEARCH = "search"
    LEARN = "learn"
    TASK_CREATE = "task_create"
    TASK_START = "task_start"
    TASK_COMPLETE = "task_complete"
    HUMAN_APPROVAL = "human_approval"


@dataclass
class WorkflowInput:
    """Schema for a declared workflow input variable."""

    description: str
    required: bool = True
    default: str = ""


@dataclass
class WorkflowStep:
    """A single named step in a workflow definition."""

    id: str
    type: StepType
    description: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    message: str = ""  # used by human_approval step type


@dataclass
class WorkflowDefinition:
    """
    A complete workflow loaded from a YAML definition file.

    Workflows are immutable once loaded — all mutable state lives in
    WorkflowExecution objects created by the WorkflowEngine at runtime.
    """

    name: str
    version: str
    description: str
    steps: list[WorkflowStep]
    triggers: list[str] = field(default_factory=list)
    inputs: dict[str, WorkflowInput] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: Path) -> WorkflowDefinition:
        """
        Load and validate a workflow definition from a YAML file.

        Raises:
            WorkflowNotFoundError:    path does not exist.
            WorkflowValidationError:  YAML is malformed or required fields are missing.
        """
        if not path.exists():
            raise WorkflowNotFoundError(f"Workflow file not found: {path}")

        try:
            raw: Any = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            raise WorkflowValidationError(f"Invalid YAML in {path}: {exc}") from exc

        if not isinstance(raw, dict):
            raise WorkflowValidationError(f"Workflow file must be a YAML mapping: {path}")

        for required_field in ("name", "version", "steps"):
            if required_field not in raw:
                raise WorkflowValidationError(
                    f"Missing required field '{required_field}' in {path}"
                )

        if not isinstance(raw["steps"], list):
            raise WorkflowValidationError(f"'steps' must be a list in {path}")

        steps: list[WorkflowStep] = []
        for i, step_raw in enumerate(raw["steps"]):
            if not isinstance(step_raw, dict):
                raise WorkflowValidationError(f"Step {i} must be a mapping in {path}")
            for req in ("id", "type"):
                if req not in step_raw:
                    raise WorkflowValidationError(
                        f"Step {i} missing required field '{req}' in {path}"
                    )
            try:
                step_type = StepType(step_raw["type"])
            except ValueError:
                valid = [s.value for s in StepType]
                raise WorkflowValidationError(
                    f"Step '{step_raw['id']}' has unknown type '{step_raw['type']}'. "
                    f"Valid types: {valid}"
                ) from None

            steps.append(WorkflowStep(
                id=step_raw["id"],
                type=step_type,
                description=step_raw.get("description", ""),
                input=step_raw.get("input") or {},
                message=step_raw.get("message", ""),
            ))

        inputs: dict[str, WorkflowInput] = {}
        for k, v in (raw.get("inputs") or {}).items():
            if isinstance(v, dict):
                inputs[k] = WorkflowInput(
                    description=v.get("description", ""),
                    required=bool(v.get("required", True)),
                    default=str(v.get("default", "")),
                )
            else:
                inputs[k] = WorkflowInput(description=str(v))

        return cls(
            name=raw["name"],
            version=str(raw["version"]),
            description=raw.get("description", ""),
            steps=steps,
            triggers=list(raw.get("triggers") or []),
            inputs=inputs,
        )
