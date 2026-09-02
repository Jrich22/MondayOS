"""
Long-conversation handling — deterministic compaction, with a summariser seam.

A conversation that runs long cannot be replayed verbatim into every request. The
naive fix is to drop old turns silently, which makes the model appear to forget
things the operator can still see on screen — the worst kind of failure, because
the transcript disagrees with the answer.

This module does three things instead:

**The stored transcript is never touched.** Compaction decides what to *send*.
The file on disk keeps every message, always. A caller that mutates messages here
is doing something wrong.

**Recent turns go verbatim.** The most recent exchanges are the ones a follow-up
question refers to, so they are never summarised.

**Older turns become a compact, deterministic digest** — who said what, in order,
truncated per turn. No model is involved, so the same conversation always
compacts the same way, and the digest is explainable by reading this file.

Model summarisation sits behind ``ConversationSummarizer`` and is **not enabled**
in this increment. The seam exists so increment 3 can add it without touching the
service; a summariser that silently rewrote history would be a much bigger change
than it looks, because a bad summary is indistinguishable from a bad memory.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from workspace.models import Message, MessageRole

# Turns replayed verbatim. Enough for a follow-up ("and the second one?") to
# resolve against what was actually said.
VERBATIM_TURNS = 12

# Below this, a conversation is short enough to send whole and compaction is a
# no-op. Compacting a six-turn thread costs fidelity and saves nothing.
COMPACT_THRESHOLD = 20

# Per-turn cap inside the digest. Long enough to identify the turn, short enough
# that a hundred old turns stay affordable.
DIGEST_TURN_CHARS = 200


@dataclass
class Compaction:
    """What will be sent for one conversation, and what was left out."""

    verbatim: list[Message]
    digest: str = ""
    compacted_turns: int = 0

    @property
    def was_compacted(self) -> bool:
        return self.compacted_turns > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "verbatim_turns": len(self.verbatim),
            "compacted_turns": self.compacted_turns,
            "was_compacted": self.was_compacted,
            "digest": self.digest,
        }


class ConversationSummarizer(Protocol):
    """
    The seam for model-backed summarisation.

    Not used in increment 2. It exists so the decision to add it is a decision
    about summary quality, made on its own, rather than a refactor of the service
    bundled with it.
    """

    def summarize(self, messages: list[Message]) -> str:
        """Return a prose summary of older turns."""
        ...


def compact(
    messages: list[Message],
    verbatim_turns: int = VERBATIM_TURNS,
    threshold: int = COMPACT_THRESHOLD,
    summarizer: ConversationSummarizer | None = None,
) -> Compaction:
    """
    Decide what to send for a conversation.

    Returns the verbatim tail plus a digest of everything older. Never mutates
    ``messages`` and never returns a message object the caller could edit into
    the stored transcript by accident — the verbatim list holds the same objects,
    read-only by convention, and the digest is a fresh string.
    """
    turns = [m for m in messages if m.role in (MessageRole.USER, MessageRole.ASSISTANT)]

    if len(turns) <= max(threshold, verbatim_turns):
        return Compaction(verbatim=list(turns))

    split = len(turns) - verbatim_turns
    older, recent = turns[:split], turns[split:]

    if summarizer is not None:
        # The seam, wired but unused by default. A summariser that fails must not
        # take the conversation down with it: fall back to the digest, which is
        # always available because it needs nothing but the messages.
        try:
            digest = summarizer.summarize(older).strip()
        except Exception:  # noqa: BLE001 — a failed summary degrades, never blocks
            digest = ""
        if digest:
            return Compaction(verbatim=recent, digest=digest, compacted_turns=len(older))

    return Compaction(verbatim=recent, digest=_digest(older), compacted_turns=len(older))


def _digest(messages: list[Message]) -> str:
    """
    A deterministic digest of older turns.

    Reads as a condensed transcript rather than prose, because that is what it
    is. Presenting a mechanical truncation as a summary would imply that
    something judged what mattered.
    """
    if not messages:
        return ""

    lines = [
        f"Earlier in this conversation ({len(messages)} turns, condensed — "
        "each turn truncated, nothing interpreted):"
    ]
    for message in messages:
        speaker = "User" if message.role is MessageRole.USER else "MondayOS"
        text = " ".join(message.content.split())
        if len(text) > DIGEST_TURN_CHARS:
            text = text[: DIGEST_TURN_CHARS - 1].rsplit(" ", 1)[0] + "…"
        lines.append(f"  {speaker}: {text}")
    return "\n".join(lines)
