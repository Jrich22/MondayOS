"""WorkflowEngine — loads YAML workflow definitions and executes them step-by-step."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from workflows.definition import StepType, WorkflowDefinition, WorkflowStep
from workflows.errors import (
    ApprovalDenied,
    StepExecutionError,
    WorkflowNotFoundError,
    WorkflowValidationError,
)
from workflows.execution import StepExecution, StepStatus, WorkflowExecution, WorkflowStatus, _now

# Signature: (message: str, context: dict) → bool (True = approved, False = rejected)
ApprovalHandler = Callable[[str, dict], bool]


def _terminal_approval_handler(message: str, context: dict) -> bool:
    """Default approval handler: blocks on terminal input."""
    print()
    print("=" * 60)
    print("APPROVAL REQUIRED")
    print("=" * 60)
    print(message)
    print("=" * 60)
    while True:
        answer = input("Approve? [y/N]: ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no", ""):
            return False


class WorkflowEngine:
    """
    Loads YAML workflow definitions and executes them step-by-step.

    Each step delegates to the Monday public API. The execution context
    accumulates step outputs so later steps can reference earlier results
    via {step_id.output_key} template substitution.

    A WorkflowExecution object is written to logs_dir as JSON after each
    workflow completes (successfully, on failure, or when cancelled).

    Args:
        monday:           Initialised Monday instance — all step calls go through it.
        definitions_dir:  Directory containing *.yaml workflow definition files.
        logs_dir:         Directory where execution logs are written.
        approval_handler: Callable(message, context) → bool for human_approval steps.
                          Defaults to a blocking terminal prompt. Inject in tests.
    """

    def __init__(
        self,
        monday: Any,
        definitions_dir: Path,
        logs_dir: Path,
        approval_handler: ApprovalHandler | None = None,
    ) -> None:
        self._monday = monday
        self._definitions_dir = definitions_dir
        self._logs_dir = logs_dir
        self._approval_handler: ApprovalHandler = (
            approval_handler or _terminal_approval_handler
        )

    # ------------------------------------------------------------------
    # Definition management
    # ------------------------------------------------------------------

    def list_workflows(self) -> list[WorkflowDefinition]:
        """Return all valid workflow definitions found in definitions_dir."""
        if not self._definitions_dir.exists():
            return []
        result = []
        for path in sorted(self._definitions_dir.glob("*.yaml")):
            try:
                result.append(WorkflowDefinition.from_yaml(path))
            except (WorkflowNotFoundError, WorkflowValidationError):
                pass  # silently skip malformed files
        return result

    def get_workflow(self, name: str) -> WorkflowDefinition:
        """
        Load a workflow definition by name.

        Looks for {name}.yaml first, then scans all YAML files matching by
        workflow.name field.

        Raises:
            WorkflowNotFoundError: no workflow with that name exists.
        """
        path = self._definitions_dir / f"{name}.yaml"
        if path.exists():
            return WorkflowDefinition.from_yaml(path)
        # Fall back: scan by workflow name field
        for wf in self.list_workflows():
            if wf.name == name:
                return wf
        raise WorkflowNotFoundError(f"No workflow named {name!r}")

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run(
        self,
        name: str,
        inputs: dict[str, str] | None = None,
        approval_handler: ApprovalHandler | None = None,
    ) -> WorkflowExecution:
        """
        Execute a named workflow end-to-end.

        Runs every step in definition order. The human_approval step type
        blocks until the provided approval_handler returns True or False.
        All steps after a CANCELLED or FAILED step are skipped.

        The execution log is always written to logs_dir, even on failure.

        Returns:
            WorkflowExecution with final status and step-by-step history.

        Raises:
            WorkflowNotFoundError: if no workflow with that name exists.
        """
        wf = self.get_workflow(name)
        handler = approval_handler or self._approval_handler

        # Resolve inputs: user-supplied values override defaults
        resolved_inputs: dict[str, str] = {}
        for k, spec in wf.inputs.items():
            if inputs and k in inputs:
                resolved_inputs[k] = inputs[k]
            elif spec.default:
                resolved_inputs[k] = spec.default
        if inputs:
            for k, v in inputs.items():
                resolved_inputs[k] = v

        execution = WorkflowExecution.start(wf.name, wf.version, resolved_inputs)

        for step in wf.steps:
            step_exec = StepExecution(
                step_id=step.id,
                step_type=step.type.value,
                status=StepStatus.RUNNING,
                started_at=_now(),
            )
            execution.steps.append(step_exec)

            try:
                outputs = self._execute_step(step, execution.context, handler)
                step_exec.input = _resolve_dict(step.input, execution.context)
                step_exec.output = outputs
                step_exec.status = StepStatus.COMPLETED
                step_exec.completed_at = _now()
                # Namespace all outputs under step_id in the shared context
                for k, v in outputs.items():
                    execution.context[f"{step.id}.{k}"] = v

            except ApprovalDenied:
                step_exec.status = StepStatus.REJECTED
                step_exec.completed_at = _now()
                execution.status = WorkflowStatus.CANCELLED
                execution.completed_at = _now()
                execution.error = f"Approval denied at step '{step.id}'"
                execution.write_log(self._logs_dir)
                return execution

            except StepExecutionError as exc:
                step_exec.status = StepStatus.FAILED
                step_exec.error = str(exc)
                step_exec.completed_at = _now()
                execution.status = WorkflowStatus.FAILED
                execution.completed_at = _now()
                execution.error = str(exc)
                execution.write_log(self._logs_dir)
                return execution

        execution.status = WorkflowStatus.COMPLETED
        execution.completed_at = _now()
        execution.write_log(self._logs_dir)
        return execution

    # ------------------------------------------------------------------
    # Step dispatch
    # ------------------------------------------------------------------

    def _execute_step(
        self,
        step: WorkflowStep,
        context: dict[str, Any],
        approval_handler: ApprovalHandler,
    ) -> dict[str, Any]:
        resolved = _resolve_dict(step.input, context)

        if step.type == StepType.ASK:
            prompt = resolved.get("prompt", "")
            if not prompt:
                raise StepExecutionError(step.id, "ask step requires 'prompt' in input")
            r = self._monday.ask(prompt)
            return {
                "answer": r.answer,
                "confidence": r.confidence,
                "sources": r.sources,
                "model_used": r.model_used,
            }

        if step.type == StepType.SEARCH:
            query = resolved.get("query", "")
            if not query:
                raise StepExecutionError(step.id, "search step requires 'query' in input")
            limit = int(resolved.get("limit", 10))
            r = self._monday.search(query, limit=limit)
            return {"results": r.results, "total_found": r.total_found}

        if step.type == StepType.LEARN:
            content = resolved.get("content", "")
            if not content:
                raise StepExecutionError(step.id, "learn step requires 'content' in input")
            r = self._monday.learn(
                content=content,
                title=resolved.get("title", ""),
                entry_type=resolved.get("entry_type", "pattern"),
                tags=_to_list(resolved.get("tags")),
                components=_to_list(resolved.get("components")),
            )
            if not r.accepted:
                raise StepExecutionError(step.id, r.message)
            return {"entry_id": r.entry_id, "accepted": r.accepted}

        if step.type == StepType.TASK_CREATE:
            title = resolved.get("title", "")
            objective = resolved.get("objective", "")
            if not title or not objective:
                raise StepExecutionError(
                    step.id, "task_create step requires 'title' and 'objective'"
                )
            r = self._monday.task(
                "create",
                title=title,
                objective=objective,
                task_type=resolved.get("task_type", "feature"),
                priority=resolved.get("priority", "P2"),
                created_by=resolved.get("created_by", f"workflow:{step.id}"),
            )
            if not r.success:
                raise StepExecutionError(step.id, r.message)
            return {"task_id": r.task_id, "success": True}

        if step.type == StepType.TASK_START:
            task_id = resolved.get("task_id", "")
            if not task_id:
                raise StepExecutionError(step.id, "task_start step requires 'task_id'")
            r = self._monday.task("start", task_id=task_id)
            if not r.success:
                raise StepExecutionError(step.id, r.message)
            return {"task_id": r.task_id, "success": True, "status": r.data.get("status", "")}

        if step.type == StepType.TASK_COMPLETE:
            task_id = resolved.get("task_id", "")
            if not task_id:
                raise StepExecutionError(step.id, "task_complete step requires 'task_id'")
            r = self._monday.task(
                "complete",
                task_id=task_id,
                reason=resolved.get("reason", ""),
            )
            if not r.success:
                raise StepExecutionError(step.id, r.message)
            return {"task_id": r.task_id, "success": True}

        if step.type == StepType.HUMAN_APPROVAL:
            raw_msg = step.message or resolved.get("message", "Approval required.")
            message = _resolve_str(raw_msg, context)
            approved = approval_handler(message, dict(context))
            if not approved:
                raise ApprovalDenied(step.id)
            return {"approved": True}

        raise StepExecutionError(step.id, f"Unhandled step type: {step.type}")


# ------------------------------------------------------------------
# Template resolution helpers
# ------------------------------------------------------------------

def _resolve_str(text: str, context: dict[str, Any]) -> str:
    """Replace {key} placeholders with values from context. Unknown keys are left as-is."""
    def replace(m: re.Match) -> str:
        key = m.group(1)
        return str(context[key]) if key in context else m.group(0)
    return re.sub(r"\{([^}]+)\}", replace, text)


def _resolve_dict(d: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Recursively resolve template strings in a dict's values."""
    result: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, str):
            result[k] = _resolve_str(v, context)
        elif isinstance(v, dict):
            result[k] = _resolve_dict(v, context)
        elif isinstance(v, list):
            result[k] = [
                _resolve_str(item, context) if isinstance(item, str) else item
                for item in v
            ]
        else:
            result[k] = v
    return result


def _to_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    return [str(value)]
