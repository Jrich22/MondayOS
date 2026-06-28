"""Internal reasoning engine for Monday.ask().

Answers engineering questions by searching the knowledge store and task
manager, traversing relationships, ranking results, and synthesizing a
structured plain-text response. No external model calls are made.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from knowledge.store import KnowledgeStore
    from tasks.manager import TaskManager

from knowledge.entry import KnowledgeEntry, KnowledgeType
from knowledge.errors import KnowledgeNotFoundError


class QuestionIntent(Enum):
    """Inferred intent category of a prompt."""
    GENERAL = auto()        # Generic knowledge query
    HISTORICAL = auto()     # "Have we seen this before?"
    TYPE_BUG = auto()       # "Show related bugs"
    TYPE_DECISION = auto()  # "Show ADRs / decisions"
    TYPE_TASK = auto()      # "Show related tasks"
    BLOCKED_TASKS = auto()  # "What is currently blocked?"
    RECENT_CHANGES = auto() # "What changed recently?"
    SUMMARY = auto()        # "Summarize what we know about X"
    ONBOARDING = auto()     # "What should I read first?"


@dataclass
class ReasoningResult:
    """Structured output from the reasoning engine."""
    answer: str
    sources: list[str] = field(default_factory=list)
    model_used: str = "monday-reasoning/1.0"
    confidence: float = 0.0
    supporting_entries: list[dict[str, Any]] = field(default_factory=list)
    related_tasks: list[dict[str, Any]] = field(default_factory=list)
    related_decisions: list[dict[str, Any]] = field(default_factory=list)
    suggested_next_actions: list[str] = field(default_factory=list)


class ReasoningEngine:
    """
    Internal reasoning engine for Monday.ask().

    Answers engineering questions purely from data already stored in
    MondayOS — no external model calls, no network I/O.

    Architecture:
        1. Classify the prompt into a QuestionIntent (pattern matching).
        2. Extract meaningful search terms (stop-word removal).
        3. Search the knowledge store and/or task manager per intent.
        4. Traverse one hop of relationships on the top results.
        5. Rank, deduplicate, and split results by type.
        6. Synthesize a plain-text answer using intent-specific templates.
        7. Calculate a confidence score from quantity + quality signals.

    Storage independence:
        The engine depends only on the public interfaces of KnowledgeStore
        and TaskManager, not their Markdown-on-disk internals. Replacing
        either with a graph or vector backend requires no changes here.

    Future integration:
        When an LLM integration is added, it slots in at step 6 — the
        classified intent + ranked entries are passed to the model as a
        structured prompt, and the model's output replaces the template
        synthesis. The rest of the pipeline is unchanged.
    """

    def __init__(
        self,
        knowledge_store: KnowledgeStore,
        task_manager: TaskManager,
    ) -> None:
        self._knowledge = knowledge_store
        self._tasks = task_manager

    def answer(
        self,
        prompt: str,
        context: dict[str, Any] | None = None,
    ) -> ReasoningResult:
        """
        Answer an engineering question using internal knowledge.

        Returns a ReasoningResult with answer text, ranked sources, related
        tasks and decisions, confidence, and suggested follow-up actions.
        """
        if not prompt.strip():
            return ReasoningResult(
                answer="No question provided.",
                confidence=0.0,
            )

        intent = _classify_intent(prompt)
        terms = _extract_terms(prompt)
        topic = " ".join(terms) if terms else prompt.strip()

        knowledge_hits = self._search_knowledge(intent, terms, topic)
        task_hits = self._search_tasks(intent, terms)

        # Traverse one hop of relationships on top 3 results
        traversed = self._traverse_relationships(knowledge_hits[:3])
        seen_ids = {e.id for e in knowledge_hits}
        for entry in traversed:
            if entry.id not in seen_ids:
                knowledge_hits.append(entry)
                seen_ids.add(entry.id)

        # Partition decisions out so they appear in their own field
        decision_hits = [e for e in knowledge_hits if e.entry_type == KnowledgeType.DECISION]
        support_hits = [e for e in knowledge_hits if e.entry_type != KnowledgeType.DECISION]

        answer = _synthesize(prompt, intent, topic, knowledge_hits, task_hits)
        actions = _suggest_actions(intent, topic, knowledge_hits, task_hits)
        confidence = _calculate_confidence(knowledge_hits, task_hits, intent)
        sources = [e.id for e in knowledge_hits] + [t["id"] for t in task_hits]

        return ReasoningResult(
            answer=answer,
            sources=sources,
            confidence=confidence,
            supporting_entries=[_entry_dict(e) for e in support_hits],
            related_tasks=task_hits,
            related_decisions=[_entry_dict(e) for e in decision_hits],
            suggested_next_actions=actions,
        )

    # ------------------------------------------------------------------
    # Search helpers
    # ------------------------------------------------------------------

    def _search_knowledge(
        self,
        intent: QuestionIntent,
        terms: list[str],
        topic: str,
    ) -> list[KnowledgeEntry]:
        if intent == QuestionIntent.RECENT_CHANGES:
            entries = self._knowledge.list_all()
            entries.sort(key=lambda e: e.updated_at or e.created_at, reverse=True)
            return entries[:10]

        if intent == QuestionIntent.BLOCKED_TASKS:
            return []

        if not terms:
            return []

        # Search full topic first (best multi-word recall)
        hits = self._knowledge.search(topic, limit=20)

        # Supplement with per-term searches when few hits
        if len(hits) < 3 and len(terms) > 1:
            seen = {e.id for e in hits}
            for term in terms:
                for entry in self._knowledge.search(term, limit=5):
                    if entry.id not in seen:
                        hits.append(entry)
                        seen.add(entry.id)

        # Apply type filter for specific intents
        type_filter = _intent_type_filter(intent)
        if type_filter is not None:
            hits = [e for e in hits if e.entry_type == type_filter]

        return hits[:10]

    def _search_tasks(
        self,
        intent: QuestionIntent,
        terms: list[str],
    ) -> list[dict[str, Any]]:
        from tasks.task import TaskStatus

        if intent == QuestionIntent.BLOCKED_TASKS:
            blocked = self._tasks.list_active(status=TaskStatus.BLOCKED)
            return [_task_dict(t) for t in blocked]

        if intent in (QuestionIntent.TYPE_TASK, QuestionIntent.RECENT_CHANGES):
            active = self._tasks.list_active()
            if terms:
                active = [
                    t for t in active
                    if any(
                        term.lower() in t.title.lower()
                        or term.lower() in t.objective.lower()
                        for term in terms
                    )
                ]
            return [_task_dict(t) for t in active[:5]]

        # General: include tasks that match terms
        if terms:
            active = self._tasks.list_active()
            matched = [
                t for t in active
                if any(
                    term.lower() in t.title.lower()
                    or term.lower() in t.objective.lower()
                    for term in terms
                )
            ]
            return [_task_dict(t) for t in matched[:3]]

        return []

    def _traverse_relationships(
        self,
        entries: list[KnowledgeEntry],
    ) -> list[KnowledgeEntry]:
        """Follow one hop of relationships for each entry (depth=1 BFS)."""
        related: list[KnowledgeEntry] = []
        for entry in entries:
            for rel in entry.relationships:
                try:
                    related.append(self._knowledge.get(rel.target_id))
                except (KnowledgeNotFoundError, Exception):
                    pass  # Target may not exist; skip silently
        return related


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _classify_intent(prompt: str) -> QuestionIntent:
    lower = prompt.lower()

    if "block" in lower:
        return QuestionIntent.BLOCKED_TASKS
    if any(kw in lower for kw in ("recent", "changed recently", "what changed", "what's new")):
        return QuestionIntent.RECENT_CHANGES
    if any(kw in lower for kw in ("summarize", "summary", "everything we know", "what do we know")):
        return QuestionIntent.SUMMARY
    if any(kw in lower for kw in ("read first", "where to start", "understand", "onboard")):
        return QuestionIntent.ONBOARDING
    if any(kw in lower for kw in ("have we seen", "seen before", "before?", "seen this")):
        return QuestionIntent.HISTORICAL
    if any(kw in lower for kw in ("adr", "decision", "decisions", "architectural")):
        return QuestionIntent.TYPE_DECISION
    if any(kw in lower for kw in ("bug", "bugs", "issue", "error", "failure")):
        return QuestionIntent.TYPE_BUG
    if any(kw in lower for kw in ("task", "tasks", "ticket")):
        return QuestionIntent.TYPE_TASK

    return QuestionIntent.GENERAL


def _intent_type_filter(intent: QuestionIntent) -> KnowledgeType | None:
    return {
        QuestionIntent.TYPE_BUG: KnowledgeType.BUG,
        QuestionIntent.TYPE_DECISION: KnowledgeType.DECISION,
    }.get(intent)


# ---------------------------------------------------------------------------
# Term extraction
# ---------------------------------------------------------------------------

_STOP: frozenset[str] = frozenset({
    "a", "about", "all", "an", "and", "any", "are", "as", "at",
    "be", "been", "before", "but", "by", "can", "currently",
    "do", "does", "find", "first", "for", "from", "get", "give",
    "have", "how", "i", "in", "into", "is", "it",
    "its", "know", "let", "list", "me", "most", "my",
    "new", "now", "of", "on", "or", "our",
    "read", "related", "see", "seen", "should", "show", "since",
    "so", "some", "summarize", "summary", "tell",
    "that", "the", "there", "these", "they", "this",
    "to", "understand", "us", "was", "we",
    "what", "when", "where", "which", "who", "with", "you",
})


def _extract_terms(prompt: str) -> list[str]:
    """Return significant words from prompt after stripping stop words and punctuation."""
    words = re.sub(r"[^\w\s]", " ", prompt.lower()).split()
    return [w for w in words if w not in _STOP and len(w) > 2]


# ---------------------------------------------------------------------------
# Answer synthesis
# ---------------------------------------------------------------------------

def _synthesize(
    prompt: str,
    intent: QuestionIntent,
    topic: str,
    entries: list[KnowledgeEntry],
    tasks: list[dict[str, Any]],
) -> str:
    n = len(entries)
    nt = len(tasks)

    if intent == QuestionIntent.BLOCKED_TASKS:
        if not tasks:
            return "No tasks are currently blocked."
        lines = "\n".join(f"  • {t['id']}: {t['title']}" for t in tasks)
        return f"{nt} blocked task(s):\n{lines}"

    if intent == QuestionIntent.RECENT_CHANGES:
        if not entries and not tasks:
            return "No recent activity found in MondayOS."
        parts = []
        if entries:
            lines = "\n".join(
                f"  • [{e.id}] {e.title}  (updated {_fmt_date(e.updated_at or e.created_at)})"
                for e in entries[:5]
            )
            parts.append(f"Recent knowledge base activity ({n} entries):\n{lines}")
        if tasks:
            task_lines = "\n".join(f"  • {t['id']}: {t['title']} [{t['status']}]" for t in tasks[:3])
            parts.append(f"Active tasks ({nt}):\n{task_lines}")
        return "\n\n".join(parts)

    if intent == QuestionIntent.HISTORICAL:
        if not entries:
            return f"No recorded entries found related to '{topic}'. This may be a new issue."
        top = entries[0]
        suffix = f" (and {n - 1} more)" if n > 1 else ""
        summary_text = top.summary or top.body[:200].rstrip()
        return (
            f"Yes — {n} entry/entries found related to '{topic}'{suffix}.\n\n"
            f"Most relevant: [{top.id}] {top.title}\n{summary_text}"
        )

    if intent == QuestionIntent.SUMMARY:
        if not entries:
            return f"No knowledge entries found about '{topic}'."
        titles = "\n".join(f"  • [{e.id}] {e.title}" for e in entries[:5])
        extra = f" (showing top 5 of {n})" if n > 5 else ""
        top_summary = entries[0].summary or entries[0].body[:300].rstrip()
        return (
            f"Found {n} entries about '{topic}'{extra}:\n{titles}\n\n"
            f"Top entry: {top_summary}"
        )

    if intent == QuestionIntent.TYPE_DECISION:
        if not entries:
            return f"No decision records (ADRs) found matching '{topic}'."
        lines = "\n".join(f"  • [{e.id}] {e.title}" for e in entries[:5])
        return f"Found {n} decision(s) related to '{topic}':\n{lines}"

    if intent == QuestionIntent.TYPE_BUG:
        if not entries:
            return f"No bug entries found matching '{topic}'."
        lines = "\n".join(f"  • [{e.id}] {e.title}" for e in entries[:5])
        return f"Found {n} bug entry/entries related to '{topic}':\n{lines}"

    if intent == QuestionIntent.TYPE_TASK:
        if not tasks:
            return f"No active tasks found related to '{topic}'."
        lines = "\n".join(f"  • {t['id']}: {t['title']} [{t['status']}]" for t in tasks[:5])
        return f"Found {nt} task(s) related to '{topic}':\n{lines}"

    if intent == QuestionIntent.ONBOARDING:
        if not entries:
            return f"No knowledge entries found about '{topic}'."
        scored = sorted(entries, key=lambda e: len(e.relationships), reverse=True)
        lines = "\n".join(
            f"  {i + 1}. [{e.id}] {e.title}"
            for i, e in enumerate(scored[:5])
        )
        return f"Suggested reading order for '{topic}':\n{lines}"

    # GENERAL
    if not entries and not tasks:
        return f"No results found for '{topic}' in MondayOS."

    parts = []
    if entries:
        top = entries[0]
        extra = f" (+{n - 1} more)" if n > 1 else ""
        summary_text = top.summary or top.body[:200].rstrip()
        parts.append(
            f"Found {n} knowledge entry/entries related to '{topic}'{extra}.\n\n"
            f"Top result: [{top.id}] {top.title}\n{summary_text}"
        )
    if tasks:
        task_titles = ", ".join(f"{t['id']}: {t['title']}" for t in tasks[:3])
        parts.append(f"Related active tasks: {task_titles}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Suggested actions
# ---------------------------------------------------------------------------

def _suggest_actions(
    intent: QuestionIntent,
    topic: str,
    entries: list[KnowledgeEntry],
    tasks: list[dict[str, Any]],
) -> list[str]:
    actions: list[str] = []

    if not entries and not tasks:
        actions.append(
            f"monday.learn(content=\"...\", entry_type=\"pattern\") "
            f"— record what you know about '{topic}'"
        )
        return actions

    if intent == QuestionIntent.BLOCKED_TASKS:
        for t in tasks[:3]:
            actions.append(
                f"monday.task('update_status', task_id='{t['id']}', "
                f"new_status='in-progress') — unblock {t['id']}"
            )
        return actions[:5]

    for entry in entries[:2]:
        actions.append(
            f"monday.search('{entry.id}') — retrieve full entry: {entry.title}"
        )

    if intent in (QuestionIntent.TYPE_BUG, QuestionIntent.HISTORICAL):
        actions.append(f"monday.search('resolved {topic}') — check for resolution")

    if intent == QuestionIntent.TYPE_DECISION:
        actions.append("monday.search('decision') — view all recorded decisions")

    if entries and intent != QuestionIntent.ONBOARDING:
        actions.append(
            f"monday.learn(content=\"...\", entry_type=\"pattern\") "
            f"— add new knowledge about '{topic}'"
        )

    return actions[:5]


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------

def _calculate_confidence(
    entries: list[KnowledgeEntry],
    tasks: list[dict[str, Any]],
    intent: QuestionIntent,
) -> float:
    if not entries and not tasks:
        return 0.0

    total = len(entries) + len(tasks)

    # Quantity: each result contributes 0.10, capped at 0.70
    base = min(total * 0.10, 0.70)

    # Quality bonus: entries with summaries are better-indexed
    summary_bonus = sum(0.04 for e in entries[:3] if e.summary)

    # Intent-type alignment bonus: results match what was asked for
    type_filter = _intent_type_filter(intent)
    if type_filter is not None:
        aligned = sum(1 for e in entries if e.entry_type == type_filter)
        alignment_bonus = min(aligned * 0.05, 0.15)
    else:
        alignment_bonus = 0.0

    # Relationship richness bonus (connected entries are well-understood)
    rel_bonus = min(sum(len(e.relationships) for e in entries[:3]) * 0.02, 0.10)

    # Cap below 0.95 — never claim certainty without LLM validation
    raw = base + summary_bonus + alignment_bonus + rel_bonus
    return round(min(raw, 0.95), 2)


# ---------------------------------------------------------------------------
# Dict conversion helpers
# ---------------------------------------------------------------------------

def _entry_dict(entry: KnowledgeEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "title": entry.title,
        "entry_type": entry.entry_type.value,
        "status": entry.status.value,
        "summary": entry.summary,
        "tags": list(entry.tags),
        "components": list(entry.components),
        "confidence": entry.confidence,
    }


def _task_dict(task: Any) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status.value,
        "priority": task.priority.value,
        "objective": task.objective,
    }


def _fmt_date(dt: Any) -> str:
    if dt is None:
        return "unknown"
    try:
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return str(dt)
