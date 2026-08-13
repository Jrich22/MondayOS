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
import type { Candidate, SkippedCandidate, SourcingSession } from "./types";
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
  /** Brief version being sourced against, so later counts stay interpretable. */
  briefVersion?: number;
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
    briefVersion: input.briefVersion,
    capturedCandidateIds: [],
    skipped: [],
    pauseCount: 0,
  };
}

export interface CaptureOptions {
  /**
   * True when this candidate already existed in the talent pool and is being
   * attached to another requisition, rather than created by this capture.
   *
   * Must be stated explicitly. See the origin check below for why.
   */
  reusedFromPool?: boolean;
}

/**
 * Record that the operator manually captured a candidate during the session.
 *
 * Takes an already-constructed Candidate: this function does not fetch, parse,
 * or derive profile data. The operator supplies it.
 *
 * ## The origin check, and why reuse is exempt
 *
 * A NEW person recorded during a session must carry
 * `origin: "supervised-linkedin"`. That guard stops a bulk import being
 * laundered through a session to look human-reviewed — the boundary's whole
 * purpose.
 *
 * `Candidate.origin` records how a person FIRST entered the pool, which is a
 * different fact from whether THIS capture was supervised. Someone who arrived
 * as a `referral` two years ago and is being sourced for a new req today was
 * genuinely reviewed by the operator in this session, but their origin is —
 * correctly — still `referral`. Rewriting it would destroy real provenance to
 * satisfy a check about something else.
 *
 * So reuse is permitted, but only when the caller says so explicitly. It cannot
 * happen by accident, and the resulting session record still shows exactly
 * which people were captured.
 */
export function recordManualCapture(
  session: SourcingSession,
  candidate: Candidate,
  options: CaptureOptions = {},
): SourcingSession {
  if (session.status !== "in-progress") {
    throw new SupervisionRequiredError("the session is not in progress");
  }
  if (!options.reusedFromPool && candidate.origin !== "supervised-linkedin") {
    throw new SupervisionRequiredError(
      `candidate origin must be "supervised-linkedin", got "${candidate.origin}"`,
    );
  }
  return {
    ...session,
    capturedCandidateIds: [...(session.capturedCandidateIds ?? []), candidate.id],
    candidatesAdded: session.candidatesAdded + 1,
  };
}

/**
 * @deprecated Use {@link completeSession}. Thin forwarding wrapper kept for
 * callers written against Increment 1; it holds no logic of its own.
 *
 * `completeSession` is canonical because the session lifecycle reads
 * start → pause → resume → complete, and that is the vocabulary the UI and
 * TASK-0057 use. Both names shipped briefly with identical bodies, which is an
 * ambiguous public surface — this forwards instead.
 */
export function endSession(session: SourcingSession, notes = ""): SourcingSession {
  return completeSession(session, notes);
}

// ---------------------------------------------------------------------------
// Increment 3 — pause / resume / complete, skips, and counts
// ---------------------------------------------------------------------------

/**
 * True while the session is live — in progress or paused.
 *
 * A paused session is still the operator's session: it can be resumed, and
 * nothing about it has concluded. What a pause suspends is *capture*, not
 * existence.
 */
export function isActive(session: SourcingSession): boolean {
  return session.status === "in-progress" || session.status === "paused";
}

/**
 * Pause a session.
 *
 * Sourcing is interrupted constantly — a meeting, a call, the end of a day.
 * Without pause the operator either leaves a session open (making its duration
 * meaningless) or ends it and starts another (fragmenting one search into
 * several). Both corrupt the counts this record exists to keep.
 */
export function pauseSession(session: SourcingSession): SourcingSession {
  if (session.status !== "in-progress") {
    throw new SupervisionRequiredError("only an in-progress session can be paused");
  }
  return {
    ...session,
    status: "paused",
    pausedAt: nowIso(),
    pauseCount: (session.pauseCount ?? 0) + 1,
  };
}

/**
 * Resume a paused session.
 *
 * Requires the policy acknowledgement to still hold. Resuming is picking the
 * same supervised session back up, not starting an unsupervised one.
 */
export function resumeSession(session: SourcingSession): SourcingSession {
  if (session.status !== "paused") {
    throw new SupervisionRequiredError("only a paused session can be resumed");
  }
  if (!session.acknowledgedPolicy) {
    throw new SupervisionRequiredError("the supervision policy acknowledgement is missing");
  }
  return { ...session, status: "in-progress", resumedAt: nowIso() };
}

/** Complete a session. Terminal — a completed session cannot be reopened. */
export function completeSession(session: SourcingSession, notes = ""): SourcingSession {
  if (!isActive(session)) {
    throw new SupervisionRequiredError("the session is not in progress");
  }
  return { ...session, status: "ended", endedAt: nowIso(), notes: notes.trim() };
}

/**
 * Record that the operator reviewed someone and did not add them.
 *
 * Deliberately does NOT create a Candidate. A skip is a judgement made inside
 * one search, not a durable fact about a person; minting a persistent record
 * for everyone glanced at would fill the talent pool with people nobody
 * evaluated.
 */
export function recordSkip(
  session: SourcingSession,
  input: { name: string; reason: string; closeCall?: boolean },
): SourcingSession {
  if (session.status !== "in-progress") {
    throw new SupervisionRequiredError("the session is not in progress");
  }
  const name = input.name.trim();
  if (!name) {
    throw new SupervisionRequiredError("a skipped candidate needs a name to be meaningful");
  }
  const entry: SkippedCandidate = {
    id: newId("skip"),
    name,
    reason: input.reason.trim(),
    closeCall: Boolean(input.closeCall),
    at: nowIso(),
  };
  return { ...session, skipped: [...(session.skipped ?? []), entry] };
}

export interface SessionCounts {
  captured: number;
  skipped: number;
  closeCalls: number;
  reviewed: number;
  /** Share of reviewed people who were captured, 0-100. Null when none seen. */
  captureRate: number | null;
  pauseCount: number;
}

/** Session totals, for the live counter and the history list. */
export function sessionCounts(session: SourcingSession): SessionCounts {
  const captured = session.capturedCandidateIds?.length ?? session.candidatesAdded;
  const skips = session.skipped ?? [];
  const reviewed = captured + skips.length;
  return {
    captured,
    skipped: skips.length,
    closeCalls: skips.filter((s) => s.closeCall).length,
    reviewed,
    captureRate: reviewed === 0 ? null : Math.round((captured / reviewed) * 100),
    pauseCount: session.pauseCount ?? 0,
  };
}

/** Totals across several sessions — the per-req rollup. */
export function aggregateCounts(sessions: SourcingSession[]): SessionCounts {
  const all = sessions.map(sessionCounts);
  const captured = all.reduce((s, c) => s + c.captured, 0);
  const skipped = all.reduce((s, c) => s + c.skipped, 0);
  const reviewed = captured + skipped;
  return {
    captured,
    skipped,
    closeCalls: all.reduce((s, c) => s + c.closeCalls, 0),
    reviewed,
    captureRate: reviewed === 0 ? null : Math.round((captured / reviewed) * 100),
    pauseCount: all.reduce((s, c) => s + c.pauseCount, 0),
  };
}

/** The live session for a req, if one is open. At most one may be active. */
export function activeSessionFor(
  reqId: string,
  sessions: SourcingSession[],
): SourcingSession | null {
  return sessions.find((s) => s.reqId === reqId && isActive(s)) ?? null;
}

/** Close calls across a req's sessions — who to revisit when the well runs dry. */
export function closeCallsFor(reqId: string, sessions: SourcingSession[]): SkippedCandidate[] {
  return sessions
    .filter((s) => s.reqId === reqId)
    .flatMap((s) => s.skipped ?? [])
    .filter((s) => s.closeCall)
    .sort((a, b) => b.at.localeCompare(a.at));
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
