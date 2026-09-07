"""
Comparing strategic options, and pricing what it costs to be wrong.

A single recommendation is advice. A recommendation *with the alternatives that
lost and why* is a decision someone else can audit, disagree with, or take
responsibility for — and that difference is most of what separates a CTO from a
suggestion box. So options are compared explicitly and the losers are kept.

The module also splits one number into three, because they answer different
questions and collapsing them hides the interesting case:

- **Evidence strength** — how well-supported are the facts underneath this?
- **Recommendation confidence** — how sure are we this is the right call?
- **Execution risk** — how likely is doing it to go badly?

Those come apart constantly. "Add tests to the payments module" rests on strong
evidence, is a confident call, and carries low execution risk. "Rewrite the
scheduler" can rest on equally strong evidence and still be the riskiest thing on
the list. A single confidence score reports those as similar, which is exactly
backwards for anyone deciding what to do on Monday morning.

Execution risk is computed from properties of the *work*, not of the argument for
it: how many capabilities it touches, whether those have tests, whether anything
it depends on is blocked. Nothing here asks a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from initiatives.models import Health, Initiative

# Risk band thresholds. Three bands for the same reason confidence has three:
# the difference between 0.41 and 0.47 is not something anybody should act on.
_HIGH_RISK = 0.60
_MODERATE_RISK = 0.30


class RiskBand(Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


@dataclass(frozen=True)
class Risk:
    """
    How likely this work is to go wrong, and why.

    Higher is worse — the inverse of confidence, and deliberately so. A reader
    scanning a list should never have to remember which direction a number runs.
    """

    score: float
    because: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "score", max(0.0, min(1.0, float(self.score))))

    @property
    def band(self) -> RiskBand:
        if self.score >= _HIGH_RISK:
            return RiskBand.HIGH
        if self.score >= _MODERATE_RISK:
            return RiskBand.MODERATE
        return RiskBand.LOW

    @property
    def percent(self) -> int:
        return int(round(self.score * 100))

    def display(self) -> str:
        return f"{self.band.value} ({self.percent}%)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 3),
            "percent": self.percent,
            "band": self.band.value,
            "because": list(self.because),
        }


@dataclass(frozen=True)
class Alternative:
    """
    An option that was considered and not chosen.

    ``why_not`` is mandatory in spirit: an alternative listed without a reason for
    rejecting it is decoration that makes the analysis look more thorough than it
    was. The reader's most common and most valuable response to advice is "why not
    the other thing", and this is the field that answers it.
    """

    statement: str
    why_not: str
    tradeoffs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement,
            "why_not": self.why_not,
            "tradeoffs": list(self.tradeoffs),
        }


# Risk contributions. Each is small; they accumulate, and every one that fires
# records its reason so the total can be read back as an argument.
_NO_TESTS = 0.25
_BLOCKED_DEPENDENCY = 0.20
_PER_DEPENDENCY = 0.06
_DEPENDENCY_CAP = 0.24
_LARGE_SURFACE = 0.15
_UNDOCUMENTED = 0.08
_GREENFIELD_DISCOUNT = 0.12

# Members above which a capability counts as a large surface to change.
LARGE_SURFACE_MEMBERS = 60


@dataclass
class Option:
    """One candidate course of action, with everything needed to rank it."""

    statement: str
    rationale: str
    leverage: float
    tradeoffs: tuple[str, ...] = ()
    effort: str = ""
    # The capability this work lands in, when it lands in one. Execution risk is
    # mostly a property of where the change happens, not of the change itself.
    initiative: Initiative | None = None
    greenfield: bool = False
    evidence: Any = field(default=None)


def execution_risk(option: Option, blocked_slugs: frozenset[str] = frozenset()) -> Risk:
    """
    How risky this work is to carry out.

    Deliberately about the work, not the argument for it. Strong evidence that
    something needs doing says nothing about whether doing it will go smoothly,
    and conflating the two is how confident-sounding advice leads somewhere bad.
    """
    reasons: list[str] = []
    score = 0.10
    reasons.append("baseline risk for any change")

    initiative = option.initiative
    if initiative is not None:
        if initiative.progress.has_code and not initiative.progress.has_tests:
            score += _NO_TESTS
            reasons.append(f"{initiative.name} has implementation but no tests")
        if not initiative.progress.has_docs:
            score += _UNDOCUMENTED
            reasons.append(f"{initiative.name} is undocumented, so intent is hard to recover")
        if len(initiative.members) >= LARGE_SURFACE_MEMBERS:
            score += _LARGE_SURFACE
            reasons.append(f"{initiative.name} is large ({len(initiative.members)} artefacts)")
        if initiative.dependencies:
            bonus = min(_PER_DEPENDENCY * len(initiative.dependencies), _DEPENDENCY_CAP)
            score += bonus
            reasons.append(
                f"{len(initiative.dependencies)} dependent capabilit(ies) could be affected"
            )
        if any(d.on in blocked_slugs for d in initiative.dependencies):
            score += _BLOCKED_DEPENDENCY
            reasons.append("depends on a capability that is itself blocked")
        if initiative.health is Health.BLOCKED:
            score += _BLOCKED_DEPENDENCY
            reasons.append(f"{initiative.name} is currently blocked")

    if option.greenfield:
        # Building something new cannot regress what already works. It can still
        # be wrong; it is less likely to be destructive.
        score -= _GREENFIELD_DISCOUNT
        reasons.append("new work, so it cannot regress existing behaviour")

    return Risk(score=score, because=tuple(reasons))


def compare(options: list[Option], keep: int = 3) -> tuple[list[Option], list[Alternative]]:
    """
    Rank options and turn the losers into stated alternatives.

    Returns the survivors and the rejected set. The rejected are not discarded,
    because "what else did you consider" is the first question any competent
    reader asks, and an answer assembled after the fact is not the same thing.
    """
    ranked = sorted(options, key=lambda o: -o.leverage)
    chosen = ranked[:keep]
    rejected = ranked[keep : keep + 4]

    alternatives = [
        Alternative(
            statement=option.statement,
            why_not=_why_not(option, chosen[0] if chosen else None),
            tradeoffs=option.tradeoffs,
        )
        for option in rejected
    ]
    return chosen, alternatives


def _why_not(option: Option, winner: Option | None) -> str:
    """A specific reason this option lost, rather than 'lower priority'."""
    if winner is None:
        return "no option outranked it; nothing was selected"
    gap = winner.leverage - option.leverage
    if gap >= 0.3:
        return f"substantially lower leverage than {winner.statement.lower()}"
    if gap >= 0.1:
        return f"worth doing, but {winner.statement.lower()} unblocks more first"
    return f"nearly as strong as {winner.statement.lower()}; a defensible second choice"
