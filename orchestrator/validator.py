"""
ResultValidator — deterministically validates a provider's response before the
orchestrator is allowed to capture knowledge or update a task.

Validation is intentionally provider-agnostic and deterministic: it inspects the
ProviderResponse content against the plan and task, never calling another model.
A response that fails validation stops the pipeline — no knowledge is captured
and the task is not advanced — so a weak or empty AI result cannot silently
mutate project state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from brain.providers.base import ProviderResponse
    from orchestrator.planner import ExecutionPlan

# Substrings that, alone on a near-empty response, signal the provider failed
# to actually do the work rather than producing a result.
_REFUSAL_MARKERS = (
    "i cannot",
    "i can't help",
    "i am unable",
    "i'm unable",
    "as an ai language model",
)

_MIN_CONTENT_CHARS = 20


@dataclass
class ValidationResult:
    """
    Outcome of validating one provider response.

    Attributes:
        valid:  True if the response is fit to act on.
        score:  Confidence in the result, 0.0–1.0 (also used as the report's
                confidence). Capped below 1.0 — deterministic validation never
                claims certainty.
        issues: Human-readable problems found (empty when valid).
        checks: Per-check pass/fail map, for transparency in the report.
    """

    valid: bool
    score: float = 0.0
    issues: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "score": round(self.score, 2),
            "issues": self.issues,
            "checks": self.checks,
        }


class ResultValidator:
    """
    Validates a ProviderResponse against the ExecutionPlan and task.

    The checks are deliberately simple, deterministic, and explainable:
      - non_empty:      the response actually contains content
      - sufficient:     content exceeds a minimal length
      - not_refusal:    the provider did not refuse / punt
      - addresses_goal: the response references the objective's key terms

    Each passing check contributes to the confidence score.
    """

    def validate(
        self,
        plan: "ExecutionPlan",
        response: "ProviderResponse | None",
        task: dict[str, Any],
    ) -> ValidationResult:
        checks: dict[str, bool] = {}
        issues: list[str] = []

        content = (response.content if response else "") or ""
        stripped = content.strip()

        # 1. Non-empty
        non_empty = bool(stripped)
        checks["non_empty"] = non_empty
        if not non_empty:
            issues.append("Provider returned empty content.")

        # 2. Sufficient length
        sufficient = len(stripped) >= _MIN_CONTENT_CHARS
        checks["sufficient"] = sufficient
        if non_empty and not sufficient:
            issues.append(
                f"Response too short ({len(stripped)} chars; "
                f"need ≥ {_MIN_CONTENT_CHARS})."
            )

        # 3. Not a refusal
        lowered = stripped.lower()
        is_refusal = any(marker in lowered for marker in _REFUSAL_MARKERS) and len(stripped) < 160
        not_refusal = not is_refusal
        checks["not_refusal"] = not_refusal
        if is_refusal:
            issues.append("Response appears to be a refusal rather than a result.")

        # 4. Addresses the objective
        addresses_goal = self._addresses_goal(lowered, plan.objective)
        checks["addresses_goal"] = addresses_goal
        if non_empty and not addresses_goal:
            issues.append("Response does not visibly reference the objective.")

        score = self._score(checks)
        valid = non_empty and sufficient and not_refusal

        return ValidationResult(valid=valid, score=score, issues=issues, checks=checks)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _addresses_goal(self, lowered_content: str, objective: str) -> bool:
        """True if the response references any salient term from the objective."""
        terms = [t for t in _salient_terms(objective) if len(t) >= 4]
        if not terms:
            return True  # nothing specific to match against
        return any(term in lowered_content for term in terms)

    def _score(self, checks: dict[str, bool]) -> float:
        """Weighted confidence from the passing checks. Capped at 0.9."""
        if not checks:
            return 0.0
        weights = {
            "non_empty": 0.35,
            "sufficient": 0.2,
            "not_refusal": 0.2,
            "addresses_goal": 0.15,
        }
        score = sum(weights.get(name, 0.0) for name, ok in checks.items() if ok)
        return min(0.9, round(score, 2))


def _salient_terms(text: str) -> list[str]:
    """Lowercase alphanumeric terms from text, minus trivial stopwords."""
    import re

    stop = {
        "the", "a", "an", "and", "or", "to", "of", "for", "in", "on", "with",
        "that", "this", "should", "must", "will", "is", "are", "be", "by",
    }
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w for w in words if w not in stop]
