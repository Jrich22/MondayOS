/**
 * Supervised LinkedIn sourcing — BOUNDARY ONLY.
 *
 * This module contains no browser automation, issues no network requests, and
 * holds no session credentials. It exists to make the supervision boundary an
 * enforced product rule rather than a promise in a document: a sourcing session
 * is a *record that a human did something*, and it cannot be created without a
 * named operator who has acknowledged the policy.
 *
 * Explicitly NOT built, now or later, per TASK-0052/TASK-0053 and
 * docs/LINKEDIN_POLICY.md:
 *   • unattended or scheduled scraping
 *   • rate-limit bypass, throttle evasion, or request pacing tricks
 *   • automation-evasion (fingerprint spoofing, headless masking, CAPTCHA solving)
 *   • bulk export of profiles the operator did not individually open
 *
 * The full human-driven LinkedIn workflow is deferred to a later increment; see
 * docs/ROADMAP.md. What ships here is the gate that workflow must pass through.
 */
import type { Candidate, SourcingSession } from "./types";
import { newId, nowIso } from "./ids";

/** The acknowledgement an operator must accept before each session. */
export const SUPERVISION_POLICY = [
  "I am initiating and personally supervising this sourcing session.",
  "I will open and review each profile myself; sourcingBOT will not browse for me.",
  "I will record only candidates I have personally reviewed.",
  "I will respect LinkedIn's rate limits and terms; no bypass or evasion.",
] as const;

export class SupervisionRequiredError extends Error {
  constructor(reason: string) {
    super(`Supervised sourcing session refused: ${reason}`);
    this.name = "SupervisionRequiredError";
  }
}

export interface StartSessionInput {
  reqId: string;
  operator: string;
  acknowledgedPolicy: boolean;
  /** Set true only when the requisition is genuinely open for sourcing. */
  reqAcceptsSourcing: boolean;
}

/**
 * Start a supervised session.
 *
 * Refuses rather than degrades. Every failure mode here is a case where
 * continuing would produce a record implying human oversight that did not
 * happen — the exact misrepresentation this boundary exists to prevent.
 */
export function startSession(input: StartSessionInput): SourcingSession {
  if (!input.operator.trim()) {
    throw new SupervisionRequiredError("a named human operator is required");
  }
  if (!input.acknowledgedPolicy) {
    throw new SupervisionRequiredError("the operator must acknowledge the supervision policy");
  }
  if (!input.reqAcceptsSourcing) {
    throw new SupervisionRequiredError("the requisition is not open for sourcing");
  }

  return {
    id: newId("sess"),
    reqId: input.reqId,
    operator: input.operator.trim(),
    status: "in-progress",
    acknowledgedPolicy: true,
    startedAt: nowIso(),
    candidatesAdded: 0,
    notes: "",
  };
}

/**
 * Record that the operator manually captured a candidate during the session.
 *
 * Takes an already-constructed Candidate: this function does not fetch, parse,
 * or derive profile data. The operator supplies it.
 */
export function recordManualCapture(
  session: SourcingSession,
  candidate: Candidate,
): SourcingSession {
  if (session.status !== "in-progress") {
    throw new SupervisionRequiredError("the session is not in progress");
  }
  if (candidate.origin !== "supervised-linkedin") {
    throw new SupervisionRequiredError(
      `candidate origin must be "supervised-linkedin", got "${candidate.origin}"`,
    );
  }
  return { ...session, candidatesAdded: session.candidatesAdded + 1 };
}

export function endSession(session: SourcingSession, notes = ""): SourcingSession {
  if (session.status !== "in-progress") {
    throw new SupervisionRequiredError("the session is not in progress");
  }
  return { ...session, status: "ended", endedAt: nowIso(), notes: notes.trim() };
}

export function sessionsForReq(reqId: string, all: SourcingSession[]): SourcingSession[] {
  return all.filter((s) => s.reqId === reqId).sort((a, b) => b.startedAt.localeCompare(a.startedAt));
}

/**
 * Capabilities this product does not implement. Rendered in the UI and asserted
 * in tests, so the boundary is visible to operators and regressions are caught.
 */
export const PROHIBITED_CAPABILITIES = [
  "unattended-scraping",
  "scheduled-crawling",
  "rate-limit-bypass",
  "automation-evasion",
  "bulk-profile-export",
  "credential-storage",
] as const;

export type ProhibitedCapability = (typeof PROHIBITED_CAPABILITIES)[number];

/** Always false. A single, greppable place asserting the boundary holds. */
export function supportsCapability(_capability: ProhibitedCapability): boolean {
  return false;
}
