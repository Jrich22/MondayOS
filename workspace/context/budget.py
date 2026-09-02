"""
Deterministic context budgeting.

The budget answers one question: given more project material than fits, what goes
in? It answers it the same way every time, from a fixed priority order and fixed
caps — no model call, no scoring, no heuristics (ADR-016).

Determinism is the point. A budget that reorders context based on a similarity
score would make "why did Monday know this?" unanswerable without replaying the
scorer, and would make two runs of the same question return different answers for
reasons nobody can see. Relevance ranking arrives in increment 2, on top of this
baseline rather than instead of it.

Priority order, highest first:

    1. identity   — who this project is. Without it nothing else has meaning.
    2. docs       — architecture and ADRs: the decisions already made.
    3. tasks      — what is actively being worked on.
    4. knowledge  — what has been learned and written down.
    5. git        — what changed recently.

Identity before git is deliberate. A model that knows the project's purpose and
constraints but not last week's commits gives a useful answer; one that knows the
commits but not the project gives a confident irrelevant one.
"""

from __future__ import annotations

from dataclasses import dataclass

from workspace.context.snapshot import ContextSource

# Budget priority. The list order IS the priority; a source not named here is
# appended last, so adding an adapter never silently outranks an existing one.
PRIORITY: tuple[str, ...] = ("identity", "docs", "tasks", "knowledge", "git")

# Per-source character caps. Chosen so a full snapshot lands near 6-8k tokens:
# comfortably inside any provider's window, and small enough that a human can
# actually read what was sent.
SOURCE_CAPS: dict[str, int] = {
    "identity": 2_000,
    "docs": 6_000,
    "tasks": 6_000,
    "knowledge": 6_000,
    "git": 4_000,
}

DEFAULT_SOURCE_CAP = 4_000

# Total cap across all sources. A source that would cross it is truncated, and if
# nothing of it fits, it is omitted by name rather than silently dropped.
TOTAL_CAP = 24_000


@dataclass
class BudgetResult:
    """What the budget decided, and what it left out."""

    sources: list[ContextSource]
    omitted: list[str]


def priority_index(name: str) -> int:
    """Rank a source name. Unknown sources sort last, in a stable order."""
    try:
        return PRIORITY.index(name)
    except ValueError:
        return len(PRIORITY)


def apply(
    sources: list[ContextSource],
    total_cap: int = TOTAL_CAP,
    source_caps: dict[str, int] | None = None,
) -> BudgetResult:
    """
    Fit sources into the budget, highest priority first.

    Truncation happens on whole items, never mid-item: half a commit message or
    half an ADR title is worse than one fewer of them, because a truncated fact
    reads as a complete one.

    A source that cannot fit even one item is omitted by name. That omission is
    recorded on the snapshot so a thin context is visible rather than assumed.
    """
    caps = source_caps if source_caps is not None else SOURCE_CAPS
    ordered = sorted(sources, key=lambda s: (priority_index(s.name), s.name))

    kept: list[ContextSource] = []
    omitted: list[str] = []
    spent = 0

    for source in ordered:
        # A failed adapter still belongs in the snapshot: it carries the reason
        # the context is thin, and it costs nothing because it has no items.
        if not source.items:
            kept.append(source)
            continue

        cap = min(caps.get(source.name, DEFAULT_SOURCE_CAP), max(0, total_cap - spent))
        fitted, truncated = _fit(source.items, cap)

        if not fitted:
            omitted.append(source.name)
            continue

        kept.append(
            ContextSource(
                name=source.name,
                label=source.label,
                items=fitted,
                # Reasons are parallel to items and must be cut to the same
                # length. Dropping them here would silently discard the ranking
                # attribution ADR-016 exists to preserve — the budget would make
                # a ranked snapshot indistinguishable from an unranked one.
                reasons=source.reasons[: len(fitted)],
                truncated=source.truncated or truncated,
                error=source.error,
                origin=source.origin,
            )
        )
        spent += sum(len(i) for i in fitted)

    # Restore priority order for rendering, so the prompt reads identity-first.
    kept.sort(key=lambda s: (priority_index(s.name), s.name))
    return BudgetResult(sources=kept, omitted=omitted)


def _fit(items: list[str], cap: int) -> tuple[list[str], bool]:
    """Take whole items until the cap is reached. Returns (kept, was_truncated)."""
    kept: list[str] = []
    used = 0
    for item in items:
        if used + len(item) > cap:
            return kept, True
        kept.append(item)
        used += len(item)
    return kept, False
