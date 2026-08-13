/**
 * Manual candidate capture — the operator records someone they reviewed.
 *
 * This module orchestrates existing pieces; it introduces no new entity. A
 * capture touches four records that already exist:
 *
 *   Candidate     created, or REUSED if the person is already in the pool
 *   ReqCandidate  created for the active req — the evaluation
 *   SourcingSession  the capture is attributed to it
 *   SourcingBrief    read only, to compute fit
 *
 * The load-bearing decision is the second word above: **reused**. The whole
 * point of a persistent talent pool is that sourcing the same person for a
 * second req adds an evaluation, not a duplicate human. Capture is therefore
 * duplicate-aware by construction rather than by a cleanup job later.
 *
 * Nothing here fetches, parses, or derives profile data. The operator supplies
 * every field, having reviewed the profile themselves. See
 * docs/LINKEDIN_POLICY.md.
 */
import type {
  Candidate,
  CandidateRole,
  ReqCandidate,
  SourcingBrief,
  SourcingSession,
} from "./types";
import { findPossibleDuplicates, newCandidate } from "./candidate";
import { isAlreadyOnReq, newReqCandidate, withFitScore, assess } from "./req-candidate";
import { recordManualCapture, SupervisionRequiredError } from "./sourcing-session";

/** What the operator types in, having reviewed the profile themselves. */
export interface CaptureInput {
  fullName: string;
  headline?: string;
  location?: string;
  email?: string;
  /** Recorded only because a human opened and reviewed the profile. */
  linkedInUrl?: string;
  currentTitle?: string;
  currentCompany?: string;
  skills?: string[];
  /** Why this person, for THIS req. Stored on the ReqCandidate. */
  rationale?: string;
  /** Notes about the PERSON, durable across reqs. Stored on the Candidate. */
  personNotes?: string;
  /** Per-requirement judgements, if the operator made them during capture. */
  assessments?: Array<{ requirementId: string; met: "yes" | "no" | "unknown"; note?: string }>;
}

export type DuplicateResolution =
  /** Create a new person. */
  | { kind: "new" }
  /** Attach to an existing person already in the pool. */
  | { kind: "existing"; candidateId: string };

export interface CaptureResult {
  candidate: Candidate;
  reqCandidate: ReqCandidate;
  session: SourcingSession;
  /** True when an existing person was reused rather than created. */
  reusedExistingCandidate: boolean;
}

export class CaptureError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "CaptureError";
  }
}

/**
 * Build the Candidate a capture *would* create, without persisting it.
 *
 * Used to run duplicate detection before the operator commits, so the warning
 * appears while they can still act on it rather than after the fact.
 */
export function draftCandidateFrom(input: CaptureInput): Candidate {
  const roles: CandidateRole[] = [];
  if (input.currentTitle?.trim() || input.currentCompany?.trim()) {
    roles.push({
      title: input.currentTitle?.trim() ?? "",
      company: input.currentCompany?.trim() ?? "",
      // Coarse by design — no scraped precision implied.
      startedAt: new Date().toISOString().slice(0, 7),
    });
  }
  return newCandidate({
    fullName: input.fullName,
    headline: input.headline,
    location: input.location,
    email: input.email,
    linkedInUrl: input.linkedInUrl,
    roles,
    skills: input.skills,
    origin: "supervised-session",
    notes: input.personNotes,
  });
}

/**
 * People already in the pool who may be this person.
 *
 * Advisory only — the operator decides. Auto-merging on a name match would
 * silently fuse two different humans, which is far more damaging than carrying
 * a duplicate until someone notices.
 */
export function findDuplicatesFor(input: CaptureInput, pool: Candidate[]): Candidate[] {
  return findPossibleDuplicates(draftCandidateFrom(input), pool);
}

export interface CaptureContext {
  session: SourcingSession;
  reqId: string;
  brief: SourcingBrief | undefined;
  /** The whole persistent pool, for duplicate detection and reuse. */
  candidates: Candidate[];
  /** Existing evaluations, to refuse adding the same person to a req twice. */
  reqCandidates: ReqCandidate[];
  operator: string;
}

/**
 * Capture one candidate into the active session and req.
 *
 * Returns the records to persist; writes nothing itself, so the caller commits
 * all four together and a half-applied capture cannot exist.
 */
export function captureCandidate(
  ctx: CaptureContext,
  input: CaptureInput,
  resolution: DuplicateResolution = { kind: "new" },
): CaptureResult {
  if (!input.fullName.trim()) {
    throw new CaptureError("A candidate needs a name.");
  }
  if (ctx.session.status !== "in-progress") {
    throw new SupervisionRequiredError("the session is not in progress");
  }

  let candidate: Candidate;
  let reused = false;

  if (resolution.kind === "existing") {
    const found = ctx.candidates.find((c) => c.id === resolution.candidateId);
    if (!found) throw new CaptureError("That person is no longer in the talent pool.");
    // The existing record is left untouched: facts captured during an earlier
    // search are not overwritten by a later, hastier one. Req-scoped judgement
    // from this session goes on the ReqCandidate instead.
    candidate = found;
    reused = true;
  } else {
    candidate = draftCandidateFrom(input);
  }

  if (isAlreadyOnReq(candidate.id, ctx.reqId, ctx.reqCandidates)) {
    throw new CaptureError(
      `${candidate.fullName} is already on this requisition. Open their evaluation instead.`,
    );
  }

  let reqCandidate = newReqCandidate({
    reqId: ctx.reqId,
    candidateId: candidate.id,
    briefVersion: ctx.brief?.version ?? 0,
    by: ctx.operator,
    rationale: input.rationale,
  });

  for (const a of input.assessments ?? []) {
    reqCandidate = assess(reqCandidate, {
      requirementId: a.requirementId,
      met: a.met,
      note: a.note ?? "",
    });
  }
  if (ctx.brief && (input.assessments?.length ?? 0) > 0) {
    reqCandidate = withFitScore(reqCandidate, ctx.brief);
  }

  // Routed through the supervision boundary, which re-checks session state and
  // refuses any NEW candidate not originating from supervised capture. Reuse is
  // declared explicitly so it cannot happen by accident — an existing person's
  // origin records how they first entered the pool, not whether this capture
  // was supervised.
  const session = recordManualCapture(ctx.session, candidate, { reusedFromPool: reused });

  return { candidate, reqCandidate, session, reusedExistingCandidate: reused };
}
