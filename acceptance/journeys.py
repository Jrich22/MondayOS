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
