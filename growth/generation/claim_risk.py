"""
Claim-risk classification - a review escalation layer, not a fact-checker.

This module does not decide whether a claim is true. It cannot, and pretending
otherwise with a longer list of regexes would be worse than useless: it would
produce a confident "clean" verdict on text nobody verified.

What it does is narrower and honest. It spots the *shapes* of claims that a human
must look at before publication - a number, a customer, a comparison, a
legal/medical/financial assertion, an external fact - and marks the draft as
requiring enhanced review. The draft stays a perfectly normal draft. It may move
to ready-for-review. What it may not do is clear review on the strength of a
regex having found nothing wrong.

The distinction from ``check_safety`` matters:

    check_safety   BLOCKS. A fabricated statistic must not reach a reviewer
                   looking reviewable.
    classify_claims ESCALATES. A legitimate number still needs a human to
                   confirm it is the right number, and says so.

Both run on generated output rather than on the prompt, so a model that ignores
its instructions is caught by the same pass that catches a human editing a claim
in afterwards.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ClaimRisk(Enum):
    """Shapes of claim that require a human to verify before publication."""

    NUMERIC = "numeric-claim"
    TESTIMONIAL = "testimonial"
    CUSTOMER = "customer-claim"
    COMPARATIVE = "comparative-claim"
    LEGAL = "legal-claim"
    MEDICAL = "medical-claim"
    FINANCIAL = "financial-claim"
    EXTERNAL_FACT = "unverifiable-external-fact"


# Detection patterns. Deliberately broad: this layer is meant to over-flag, since
# the cost of an unnecessary human glance is far below the cost of an unverified
# claim going out under a brand's name.
_PATTERNS: tuple[tuple[ClaimRisk, str, str], ...] = (
    (
        ClaimRisk.NUMERIC,
        r"\b\d[\d,]*(\.\d+)?\s*(%|percent|x\b|times|million|billion|k\b|hours?|days?|weeks?)",
        "states a figure that a human must confirm is the right figure",
    ),
    (
        ClaimRisk.NUMERIC,
        r"\b(doubl|tripl|halv)(e|ed|es|ing)\b",
        "implies a quantified change",
    ),
    (
        ClaimRisk.TESTIMONIAL,
        r"[\"“][^\"”]{15,}[\"”]|\b(said|says|told us|according to)\b",
        "presents a quote or attributed statement",
    ),
    (
        ClaimRisk.CUSTOMER,
        r"\b(our (customers?|clients?|users?)|one (customer|client|team)|a customer)\b",
        "makes a claim about customers that must be true of real ones",
    ),
    (
        ClaimRisk.COMPARATIVE,
        r"\b(faster|better|cheaper|more effective|outperform\w*|unlike|compared to|versus|vs\.?)\b",
        "compares against something, which invites a substantiation question",
    ),
    (
        ClaimRisk.LEGAL,
        r"\b(compliant|compliance|gdpr|ccpa|soc ?2|iso ?\d+|certified|guarantee\w*|"
        r"warrant\w*|liability|patent|trademark)\b",
        "asserts a legal or certification position",
    ),
    (
        ClaimRisk.MEDICAL,
        r"\b(health|clinical|patient|diagnos\w*|treatment|therapy|medical)\b",
        "touches a medical subject",
    ),
    (
        ClaimRisk.FINANCIAL,
        r"\b(roi|revenue|savings?|cost reduction|profit|payback|return on investment)\b",
        "asserts a financial outcome",
    ),
    (
        ClaimRisk.EXTERNAL_FACT,
        r"\b(research shows|studies show|industry (average|standard|data)|"
        r"market (size|data|research)|report(s|ed)? that|survey)\b",
        "cites an external fact or source that must be real and correctly quoted",
    ),
)

_COMPILED: tuple[tuple[ClaimRisk, re.Pattern[str], str], ...] = tuple(
    (risk, re.compile(pattern, re.IGNORECASE), why) for risk, pattern, why in _PATTERNS
)


@dataclass
class ClaimFlag:
    """One claim shape found in a draft, and why a human should look."""

    risk: ClaimRisk
    excerpt: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk": self.risk.value,
            "excerpt": self.excerpt,
            "reason": self.reason,
        }


@dataclass
class ClaimAssessment:
    """The claim-risk verdict for one draft."""

    flags: list[ClaimFlag] = field(default_factory=list)

    @property
    def requires_enhanced_review(self) -> bool:
        """True when a human must verify a claim before this may be published."""
        return bool(self.flags)

    @property
    def risks(self) -> list[str]:
        """Distinct risk categories present, sorted."""
        return sorted({f.risk.value for f in self.flags})

    def summary(self) -> str:
        """One line a reviewer can act on."""
        if not self.flags:
            return "No claim-shaped statements detected."
        return (
            f"Enhanced review required: {', '.join(self.risks)}. "
            "These are claim SHAPES, not verified errors - a human must confirm each "
            "is true and correctly stated before publication."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "requires_enhanced_review": self.requires_enhanced_review,
            "risks": self.risks,
            "flags": [f.to_dict() for f in self.flags],
            "summary": self.summary(),
            "note": (
                "This is a review escalation layer, not a fact-checking engine. An "
                "absence of flags is not a verification that the copy is accurate."
            ),
        }


def classify_claims(text: str) -> ClaimAssessment:
    """
    Find claim shapes in a draft that require human verification.

    Over-flags on purpose. A false positive costs a reviewer a glance; a false
    negative puts an unverified claim in front of an audience under the brand's
    name.
    """
    assessment = ClaimAssessment()
    seen: set[tuple[ClaimRisk, str]] = set()

    for risk, pattern, why in _COMPILED:
        for match in pattern.finditer(text or ""):
            excerpt = _excerpt(text, match.start(), match.end())
            key = (risk, excerpt)
            if key in seen:
                continue
            seen.add(key)
            assessment.flags.append(ClaimFlag(risk=risk, excerpt=excerpt, reason=why))
    assessment.flags.sort(key=lambda f: (f.risk.value, f.excerpt))
    return assessment


def _excerpt(text: str, start: int, end: int, window: int = 40) -> str:
    """A readable snippet around a match, so a reviewer sees the claim in context."""
    left = max(0, start - window)
    right = min(len(text), end + window)
    snippet = " ".join(text[left:right].split())
    prefix = "…" if left > 0 else ""
    suffix = "…" if right < len(text) else ""
    return f"{prefix}{snippet}{suffix}"
