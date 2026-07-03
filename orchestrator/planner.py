"""
ExecutionPlanner — turns a task (plus optional advisory context) into a concrete,
provider-agnostic ExecutionPlan.

Planning is deterministic and happens *before* provider selection (see the
execution flow): the plan describes the prompt and the steps, independent of
which provider will ultimately run it. The selected provider then executes
`plan.prompt`. This keeps planning and execution cleanly separated and means a
dry-run can produce a full plan without ever calling a provider.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutionPlan:
    """
    A provider-agnostic plan for executing one task.

    Attributes:
        task_id:   The task this plan executes.
        objective: The task's objective (what success looks like).
        prompt:    The fully-assembled prompt to send to the selected provider.
        context:   Background text (project state, advisory hints) passed
                   alongside the prompt.
        steps:     Human-readable plan steps, deterministically derived.
        rationale: Why this plan was chosen.
        source:    "deterministic" — planning never depends on a provider.
    """

    task_id: str
    objective: str
    prompt: str
    context: str = ""
    steps: list[str] = field(default_factory=list)
    rationale: str = ""
    source: str = "deterministic"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "objective": self.objective,
            "prompt": self.prompt,
            "context": self.context,
            "steps": self.steps,
            "rationale": self.rationale,
            "source": self.source,
        }


class ExecutionPlanner:
    """
    Builds an ExecutionPlan from a task dict and optional advisory data.

    The planner composes (but does not duplicate) information already produced
    by other subsystems: the task fields come from TaskManager (via the public
    API), and the advisory hints come from the AdvisorEngine. No AI call is made
    here — planning is deterministic so it is reproducible and dry-run-safe.
    """

    def plan(
        self,
        task: dict[str, Any],
        advisory: dict[str, Any] | None = None,
    ) -> ExecutionPlan:
        """
        Construct an ExecutionPlan for the given task.

        Args:
            task:     Task data dict (id, title, objective, context,
                      acceptance_criteria, task_type, priority).
            advisory: Optional advisory dict from Monday.advise() used to enrich
                      the execution context. None when unavailable.

        Returns:
            A fully-populated ExecutionPlan ready for a provider to execute.
        """
        task_id = task.get("id", "")
        title = task.get("title", "")
        objective = task.get("objective", "") or title
        task_context = task.get("context", "")
        task_type = task.get("task_type", "task")
        criteria = task.get("acceptance_criteria", []) or []

        context = self._build_context(task_type, task_context, advisory)
        prompt = self._build_prompt(title, objective, criteria, task_type)
        steps = self._build_steps(task_type, criteria)
        rationale = (
            f"Deterministic plan for {task_type} task {task_id!r}. "
            f"Provider will execute the assembled prompt; results are validated "
            f"before any task or knowledge update."
        )

        return ExecutionPlan(
            task_id=task_id,
            objective=objective,
            prompt=prompt,
            context=context,
            steps=steps,
            rationale=rationale,
        )

    # ------------------------------------------------------------------
    # Internal builders
    # ------------------------------------------------------------------

    def _build_context(
        self,
        task_type: str,
        task_context: str,
        advisory: dict[str, Any] | None,
    ) -> str:
        parts: list[str] = []
        if task_context:
            parts.append(f"Task context:\n{task_context}")

        if advisory:
            summary = advisory.get("repository_summary", "")
            if summary:
                parts.append(f"Repository state:\n{summary}")
            sprint_goal = advisory.get("sprint_goal", "")
            if sprint_goal:
                parts.append(f"Current sprint goal: {sprint_goal}")
            risks = advisory.get("risks", [])
            if risks:
                risk_lines = "\n".join(
                    f"- [{r.get('severity', '').upper()}] {r.get('title', '')}"
                    for r in risks[:3]
                )
                parts.append(f"Top engineering risks:\n{risk_lines}")

        return "\n\n".join(parts)

    def _build_prompt(
        self,
        title: str,
        objective: str,
        criteria: list[str],
        task_type: str,
    ) -> str:
        lines = [
            f"You are executing a MondayOS engineering task of type '{task_type}'.",
            "",
            f"Title: {title}",
            f"Objective: {objective}",
        ]
        if criteria:
            lines.append("")
            lines.append("Acceptance criteria:")
            lines.extend(f"  - {c}" for c in criteria)
        lines.append("")
        lines.append(
            "Produce a concrete, actionable result that fulfils the objective. "
            "Be specific and reference the acceptance criteria where relevant."
        )
        return "\n".join(lines)

    def _build_steps(self, task_type: str, criteria: list[str]) -> list[str]:
        steps = [
            "Interpret the objective and acceptance criteria.",
            f"Produce the {task_type} deliverable through the selected AI provider.",
            "Validate the result against the objective.",
        ]
        if criteria:
            steps.append(f"Confirm all {len(criteria)} acceptance criteria are addressed.")
        steps.append("Capture resulting knowledge and update the task.")
        return steps
