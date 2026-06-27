"""
MondayOS Tasks — task lifecycle management and complete audit trail.

The task system is the operational record of everything MondayOS does.
Every unit of work — whether initiated by a human or an AI agent — is
a Task. Tasks flow through a defined lifecycle and accumulate a full
audit trail in their status_history.

All task mutations go through TaskManager. No code writes task files
directly.

Task types:   feature, fix, refactor, docs, research, ops, review
Priorities:   P0 (system down) → P3 (background)
Statuses:     backlog → assigned → in-progress → [blocked] → review → completed
Approval:     none | human-review | human-approval

See docs/TASK_SYSTEM.md for the full lifecycle diagram and schema.

Public interface:
    TaskManager      — create, read, update, archive tasks (the only mutation path)
    Task             — task data model
    TaskType         — enum of task type variants
    TaskStatus       — enum of lifecycle statuses
    TaskPriority     — enum of priority levels P0–P3
    ApprovalLevel    — enum of human approval gate levels
    StatusTransition — immutable record of a single status change
"""
from __future__ import annotations

from tasks.manager import TaskManager
from tasks.task import ApprovalLevel, StatusTransition, Task, TaskPriority, TaskStatus, TaskType

__all__ = [
    "TaskManager",
    "Task",
    "TaskType",
    "TaskStatus",
    "TaskPriority",
    "ApprovalLevel",
    "StatusTransition",
]
