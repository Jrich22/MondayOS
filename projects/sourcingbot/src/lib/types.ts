/**
 * sourcingBOT shared domain types.
 *
 * THE CENTRAL PRODUCT RULE (see docs/DATA_MODEL.md, DEC-SB-001):
 *
 *   Candidate     — a persistent PERSON. Exists once, independent of any
 *                   requisition, and survives every req they are considered
 *                   for. Owns identity, contact, and career facts.
 *
 *   ReqCandidate  — that Candidate's EVALUATION AND STATUS for ONE requisition.
 *                   Owns pipeline stage, assessment, and req-scoped history.
 *                   Holds `candidateId` + `reqId` and NOTHING that duplicates
 *                   the Candidate record.
 *
 * The separation is what lets the same person be sourced for many reqs over
 * years while each req keeps its own independent evaluation. Collapsing them
 * (a "candidate" row per req) is the failure mode this model exists to prevent:
 * it fragments the person, loses cross-req history, and makes talent
 * concentration analytics impossible.
 *
 * Enforcement is mechanical, not conventional — `assertNoIdentityDuplication`
 * in req-candidate.ts fails loudly if identity fields leak onto a ReqCandidate.
 */

// ---------------------------------------------------------------------------
// Requisition
// ---------------------------------------------------------------------------

export type ReqStatus = "draft" | "open" | "on-hold" | "closed";

export interface Req {
  id: string;
  /** Human-facing requisition code, e.g. "REQ-014". */
  code: string;
  title: string;
  team: string;
  location: string;
  /** Employment arrangement, kept free-form for the foundation increment. */
  workModel: "onsite" | "hybrid" | "remote";
  status: ReqStatus;
  /** Optional owning hiring manager name (display only in this increment). */
  hiringManager: string;
  openings: number;
  createdAt: string;
  updatedAt: string;
  closedAt?: string;
}

// ---------------------------------------------------------------------------
// Sourcing Brief — the structured, reusable search definition for a Req
// ---------------------------------------------------------------------------

export type SeniorityBand = "junior" | "mid" | "senior" | "staff" | "principal" | "executive";

/** A single must-have or nice-to-have requirement. */
export interface BriefRequirement {
  id: string;
  label: string;
  /** required = disqualifying if absent; preferred = weighted, not blocking. */
  kind: "required" | "preferred";
  /** Relative weight used by evaluation guidance. 1–5. */
  weight: number;
}

export interface SourcingBrief {
  id: string;
  reqId: string;
  /** Bumped whenever the brief content changes, so evaluations cite a version. */
  version: number;
  headline: string;
  seniority: SeniorityBand;
  requirements: BriefRequirement[];
  /** Target companies/industries to source from. */
  targetCompanies: string[];
  /** Explicit exclusions (e.g. current portfolio conflicts). */
  excludedCompanies: string[];
  keywords: string[];
  locations: string[];
  /** Free-text framing the recruiter uses when reaching out. */
  outreachAngle: string;
  createdAt: string;
  updatedAt: string;
}

// ---------------------------------------------------------------------------
// Candidate — the persistent person
// ---------------------------------------------------------------------------

/** How this person first entered the system. Audit-relevant. */
export type CandidateOrigin = "manual-entry" | "referral" | "inbound" | "supervised-linkedin";

export interface CandidateRole {
  title: string;
  company: string;
  /** ISO date (YYYY-MM) — coarse by design; no scraped precision implied. */
  startedAt: string;
  endedAt?: string;
}

export interface Candidate {
  id: string;
  fullName: string;
  /** Current headline/title, denormalized for list rendering. */
  headline: string;
  location: string;
  email?: string;
  /** Profile URL recorded ONLY when a human supervised its capture. */
  linkedInUrl?: string;
  roles: CandidateRole[];
  skills: string[];
  origin: CandidateOrigin;
  /** Free-form recruiter notes about the PERSON, not about any one req. */
  notes: string;
  createdAt: string;
  updatedAt: string;
}

// ---------------------------------------------------------------------------
// ReqCandidate — one Candidate's evaluation for one Req
// ---------------------------------------------------------------------------

export type PipelineStage =
  | "identified"
  | "reviewing"
  | "contacted"
  | "responded"
  | "advanced"
  | "rejected";

export interface StageEvent {
  from: PipelineStage | null;
  to: PipelineStage;
  at: string;
  by: string;
  reason: string;
}

/** Per-requirement assessment, referencing the brief version it was made against. */
export interface RequirementAssessment {
  requirementId: string;
  met: "yes" | "no" | "unknown";
  note: string;
}

export interface ReqCandidate {
  id: string;
  reqId: string;
  candidateId: string;
  stage: PipelineStage;
  /** Brief version this evaluation was made against. */
  briefVersion: number;
  assessments: RequirementAssessment[];
  /** Recruiter's req-scoped rationale — why this person, for THIS role. */
  rationale: string;
  /** 0–100 fit score for this req only. Never stored on Candidate. */
  fitScore: number | null;
  history: StageEvent[];
  addedAt: string;
  updatedAt: string;
}

// ---------------------------------------------------------------------------
// Supervised LinkedIn sourcing session (boundary record only)
// ---------------------------------------------------------------------------

export type SessionStatus = "not-started" | "in-progress" | "ended";

/**
 * A record that a human conducted a sourcing session. This increment records
 * intent, supervision, and outcome counts ONLY — it drives no browser, issues
 * no requests, and holds no automation state. See docs/LINKEDIN_POLICY.md.
 */
export interface SourcingSession {
  id: string;
  reqId: string;
  /** Who initiated. A session cannot exist without a named human operator. */
  operator: string;
  status: SessionStatus;
  /** Explicit per-session acknowledgement of the supervision policy. */
  acknowledgedPolicy: boolean;
  startedAt: string;
  endedAt?: string;
  /** Candidates the operator manually recorded during the session. */
  candidatesAdded: number;
  notes: string;
}
