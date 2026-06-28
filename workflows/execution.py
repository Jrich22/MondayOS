"""Workflow execution state — runtime objects created by WorkflowEngine."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class WorkflowStatus(Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class StepExecution:
    """Mutable runtime state for a single workflow step."""

    step_id: str
    step_type: str
    status: StepStatus = StepStatus.PENDING
    started_at: str = ""
    completed_at: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass
class WorkflowExecution:
    """
    Complete runtime state for a single workflow run.

    Created by WorkflowEngine.run() and updated in place as each step executes.
    Written to disk as JSON after the workflow completes or fails.
    """

    execution_id: str
    workflow_name: str
    workflow_version: str
    status: WorkflowStatus = WorkflowStatus.RUNNING
    started_at: str = ""
    completed_at: str = ""
    inputs: dict[str, str] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    steps: list[StepExecution] = field(default_factory=list)
    error: str = ""

    @classmethod
    def start(
        cls,
        workflow_name: str,
        workflow_version: str,
        inputs: dict[str, str] | None = None,
    ) -> WorkflowExecution:
        """Create a new execution record in RUNNING state."""
        resolved = dict(inputs or {})
        context: dict[str, Any] = {f"inputs.{k}": v for k, v in resolved.items()}
        return cls(
            execution_id=str(uuid.uuid4()),
            workflow_name=workflow_name,
            workflow_version=workflow_version,
            started_at=_now(),
            inputs=resolved,
            context=context,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict suitable for JSON output."""
        return {
            "execution_id": self.execution_id,
            "workflow_name": self.workflow_name,
            "workflow_version": self.workflow_version,
            "status": self.status.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "inputs": self.inputs,
            "context": {
                k: v for k, v in self.context.items()
                if not isinstance(v, (dict, list)) or True  # include all; JSON handles it
            },
            "steps": [
                {
                    "step_id": s.step_id,
                    "step_type": s.step_type,
                    "status": s.status.value,
                    "started_at": s.started_at,
                    "completed_at": s.completed_at,
                    "input": s.input,
                    "output": s.output,
                    "error": s.error,
                }
                for s in self.steps
            ],
            "error": self.error,
        }

    def write_log(self, logs_dir: Path) -> Path:
        """Write execution state as JSON to logs_dir. Returns the path written."""
        logs_dir.mkdir(parents=True, exist_ok=True)
        safe_name = self.workflow_name.replace("/", "-").replace(" ", "_")
        filename = f"{safe_name}-{self.execution_id[:8]}.json"
        path = logs_dir / filename
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str))
        return path


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
