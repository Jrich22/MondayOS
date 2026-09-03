"""
The Context Engine — an OS service, not a bot.

``ContextEngine.build(project, query)`` assembles what MondayOS knows about one
project into an attributed, budgeted ``ContextSnapshot``. It calls no model and
is deterministic: the same project state and the same request produce the same
snapshot, ranking included.

It is one service rather than a per-caller helper for a specific reason: the
isolation rules and the secret-redaction pass have to live in exactly one place.
Two callers assembling their own context means two implementations of "never
cross projects", and eventually one of them is wrong (ADR-017).

**Reuse is conservative.** A snapshot is reused only when a fingerprint of the
things it was built from is unchanged — project, git HEAD, working-tree state,
task statuses, knowledge ids, docs mtimes — and only when the request would rank
it the same way. Stale context is worse than a rebuild: it produces a confident
answer about a state that no longer exists, and nothing in the transcript reveals
that. Every uncertainty here resolves to rebuilding.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workspace.context import adapters, budget, relevance
from workspace.context.snapshot import ContextSnapshot, ContextSource, snapshot_id

# How many of each kind of record are worth pulling before budgeting. Generous
# enough that ranking and the budget are what decide, small enough that a huge
# project does not build a thousand-item list to throw it away.
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
        ask_intelligence: Callable[[str, str, str], Any] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._resolve = resolve_project
        self._read_tasks = read_tasks
        self._read_knowledge = read_knowledge
        # Deterministic project retrieval. Optional: without it the snapshot is
        # exactly what increments 1 and 2 produced, which is still a valid
        # snapshot rather than a degraded one.
        self._ask_intelligence = ask_intelligence
        self._now = now or (lambda: datetime.now(tz=UTC))
        self._cache: dict[str, ContextSnapshot] = {}

    # ------------------------------------------------------------------ build

    def build(self, project: str, query: str = "", carry: str = "") -> ContextSnapshot:
        """
        Assemble a snapshot for exactly one project.

        ``query`` is the request being answered. It ranks items *within* each
        category and is recorded on the snapshot. An empty query reorders
        nothing — no query is no evidence, and inventing an order from no
        evidence is worse than the order the data arrived in.

        Raises whatever ``resolve_project`` raises for an unknown project — the
        one failure that must be loud, because context for a project that does
        not exist would produce a confident answer about nothing.
        """
        slug, root, description = self._resolve(project)
        created_at = self._now()

        gathered: list[ContextSource] = [
            # Identity is never ranked: every line of it is load-bearing.
            adapters.identity_source(slug, root, description),
            adapters.docs_source(slug, root, query=query),
            self._tasks(slug, query),
            self._knowledge(slug, query),
            adapters.git_source(slug, root, commit_limit=MAX_COMMITS, query=query),
            self._intelligence(slug, query, carry),
        ]

        result = budget.apply(gathered)
        material = "\n".join(s.render() for s in result.sources)
        snapshot = ContextSnapshot(
            id=snapshot_id(slug, created_at, material),
            project=slug,
            created_at=created_at,
            sources=result.sources,
            omitted=result.omitted,
            fingerprint=self._fingerprint(slug, root),
            query=query,
        )
        self._cache[slug] = snapshot
        return snapshot

    def build_or_reuse(
        self, project: str, query: str = "", carry: str = ""
    ) -> tuple[ContextSnapshot, bool]:
        """
        Return a snapshot and whether it was reused.

        Reuse requires two things. The **fingerprint** must match, covering
        project, git state, tasks, knowledge and docs. And the **ranking must be
        equivalent**: a snapshot ranked for a different question may have dropped
        the very items this one needs, so a changed query rebuilds even when the
        world has not moved.
        """
        slug, root, _ = self._resolve(project)
        cached = self._cache.get(slug)
        if cached is not None and cached.fingerprint and self._reusable(cached, root, query):
            return cached, True
        return self.build(project, query, carry), False

    def invalidate(self, project: str = "") -> None:
        """Drop cached context for one project, or for all of them."""
        if project:
            self._cache.pop(project, None)
        else:
            self._cache.clear()

    # ------------------------------------------------------------- reuse rules

    def _reusable(self, cached: ContextSnapshot, root: Path, query: str) -> bool:
        """
        Whether a cached snapshot is still safe to serve.

        Every condition answers "could this be missing something the new request
        needs?" — and any uncertainty resolves to rebuilding.
        """
        if cached.fingerprint != self._fingerprint(cached.project, root):
            return False
        # Ranking is query-dependent: a snapshot ranked for a different question
        # may have dropped what this one needs. Equivalent terms are the only
        # safe reuse.
        return relevance.terms(cached.query) == relevance.terms(query)

    def _fingerprint(self, slug: str, root: Path) -> str:
        """
        A cheap digest of everything a snapshot was built from.

        Reads only metadata — git HEAD, porcelain status, task ids and statuses,
        knowledge ids, docs mtimes — so it costs far less than assembling the
        snapshot while still changing whenever the snapshot would.

        Fails toward rebuilding: any error yields a unique value, so an
        unreadable source can never look unchanged.
        """
        try:
            parts: list[str] = [slug]

            head = adapters.git_command(root, "rev-parse", "HEAD")
            status = adapters.git_command(root, "status", "--porcelain", "--", str(root))
            parts.append(f"git:{head}:{hashlib.sha256(status.encode()).hexdigest()[:16]}")

            if self._read_tasks is not None:
                rows = self._read_tasks(slug)
                parts.append("tasks:" + ",".join(f"{r.get('id')}={r.get('status')}" for r in rows))

            if self._read_knowledge is not None:
                entries = self._read_knowledge(slug)
                parts.append("knowledge:" + ",".join(str(e.get("id")) for e in entries))

            docs = root / "docs"
            if docs.is_dir():
                stamps = sorted(
                    f"{p.name}:{int(p.stat().st_mtime)}"
                    for p in docs.iterdir()
                    if p.is_file() and p.suffix.lower() == ".md"
                )
                parts.append("docs:" + ",".join(stamps))

            return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]
        except Exception:  # noqa: BLE001 — fail toward rebuilding, never toward stale
            return f"unknown-{self._now().timestamp()}"

    def _intelligence(self, slug: str, query: str, carry: str = "") -> ContextSource:
        """Evidence retrieved for this request, when an indexer is wired."""
        from workspace.context.intelligence_source import intelligence_source

        if self._ask_intelligence is None or not query:
            return ContextSource(
                name="intelligence",
                label="Project intelligence",
                origin="project index (not configured)",
            )
        ask = self._ask_intelligence
        return intelligence_source(slug, lambda: ask(slug, query, carry), query=query)

    # ---------------------------------------------------------------- readers

    def _tasks(self, slug: str, query: str) -> ContextSource:
        """Tasks for this project, or an empty source when no reader is wired."""
        if self._read_tasks is None:
            return ContextSource(name="tasks", label="Tasks", origin="TaskManager (not configured)")
        reader = self._read_tasks
        return adapters.tasks_source(slug, lambda: reader(slug), query=query)

    def _knowledge(self, slug: str, query: str) -> ContextSource:
        if self._read_knowledge is None:
            return ContextSource(
                name="knowledge",
                label="Project knowledge",
                origin="KnowledgeStore (not configured)",
            )
        reader = self._read_knowledge
        return adapters.knowledge_source(slug, lambda: reader(slug), query=query)
