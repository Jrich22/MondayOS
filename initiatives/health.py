"""
How an initiative is doing.

Separated from the model because "what an initiative is" and "how it is going"
change for different reasons: membership changes when the project is reorganised,
health when work happens. Keeping them apart means a health rule can be revised
without touching the type every other module reads.

Two judgements here are worth defending.

**Progress refuses to invent a denominator.** With tasks, completed-over-total is
a real ratio. Without them, there is nothing honest to divide, and counting files
would produce a number that moves when someone splits a module in two. So an
initiative with no tasks reports maturity signals instead of a percentage. A
missing number with a stated basis beats a confident wrong one — the same
principle the confidence engine runs on.

**Health leads on blockers, not on progress.** An initiative at 80% with a blocked
task needs attention more than one at 20% moving steadily, and a status that
sorted by percentage would bury it. The bands answer "should I worry", which is
the question actually being asked.
"""

from __future__ import annotations

from typing import Any

from initiatives.models import (
    Basis,
    Dependency,
    Health,
    Initiative,
    Milestone,
    Progress,
)
from intelligence.models import NodeKind

# Recent commits touching an initiative, below which it reads as stalled. Any
# in-flight work with no recent history is either paused or forgotten, and both
# are worth surfacing.
STALE_COMMITS = 1


def _status_of(label: str) -> str:
    """`TASK-0074 [in-progress] Title` -> `in-progress`."""
    if "[" in label and "]" in label:
        return label[label.index("[") + 1 : label.index("]")].strip().lower()
    return ""


def assess(initiative: Initiative, tasks: list[dict[str, Any]] | None = None) -> Initiative:
    """
    Fill in an initiative's assessment fields, in place.

    Returns the same object for convenience. Mutating rather than copying because
    the caller always wants the assessed version and a second object would invite
    the two drifting apart.
    """
    _progress(initiative)
    _blockers(initiative)
    _risks_and_missing(initiative)
    _health(initiative)
    _milestone(initiative, tasks or [])
    return initiative


def _progress(initiative: Initiative) -> None:
    task_members = initiative.of_kind(NodeKind.TASK)
    progress = Progress()

    if task_members:
        statuses = [_status_of(m.label) for m in task_members]
        completed = sum(1 for s in statuses if s in ("completed", "done", "closed"))
        progress.basis = Basis.TASKS
        progress.completed = completed
        progress.total = len(statuses)
    elif initiative.members:
        progress.basis = Basis.SIGNALS
    else:
        progress.basis = Basis.NONE

    files = initiative.of_kind(NodeKind.FILE)
    progress.has_code = any(not m.label.lower().endswith(".md") for m in files)
    progress.has_tests = bool(initiative.of_kind(NodeKind.TEST))
    progress.has_docs = any(m.label.lower().endswith(".md") for m in files)
    progress.has_decision = bool(initiative.of_kind(NodeKind.DECISION))
    initiative.progress = progress


def _blockers(initiative: Initiative) -> None:
    initiative.blockers = [
        m.label for m in initiative.of_kind(NodeKind.TASK) if "block" in _status_of(m.label)
    ]


def _risks_and_missing(initiative: Initiative) -> None:
    risks: list[str] = []
    missing: list[str] = []
    progress = initiative.progress

    if progress.has_code and not progress.has_tests:
        risks.append("implemented but has no tests, so regressions are silent")
        missing.append("a test suite")
    if progress.has_code and not progress.has_decision:
        missing.append("an architecture decision record")
    if progress.has_code and not progress.has_docs:
        missing.append("documentation")
    if initiative.blockers:
        risks.append(f"{len(initiative.blockers)} blocked task(s)")

    in_progress = [
        m for m in initiative.of_kind(NodeKind.TASK) if "progress" in _status_of(m.label)
    ]
    commits = initiative.of_kind(NodeKind.COMMIT)
    if in_progress and len(commits) < STALE_COMMITS:
        risks.append("work is in progress but no recent commit mentions it")

    initiative.risks = risks
    initiative.missing = missing

    opportunities: list[str] = []
    if progress.has_code and progress.has_tests and not progress.has_docs:
        opportunities.append("built and tested but undocumented — cheap to make usable by others")
    if progress.basis is Basis.TASKS and progress.total and progress.completed == progress.total:
        opportunities.append("all known tasks complete — ready for a next milestone")
    initiative.opportunities = opportunities


def _health(initiative: Initiative) -> None:
    progress = initiative.progress

    if not initiative.members:
        initiative.health = Health.NOT_STARTED
        initiative.health_because = (
            "declared on the roadmap, but nothing in the repository implements it yet"
            if initiative.declared
            else "no work found"
        )
        return

    if initiative.blockers:
        initiative.health = Health.BLOCKED
        initiative.health_because = f"{len(initiative.blockers)} task(s) blocked"
        return

    in_progress = [
        m for m in initiative.of_kind(NodeKind.TASK) if "progress" in _status_of(m.label)
    ]
    if in_progress and not initiative.of_kind(NodeKind.COMMIT):
        initiative.health = Health.STALLED
        initiative.health_because = (
            f"{len(in_progress)} task(s) in progress with no commit mentioning this work"
        )
        return

    if progress.has_code and not progress.has_tests:
        initiative.health = Health.AT_RISK
        initiative.health_because = "has implementation but no tests"
        return

    initiative.health = Health.HEALTHY
    initiative.health_because = (
        f"{progress.display()}, no blockers"
        if progress.basis is Basis.TASKS
        else f"{progress.display()}, no blockers"
    )


def _milestone(initiative: Initiative, tasks: list[dict[str, Any]]) -> None:
    """
    The next meaningful step.

    Ordered by what unblocks the most: a blocker holds everything behind it, an
    unstarted declaration needs a first step before anything else is meaningful,
    and a missing test suite is what stops shipped work from being trusted.
    """
    if initiative.blockers:
        initiative.next_milestone = Milestone(
            statement=f"Clear the blocker on {initiative.blockers[0]}",
            rationale="blocked work holds everything behind it and consumes no capacity",
            unblocks=tuple(initiative.blockers[:3]),
        )
        return

    if not initiative.members and initiative.declared:
        initiative.next_milestone = Milestone(
            statement=f"Define the first increment of {initiative.name}",
            rationale="declared on the roadmap with nothing implementing it yet",
        )
        return

    open_tasks = [
        m
        for m in initiative.of_kind(NodeKind.TASK)
        if _status_of(m.label) not in ("completed", "done", "closed")
    ]
    if open_tasks:
        initiative.next_milestone = Milestone(
            statement=f"Finish {open_tasks[0].label}",
            rationale="already scoped and in flight, so it realises value soonest",
        )
        return

    if initiative.progress.has_code and not initiative.progress.has_tests:
        initiative.next_milestone = Milestone(
            statement=f"Add a test suite for {initiative.name}",
            rationale="shipped code with no tests cannot be changed with confidence",
        )
        return

    if initiative.missing:
        initiative.next_milestone = Milestone(
            statement=f"Add {initiative.missing[0]} for {initiative.name}",
            rationale="closes the largest remaining gap in this capability",
        )


def link(initiatives: list[Initiative]) -> list[Initiative]:
    """
    Record dependencies between initiatives, from shared artefacts only.

    A file belonging to two initiatives is those two initiatives touching the same
    code — a stated fact. Nothing here infers a dependency from similarity: an
    invented constraint would be acted on as though the project had agreed to it.
    """
    by_node: dict[str, list[Initiative]] = {}
    for initiative in initiatives:
        for member in initiative.members:
            by_node.setdefault(member.node_id, []).append(initiative)

    shared: dict[str, dict[str, str]] = {i.slug: {} for i in initiatives}
    for node_id, owners in by_node.items():
        if len(owners) < 2:
            continue
        for one in owners:
            for other in owners:
                if one.slug != other.slug and other.slug not in shared[one.slug]:
                    shared[one.slug][other.slug] = node_id

    for initiative in initiatives:
        initiative.dependencies = [
            Dependency(on=slug, because=f"shares {node_id}")
            for slug, node_id in sorted(shared[initiative.slug].items())
        ][:5]
    return initiatives
