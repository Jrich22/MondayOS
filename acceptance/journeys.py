"""
The user journey each project is put through.

Nine turns and a stale scenario, in order, because the order is the test. C only
means something after B: a question about the evidence behind a recommendation is
a different question when there is no recommendation. F only means something
after an executive turn, since the property it checks is that a lookup escapes
executive narration rather than that a lookup works at all.

Questions are phrased the way somebody would actually type them, and `{topic}`
and `{symbol}` are filled from the corpus's own nouns -- the same nouns
`benchmark/` uses, so the two harnesses ask about the same things.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Turn:
    """
    One question, and what MondayOS owes the person who asked it.

    ``expect_register`` is the routing contract. ``requires_prior_strategy``
    marks a turn that is only meaningful once a decision exists; the runner
    records it as skipped rather than failed when the preceding strategic turn
    produced nothing, because a missing recommendation is the earlier turn's
    failure and reporting it twice would double-count one defect.
    """

    id: str
    question: str
    expect_register: str
    why: str
    requires_prior_strategy: bool = False
    # Gate ids this turn is the evidence for. Kept on the turn so the report can
    # say which question exercised which guarantee.
    gates: tuple[int, ...] = field(default_factory=tuple)


JOURNEY: tuple[Turn, ...] = (
    Turn(
        id="A.overview",
        question="Give me a 60 second overview of this project.",
        expect_register="grounded",
        why="an orientation question is answered from what the project says, not from strategy",
        gates=(1, 2, 11, 13),
    ),
    Turn(
        id="B.next",
        question="What should we build next?",
        expect_register="executive",
        why="asks what to do, so it owes a recommendation with alternatives and scores",
        gates=(2, 6, 13),
    ),
    Turn(
        id="C.evidence",
        question="What evidence supports that recommendation?",
        expect_register="continuation",
        why="refers to a decision already made; the ranking must not be re-run",
        requires_prior_strategy=True,
        gates=(5, 6, 11),
    ),
    Turn(
        id="D.change-mind",
        question="What would make you change your mind?",
        expect_register="continuation",
        why="asks about the conditions around a decision, not for a new one",
        requires_prior_strategy=True,
        gates=(5,),
    ),
    Turn(
        id="E.risk",
        question="What is the biggest risk?",
        expect_register="executive",
        why="a strategic question in its own right, answered about this project only",
        gates=(1, 2),
    ),
    Turn(
        id="F.where",
        question="Where is that implemented?",
        expect_register="grounded",
        why="the lookup override: a question wanting a file and a line gets one, mid-strategy",
        gates=(3, 11),
    ),
    Turn(
        id="G.changed",
        question="What changed recently?",
        expect_register="grounded",
        why="history is this project's own; a nested project must not inherit its parent's",
        gates=(9,),
    ),
    Turn(
        id="H.why",
        question="Why was {topic} designed this way?",
        expect_register="grounded",
        why="decision evidence when it exists, and a plain admission when it does not",
        gates=(10,),
    ),
    Turn(
        id="I.say-more-warm",
        question="Say more about that.",
        expect_register="continuation",
        why="with a decision in play, an elaboration continues it",
        requires_prior_strategy=True,
        gates=(8,),
    ),
)

# Asked in a conversation of its own, with nothing before it. The same words as
# I.say-more-warm, and the opposite correct answer: with no decision to refer to,
# "say more" is an ordinary question and answering it with a memo would invent a
# conversation that never happened.
COLD_TURN = Turn(
    id="I.say-more-cold",
    question="Say more about that.",
    expect_register="grounded",
    why="no prior strategic state, so there is nothing to continue",
    gates=(7,),
)

# Asked after the project's fingerprint has been moved in the conversation
# record. Nothing on disk changes: the stored decision is made to describe a
# world that no longer matches, which is exactly what staleness is.
STALE_TURN = Turn(
    id="J.still-best",
    question="Is that still the best thing to work on?",
    expect_register="executive",
    why="a current-action question against a moved world must reassess, not reassure",
    requires_prior_strategy=True,
    gates=(5,),
)

# The same strategic question as B, asked again in a fresh conversation against
# an unchanged project. Wording may differ; identity may not.
DETERMINISM_TURN = Turn(
    id="K.determinism",
    question="What should we build next?",
    expect_register="executive",
    why="an unchanged project must yield the same decision, whatever the prose",
    gates=(14,),
)


# --------------------------------------------------------------------------- #
# the streaming integrity journey
# --------------------------------------------------------------------------- #
#
# The journey above drives `send-message`, which calls `respond()` and does not
# stream. A hosted run therefore reported "streaming verified" having never
# emitted a provider chunk, and buffering -- the guarantee that no unverified
# citation reaches a reader -- was asserted only by offline tests.
#
# This drives `Monday.workspace_stream`, the same method the dashboard uses. It
# is deliberately a second path rather than a replacement: one returns a finished
# answer, the other yields deltas the caller may stop, and collapsing them would
# prove neither.

# One evidence-heavy question, because that is the turn that exercises
# buffering: a conversational reply with nothing to cite would stream happily and
# establish nothing about the guarantee.
STREAMING_QUESTION = "What changed recently?"


@dataclass
class StreamObservation:
    """What one streamed turn actually did, in the terms the gates score."""

    project: str
    question: str
    deltas: list[str] = field(default_factory=list)
    provider_chunks: int = 0
    events: list[str] = field(default_factory=list)
    final_content: str = ""
    persisted_content: str = ""
    stop_reason: str = ""
    incomplete: bool = False
    error: str = ""
    evidence_validation: dict[str, Any] = field(default_factory=dict)

    @property
    def visible_before_validation(self) -> str:
        """
        Everything a reader could have seen.

        Every delta, concatenated. On a buffered turn the validated answer is
        released as a single delta after checking, so this equals the final
        content; on an unbuffered one it is the running text. Either way it is
        what must contain no unverified identifier.
        """
        return "".join(self.deltas)

    @property
    def streamed_progressively(self) -> bool:
        """More than one delta -- the answer arrived in pieces."""
        return len(self.deltas) > 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "question": self.question,
            "delta_count": len(self.deltas),
            "provider_chunks": self.provider_chunks,
            "events": self.events,
            "streamed_progressively": self.streamed_progressively,
            "final_content": self.final_content,
            "persisted_content": self.persisted_content,
            "persisted_matches_shown": self.persisted_matches_shown,
            "stop_reason": self.stop_reason,
            "incomplete": self.incomplete,
            "error": self.error,
            "evidence_validation": self.evidence_validation,
        }

    @property
    def persisted_matches_shown(self) -> bool:
        """
        What was stored is what was shown.

        A stored answer that differs from the delivered one would mean the
        transcript disagrees with what the user read, which is the same class of
        failure as an unverified citation: the record stops being checkable.
        """
        return self.persisted_content.strip() == self.final_content.strip()


def observe_stream(
    monday: Any,
    project: str,
    question: str = STREAMING_QUESTION,
    *,
    title: str = "streaming-integrity",
) -> StreamObservation:
    """
    Drive one streamed turn through the production entry point and record it.

    Nothing here interprets the result: it collects what happened so the gates
    can score it. A turn that fails closed is recorded like any other -- the
    refusal is the product working, and the observation exists to show that no
    unverified identifier reached a delta on the way to it.
    """
    observation = StreamObservation(project=project, question=question)

    created = monday.workspace("create-conversation", project=project, title=title)
    conversation_id = (getattr(created, "data", {}) or {}).get("id", "")
    if not conversation_id:
        observation.error = "could not create a conversation"
        return observation

    for event in monday.workspace_stream(project, conversation_id, question):
        kind = str(event.get("type", ""))
        observation.events.append(kind)
        if kind == "delta":
            observation.deltas.append(str(event.get("text", "")))
        elif kind == "done":
            message = event.get("message") or {}
            observation.final_content = str(message.get("content", "") or "")
            observation.stop_reason = str(message.get("stop_reason", "") or "")
            observation.incomplete = bool(message.get("incomplete", False))
            observation.error = str(message.get("error", "") or "")
        elif kind == "error":
            observation.error = str(event.get("message", "") or event.get("error", ""))

    stored = monday.workspace("get-conversation", project=project, conversation_id=conversation_id)
    data = getattr(stored, "data", {}) or {}
    messages = data.get("messages") or (data.get("conversation") or {}).get("messages") or []
    assistants = [m for m in messages if m.get("role") == "assistant"]
    if assistants:
        observation.persisted_content = str(assistants[-1].get("content", "") or "")
        if not observation.stop_reason:
            observation.stop_reason = str(assistants[-1].get("stop_reason", "") or "")

    return observation
