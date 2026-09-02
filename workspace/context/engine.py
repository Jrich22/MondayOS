"""
The Context Engine — an OS service, not a bot.

``ContextEngine.build(project)`` assembles what MondayOS knows about one project
into an attributed, budgeted ``ContextSnapshot``. It calls no model, holds no
domain state of its own, and reads every subsystem through the narrow adapters in
``adapters.py``.

It is one service rather than a per-caller helper for a specific reason: the
isolation rules and the secret-redaction pass have to live in exactly one place.
Two callers assembling their own context means two implementations of "never
cross projects", and eventually one of them is wrong (ADR-017).

Determinism is a property, not an accident. Given the same project state, the
same snapshot comes out — same sources, same order, same truncation. That is what
makes context testable without a provider and what makes "why did Monday know
this?" answerable from the record.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workspace.context import adapters, budget
from workspace.context.snapshot import ContextSnapshot, ContextSource, snapshot_id

# How many of each kind of record are worth pulling before budgeting. Generous
# enough that the budget is what decides, small enough that a huge project does
# not build a thousand-item list to throw it away.
MAX_ACTIVE_TASKS = 15
MAX_RECENT_TASKS = 5
MAX_KNOWLEDGE = 12
MAX_COMMITS = 10


class ContextEngine:
    """
    Builds project context snapshots from existing MondayOS subsystems.

    The subsystem readers are injected as plain callables rather than concrete
    types. That keeps the engine testable without a filesystem or a TaskManager,
    and — more importantly — it means each reader is already project-scoped by the
    time the engine holds it. The engine cannot widen a scope it never had.
    """

    def __init__(
        self,
        resolve_project: Callable[[str], tuple[str, Path, str]],
        read_tasks: Callable[[str], list[dict[str, Any]]] | None = None,
        read_knowledge: Callable[[str], list[dict[str, Any]]] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._resolve = resolve_project
        self._read_tasks = read_tasks
        self._read_knowledge = read_knowledge
        self._now = now or (lambda: datetime.now(tz=UTC))

    def build(self, project: str) -> ContextSnapshot:
        """
        Assemble a snapshot for exactly one project.

        Raises whatever ``resolve_project`` raises for an unknown project — that
        is the one failure that must be loud, because building context for a
        project that does not exist would silently produce an empty snapshot and
        an answer about nothing.
        """
        slug, root, description = self._resolve(project)
        created_at = self._now()

        gathered: list[ContextSource] = [
            adapters.identity_source(slug, root, description),
            adapters.docs_source(slug, root),
            self._tasks(slug),
            self._knowledge(slug),
            adapters.git_source(slug, root, commit_limit=MAX_COMMITS),
        ]

        result = budget.apply(gathered)
        material = "\n".join(s.render() for s in result.sources)
        return ContextSnapshot(
            id=snapshot_id(slug, created_at, material),
            project=slug,
            created_at=created_at,
            sources=result.sources,
            omitted=result.omitted,
        )

    # ---------------------------------------------------------------- readers

    def _tasks(self, slug: str) -> ContextSource:
        """Tasks for this project, or an empty source when no reader is wired."""
        if self._read_tasks is None:
            return ContextSource(name="tasks", label="Tasks", origin="TaskManager (not configured)")
        reader = self._read_tasks
        return adapters.tasks_source(slug, lambda: reader(slug))

    def _knowledge(self, slug: str) -> ContextSource:
        if self._read_knowledge is None:
            return ContextSource(
                name="knowledge",
                label="Project knowledge",
                origin="KnowledgeStore (not configured)",
            )
        reader = self._read_knowledge
        return adapters.knowledge_source(slug, lambda: reader(slug))
