"""TaskHealthAnalyzer — inspects the MondayOS task system."""
from __future__ import annotations

import time
from collections import Counter

from doctor.base import BaseAnalyzer
from doctor.finding import Finding, Severity
from doctor.result import AnalyzerResult

_CATEGORY = "tasks"


class TaskHealthAnalyzer(BaseAnalyzer):
    """
    Audits MondayOS tasks for health signals.

    Checks:
      - Blocked tasks (WARNING per blocked task)
      - Tasks without objectives (WARNING)
      - Tasks without assigned owner (INFO)
      - Status distribution breakdown (INFO)
    """

    NAME = "tasks"

    def analyze(self) -> AnalyzerResult:
        start = time.monotonic()
        findings: list[Finding] = []

        if self._monday is None:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.INFO,
                title="Task analysis skipped (no Monday instance)",
            ))
            return AnalyzerResult(name=self.NAME, findings=findings,
                                  duration_ms=(time.monotonic() - start) * 1000)

        try:
            from tasks import TaskManager, TaskStatus
            manager = TaskManager(self._root)
            active_tasks = manager.list_active()
        except Exception as exc:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.WARNING,
                title="Could not load task manager",
                detail=str(exc),
                recommendation="Check tasks/ACTIVE.md and tasks/BACKLOG.md for corruption.",
            ))
            return AnalyzerResult(name=self.NAME, findings=findings,
                                  duration_ms=(time.monotonic() - start) * 1000)

        total = len(active_tasks)
        if total == 0:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.INFO,
                title="No active tasks",
                recommendation="Create tasks with `monday task create` to track work.",
            ))
            return AnalyzerResult(name=self.NAME, findings=findings,
                                  duration_ms=(time.monotonic() - start) * 1000)

        findings.append(Finding(
            category=_CATEGORY,
            severity=Severity.INFO,
            title=f"{total} active task(s)",
            data={"total_tasks": total},
        ))

        # Status breakdown
        status_counts = Counter(t.status.value for t in active_tasks)
        findings.append(Finding(
            category=_CATEGORY,
            severity=Severity.INFO,
            title="Task status breakdown",
            detail="\n".join(f"  {k}: {v}" for k, v in sorted(status_counts.items())),
            data={"by_status": dict(status_counts)},
        ))

        # Blocked tasks
        blocked = [t for t in active_tasks if t.status.value == "blocked"]
        if blocked:
            detail = "\n".join(f"  {t.id}: {t.title}" for t in blocked)
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.WARNING,
                title=f"{len(blocked)} task(s) BLOCKED",
                detail=detail,
                recommendation="Resolve blockers for: " + ", ".join(t.id for t in blocked),
                data={"blocked_ids": [t.id for t in blocked]},
            ))
        else:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.OK,
                title="No blocked tasks",
            ))

        # Tasks without objectives
        no_objective = [t for t in active_tasks if not (t.objective or "").strip()]
        if no_objective:
            detail = "\n".join(f"  {t.id}: {t.title}" for t in no_objective)
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.WARNING,
                title=f"{len(no_objective)} task(s) without an objective",
                detail=detail,
                recommendation="Add objectives so tasks have clear success criteria.",
                data={"no_objective_ids": [t.id for t in no_objective]},
            ))

        # Tasks without assignee (in-progress tasks only — should always be assigned)
        from tasks import TaskStatus
        in_progress_unassigned = [
            t for t in active_tasks
            if t.status == TaskStatus.IN_PROGRESS and not (t.assigned_to or "").strip()
        ]
        if in_progress_unassigned:
            detail = "\n".join(f"  {t.id}: {t.title}" for t in in_progress_unassigned)
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.INFO,
                title=f"{len(in_progress_unassigned)} in-progress task(s) without an assignee",
                detail=detail,
                recommendation="Assign ownership for in-progress tasks.",
                data={"unassigned_ids": [t.id for t in in_progress_unassigned]},
            ))

        # High-priority tasks sitting in BACKLOG (P0/P1 that haven't been started)
        from tasks import TaskPriority
        urgent_backlog = [
            t for t in active_tasks
            if t.status.value == "backlog" and t.priority in (TaskPriority.P0, TaskPriority.P1)
        ]
        if urgent_backlog:
            detail = "\n".join(f"  [{t.priority.value}] {t.id}: {t.title}" for t in urgent_backlog)
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.WARNING,
                title=f"{len(urgent_backlog)} high-priority task(s) still in BACKLOG",
                detail=detail,
                recommendation=(
                    "Start high-priority tasks: `monday task start <id>` or "
                    "`monday workflow run implement-function`."
                ),
                data={"urgent_backlog_ids": [t.id for t in urgent_backlog]},
            ))

        return AnalyzerResult(
            name=self.NAME,
            findings=findings,
            duration_ms=(time.monotonic() - start) * 1000,
        )
