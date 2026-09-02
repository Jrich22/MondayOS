"""
Relevance ranking *within* a context category.

Increment 1 ordered context by category and took whatever fit. That is
predictable and blunt: a project with forty tasks sent the first fifteen
regardless of what was actually asked.

This module ranks items inside a category against the current request, and — the
part that matters — **records why each item survived**. Ranking without
attribution would trade one opaque selection for another; the point of ADR-016 is
that the prompt stays explainable, and "the scorer preferred it" explains
nothing.

Every reason is a fixed, enumerable string. A human reading a snapshot sees
`active-task`, `keyword-match`, `architecture-priority` — categories they can
reason about — rather than a float they would have to trust.

No vector store, no embeddings, no second index. Scoring is keyword overlap plus
structural signals already present in the data, which keeps it deterministic:
the same request against the same project ranks identically every time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Why an item was included. The vocabulary is closed on purpose — a free-text
# reason would drift into prose nobody can aggregate.
REASON_ACTIVE_TASK = "active-task"
REASON_ARCHITECTURE = "architecture-priority"
REASON_KEYWORD = "keyword-match"
REASON_RECENT = "recent"
REASON_BASELINE = "baseline"

# Words too common to signal anything. Kept short deliberately: an aggressive
# stopword list starts discarding real query terms ("state", "test", "build" are
# all meaningful in this codebase).
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "do",
        "does",
        "did",
        "what",
        "which",
        "who",
        "when",
        "where",
        "why",
        "how",
        "that",
        "this",
        "it",
        "its",
        "we",
        "our",
        "you",
        "your",
        "i",
        "me",
        "my",
        "us",
        "can",
        "could",
        "should",
        "would",
        "will",
        "shall",
        "may",
        "might",
        "have",
        "has",
        "had",
        "not",
        "no",
        "yes",
        "at",
        "by",
        "from",
        "as",
    }
)

_WORD = re.compile(r"[a-z0-9][a-z0-9_-]*")


@dataclass
class Ranked:
    """One item, its score, and the reason a human will read."""

    text: str
    score: float
    reason: str


def terms(query: str) -> set[str]:
    """Meaningful lowercase terms from a request."""
    return {
        word
        for word in _WORD.findall((query or "").lower())
        if word not in _STOPWORDS and len(word) > 2
    }


def overlap(text: str, query_terms: set[str]) -> int:
    """How many distinct query terms appear in an item."""
    if not query_terms:
        return 0
    lowered = text.lower()
    return sum(1 for term in query_terms if term in lowered)


def rank(
    items: list[str],
    query: str,
    baseline_reason: str = REASON_BASELINE,
    priority: dict[int, str] | None = None,
    limit: int = 0,
) -> list[Ranked]:
    """
    Order items by relevance to ``query``, keeping original order among equals.

    Ties break on original position rather than arbitrarily, so a source that
    arrived in a meaningful order (tasks by priority, commits by recency) keeps
    that order wherever the query does not distinguish.

    ``priority`` marks items that outrank keyword matching regardless of the
    query — an in-progress task is relevant to almost any question about a
    project, whether or not the question happens to use its words.

    With an empty query nothing is reordered: no query means no evidence, and
    inventing an ordering from no evidence is worse than the arrival order.
    """
    query_terms = terms(query)
    pinned = priority or {}
    ranked: list[tuple[float, int, Ranked]] = []

    for index, text in enumerate(items):
        if index in pinned:
            ranked.append((1000.0, index, Ranked(text, 1000.0, pinned[index])))
            continue

        hits = overlap(text, query_terms)
        if hits:
            # Normalise by how much of the query matched, so an item hitting
            # three of three terms outranks one hitting three of ten.
            score = 10.0 * hits / max(1, len(query_terms))
            ranked.append((score, index, Ranked(text, score, REASON_KEYWORD)))
        else:
            ranked.append((0.0, index, Ranked(text, 0.0, baseline_reason)))

    ranked.sort(key=lambda row: (-row[0], row[1]))
    result = [row[2] for row in ranked]
    return result[:limit] if limit > 0 else result


def split(ranked: list[Ranked]) -> tuple[list[str], list[str]]:
    """Separate ranked items into parallel text and reason lists."""
    return [r.text for r in ranked], [r.reason for r in ranked]
