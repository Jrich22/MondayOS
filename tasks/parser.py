"""YAML + Markdown frontmatter parser for Task objects."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import yaml

from tasks.errors import TaskParseError
from tasks.task import ApprovalLevel, StatusTransition, Task, TaskPriority, TaskStatus, TaskType

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)

_REQUIRED_FIELDS = ("id", "title", "task_type", "status", "priority", "created", "updated", "created_by", "objective")


class TaskParser:
    """
    Parses task Markdown files (with YAML frontmatter) into Task objects and back.

    File format:
        ---
        id: TASK-0001
        title: "Implement knowledge capture"
        task_type: feature
        status: backlog
        priority: P2
        created: "2026-06-27T14:00:00Z"
        updated: "2026-06-27T14:00:00Z"
        created_by: "human:jrich"
        objective: "Make Monday.learn() work"
        ... (other fields)
        status_history:
          - from_status: null
            to_status: backlog
            changed_by: "human:jrich"
            changed_at: "2026-06-27T14:00:00Z"
            reason: ""
        ---

    serialize(parse(raw)) round-trips cleanly for all well-formed task files.
    """

    def parse(self, raw: str, source_path: str = "<string>") -> Task:
        """Parse a raw Markdown string into a Task."""
        match = _FRONTMATTER_RE.match(raw)
        if not match:
            raise TaskParseError(
                "No YAML frontmatter block found (expected --- ... ---)",
                source_path=source_path,
            )

        try:
            fm: dict[str, Any] = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as exc:
            raise TaskParseError(f"YAML parse error: {exc}", source_path=source_path) from exc

        for required in _REQUIRED_FIELDS:
            if required not in fm:
                raise TaskParseError(
                    f"Missing required field: {required!r}", source_path=source_path
                )

        try:
            task_type = TaskType(fm["task_type"])
        except ValueError as exc:
            raise TaskParseError(
                f"Unknown task_type: {fm['task_type']!r}", source_path=source_path
            ) from exc

        try:
            status = TaskStatus(fm["status"])
        except ValueError as exc:
            raise TaskParseError(
                f"Unknown status: {fm['status']!r}", source_path=source_path
            ) from exc

        try:
            priority = TaskPriority(fm["priority"])
        except ValueError as exc:
            raise TaskParseError(
                f"Unknown priority: {fm['priority']!r}", source_path=source_path
            ) from exc

        history: list[StatusTransition] = []
        for s in (fm.get("status_history") or []):
            try:
                history.append(StatusTransition(
                    from_status=TaskStatus(s["from_status"]) if s.get("from_status") else None,
                    to_status=TaskStatus(s["to_status"]),
                    changed_by=str(s["changed_by"]),
                    changed_at=_parse_dt(s["changed_at"]),
                    reason=str(s.get("reason", "")),
                ))
            except (KeyError, ValueError) as exc:
                raise TaskParseError(
                    f"Invalid status_history entry: {exc}", source_path=source_path
                ) from exc

        return Task(
            id=str(fm["id"]),
            title=str(fm["title"]),
            task_type=task_type,
            status=status,
            priority=priority,
            created=_parse_dt(fm["created"]),
            updated=_parse_dt(fm["updated"]),
            created_by=str(fm["created_by"]),
            objective=str(fm["objective"]),
            context=str(fm.get("context", "")),
            # `.get` with a default, not `fm["project"]`: every task written
            # before explicit association lacks the key, and a task that will not
            # load is worse than one whose project is unknown.
            project=str(fm.get("project", "") or ""),
            assigned_to=str(fm["assigned_to"]) if fm.get("assigned_to") else None,
            parent_task_id=str(fm["parent_task_id"]) if fm.get("parent_task_id") else None,
            child_task_ids=list(fm.get("child_task_ids") or []),
            approval_required=ApprovalLevel(fm.get("approval_required", "human-review")),
            blocked_by=str(fm["blocked_by"]) if fm.get("blocked_by") else None,
            knowledge_refs=list(fm.get("knowledge_refs") or []),
            commit_refs=list(fm.get("commit_refs") or []),
            acceptance_criteria=list(fm.get("acceptance_criteria") or []),
            work_log=list(fm.get("work_log") or []),
            status_history=history,
            metadata=dict(fm.get("metadata") or {}),
        )

    def serialize(self, task: Task) -> str:
        """Serialize a Task to the YAML frontmatter Markdown format."""
        fm: dict[str, Any] = {
            "acceptance_criteria": list(task.acceptance_criteria),
            "approval_required": task.approval_required.value,
            "assigned_to": task.assigned_to,
            "blocked_by": task.blocked_by,
            "child_task_ids": list(task.child_task_ids),
            "commit_refs": list(task.commit_refs),
            "context": task.context,
            "created": _fmt_dt(task.created),
            "created_by": task.created_by,
            "id": task.id,
            "knowledge_refs": list(task.knowledge_refs),
            "metadata": dict(task.metadata),
            "objective": task.objective,
            "parent_task_id": task.parent_task_id,
            "priority": task.priority.value,
            "project": task.project,
            "status": task.status.value,
            "status_history": [
                {
                    "changed_at": _fmt_dt(st.changed_at),
                    "changed_by": st.changed_by,
                    "from_status": st.from_status.value if st.from_status else None,
                    "reason": st.reason,
                    "to_status": st.to_status.value,
                }
                for st in task.status_history
            ],
            "task_type": task.task_type.value,
            "title": task.title,
            "updated": _fmt_dt(task.updated),
            "work_log": list(task.work_log),
        }
        fm_yaml = yaml.dump(
            fm,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=True,
            width=120,
        )
        return f"---\n{fm_yaml}---\n"


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        return datetime.fromisoformat(value.rstrip("Z")).replace(tzinfo=timezone.utc)
    raise ValueError(f"Cannot parse datetime from {type(value).__name__}: {value!r}")


def _fmt_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
