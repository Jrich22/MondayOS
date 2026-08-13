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

  // ── Increment 2: authoring ────────────────────────────────────────────
  // All optional so requisitions written by Increment 1 keep loading. See
  // docs/DECISIONS.md ADR-007.

  /** Full job description, as the hiring team would publish it. */
  jobDescription?: string;
  /** Notes from the hiring-manager intake conversation. */
  intakeNotes?: string;
  /** What this search is trying to achieve — the recruiter's own targets. */
  sourcingGoals?: SourcingGoals;
  /**
   * Last time this req was persisted by the authoring surface. Distinct from
   * `updatedAt`, which any mutation touches: this is what the draft indicator
   * displays, so "saved 12s ago" means what the recruiter thinks it means.
   */
  lastSavedAt?: string;
  /**
   * Monotonic edit counter, incremented by every `updateReq`.
   *
   * The unsaved-changes indicator compares this against `savedRev` rather than
   * comparing timestamps. Timestamps have millisecond resolution, so an edit
   * made in the same millisecond as a save compared equal and the surface
   * reported "all changes saved" over a pending edit. Whether an edit is
   * persisted is not a question clock resolution should be able to answer.
   */
  rev?: number;
  /** The `rev` that was last written to the store. */
  savedRev?: number;
}

/** Recruiter-set targets for a search. Advisory — nothing enforces them. */
export interface SourcingGoals {
  /** Candidates the recruiter intends to source into the pipeline. */
  targetCandidates?: number;
  /** Candidates they intend to contact. */
  targetContacts?: number;
  /** Free-text framing: "two strong staff-level profiles by end of month". */
  notes?: string;
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

  // ── Increment 2: targeting ────────────────────────────────────────────
  // Optional for backward compatibility with Increment 1 briefs.

  /** Industries to source from. */
  targetIndustries?: string[];
  /** Industries to avoid — conflicts, non-transferable domains. */
  excludedIndustries?: string[];
  /**
   * Narrative experience guidance. `seniority` is the coarse band used for
   * filtering; this is the nuance a band cannot carry ("depth over breadth —
   * eight years on one hard problem beats fifteen across five teams").
   */
  experienceGuidance?: string;
}

// ---------------------------------------------------------------------------
// Candidate — the persistent person
// ---------------------------------------------------------------------------

/**
 * How this person first entered the system. Audit-relevant.
 *
 * `supervised-session` records that a human reviewed this person during a
 * supervised sourcing session. WHICH channel that session used is recorded on
 * the session's `providerId`, not here — a person sourced through LinkedIn in
 * 2026 and through a licensed integration in 2027 is the same fact about the
 * same human, and stamping a channel onto them permanently would make the
 * record wrong the moment the channel changed.
 */
export type CandidateOrigin =
  | "manual-entry"
  | "referral"
  | "inbound"
  | "supervised-session"
  /**
   * @deprecated Renamed to `supervised-session`. Retained in the union so
   * workspaces written before the provider boundary still typecheck on load;
   * `migrateWorkspace` in store.ts maps it forward. Never write this value.
   */
  | "supervised-linkedin";

/** The origin recorded for a person first captured in a supervised session. */
export const SUPERVISED_ORIGIN = "supervised-session" as const;

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

/**
 * Who put an option forward, as distinct from who decided it.
 *
 * `operator` means a human both proposed and decided. `agent` means something
 * suggested it and a human still decided — the two are never collapsed, because
 * "Claude found them, I chose them" and "I found them" are different facts and
 * an audit needs to tell them apart years later.
 */
export type ProposedBy = "operator" | "agent";

export interface StageEvent {
  from: PipelineStage | null;
  to: PipelineStage;
  at: string;
  /**
   * The human accountable for this decision. Always a named person — this IS
   * the decider, which is why no separate `decidedBy` field exists here.
   */
  by: string;
  reason: string;
  /** Who proposed it. Absent on historical events, which were all `operator`. */
  proposedBy?: ProposedBy;
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

/**
 * `paused` and `halted` are deliberately separate states.
 *
 * A pause is the operator's own choice — a meeting, the end of a day. A halt is
 * the platform telling you something: a warning, a restriction, a checkpoint, an
 * unexpected page. Collapsing them would make the most important event in a
 * session indistinguishable from stepping out for coffee, and would erase the
 * one signal that should stop the work immediately. See ADR-017.
 */
export type SessionStatus = "not-started" | "in-progress" | "paused" | "halted" | "ended";

/** What caused a session to stop for safety reasons. */
export type HaltReason =
  | "platform-warning"
  | "rate-limit-notice"
  | "checkpoint"
  | "unexpected-page"
  | "operator-stop";

/**
 * A record that a session was stopped by something other than ordinary work.
 *
 * Kept as a list rather than a single field: a session can be halted, resumed
 * after the operator investigates, and halted again, and the sequence is the
 * interesting part. One overwritten field would hide a pattern of repeated
 * warnings — exactly the pattern that should end a session for good.
 */
export interface SessionHalt {
  id: string;
  at: string;
  /** Whoever saw it first. An agent noticing a warning is a valid source. */
  raisedBy: ProposedBy;
  reason: HaltReason;
  detail: string;
}

/**
 * Why the operator looked at someone and did not add them.
 *
 * Kept per session rather than as a Candidate record on purpose: a skip is a
 * judgement made during one search, not a durable fact about a person. Creating
 * a Candidate for everyone glanced at would fill the persistent pool with
 * people nobody evaluated, and `closeCall` is exactly the signal a recruiter
 * wants when the well runs dry — "who did I nearly take?"
 */
export interface SkippedCandidate {
  id: string;
  /** Name as the operator typed it. No profile is fetched or stored. */
  name: string;
  reason: string;
  /** Worth revisiting if the pipeline thins out. */
  closeCall: boolean;
  at: string;

  // ── Provider boundary ─────────────────────────────────────────────────
  // Optional so Increment 1-3 skips keep loading; both default to the
  // operator, which is what every historical skip was.

  /** The named human who made this call. */
  decidedBy?: string;
  /** Who put the person forward for the decision. */
  proposedBy?: ProposedBy;
}

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

  // ── Increment 3 ───────────────────────────────────────────────────────
  // Optional so Increment 1/2 sessions keep loading. See ADR-010.

  /** Brief version this session sourced against, so counts stay interpretable. */
  briefVersion?: number;
  /** Candidates captured in this session, in capture order. */
  capturedCandidateIds?: string[];
  /** People reviewed and not added, with the reason. */
  skipped?: SkippedCandidate[];
  pausedAt?: string;
  resumedAt?: string;
  /** How many times this session was paused — a proxy for interruption. */
  pauseCount?: number;

  // ── Increment 4: provider boundary, halts, presence ───────────────────
  // All optional so every earlier session keeps loading. See ADR-016/ADR-017.

  /**
   * The channel this session was conducted through — the durable provider
   * attribution. Absent on sessions written before providers existed; those
   * default to `manual`, which is what they necessarily were.
   */
  providerId?: string;
  /** Safety stops, in order. See `SessionHalt`. */
  halts?: SessionHalt[];
  /**
   * When the operator last confirmed they are present and watching.
   *
   * Recorded in this increment; NOT enforced by it. Enforcement — refusing
   * agent writes outside the window and auto-pausing — arrives with the MCP
   * surface in TASK-0061, because there is nothing to enforce against until
   * something other than the operator can write. See ADR-017.
   */
  lastOperatorConfirmationAt?: string;
}
