/**
 * Pure approval-flow helpers. Execution of an approval is a MondayOS action
 * (Phase 2, via the adapter), but the *client-side guards* — is this approvable,
 * is this a duplicate — are pure and belong here so they can be unit-tested and
 * reused by the approval UI without duplicating MondayOS's authority.
 */

import type { Approval, ApprovalStatus } from "@/adapter/types";

export type ApprovalDecision = "approve" | "reject";

/** Only open approvals can be acted on. */
export function canDecide(approval: Pick<Approval, "status">): boolean {
  return approval.status === "open";
}

export interface ApplyResult {
  approval: Approval;
  /** True when the decision was a no-op because it was already decided. */
  duplicate: boolean;
}

/**
 * Apply a decision to an approval, guarding against duplicates. If the approval
 * is already decided, returns it unchanged with `duplicate: true` — the UI shows
 * a graceful "already decided" state instead of double-submitting to MondayOS.
 */
export function applyDecision(approval: Approval, decision: ApprovalDecision): ApplyResult {
  if (!canDecide(approval)) {
    return { approval, duplicate: true };
  }
  const status: ApprovalStatus = decision === "approve" ? "approved" : "rejected";
  return { approval: { ...approval, status }, duplicate: false };
}

export function decisionLabel(status: ApprovalStatus): string {
  switch (status) {
    case "approved":
      return "Approved";
    case "rejected":
      return "Rejected";
    default:
      return "Awaiting review";
  }
}
