"""AdvisorEngine — composes existing subsystems into an engineering advisory."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from advisor.advisory import Advisory
from advisor.reasoning import (
    compute_confidence,
    summarize_repository,
    synthesize_debt,
    synthesize_documentation_gaps,
    synthesize_knowledge_gaps,
    synthesize_next_actions,
    synthesize_risks,
    synthesize_sprint_goal,
)

if TYPE_CHECKING:
    from brain.providers.base import AIProvider
    from doctor.result import DoctorReport


class AdvisorEngine:
    """
    Produces an Engineering Advisory by composing:
      - RepositoryInspector (Doctor) — repository health findings
      - KnowledgeStore — entries, types, relationship density
      - TaskManager — task status, priorities, blockers
      - Workflow logs — execution history
      - Reasoning functions (reasoning.py) — pure synthesis

    The engine never calls an external model. All intelligence comes
    from structured data that already exists in the MondayOS project.

    Args:
        monday:       Initialised Monday instance for subsystem access.
        project_root: Root of the project to analyse.
    """

    def __init__(
        self,
        monday: Any,
        project_root: Path,
        provider: "AIProvider | None" = None,
    ) -> None:
        self._monday = monday
        self._root = project_root
        self._provider = provider

    def analyze(self, doctor_report: "DoctorReport | None" = None) -> Advisory:
        """
        Run the full advisory analysis.

        Args:
            doctor_report: Pre-computed DoctorReport. When None, the engine
                           runs RepositoryInspector internally. Accepting a
                           pre-computed report avoids double-running inspection
                           when the caller already has one.

        Returns:
            Advisory with all fields populated from available data.
        """
        # ── 1. Repository health ─────────────────────────────────────────────
        if doctor_report is None:
            doctor_report = self._run_doctor()

        doctor_ok = not any(r.error for r in doctor_report.results)

        # ── 2. Knowledge store ───────────────────────────────────────────────
        knowledge_entries = self._load_knowledge()

        # ── 3. Task system ───────────────────────────────────────────────────
        active_tasks = self._load_tasks()

        # ── 4. Workflow history ──────────────────────────────────────────────
        workflow_runs = self._load_workflow_runs()

        # ── 5. Synthesize ────────────────────────────────────────────────────
        risks = synthesize_risks(doctor_report, knowledge_entries, active_tasks)
        actions = synthesize_next_actions(
            risks, active_tasks, knowledge_entries, doctor_report, workflow_runs
        )
        sprint_goal, sprint_rationale = synthesize_sprint_goal(
            risks, active_tasks, knowledge_entries
        )
        debt_summary, debt_items = synthesize_debt(doctor_report, knowledge_entries)
        knowledge_gaps = synthesize_knowledge_gaps(knowledge_entries, active_tasks)
        doc_gaps = synthesize_documentation_gaps(doctor_report)
        repo_summary = summarize_repository(
            self._root.name, doctor_report, knowledge_entries, active_tasks, workflow_runs
        )
        confidence = compute_confidence(
            knowledge_entries, active_tasks, workflow_runs, doctor_ok
        )

        data_sources = ["doctor"]
        if knowledge_entries:
            data_sources.append("knowledge")
        if active_tasks:
            data_sources.append("tasks")
        if workflow_runs:
            data_sources.append("workflows")

        advisory = Advisory(
            generated_at=datetime.now(tz=timezone.utc).isoformat(),
            confidence=confidence,
            repository_summary=repo_summary,
            health_score=doctor_report.health_score,
            health_grade=doctor_report.grade,
            risks=risks,
            next_actions=actions,
            sprint_goal=sprint_goal,
            sprint_rationale=sprint_rationale,
            technical_debt_summary=debt_summary,
            debt_items=debt_items,
            knowledge_gaps=knowledge_gaps,
            documentation_gaps=doc_gaps,
            data_sources=data_sources,
        )

        if self._provider is not None:
            self._enrich_advisory_with_ai(advisory)

        return advisory

    # ------------------------------------------------------------------
    # Subsystem loaders
    # ------------------------------------------------------------------

    def _run_doctor(self) -> "DoctorReport":
        from doctor.inspector import RepositoryInspector
        inspector = RepositoryInspector(
            project_root=self._root,
            monday=self._monday,
        )
        return inspector.run()

    def _load_knowledge(self) -> list[Any]:
        try:
            from knowledge.store import KnowledgeStore
            store = KnowledgeStore(self._root)
            return store.list_all()
        except Exception:
            return []

    def _load_tasks(self) -> list[Any]:
        try:
            from tasks.manager import TaskManager
            manager = TaskManager(self._root)
            return manager.list_active()
        except Exception:
            return []

    def _enrich_advisory_with_ai(self, advisory: Advisory) -> None:
        """
        Overlay AI-generated enrichment onto a deterministically-computed advisory.

        Enrichment is purely additive: deterministic results are never replaced,
        only extended. Any provider failure is silently swallowed — the advisory
        is always returned even if the provider is unavailable. The confidence
        ceiling rises from 0.85 to 0.95 when enrichment runs successfully.
        """
        try:
            context = advisory.repository_summary
            if advisory.risks:
                risk_lines = "\n".join(
                    f"- [{r.severity.upper()}] {r.title}: {r.impact}"
                    for r in advisory.risks[:5]
                )
                context += f"\n\nKey risks:\n{risk_lines}"

            prompt = (
                "Based on this engineering advisory, write a precise, actionable sprint goal "
                f"in one sentence. Current deterministic goal: '{advisory.sprint_goal}'. "
                "Improve specificity if possible; otherwise confirm it as-is."
            )
            response = self._provider.ask(prompt, context=context, max_tokens=120)  # type: ignore[union-attr]
            enriched_goal = response.content.strip().rstrip(".")
            if enriched_goal:
                advisory.sprint_goal = enriched_goal

            advisory.confidence = min(0.95, advisory.confidence + 0.10)
            if self._provider.name not in advisory.data_sources:  # type: ignore[union-attr]
                advisory.data_sources.append(self._provider.name)  # type: ignore[union-attr]
        except Exception:
            pass  # AI enrichment is best-effort — provider errors must never propagate

    def _load_workflow_runs(self) -> list[dict[str, Any]]:
        logs_dir = self._root / "logs" / "workflows"
        if not logs_dir.exists():
            return []
        runs: list[dict[str, Any]] = []
        for path in sorted(logs_dir.glob("*.json"), reverse=True)[:20]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                runs.append(data)
            except (json.JSONDecodeError, OSError):
                pass
        return runs
