"""
Approval gates for the Multi-Agent Runtime.

The runtime is **review-required by default**: an agent may plan and produce
output, but the platform will not let it commit, push, touch secrets, live-trade,
or take destructive actions — nor auto-complete a task — without an explicit human
approval. This module is the single, testable place that decision lives.

Note the runtime *itself* never performs any gated action (it only calls the
provider, captures knowledge, and moves the task to REVIEW). These gates are the
explicit, future-proof policy layer on top of that structural safety: when a run
declares an intent that touches a gated action, or asks for autonomous
completion, the gate blocks it unless a human has approved.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agents.roles import GATED_ACTIONS
from orchestrator.report import ExecutionMode

__all__ = ["GATED_ACTIONS", "GateDecision", "ApprovalGate"]


@dataclass
class GateDecision:
    """
    Outcome of an approval-gate evaluation.

    Attributes:
        allowed:           True if the run may proceed.
        requires_approval: True if a human approval is (or was) required.
        gated_actions:     Requested actions that fall under GATED_ACTIONS.
        reason:            Human-readable explanation (why blocked / why allowed).
    """

    allowed: bool
    requires_approval: bool = False
    gated_actions: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "requires_approval": self.requires_approval,
            "gated_actions": list(self.gated_actions),
            "reason": self.reason,
        }


class ApprovalGate:
    """
    Decides whether an agent run may proceed under the review-required policy.

    Args:
        require_human_approval: When True (the default, mirroring
            MondayConfig.require_human_approval), autonomous completion of a task
            also requires an explicit approval — so the standing posture is
            "execute and review", never "execute and auto-finish".
    """

    def __init__(self, require_human_approval: bool = True) -> None:
        self._require_human_approval = require_human_approval

    def evaluate(
        self,
        *,
        mode: ExecutionMode,
        autonomous_enabled: bool = False,
        approved: bool = False,
        requested_actions: list[str] | None = None,
    ) -> GateDecision:
        """
        Evaluate a run request against the approval policy.

        Blocks (allowed=False) when:
          - any requested action is gated and no approval is present, or
          - autonomous mode is requested without explicit enablement, or
          - autonomous completion is requested and human approval is required
            but not present.

        Otherwise allows the run. DRY_RUN and REVIEW never mutate anything
        irreversible, so they are permitted (a gated action still blocks them,
        since even planning a commit should surface the approval requirement).
        """
        requested = list(requested_actions or [])
        gated = [a for a in requested if a in GATED_ACTIONS]

        if gated and not approved:
            return GateDecision(
                allowed=False,
                requires_approval=True,
                gated_actions=gated,
                reason=(
                    "Human approval required before an agent may "
                    f"{', '.join(sorted(set(gated)))}. Re-run with approval "
                    "(--approve) once a human has signed off."
                ),
            )

        if mode is ExecutionMode.AUTONOMOUS and not autonomous_enabled:
            return GateDecision(
                allowed=False,
                requires_approval=True,
                gated_actions=gated,
                reason=(
                    "Autonomous mode requires explicit enablement "
                    "(--enable-autonomous). Default posture is review-required."
                ),
            )

        if (
            mode is ExecutionMode.AUTONOMOUS
            and self._require_human_approval
            and not approved
        ):
            return GateDecision(
                allowed=False,
                requires_approval=True,
                gated_actions=gated,
                reason=(
                    "Autonomous completion requires human approval (--approve). "
                    "Without it the task stops at REVIEW."
                ),
            )

        return GateDecision(
            allowed=True,
            requires_approval=bool(gated) or mode is ExecutionMode.AUTONOMOUS,
            gated_actions=gated,
            reason="Permitted under the review-required policy.",
        )
