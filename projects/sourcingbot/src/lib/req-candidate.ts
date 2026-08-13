/**
 * ReqCandidate — one Candidate's evaluation and status for ONE requisition.
 *
 * This module owns everything req-scoped: pipeline stage, per-requirement
 * assessment, fit score, and stage history. It references the person by
 * `candidateId` and never copies their identity.
 *
 * The rule is enforced, not merely documented: `assertNoIdentityDuplication`
 * throws if a Candidate-owned field appears on a ReqCandidate. A convention
 * that is only written down erodes; this one fails a test the moment someone
 * denormalizes `fullName` onto the pipeline row "just for rendering".
 */
import type {
  Candidate,
  PipelineStage,
  ReqCandidate,
  RequirementAssessment,
  SourcingBrief,
  StageEvent,
} from "./types";
import { newId, nowIso } from "./ids";
import { requiredRequirements } from "./brief";

/** Fields that belong to the Candidate record and must never be mirrored here. */
const CANDIDATE_OWNED_FIELDS = [
  "fullName",
  "headline",
  "email",
  "linkedInUrl",
  "location",
  "roles",
  "skills",
  "origin",
] as const;

const VALID_TRANSITIONS: Record<PipelineStage, PipelineStage[]> = {
  identified: ["reviewing", "rejected"],
  reviewing: ["contacted", "rejected"],
  contacted: ["responded", "rejected"],
  responded: ["advanced", "rejected"],
  advanced: ["rejected"],
  rejected: ["reviewing"], // a rejection can be revisited for the same req
};

export class ReqCandidateStageError extends Error {
  constructor(from: PipelineStage, to: PipelineStage) {
    super(`Cannot move a candidate from "${from}" to "${to}" in this pipeline.`);
    this.name = "ReqCandidateStageError";
  }
}

export class IdentityDuplicationError extends Error {
  constructor(fields: string[]) {
    super(
      `ReqCandidate must not duplicate Candidate-owned field(s): ${fields.join(", ")}. ` +
        `Identity lives on the Candidate record; ReqCandidate holds only candidateId.`,
    );
    this.name = "IdentityDuplicationError";
  }
}

/**
 * Structural guard for the central product rule.
 *
 * Called on every construction and update so identity fields cannot leak onto
 * a req-scoped row, whatever route the data arrived by.
 */
export function assertNoIdentityDuplication(value: object): void {
  const leaked = CANDIDATE_OWNED_FIELDS.filter((f) => f in value);
  if (leaked.length > 0) throw new IdentityDuplicationError(leaked);
}

export interface NewReqCandidateInput {
  reqId: string;
  candidateId: string;
  briefVersion: number;
  by: string;
  rationale?: string;
}

export function newReqCandidate(input: NewReqCandidateInput): ReqCandidate {
  const at = nowIso();
  return {
    id: newId("rc"),
    reqId: input.reqId,
    candidateId: input.candidateId,
    stage: "identified",
    briefVersion: input.briefVersion,
    assessments: [],
    rationale: input.rationale?.trim() ?? "",
    fitScore: null,
    history: [{ from: null, to: "identified", at, by: input.by, reason: "Added to requisition" }],
    addedAt: at,
    updatedAt: at,
  };
}

export function canAdvance(from: PipelineStage, to: PipelineStage): boolean {
  return VALID_TRANSITIONS[from].includes(to);
}

export function advance(
  rc: ReqCandidate,
  to: PipelineStage,
  by: string,
  reason = "",
): ReqCandidate {
  if (!canAdvance(rc.stage, to)) throw new ReqCandidateStageError(rc.stage, to);
  const at = nowIso();
  const event: StageEvent = { from: rc.stage, to, at, by, reason };
  return { ...rc, stage: to, history: [...rc.history, event], updatedAt: at };
}

export function assess(
  rc: ReqCandidate,
  assessment: RequirementAssessment,
): ReqCandidate {
  const others = rc.assessments.filter((a) => a.requirementId !== assessment.requirementId);
  return { ...rc, assessments: [...others, assessment], updatedAt: nowIso() };
}

/**
 * Fit score against a brief: the weighted share of satisfied requirements.
 *
 * A single unmet REQUIRED requirement caps the score at 0 — a required item is
 * disqualifying by definition, so letting preferred matches average it away
 * would produce a confidently wrong number. `unknown` counts as unmet for
 * required items (absence of evidence is not evidence) but is simply excluded
 * from the preferred pool rather than penalized.
 */
export function computeFitScore(rc: ReqCandidate, brief: SourcingBrief): number {
  const byId = new Map(rc.assessments.map((a) => [a.requirementId, a]));

  for (const req of requiredRequirements(brief)) {
    if (byId.get(req.id)?.met !== "yes") return 0;
  }

  const preferred = brief.requirements.filter((r) => r.kind === "preferred");
  const judged = preferred.filter((r) => byId.get(r.id)?.met !== undefined);
  if (judged.length === 0) return 100; // all required met, nothing else to weigh

  const totalWeight = judged.reduce((sum, r) => sum + r.weight, 0);
  if (totalWeight === 0) return 100;
  const earned = judged.reduce(
    (sum, r) => sum + (byId.get(r.id)?.met === "yes" ? r.weight : 0),
    0,
  );
  return Math.round((earned / totalWeight) * 100);
}

export function withFitScore(rc: ReqCandidate, brief: SourcingBrief): ReqCandidate {
  return { ...rc, fitScore: computeFitScore(rc, brief), updatedAt: nowIso() };
}

/** True once this evaluation was made against an older revision of the brief. */
export function needsReassessment(rc: ReqCandidate, brief: SourcingBrief): boolean {
  return rc.briefVersion < brief.version;
}

// ---------------------------------------------------------------------------
// Cross-req views — only possible because Candidate is persistent
// ---------------------------------------------------------------------------

/** Every requisition this person has ever been evaluated for. */
export function reqHistoryFor(candidateId: string, all: ReqCandidate[]): ReqCandidate[] {
  return all
    .filter((rc) => rc.candidateId === candidateId)
    .sort((a, b) => b.addedAt.localeCompare(a.addedAt));
}

/** Guard against adding the same person to the same req twice. */
export function isAlreadyOnReq(
  candidateId: string,
  reqId: string,
  all: ReqCandidate[],
): boolean {
  return all.some((rc) => rc.candidateId === candidateId && rc.reqId === reqId);
}

export function pipelineFor(reqId: string, all: ReqCandidate[]): ReqCandidate[] {
  const order: PipelineStage[] = [
    "advanced",
    "responded",
    "contacted",
    "reviewing",
    "identified",
    "rejected",
  ];
  return all
    .filter((rc) => rc.reqId === reqId)
    .sort(
      (a, b) =>
        order.indexOf(a.stage) - order.indexOf(b.stage) ||
        (b.fitScore ?? -1) - (a.fitScore ?? -1),
    );
}

export function stageCounts(reqId: string, all: ReqCandidate[]): Record<PipelineStage, number> {
  const counts: Record<PipelineStage, number> = {
    identified: 0,
    reviewing: 0,
    contacted: 0,
    responded: 0,
    advanced: 0,
    rejected: 0,
  };
  for (const rc of all) {
    if (rc.reqId === reqId) counts[rc.stage] += 1;
  }
  return counts;
}

/** Join for rendering only — never persisted, so the two records stay separate. */
export interface PipelineRow {
  reqCandidate: ReqCandidate;
  candidate: Candidate;
}

export function joinPipeline(rows: ReqCandidate[], candidates: Candidate[]): PipelineRow[] {
  const byId = new Map(candidates.map((c) => [c.id, c]));
  const out: PipelineRow[] = [];
  for (const rc of rows) {
    const candidate = byId.get(rc.candidateId);
    if (candidate) out.push({ reqCandidate: rc, candidate });
  }
  return out;
}
