/**
 * ManualProvider — the recruiter sources with their own hands.
 *
 * This is the default provider and the only one that exists in this increment.
 * It describes what sourcingBOT has always done: the operator opens and reviews
 * every profile themselves, and the product records what they decided.
 *
 * ## Why the policy text lives here, verbatim
 *
 * `supervisionPolicy` below is the exact wording that shipped as the global
 * `SUPERVISION_POLICY` constant. It is reproduced character for character, and a
 * test asserts that, because this text is what operators have been signing. If
 * moving the constant into a provider had also reworded it, every existing
 * session's attestation would silently refer to text that no longer exists.
 *
 * The fourth line names LinkedIn. That is correct and intended: a provider
 * describes a real channel, and this is the channel recruiters use. The rule the
 * boundary enforces is that the DOMAIN does not name it — providers may, because
 * naming the channel is their whole job.
 *
 * This module contains no browser control, no URLs, no selectors, and no
 * credentials, and it never will. See docs/DECISIONS.md ADR-015.
 */
import type { SourcingProvider } from "../provider";

export const ManualProvider: SourcingProvider = {
  id: "manual",
  label: "Manual sourcing",
  summary: "You browse and review each profile yourself; sourcingBOT records your decisions.",
  agentOperatesBrowser: false,
  supervisionPolicy: [
    "I am initiating and personally supervising this sourcing session.",
    "I will open and review each profile myself; sourcingBOT will not browse for me.",
    "I will record only candidates I have personally reviewed.",
    "I will respect LinkedIn's rate limits and terms; no bypass or evasion.",
  ] as const,
};
