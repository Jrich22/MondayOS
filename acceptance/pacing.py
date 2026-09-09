"""
Telling a provider having a bad day apart from a product that is broken.

An earlier live run measured rate limiting and reported it as quality. The
difference matters more here than anywhere else in MondayOS: an overloaded API
says nothing about whether routing is correct, and counting a 529 as a
correctness failure would make the benchmark's headline number a function of how
busy someone else's servers were.

So every turn is classified before it is scored. Only `PRODUCT` can fail a gate.
Transient provider trouble is retried, then recorded as an incident and reported
separately. A MondayOS exception is never retried -- that is the product failing,
and hiding it behind a retry is how a real defect becomes an intermittent one.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Outcome(Enum):
    """What happened on one attempt, in the only terms scoring cares about."""

    OK = "ok"
    # The provider was busy, rate limited, or briefly unreachable. Not a defect.
    PROVIDER_TRANSIENT = "provider_transient"
    # The provider refused in a way retrying cannot fix: no key, unknown model.
    PROVIDER_FATAL = "provider_fatal"
    # MondayOS raised. This is the only class that fails a gate.
    PRODUCT = "product_error"


# Signatures of a provider under load rather than a request that was wrong.
# Matched on the message because provider SDKs surface status codes
# inconsistently, and a harness that only understood one vendor's exception types
# would quietly reclassify everything else as a product failure.
_TRANSIENT = re.compile(
    r"\b(429|500|502|503|504|529)\b"
    r"|overloaded|rate.?limit|too many requests|capacity"
    r"|timed? ?out|timeout|temporarily unavailable|connection reset"
    r"|connection aborted|remote end closed|service unavailable",
    re.I,
)

# Refusals that will be identical on the next attempt.
_FATAL = re.compile(
    r"\b(401|403|404)\b"
    r"|invalid[_ ]api[_ ]key|authentication|unauthorized|permission"
    r"|model not found|unknown model|no ai provider is configured",
    re.I,
)


def classify(error: str) -> Outcome:
    """Which kind of failure an error message describes."""
    text = (error or "").strip()
    if not text:
        return Outcome.OK
    if _FATAL.search(text):
        return Outcome.PROVIDER_FATAL
    if _TRANSIENT.search(text):
        return Outcome.PROVIDER_TRANSIENT
    # An unrecognised provider error is treated as fatal rather than as a product
    # failure. Guessing "this must be MondayOS" would manufacture defects out of
    # vendor messages nobody has seen yet.
    return Outcome.PROVIDER_FATAL


@dataclass
class Incident:
    """One provider failure, recorded and reported but never scored."""

    project: str
    turn: str
    outcome: str
    message: str
    attempts: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "turn": self.turn,
            "outcome": self.outcome,
            "message": self.message[:400],
            "attempts": self.attempts,
            "counted_as_product_failure": False,
        }


@dataclass
class Pacing:
    """
    How hard to push the provider.

    Sequential by construction: the journey's later turns depend on the earlier
    ones having happened, so there is nothing to parallelise even if it were
    polite to.
    """

    between_turns: float = 3.0
    after_executive: float = 6.0
    attempts: int = 3
    backoff: tuple[float, ...] = (2.0, 8.0, 30.0)
    sleep: Callable[[float], None] = time.sleep

    def pause(self, executive: bool) -> None:
        self.sleep(self.after_executive if executive else self.between_turns)

    def wait_before_retry(self, attempt: int) -> None:
        index = min(attempt, len(self.backoff) - 1)
        self.sleep(self.backoff[index])
