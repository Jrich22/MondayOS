"""Provider cost model for avoidance accounting (§1, §8).

When the memory-first pipeline answers without calling an external provider,
it records the call it *would* have made and the cost it avoided. These are
estimates — deliberately simple and transparent — not billing figures. The
numbers live in one place so they can be tuned without touching pipeline logic.
"""
from __future__ import annotations

from dataclasses import dataclass

from brain.router import ModelTier

# Blended $/1K token estimate per tier (input+output averaged). Local models
# have no per-token cost. Figures are order-of-magnitude planning estimates.
_TIER_PRICE_PER_1K: dict[ModelTier, float] = {
    ModelTier.HIGH: 0.015,
    ModelTier.STANDARD: 0.003,
    ModelTier.FAST: 0.0005,
    ModelTier.LOCAL: 0.0,
}

# Default total tokens (prompt + completion) for a typical reasoning call,
# used when the caller does not supply its own estimate.
DEFAULT_CALL_TOKENS = 1500

# A verification call re-checks an existing answer rather than generating it
# from scratch, so it costs a fraction of a full call.
VERIFY_TOKEN_FRACTION = 0.25


@dataclass
class CostEstimate:
    """Estimated tokens and dollars for a (would-be) provider call."""

    tier: ModelTier
    tokens: int
    usd: float

    def to_dict(self) -> dict[str, object]:
        return {"tier": self.tier.value, "tokens": self.tokens, "usd": round(self.usd, 6)}


def estimate_call(tier: ModelTier, tokens: int = DEFAULT_CALL_TOKENS) -> CostEstimate:
    """Estimate the cost of one provider call at the given tier."""
    price = _TIER_PRICE_PER_1K.get(tier, _TIER_PRICE_PER_1K[ModelTier.STANDARD])
    usd = (tokens / 1000.0) * price
    return CostEstimate(tier=tier, tokens=tokens, usd=usd)


def estimate_verification(tier: ModelTier, tokens: int = DEFAULT_CALL_TOKENS) -> CostEstimate:
    """Estimate the cost of a lightweight verification call at the given tier."""
    verify_tokens = int(tokens * VERIFY_TOKEN_FRACTION)
    return estimate_call(tier, verify_tokens)
