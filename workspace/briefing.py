"""
Continue Working — reconstructing where the operator left off.

The whole value of this module is that it **invents nothing**. Every line it
produces is read from stored MondayOS state: the conversation store, the task
system, git. A briefing that guessed at priorities would be worse than no
briefing, because it would be indistinguishable from one that knew.

So the rules are strict:

- If there is no recent conversation, it says so. It does not pick an arbitrary one.
- The recommended next step comes from task state — an in-progress task, else the
  highest-priority backlog task. If neither exists, there is no recommendation,
  and the briefing says that rather than manufacturing an objective.
- "Since your last session" is measured against the most recent conversation
  timestamp, not a wall-clock guess about when the operator was last here.

The greeting is time-of-day only. Monday is a senior technical operator, not a
butler: "Good afternoon" is the whole personality budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

# Below this, "since your last session" is not a meaningful frame — the operator
# was here minutes ago and knows what happened.
RETURN_THRESHOLD = timedelta(hours=2)

# Priority order for picking a recommendation. P0 first.
_PRIORITY_ORDER = ("P0", "P1", "P2", "P3")


@dataclass
class NextStep:
    """A recommendation, and the state it was derived from."""

    task_id: str
    title: str
    status: str
    priority: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "status": self.status,
            "priority": self.priority,
            "reason": self.reason,
        }


@dataclass
class Briefing:
    """What MondayOS can say about where work stands, from stored state alone."""

    greeting: str
    project: str = ""
    conversation_id: str = ""
    conversation_title: str = ""
    last_active: str = ""
    away_hours: float = 0.0
    is_return: bool = False
    active_task: dict[str, Any] | None = None
    last_completed: dict[str, Any] | None = None
    recent_commits: list[str] = field(default_factory=list)
    branch: str = ""
    open_task_count: int = 0
    next_step: NextStep | None = None
    # Stated plainly when a section had nothing to report, so an empty briefing
    # reads as "nothing recorded" rather than "nothing happened".
    notes: list[str] = field(default_factory=list)

    @property
    def can_continue(self) -> bool:
        """True when there is a specific place to return to."""
        return bool(self.project and self.conversation_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "greeting": self.greeting,
            "project": self.project,
            "conversation_id": self.conversation_id,
            "conversation_title": self.conversation_title,
            "last_active": self.last_active,
            "away_hours": round(self.away_hours, 1),
            "is_return": self.is_return,
            "active_task": self.active_task,
            "last_completed": self.last_completed,
            "recent_commits": list(self.recent_commits),
            "branch": self.branch,
            "open_task_count": self.open_task_count,
            "next_step": self.next_step.to_dict() if self.next_step else None,
            "notes": list(self.notes),
            "can_continue": self.can_continue,
        }


def greeting_for(now: datetime) -> str:
    """Time-of-day greeting. The entire personality budget."""
    hour = now.astimezone(UTC).hour
    if hour < 12:
        return "Good morning."
    if hour < 18:
        return "Good afternoon."
    return "Good evening."


def choose_next_step(
    active: list[dict[str, Any]],
    in_progress_first: bool = True,
) -> NextStep | None:
    """
    The task to recommend, from task state alone.

    An in-progress task wins: work already started is what "next" means, and
    recommending something else invites abandoning it. Otherwise the
    highest-priority backlog task. With no tasks there is no recommendation —
    this returns None rather than inventing an objective.
    """
    if not active:
        return None

    if in_progress_first:
        for task in active:
            if str(task.get("status")) == "in-progress":
                return NextStep(
                    task_id=str(task.get("id", "")),
                    title=str(task.get("title", "")),
                    status="in-progress",
                    priority=str(task.get("priority", "")),
                    reason="already in progress",
                )

    backlog = [t for t in active if str(t.get("status")) in ("backlog", "assigned")]
    if not backlog:
        return None

    backlog.sort(
        key=lambda t: (
            _PRIORITY_ORDER.index(str(t.get("priority", "P3")))
            if str(t.get("priority", "P3")) in _PRIORITY_ORDER
            else len(_PRIORITY_ORDER),
            str(t.get("id", "")),
        )
    )
    top = backlog[0]
    return NextStep(
        task_id=str(top.get("id", "")),
        title=str(top.get("title", "")),
        status=str(top.get("status", "")),
        priority=str(top.get("priority", "")),
        reason=f"highest-priority open task ({top.get('priority', '')})",
    )


def build_briefing(
    now: datetime,
    latest: dict[str, Any] | None,
    active_tasks: list[dict[str, Any]],
    completed_tasks: list[dict[str, Any]],
    git_lines: list[str],
) -> Briefing:
    """
    Assemble a briefing from already-gathered state.

    Takes plain data rather than subsystems so it stays pure and testable, and so
    the caller — which is the only thing that knows how to scope a project —
    remains responsible for scoping.
    """
    briefing = Briefing(greeting=greeting_for(now))

    if latest is None:
        briefing.notes.append("No conversations recorded yet.")
    else:
        briefing.project = str(latest.get("project", ""))
        briefing.conversation_id = str(latest.get("id", ""))
        briefing.conversation_title = str(latest.get("title", ""))
        briefing.last_active = str(latest.get("updated_at", ""))
        away = _hours_since(now, briefing.last_active)
        briefing.away_hours = away
        briefing.is_return = away >= RETURN_THRESHOLD.total_seconds() / 3600

    in_progress = [t for t in active_tasks if str(t.get("status")) == "in-progress"]
    briefing.active_task = in_progress[0] if in_progress else None
    briefing.open_task_count = len(active_tasks)
    briefing.last_completed = completed_tasks[0] if completed_tasks else None

    if not active_tasks:
        briefing.notes.append("No open tasks for this project.")
    if not completed_tasks:
        briefing.notes.append("No completed tasks recorded for this project.")

    for line in git_lines:
        stripped = line.strip()
        if stripped.startswith("Current branch:"):
            briefing.branch = stripped.split(":", 1)[1].strip()
        elif _looks_like_commit(stripped):
            briefing.recent_commits.append(stripped)
    briefing.recent_commits = briefing.recent_commits[:5]

    briefing.next_step = choose_next_step(active_tasks)
    if briefing.next_step is None:
        briefing.notes.append("No recommendation: task state does not name an obvious next step.")

    return briefing


def _hours_since(now: datetime, iso: str) -> float:
    if not iso:
        return 0.0
    try:
        then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    return max(0.0, (now.astimezone(UTC) - then).total_seconds() / 3600)


def _looks_like_commit(line: str) -> bool:
    """A git-source line that is a commit rather than a heading or status."""
    parts = line.split(maxsplit=1)
    return (
        len(parts) == 2
        and 6 <= len(parts[0]) <= 12
        and all(c in "0123456789abcdef" for c in parts[0])
    )
