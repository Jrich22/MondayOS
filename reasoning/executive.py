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
from typing import Any


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


# Retrieval verbs and code nouns. A question phrased this way wants a file and a
# line, and answering it with strategy is worse than useless -- it buries the
# answer. This guard runs FIRST and unconditionally, which is what keeps executive
# framing from leaking into ordinary code questions inside a strategic thread.
_LOOKUP = re.compile(
    r"\bwhere (?:is|are|does|do|was|were)\b"
    r"|\bshow (?:me|us|the)\b"
    r"|\bfind (?:every|all|the|any)\b"
    r"|\b(?:which|what) file\b"
    r"|\bwhat (?:module|package|class|function|method)\b"
    r"|\bopen the\b"
    r"|\blist (?:the|all|every)\b"
    r"|\breferences? to\b"
    r"|\bdefined in\b"
    r"|\bimplemented (?:in|at)\b"
    r"|\bwhat changed\b"
    r"|\bwhat did (?:we|i|you) (?:change|commit|ship|build)\b"
    r"|\blast \w+ commits?\b",
    re.I,
)

# Words that only an assessment produces. A follow-up naming one of these is
# talking about a recommendation rather than about code.
_STRATEGIC_VOCAB = re.compile(
    r"\brecommendation\b|\brecommend(?:ed|ing)?\b"
    r"|\bevidence\b"
    r"|\bconfiden(?:ce|t)\b"
    r"|\brisk(?:s|y)?\b"
    r"|\balternative(?:s)?\b|\brunner[- ]?up\b"
    r"|\btrade[- ]?offs?\b"
    r"|\boption(?:s)?\b"
    r"|\bchange your mind\b|\bchanged your mind\b"
    r"|\bhow (?:sure|certain)\b"
    r"|\bpick(?:ed)? that\b|\bchose\b|\bchosen\b"
    r"|\bthat one\b"
    r"|\bbest option\b|\bstill (?:the )?best\b",
    re.I,
)

# Phrases that can only be about a decision already made. "How confident are
# you?" has no other possible referent in a conversation, and demanding a pronoun
# alongside it would reject the most natural way to ask. These are sufficient on
# their own; the general vocabulary below is not.
_ASSESSMENT_DEICTIC = re.compile(
    r"\bchange[d]? your mind\b"
    r"|\bhow (?:confident|sure|certain) are you\b"
    r"|\bhow confident\b"
    r"|\brunner[- ]?up\b"
    r"|\bsecond (?:option|choice|best|place)\b"
    r"|\bstill (?:the )?best\b"
    r"|\bwhy (?:did|do) you (?:pick|choose|recommend|prefer)\b"
    # "Should we still build that?" asks whether a prior call holds. Narrow on
    # purpose: bare "still" would catch "is that still cached?", which is a code
    # question that happens to share a word.
    r"|\bshould (?:we|i) still\b"
    r"|\bdo you still (?:recommend|think|stand by)\b"
    r"|\bwhat (?:else|other options?) did you consider\b",
    re.I,
)

# A directive applied to what was just said. With a strategic decision in play,
# "turn that into a plan" has one plausible referent. Kept narrow -- an imperative
# plus a back-reference, not any sentence containing "plan".
_APPLY_TO_PRIOR = re.compile(
    r"\b(?:turn|make|write|expand|break|flesh|spell)\b[^.?!]{0,24}\b(?:that|it|this|those)\b"
    r"|\b(?:that|it|this)\b[^.?!]{0,24}\binto a (?:plan|roadmap|sequence|breakdown)\b",
    re.I,
)

# Pronouns that make a sentence a follow-up. Same set the question engine uses for
# grounded carry-over -- the signal is identical, only the referent differs.
_BACK_REFERENCE = re.compile(r"\b(?:it|its|that|this|them|those|these|the same)\b", re.I)

# "the second option", "the first two", "the runner-up".
_ORDINAL = re.compile(
    r"\b(?:first|second|third|1st|2nd|3rd|runner[- ]?up|top two|first two)\b", re.I
)

# A follow-up with no subject of its own. "Why?" is unambiguously about whatever
# was just said, and it is the shortest question a user actually asks.
_BARE_FOLLOWUP = re.compile(r"^\s*(?:so\s+)?why(?:\s+(?:that|is|not))?\s*\??\s*$", re.I)

# Asks what to do now, or whether the prior call still holds. These are the
# follow-ups that must not be answered from a stale record: acting on an
# out-of-date recommendation is a different failure from describing one.
_CURRENT_ACTION = re.compile(
    r"\bstill\b"
    r"|\b(?:what|which) (?:should|do) (?:we|i) (?:do|build|start|ship|tackle) (?:now|next|first)"
    r"|\bwhat should (?:i|we) do (?:now|first|next)\b"
    r"|\bshould we (?:still |now )?(?:build|do|start|ship|proceed|go)"
    r"|\bgo ahead\b|\bproceed\b"
    r"|\bwhat (?:do we|should we) do now\b"
    r"|\bright now\b",
    re.I,
)


@dataclass(frozen=True)
class Routing:
    """
    Which register a question belongs in, and why it was routed there.

    ``continuation`` distinguishes a fresh strategic question from a follow-up
    about one already answered. Both are executive -- same budget, same register
    -- but a continuation must not re-rank, so the distinction travels rather
    than being inferred downstream.
    """

    executive: bool
    topic: Topic | None = None
    reason: str = ""
    continuation: bool = False
    # True when the follow-up asks what to do now rather than what was decided.
    # Only meaningful for a continuation, and only load-bearing when the stored
    # state is stale.
    current_action: bool = False


def is_continuation(question: str, state: Any = None) -> tuple[bool, str]:
    """
    Whether this question refers to a strategic decision already made.

    Requires **two independent signals**, because false positives are worse than
    misses here: answering "what's the risk of that migration?" during a code
    review with a roadmap memo is jarring and unhelpful, while missing a genuine
    follow-up merely returns the grounded answer MondayOS already gave.

    The signals:

    - **referential** — a pronoun, an ordinal, or a bare "why?"; what makes the
      sentence a follow-up rather than a question in its own right
    - **strategic vocabulary** — a word only an assessment produces, or the name
      of a capability the stored state actually mentions

    A bare follow-up is accepted on its own. "Why?" has no subject to supply and
    cannot mean anything except the thing just said.

    Deliberately no fuzzy matching. Every signal is a literal pattern or a lookup
    against stored slugs, so a routing decision can be explained by pointing at
    the word that caused it.
    """
    if state is None:
        return False, "no strategic state"

    text = (question or "").strip()
    if not text:
        return False, "empty question"

    if _BARE_FOLLOWUP.match(text):
        return True, "bare follow-up with no subject of its own"

    # Self-referential by construction: no pronoun needed because no other
    # referent is possible.
    if _ASSESSMENT_DEICTIC.search(text):
        return True, "phrase can only refer to a decision already made"

    # An imperative aimed at what was just said.
    if _APPLY_TO_PRIOR.search(text):
        return True, "directive applied to the prior answer"

    referential = bool(_BACK_REFERENCE.search(text)) or bool(_ORDINAL.search(text))
    vocab = bool(_STRATEGIC_VOCAB.search(text))

    # Naming a capability the stored decision is about counts as strategic
    # vocabulary: "is Cue App still the right call" is unambiguous.
    named = ""
    lowered = text.lower()
    for slug in list(getattr(state, "initiative_slugs", []) or []) + [
        getattr(state, "initiative_slug", "") or ""
    ]:
        candidate = str(slug).strip().lower()
        if candidate and (candidate in lowered or candidate.replace("-", " ") in lowered):
            named = candidate
            break

    if referential and (vocab or named):
        detail = f"names '{named}'" if named else "strategic vocabulary"
        return True, f"back-reference plus {detail}"
    if vocab and named:
        return True, f"strategic vocabulary naming '{named}'"

    if referential:
        return False, "back-reference without strategic vocabulary"
    if vocab:
        return False, "strategic vocabulary without a back-reference"
    return False, "no continuation signal"


def is_current_action(question: str) -> bool:
    """
    Whether a follow-up asks what to do now rather than what was decided.

    The distinction only matters when the stored state is stale, and then it
    matters a great deal: describing a past recommendation accurately is always
    safe, while acting on one computed against a repository that has since moved
    is how a system gives confidently obsolete advice.
    """
    return bool(_CURRENT_ACTION.search(question or ""))


def route(question: str, state: Any = None) -> Routing:
    """
    Deprecated. Use ``reasoning.router.ROUTER.route``.

    This was one of two routing implementations. It is kept only as a thin
    redirect so nothing can accidentally reintroduce a second policy by importing
    it, and it now delegates rather than deciding: the router is the single
    authority, and this returns its verdict in the old shape.
    """
    from reasoning.router import ROUTER

    decision = ROUTER.route(question, strategy=state)
    return Routing(
        executive=decision.executive,
        topic=decision.topic,
        reason=decision.reason,
        continuation=decision.continuation,
        current_action=decision.current_action,
    )
