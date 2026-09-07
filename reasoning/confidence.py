"""
Computing how much a claim can be trusted.

Every score here is arithmetic over retrieved evidence. None is asked of a model,
and that is the single most consequential decision in this package. A model asked
to rate its own confidence will produce a fluent, plausible number that tracks how
confident the *prose* sounds rather than how strong the *evidence* is. It carries
the authority of a measurement while measuring nothing, and it fails hardest in
the case that matters most: a well-written answer built on almost no support.

The scoring rests on one idea worth stating plainly, because it is what makes the
number informative rather than decorative:

    **Corroboration across independent source kinds beats volume.**

Ten files that all mention a term are close to one piece of evidence — they are
the same fact observed ten times, and are frequently ten copies of one mistake. A
source file *plus* a test that exercises it *plus* an ADR that decided it is three
genuinely independent confirmations, because each would have had to go wrong on
its own. So distinct `CitationKind`s move the score substantially and raw citation
count barely moves it at all.

Everything is bounded, monotonic, and explainable: every adjustment appends its
own reason, so a score can always be read back as the argument that produced it.
"""

from __future__ import annotations

from intelligence.evidence import CitationKind, Evidence
from reasoning.models import ClaimKind, Confidence

# Starting points by claim kind. A fact begins high because it was read directly
# from the repository; it is not 1.0 because reading can still go wrong — a stale
# index, a misparsed heading, a file that moved. Certainty is not available and
# pretending otherwise is the failure this module exists to avoid.
_BASE: dict[ClaimKind, float] = {
    ClaimKind.FACT: 0.88,
    ClaimKind.INFERENCE: 0.55,
    ClaimKind.RECOMMENDATION: 0.50,
}

# Per distinct source kind beyond the first, and the ceiling on that bonus. The
# cap exists because the fourth independent kind genuinely adds less than the
# second: past a point the claim is corroborated and more agreement is redundant.
_PER_KIND = 0.07
_KIND_CAP = 0.24

# Volume moves the needle, but only just — see the module docstring.
_VOLUME_SMALL = 0.03
_VOLUME_LARGE = 0.03

# Each step of inference away from a directly-read fact. Reasoning chains degrade:
# a conclusion three rules deep has three chances to be wrong, and a system that
# does not price that will state its most speculative conclusions most firmly.
_PER_STEP = 0.09

# Contradicted evidence is the strongest signal available and is priced like it.
# A claim the repository argues with is not a slightly weaker claim.
_CONTRADICTION = 0.28

# Recent activity in the supporting files. Small, because recency is weak evidence
# of correctness — it says the area is alive, not that the claim about it is true.
_RECENT = 0.05


def _distinct_kinds(evidence: Evidence) -> set[CitationKind]:
    return {c.kind for c in evidence.citations}


def score(
    kind: ClaimKind,
    evidence: Evidence,
    steps: int = 1,
    contradictions: int = 0,
    recent: bool = False,
) -> Confidence:
    """
    Score one claim from its evidence.

    ``steps`` is the length of the derivation chain — 1 for something read
    directly, higher for a conclusion built on other conclusions. ``contradictions``
    counts sources that disagree with the claim, which is deliberately expensive.

    A claim with no evidence at all scores near zero regardless of kind. That case
    is not hypothetical: it is exactly what a confidently-worded guess looks like
    from the inside, and it is the one the number has to catch.
    """
    reasons: list[str] = []

    if evidence.empty:
        return Confidence(
            score=0.05,
            because=("no supporting evidence was retrieved",),
        )

    value = _BASE[kind]
    reasons.append(f"{kind.value} claim (base {_BASE[kind]:.2f})")

    kinds = _distinct_kinds(evidence)
    if len(kinds) > 1:
        bonus = min(_PER_KIND * (len(kinds) - 1), _KIND_CAP)
        value += bonus
        names = ", ".join(sorted(k.value for k in kinds))
        reasons.append(f"corroborated by {len(kinds)} independent source kinds ({names})")
    else:
        only = next(iter(kinds)).value
        reasons.append(f"single source kind ({only}) — no independent corroboration")

    count = len(evidence.citations)
    if count >= 3:
        value += _VOLUME_SMALL
    if count >= 6:
        value += _VOLUME_LARGE
    if count >= 3:
        reasons.append(f"{count} citations")

    if steps > 1:
        penalty = _PER_STEP * (steps - 1)
        value -= penalty
        reasons.append(f"{steps} reasoning steps from directly-read evidence")

    if contradictions:
        value -= _CONTRADICTION * contradictions
        reasons.append(
            f"{contradictions} source(s) contradict this — "
            "the repository disagrees with itself here"
        )

    if recent:
        value += _RECENT
        reasons.append("supporting files changed recently")

    return Confidence(score=min(value, CEILING), because=tuple(reasons))


# Nothing reads as certain. Even a fact corroborated from every source kind was
# read by an indexer that can be stale or wrong, and a displayed "100%" invites a
# reader to stop checking — the one behaviour this whole module exists to prevent.
CEILING = 0.95


def for_recommendation(
    supporting: Evidence,
    inference_count: int,
    gap_driven: bool = False,
) -> Confidence:
    """
    Score a recommendation.

    Recommendations are scored apart from claims because they are a different kind
    of statement: they weigh evidence against goals, so no amount of evidence makes
    one certain. The ceiling is deliberately below a fact's.

    ``gap_driven`` marks advice that rests on something being *absent*. Absence is
    weaker evidence than presence — the thing may exist somewhere the index does
    not reach — so it is priced down rather than presented with the same footing as
    a recommendation built on what is actually there.
    """
    base = score(ClaimKind.RECOMMENDATION, supporting, steps=max(1, inference_count))
    value = base.score
    reasons = list(base.because)

    if gap_driven:
        value -= 0.10
        reasons.append("rests on absent evidence, which is weaker than present evidence")

    # A judgement never reaches the confidence of a reading. Capping here rather
    # than hoping the arithmetic stays low keeps that guarantee explicit.
    value = min(value, 0.82)

    return Confidence(score=value, because=tuple(reasons))
