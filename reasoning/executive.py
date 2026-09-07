"""
Recognising a question that wants a decision rather than a document.

"Where is the responder implemented" and "what should we build next" are not the
same kind of question, and answering the second the way you answer the first is
the failure this package was built to fix. The first wants a file path. The second
wants a judgement, its alternatives, and the confidence behind it — and a correct,
well-cited list of documents is a non-answer to it.

Detection is pattern-based rather than model-based, for the same reason indexing
is: routing has to be predictable. If the register of an answer depended on a
model's read of the question, the same question could get a memo on Monday and a
file path on Tuesday, and nobody could tell whether that was a bug.

The patterns are deliberately narrow. A false positive is worse than a false
negative here: answering "where is X" with strategic advice is jarring and
useless, while missing a strategic question merely returns the grounded answer
that MondayOS already gave — which is the current behaviour, and is survivable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Topic(Enum):
    """
    What a strategic question is actually asking for.

    The topic selects which reasoning runs. "What worries you" and "what should we
    build next" both deserve the executive register but want genuinely different
    analysis — risk surfacing versus opportunity ranking — and collapsing them into
    one strategic mode would answer both with the same generic memo.
    """

    NEXT_WORK = "next-work"
    GAPS = "gaps"
    RISKS = "risks"
    READINESS = "readiness"
    INVESTOR = "investor"
    PRIORITIES = "priorities"


# Ordered: the first match wins, so more specific patterns precede general ones.
# "Are we ready for a demo" mentions readiness and would also match a loose risk
# pattern; readiness is the better answer, so it is tested first.
_PATTERNS: tuple[tuple[Topic, re.Pattern[str]], ...] = (
    (
        Topic.READINESS,
        re.compile(
            r"\b(are we|am i|is (?:this|it))\s+ready\b"
            r"|\bready (?:for|to)\s+(?:a\s+)?(demo|launch|ship|production|beta|investors?|users?)"
            r"|\b(can|could) we (?:demo|launch|ship|release)\b",
            re.I,
        ),
    ),
    (
        Topic.INVESTOR,
        re.compile(
            r"\binvestors?\b.*\b(ask|question|think|say|want|look)"
            r"|\b(what|which).{0,30}\b(investor|vc|due diligence)\b"
            r"|\bdue diligence\b",
            re.I,
        ),
    ),
    (
        Topic.RISKS,
        re.compile(
            r"\bwhat (?:worries|concerns) (?:you|me|us)\b"
            r"|\b(biggest|main|largest|greatest) (?:technical )?(?:risk|concern|worry|danger)"
            r"|\bwhat (?:should|would) (?:i|we) (?:be )?(?:worry|worried|concerned) about"
            r"|\bwhat(?:'s| is) (?:the )?(?:most )?(?:risky|fragile|brittle)"
            r"|\bwhere (?:are|is) (?:we|this) (?:most )?(?:weak|fragile|exposed)"
            # "Which initiative is most at risk" — the comparative form, which the
            # superlative-adjective patterns above do not reach.
            r"|\bmost at risk\b"
            r"|\b(?:which|what)\b.{0,40}\b(?:at risk|riskiest|most fragile|most exposed)\b"
            # "What are you least confident about" asks which conclusions are
            # weakest — a question about the assessment's own soft spots.
            r"|\bleast (?:confident|certain|sure)\b"
            r"|\bwhat(?:'s| is| are)?\b.{0,30}\bweakest\b",
            re.I,
        ),
    ),
    (
        Topic.GAPS,
        re.compile(
            r"\bwhat(?:'s| is| are)?\s+missing\b"
            r"|\b(biggest|main|largest) gap\b"
            r"|\bwhat (?:do|don't|dont) we (?:not )?have\b"
            r"|\bwhat(?:'s| is) (?:incomplete|unfinished|not done)\b"
            r"|\bwhere are (?:we|the) (?:gaps|holes)\b"
            # "What are we neglecting" is the same question asked about attention
            # rather than about artefacts.
            r"|\bneglect(?:ing|ed)?\b"
            r"|\b(?:overlook|ignoring|forgetting|falling behind)\b"
            r"|\bnot paying (?:enough )?attention\b",
            re.I,
        ),
    ),
    (
        Topic.NEXT_WORK,
        re.compile(
            r"\bwhat should (?:we|i)\s+(?:build|do|work on|tackle|ship|focus)"
            r"|\bwhat(?:'s| is) next\b"
            r"|\bwhat should (?:increment|phase|sprint|milestone|version|v)\s*\d*\s*be"
            r"|\b(?:next|following) (?:increment|phase|sprint|milestone)\b"
            r"|\bwhere should (?:we|i) (?:go|focus|start)"
            r"|\bwhat would you (?:build|do|prioriti[sz]e)\b"
            # "What is the safest high-value thing we could do next" — phrased as
            # a property of the work rather than as an instruction to choose.
            r"|\bwhat\b.{0,60}\b(?:could|should) (?:we|i) (?:do|build|ship|tackle)\b"
            r"|\b(?:safest|highest[- ]value|best|smartest)\b.{0,40}"
            r"\b(?:thing|move|next|step|option|bet)\b",
            re.I,
        ),
    ),
    (
        Topic.PRIORITIES,
        re.compile(
            r"\b(?:highest|top|most) (?:leverage|impact|important|valuable)\b"
            r"|\b(?:three|top|most important)\b.{0,30}\binitiatives?\b"
            r"|\bwhat matters most\b"
            r"|\bhow should (?:we|i) prioriti[sz]e\b"
            r"|\bwhat(?:'s| is) (?:the )?priorit(?:y|ies)\b",
            re.I,
        ),
    ),
)

# Questions that look strategic but are really lookups. "What did we build last
# increment" is a history question with a factual answer, and dressing it as a
# recommendation would be worse than useless — it would bury the answer.
_NOT_STRATEGIC = re.compile(
    r"\bwhat did (?:we|i|you)\b"
    r"|\bwhen (?:did|was)\b"
    r"|\bwhere (?:is|are|was|does)\b"
    r"|\bwho\b"
    r"|\bhow (?:do|does|did) (?:i|we|it|this|that)\b",
    re.I,
)


@dataclass(frozen=True)
class Routing:
    """Which register a question belongs in, and why it was routed there."""

    executive: bool
    topic: Topic | None = None
    reason: str = ""


def route(question: str) -> Routing:
    """
    Decide whether a question deserves the executive register.

    Returns the topic as well as the verdict, because the topic selects which
    analysis runs — a boolean would force every strategic question through one
    generic path and answer "what worries you" with a roadmap.
    """
    text = (question or "").strip()
    if not text:
        return Routing(executive=False, reason="empty question")

    # A lookup that happens to contain a strategic-sounding word stays a lookup.
    # Checked first so "what did we build next to the parser" is not a roadmap
    # request.
    if _NOT_STRATEGIC.search(text):
        # ...unless it *also* matches a strategic pattern strongly enough that the
        # question is genuinely both, e.g. "where should we focus next".
        for topic, pattern in _PATTERNS:
            if pattern.search(text):
                return Routing(
                    executive=True,
                    topic=topic,
                    reason=f"matched {topic.value} despite lookup phrasing",
                )
        return Routing(executive=False, reason="phrased as a factual lookup")

    for topic, pattern in _PATTERNS:
        if pattern.search(text):
            return Routing(executive=True, topic=topic, reason=f"matched {topic.value}")

    return Routing(executive=False, reason="no strategic pattern matched")
