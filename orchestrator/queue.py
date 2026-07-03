"""
ExecutionQueue — a priority-ordered queue of execution units.

The orchestrator enqueues an ExecutionUnit per task it is asked to run, ordered
by priority (lower rank = more urgent). For a single `monday execute TASK-0001`
the queue holds one unit; the queue exists so the same machinery scales to batch
execution and so the Advisor's prioritisation has somewhere to land.
"""
from __future__ import annotations

import bisect
import itertools
from dataclasses import dataclass, field
from typing import Any

from orchestrator.planner import ExecutionPlan


@dataclass(order=False)
class ExecutionUnit:
    """
    One unit of orchestrated work: a planned task awaiting execution.

    Attributes:
        task_id:   The task to execute.
        priority:  Rank where lower = more urgent (P0→0, P1→1, P2→2, P3→3).
        plan:      The deterministic ExecutionPlan for this task.
        objective: Convenience copy of the task objective.
    """

    task_id: str
    priority: int
    plan: ExecutionPlan
    objective: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "priority": self.priority,
            "objective": self.objective,
            "plan": self.plan.to_dict(),
        }


class ExecutionQueue:
    """
    A stable priority queue of ExecutionUnits.

    Units dequeue in ascending priority order; ties break by insertion order
    (FIFO), so equal-priority work runs in the order it was enqueued. This is a
    plain in-memory structure — persistence of *results* is the report's job,
    not the queue's.
    """

    def __init__(self) -> None:
        # Keys are (priority, insertion_seq) so ordering is stable and total.
        self._keys: list[tuple[int, int]] = []
        self._units: dict[tuple[int, int], ExecutionUnit] = {}
        self._counter = itertools.count()

    def enqueue(self, unit: ExecutionUnit) -> None:
        """Add a unit, maintaining (priority, FIFO) ordering."""
        key = (unit.priority, next(self._counter))
        idx = bisect.bisect(self._keys, key)
        self._keys.insert(idx, key)
        self._units[key] = unit

    def dequeue(self) -> ExecutionUnit | None:
        """Remove and return the highest-priority unit, or None if empty."""
        if not self._keys:
            return None
        key = self._keys.pop(0)
        return self._units.pop(key)

    def peek(self) -> ExecutionUnit | None:
        """Return the highest-priority unit without removing it, or None."""
        if not self._keys:
            return None
        return self._units[self._keys[0]]

    def is_empty(self) -> bool:
        return not self._keys

    def __len__(self) -> int:
        return len(self._keys)

    def __bool__(self) -> bool:
        return bool(self._keys)
