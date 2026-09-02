"""
Backfilling explicit project association onto existing tasks.

Increment 1 associated a task with a project by looking for the project slug in
the task's title or objective. That worked at the scale it ran at and is wrong in
general: a project named `cue` matches "the cue point in the audio pipeline", and
a project whose name never appears in prose is invisible to its own workspace.

Increment 2 adds `Task.project`. This module backfills it for tasks written
before the field existed.

Three properties make the backfill safe to run:

**It never guesses.** A task is only assigned a project when the slug appears as
a whole word in the title or objective. A partial match, an ambiguous match
against two projects, or no match at all leaves the field empty and is reported.
An empty project means "unknown", which the Context Engine handles by falling
back to the legacy heuristic for that task alone.

**It never overwrites.** A task that already carries an explicit project is left
alone, so the migration is idempotent and a human correction is never undone by
a later run.

**It reports rather than logs.** The result names every task changed, every task
skipped, and why — so the ambiguous ones can be assigned by hand instead of
being silently left behind.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tasks.manager import TaskManager
from tasks.task import Task


@dataclass
class MigrationResult:
    """What a backfill run did, in enough detail to act on."""

    assigned: dict[str, str] = field(default_factory=dict)
    already_explicit: list[str] = field(default_factory=list)
    ambiguous: dict[str, list[str]] = field(default_factory=dict)
    unmatched: list[str] = field(default_factory=list)
    dry_run: bool = True

    @property
    def changed(self) -> int:
        return len(self.assigned)

    @property
    def needs_attention(self) -> list[str]:
        """Tasks a human should assign, because the migration would not guess."""
        return sorted(set(self.ambiguous) | set(self.unmatched))

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "assigned": dict(self.assigned),
            "already_explicit": list(self.already_explicit),
            "ambiguous": {k: list(v) for k, v in self.ambiguous.items()},
            "unmatched": list(self.unmatched),
            "changed": self.changed,
            "needs_attention": self.needs_attention,
        }

    def summary(self) -> str:
        parts = [
            f"{self.changed} task(s) assigned",
            f"{len(self.already_explicit)} already explicit",
            f"{len(self.ambiguous)} ambiguous",
            f"{len(self.unmatched)} unmatched",
        ]
        prefix = "Would migrate: " if self.dry_run else "Migrated: "
        return prefix + ", ".join(parts) + "."


def infer_project(task: Task, slugs: list[str]) -> tuple[str, list[str]]:
    """
    The project a task names, if exactly one does.

    Returns ``(slug, candidates)``. When more than one distinct project matches,
    the task gets nothing — assigning the first would be the same guess the
    heuristic made, just recorded permanently.

    Matching has two wrinkles worth stating, because both were wrong on the
    first attempt:

    A slug's own hyphens must survive prose. `growth-bot` has to match "Growth
    Bot", so each hyphen in the slug matches a hyphen *or* whitespace. The
    earlier version normalised the whole haystack to hyphens instead, which made
    every word hyphen-adjacent and defeated its own word-boundary check —
    "sourcingBOT Increment 6" stopped matching `sourcingbot`.

    A shorter slug inside a longer one is not a second project. Text containing
    "cue-app" matches both `cue-app` and `cue`; the contained match is dropped
    rather than reported as ambiguous, because "cue-app" plainly means the
    cue-app project.
    """
    haystack = re.sub(r"\s+", " ", f"{task.title} {task.objective}".lower())

    spans: list[tuple[str, int, int]] = []
    for slug in slugs:
        if not slug:
            continue
        # Each hyphen in the slug matches a hyphen or run of whitespace, so a
        # slug written as prose still matches.
        body = r"[-\s]+".join(re.escape(part) for part in slug.split("-"))
        match = re.search(rf"(?<![a-z0-9])(?:{body})(?![a-z0-9])", haystack)
        if match:
            spans.append((slug, match.start(), match.end()))

    # Drop a match wholly contained in a longer one: it is the same mention.
    candidates = [
        slug
        for slug, start, end in spans
        if not any(
            other != slug
            and o_start <= start
            and end <= o_end
            and (o_end - o_start) > (end - start)
            for other, o_start, o_end in spans
        )
    ]
    return (candidates[0] if len(candidates) == 1 else ""), candidates


def backfill_projects(
    manager: TaskManager,
    slugs: list[str],
    dry_run: bool = True,
) -> MigrationResult:
    """
    Assign ``Task.project`` to tasks written before the field existed.

    Defaults to a dry run: the caller sees exactly what would change before
    anything is written. Nothing is deleted and no task is rewritten except to
    add the one field.
    """
    result = MigrationResult(dry_run=dry_run)
    ordered = sorted({s for s in slugs if s}, key=len, reverse=True)

    for task, directory in _all_tasks(manager):
        if task.project:
            result.already_explicit.append(task.id)
            continue

        inferred, candidates = infer_project(task, ordered)
        if inferred:
            result.assigned[task.id] = inferred
            if not dry_run:
                task.project = inferred
                manager._write(task, directory=directory)
        elif candidates:
            result.ambiguous[task.id] = candidates
        else:
            result.unmatched.append(task.id)

    return result


def _all_tasks(manager: TaskManager) -> list[tuple[Task, Path]]:
    """Every task on disk, paired with the directory it lives in."""
    pairs: list[tuple[Task, Path]] = []
    for directory, tasks in (
        (manager._active_dir, manager.list_active()),
        (manager._completed_dir, manager.list_completed()),
    ):
        pairs.extend((task, directory) for task in tasks)
    return pairs
