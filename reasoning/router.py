"""
The conversation router — one authority for which register a turn belongs in.

Routing used to happen twice per turn, in two places, with different
information. A closure inside `monday/api.py` called `route(question, strategy)`
and decided continuation; `ReasoningEngine.assess` then called `route(question)`
again — with no strategic state — and decided executive-versus-grounded. Both
`Routing` objects were computed for every turn, the policy was split between a
facade closure and the engine, and only one of the two could ever see that a
recommendation existed.

This module is the single decision. It is deterministic and model-independent:
every branch is a literal pattern or a comparison against stored state, so a
routing outcome can always be explained by pointing at the word or the value that
caused it.

Precedence is ordered, and the order is the design:

    1. grounded lookup override   (unconditional)
    2. fresh executive question
    3. strategic continuation
    4. grounded

The lookup guard runs first so no amount of widening the strategic patterns can
make "where is X implemented?" answer with a memo.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from reasoning.executive import (
    _APPLY_TO_PRIOR,
    _ASSESSMENT_DEICTIC,
    _BARE_FOLLOWUP,
    _LOOKUP,
    _NOT_STRATEGIC,
    _PATTERNS,
    Topic,
    is_continuation,
    is_current_action,
)


class Register(Enum):
    """
    Which register answers this turn.

    CONTINUATION is executive for everything downstream — same budget, same
    structure — but names itself so the engine knows not to re-rank. That
    distinction travels as data rather than being re-derived, which is what the
    two-call-sites version got wrong.
    """

    GROUNDED = "grounded"
    EXECUTIVE = "executive"
    CONTINUATION = "continuation"


@dataclass(frozen=True)
class Route:
    """
    One routing decision, with everything downstream needs to act on it.

    ``stale`` is decided here rather than inside the engine because staleness is
    a property of the *conversation* — the stored decision versus the world now —
    and the engine reasons about a project. Putting it here also means one place
    decides, which is the entire point of the module.
    """

    register: Register
    reason: str
    topic: Topic | None = None
    current_action: bool = False
    stale: bool = False

    @property
    def executive(self) -> bool:
        """True for both strategic registers — what the response budget follows."""
        return self.register in (Register.EXECUTIVE, Register.CONTINUATION)

    @property
    def continuation(self) -> bool:
        return self.register is Register.CONTINUATION

    def to_dict(self) -> dict[str, Any]:
        return {
            "register": self.register.value,
            "reason": self.reason,
            "topic": self.topic.value if self.topic else "",
            "current_action": self.current_action,
            "stale": self.stale,
        }


class ConversationRouter:
    """
    Decides the register for one turn.

    Holds no state and touches no model. Constructed once and shared, or
    constructed per call — it makes no difference, which is what lets every
    conversation path use the same routing without threading an object through.
    """

    def route(
        self,
        question: str,
        *,
        strategy: Any = None,
        fingerprint: str = "",
    ) -> Route:
        """
        The single routing decision for a turn.

        ``strategy`` is the conversation's stored strategic state, if any.
        ``fingerprint`` is the current world-state digest; comparing it against
        the stored one is what makes a prior decision stale.
        """
        text = (question or "").strip()
        if not text:
            return Route(Register.GROUNDED, "empty question")

        # 1. Lookup override. Unconditional, and first: a question asking where
        #    something lives gets a file and a line even mid-strategy.
        if _LOOKUP.search(text):
            return Route(Register.GROUNDED, "phrased as a retrieval lookup")

        stale = self._is_stale(strategy, fingerprint)

        # A question that can only be a follow-up is never a fresh one, however
        # much strategic vocabulary it shares with the fresh patterns.
        only_a_followup = strategy is not None and (
            bool(_BARE_FOLLOWUP.match(text))
            or bool(_ASSESSMENT_DEICTIC.search(text))
            or bool(_APPLY_TO_PRIOR.search(text))
        )

        # 2. Fresh executive.
        if not only_a_followup:
            for topic, pattern in _PATTERNS:
                if pattern.search(text):
                    return Route(Register.EXECUTIVE, f"matched {topic.value}", topic=topic)

        # 3. Continuation.
        continues, why = is_continuation(text, strategy)
        if continues:
            current_action = is_current_action(text)
            # A stale record cannot answer "what should I do now". Describing a
            # past recommendation is always safe; acting on one computed against a
            # repository that has since moved is how a system gives confidently
            # obsolete advice. Decided here so the engine never has to.
            if stale and current_action:
                return Route(
                    Register.EXECUTIVE,
                    "prior decision is stale and the question asks what to do now",
                    topic=self._topic_of(strategy),
                    current_action=True,
                    stale=True,
                )
            return Route(
                Register.CONTINUATION,
                f"continues the prior assessment: {why}",
                topic=self._topic_of(strategy),
                current_action=current_action,
                stale=stale,
            )

        # 4. Grounded.
        if _NOT_STRATEGIC.search(text):
            return Route(Register.GROUNDED, "phrased as a factual lookup")
        return Route(
            Register.GROUNDED,
            why if strategy is not None else "no strategic pattern matched",
        )

    @staticmethod
    def _is_stale(strategy: Any, fingerprint: str) -> bool:
        stored = str(getattr(strategy, "fingerprint", "") or "")
        return bool(strategy is not None and fingerprint and stored and fingerprint != stored)

    @staticmethod
    def _topic_of(strategy: Any) -> Topic | None:
        raw = str(getattr(strategy, "topic", "") or "")
        for topic in Topic:
            if topic.value == raw:
                return topic
        return None


# The shared instance. Routing is stateless, so one is enough, and a module-level
# instance is how every conversation path provably uses the same policy.
ROUTER = ConversationRouter()
