"""
Conversation search.

Scoped to one project by default, because that is the safe default: a search that
silently spans projects is the same disclosure as a context leak, arriving
through a different door. Cross-project search exists, but the caller has to ask
for it explicitly (``scope="all"``), and results always name their project so a
match is never mistaken for the current one.

Searches title and visible message content only. There is nothing else to search:
provider reasoning is not stored (ADR-015), so there is no hidden text an index
could accidentally expose.

Deliberately a linear scan over files rather than an index. At the scale one
operator generates this is fast, it cannot go stale, and it adds no second store
to keep consistent with the conversations themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from workspace.models import Conversation, Message, MessageRole

# How much text to show around a hit. Enough to recognise the match without
# reproducing the conversation in the results list.
SNIPPET_WINDOW = 60

MAX_MATCHES_PER_CONVERSATION = 3


@dataclass
class SearchHit:
    """One matching conversation, with why it matched."""

    conversation_id: str
    project: str
    title: str
    updated_at: str
    matched_title: bool
    snippets: list[str]
    message_count: int
    score: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "project": self.project,
            "title": self.title,
            "updated_at": self.updated_at,
            "matched_title": self.matched_title,
            "snippets": list(self.snippets),
            "message_count": self.message_count,
            "score": self.score,
        }


def search(conversations: list[Conversation], query: str, limit: int = 25) -> list[SearchHit]:
    """
    Rank conversations against a query.

    Scoring is simple and explainable: a title match outranks a body match,
    and more body matches outrank fewer. Nothing is weighted by recency —
    a conversation from last month that actually discusses the query is a
    better result than today's that mentions it once.
    """
    terms = [t for t in query.lower().split() if t]
    if not terms:
        return []

    hits: list[SearchHit] = []
    for conversation in conversations:
        hit = _match(conversation, terms)
        if hit is not None:
            hits.append(hit)

    hits.sort(key=lambda h: (-h.score, h.conversation_id))
    return hits[:limit]


def _match(conversation: Conversation, terms: list[str]) -> SearchHit | None:
    title_lower = conversation.title.lower()
    # Every term must appear somewhere, so a two-word query does not match a
    # conversation containing only the commoner of the two words.
    matched_title = all(term in title_lower for term in terms)

    snippets: list[str] = []
    body_hits = 0
    for message in conversation.messages:
        if message.role is MessageRole.EVENT:
            continue
        content_lower = message.content.lower()
        if not all(term in content_lower for term in terms):
            continue
        body_hits += 1
        if len(snippets) < MAX_MATCHES_PER_CONVERSATION:
            snippets.append(_snippet(message, terms[0]))

    if not matched_title and body_hits == 0:
        return None

    score = (100 if matched_title else 0) + body_hits
    return SearchHit(
        conversation_id=conversation.id,
        project=conversation.project,
        title=conversation.title,
        updated_at=conversation.updated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        matched_title=matched_title,
        snippets=snippets,
        message_count=conversation.message_count,
        score=score,
    )


def _snippet(message: Message, term: str) -> str:
    """Readable context around the first hit, on a single line."""
    text = " ".join(message.content.split())
    index = text.lower().find(term)
    if index < 0:
        return text[: SNIPPET_WINDOW * 2]
    start = max(0, index - SNIPPET_WINDOW)
    end = min(len(text), index + len(term) + SNIPPET_WINDOW)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"
