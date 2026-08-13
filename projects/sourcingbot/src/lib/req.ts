/**
 * Req Workspace — requisition domain and pure logic.
 *
 * A Req is the unit of work in sourcingBOT: every brief, session, and
 * evaluation hangs off exactly one Req. React-free so every rule is unit
 * testable in one place, matching the Cue App `lib/` convention.
 */
import type { Req, ReqStatus } from "./types";
import { newId, nowIso } from "./ids";

/** Transitions the workspace permits. Closed is terminal for this increment. */
const VALID_TRANSITIONS: Record<ReqStatus, ReqStatus[]> = {
  draft: ["open", "closed"],
  open: ["on-hold", "closed"],
  "on-hold": ["open", "closed"],
  closed: [],
};

export interface NewReqInput {
  code: string;
  title: string;
  team: string;
  location: string;
  workModel?: Req["workModel"];
  hiringManager?: string;
  openings?: number;
}

export function newReq(input: NewReqInput): Req {
  const at = nowIso();
  return {
    id: newId("req"),
    code: input.code.trim(),
    title: input.title.trim(),
    team: input.team.trim(),
    location: input.location.trim(),
    workModel: input.workModel ?? "hybrid",
    status: "draft",
    hiringManager: input.hiringManager?.trim() ?? "",
    openings: Math.max(1, input.openings ?? 1),
    createdAt: at,
    updatedAt: at,
  };
}

export function canTransition(from: ReqStatus, to: ReqStatus): boolean {
  return VALID_TRANSITIONS[from].includes(to);
}

export class ReqTransitionError extends Error {
  constructor(from: ReqStatus, to: ReqStatus) {
    super(`Cannot move a requisition from "${from}" to "${to}".`);
    this.name = "ReqTransitionError";
  }
}

export function transition(req: Req, to: ReqStatus): Req {
  if (!canTransition(req.status, to)) throw new ReqTransitionError(req.status, to);
  const at = nowIso();
  return {
    ...req,
    status: to,
    updatedAt: at,
    ...(to === "closed" ? { closedAt: at } : {}),
  };
}

/** A req only accepts sourcing work while it is actively open. */
export function acceptsSourcing(req: Req): boolean {
  return req.status === "open";
}

export interface ReqValidationIssue {
  field: keyof NewReqInput;
  message: string;
}

export function validateReq(input: NewReqInput): ReqValidationIssue[] {
  const issues: ReqValidationIssue[] = [];
  if (!input.code.trim()) issues.push({ field: "code", message: "A requisition code is required." });
  if (!input.title.trim()) issues.push({ field: "title", message: "A role title is required." });
  if (!input.team.trim()) issues.push({ field: "team", message: "An owning team is required." });
  if (input.openings !== undefined && input.openings < 1) {
    issues.push({ field: "openings", message: "Openings must be at least 1." });
  }
  return issues;
}

/** Workspace ordering: active work first, then most recently touched. */
export function sortForWorkspace(reqs: Req[]): Req[] {
  const rank: Record<ReqStatus, number> = { open: 0, draft: 1, "on-hold": 2, closed: 3 };
  return [...reqs].sort(
    (a, b) => rank[a.status] - rank[b.status] || b.updatedAt.localeCompare(a.updatedAt),
  );
}

// ---------------------------------------------------------------------------
// Increment 2 — authoring
// ---------------------------------------------------------------------------

/** Fields the authoring surface may edit. Status moves via `transition` only. */
export type ReqEdits = Partial<
  Pick<
    Req,
    | "code"
    | "title"
    | "team"
    | "location"
    | "workModel"
    | "hiringManager"
    | "openings"
    | "jobDescription"
    | "intakeNotes"
    | "sourcingGoals"
  >
>;

/**
 * Apply an edit.
 *
 * `status` is deliberately not editable here: lifecycle changes go through
 * `transition`, which validates the graph. Letting an authoring form assign a
 * status directly would route around that and let a closed req silently reopen.
 */
export function updateReq(req: Req, edits: ReqEdits): Req {
  const next: Req = {
    ...req,
    ...edits,
    updatedAt: nowIso(),
    rev: (req.rev ?? 0) + 1,
  };
  if (edits.openings !== undefined) next.openings = Math.max(1, edits.openings);
  for (const key of ["code", "title", "team", "location", "hiringManager"] as const) {
    if (edits[key] !== undefined) next[key] = String(edits[key]).trim();
  }
  return next;
}

/** Mark the req as persisted by the authoring surface. */
export function markSaved(req: Req): Req {
  return { ...req, lastSavedAt: nowIso(), savedRev: req.rev ?? 0 };
}

/**
 * True when the recruiter has made edits that are not written yet.
 *
 * Compares edit revisions, not timestamps. Timestamps resolve to the
 * millisecond, so an edit made in the same millisecond as a save compared equal
 * and the surface reported "all changes saved" over a pending edit — whether
 * work is persisted should not depend on clock resolution. Because `rev` is
 * stored, an unsaved draft is still detectable after a reload.
 */
export function hasUnsavedChanges(req: Req): boolean {
  if (req.savedRev === undefined) return true;
  return (req.rev ?? 0) !== req.savedRev;
}

/** A blank draft, ready for the authoring surface to fill in. */
export function newDraftReq(code = ""): Req {
  return newReq({ code, title: "", team: "", location: "" });
}

/** Reqs the recruiter has started but not opened for sourcing. */
export function draftReqs(reqs: Req[]): Req[] {
  return sortForWorkspace(reqs.filter((r) => r.status === "draft"));
}

/**
 * Suggest the next requisition code, continuing the highest REQ-NNN seen.
 * Falls back to REQ-001 when nothing matches, and never reuses a code.
 */
export function suggestReqCode(reqs: Req[]): string {
  let highest = 0;
  for (const r of reqs) {
    const m = /^REQ-(\d+)$/i.exec(r.code.trim());
    if (m) highest = Math.max(highest, parseInt(m[1], 10));
  }
  return `REQ-${String(highest + 1).padStart(3, "0")}`;
}
