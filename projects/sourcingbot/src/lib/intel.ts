/**
 * Workspace intelligence — the derived views behind the Candidate Workspace.
 *
 * Like `readiness.ts` (ADR-008), this stores NOTHING and owns no entity. Every
 * value is computed on read by composing functions that already exist:
 * `talentConcentration`, `reqHistoryFor`, `pipelineFor`, `stageCounts`,
 * `sessionCounts`, `closeCallsFor`, `findPossibleDuplicates`,
 * `evaluateReadiness`. A stored analytics record would be a second source of
 * truth that goes stale the moment a capture lands.
 *
 * The organising idea: **a recruiter homepage should answer questions, not
 * display rows.** Everything here resolves to either a ranked worklist item or
 * a filter over the talent pool — never a number that leads nowhere.
 */
import type {
  Candidate,
  PipelineStage,
  Req,
  ReqCandidate,
  SourcingBrief,
  SourcingSession,
  SkippedCandidate,
} from "./types";
import { currentCompany, currentRole, findPossibleDuplicates, talentConcentration } from "./candidate";
import { sortForWorkspace } from "./req";
import { evaluateReadiness } from "./readiness";
import { reqHistoryFor, stageCounts } from "./req-candidate";
import {
  aggregateCounts,
  activeSessionFor,
  closeCallsFor,
  isActive,
  sessionCounts,
  sessionsForReq,
  type SessionCounts,
} from "./linkedin";

/** Everything the workspace reads. Mirrors WorkspaceState, passed explicitly. */
export interface IntelInput {
  reqs: Req[];
  briefs: SourcingBrief[];
  candidates: Candidate[];
  reqCandidates: ReqCandidate[];
  sessions: SourcingSession[];
}

// ---------------------------------------------------------------------------
// 1. Pulse — the reflexive check
// ---------------------------------------------------------------------------

export interface Pulse {
  activeReqs: number;
  activeSessions: number;
  capturedToday: number;
  /** Every candidate ever captured into a requisition. */
  capturedTotal: number;
  closeCalls: number;
  reusedCandidates: number;
  needsReview: number;
  /** Share of reviewed people who were captured, across all sessions. */
  captureRate: number | null;
  /** Mean fit across scored evaluations. Null when nothing is scored. */
  averageFit: number | null;
}

/** Local-day boundary, so "today" means the recruiter's today. */
function isToday(iso: string): boolean {
  const d = new Date(iso);
  const now = new Date();
  return (
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  );
}

/**
 * `needsReview` counts evaluations whose brief has moved on since they were
 * made — the one queue that silently rots. A fit score computed against v2
 * requirements is not comparable to one from v4, and nothing else surfaces it.
 */
export function pulse(input: IntelInput): Pulse {
  const briefByReq = new Map(input.briefs.map((b) => [b.reqId, b]));
  const scored = input.reqCandidates
    .map((rc) => rc.fitScore)
    .filter((f): f is number => f !== null);
  return {
    activeReqs: input.reqs.filter((r) => r.status === "open").length,
    activeSessions: input.sessions.filter(isActive).length,
    capturedToday: input.reqCandidates.filter((rc) => isToday(rc.addedAt)).length,
    capturedTotal: input.reqCandidates.length,
    closeCalls: input.sessions.flatMap((s) => s.skipped ?? []).filter((s) => s.closeCall).length,
    reusedCandidates: input.candidates.filter(
      (c) => reqHistoryFor(c.id, input.reqCandidates).length > 1,
    ).length,
    needsReview: input.reqCandidates.filter((rc) => {
      const b = briefByReq.get(rc.reqId);
      return b !== undefined && rc.briefVersion < b.version;
    }).length,
    captureRate: aggregateCounts(input.sessions).captureRate,
    // Rejections score 0 and are included deliberately: an average that quietly
    // dropped them would flatter every pipeline.
    averageFit: scored.length
      ? Math.round(scored.reduce((a, b) => a + b, 0) / scored.length)
      : null,
  };
}

// ---------------------------------------------------------------------------
// 2. Recommended focus — the ranked worklist
// ---------------------------------------------------------------------------

export type FocusKind =
  | "strong-candidate"
  | "close-call"
  | "reuse-opportunity"
  | "thin-pipeline"
  | "weak-session"
  | "stale-evaluation";

/**
 * What acting on a focus item actually does.
 *
 * Described here, performed by the surface. `intel.ts` stays pure — it decides
 * WHAT the next action is, and the component routes it through the existing
 * domain functions (`advance`, `startSession`). Encoding the action as data is
 * what lets the homepage be an operating surface rather than a set of links:
 * most items are worked in place, and `navigate` is the exception for the few
 * that genuinely need another screen.
 */
export type FocusAction =
  /** Move a candidate one stage forward, in place. */
  | { type: "advance"; reqCandidateId: string; to: PipelineStage; label: string }
  /** Reject a candidate for this req, in place. */
  | { type: "reject"; reqCandidateId: string; label: string }
  /** Open the supervised session gate inline on the homepage. */
  | { type: "start-session"; reqId: string; label: string }
  /** Genuinely needs another surface — a capture form, a brief editor. */
  | { type: "navigate"; href: string; label: string };

export interface FocusItem {
  id: string;
  kind: FocusKind;
  title: string;
  /** Why this surfaced. Shown verbatim — a worklist without reasons is noise. */
  reason: string;
  /** The action worked in place, or navigation when the work needs a surface. */
  action: FocusAction;
  /** Optional secondary action, e.g. rejecting instead of advancing. */
  secondary?: FocusAction;
  /** Where to look at the item in full, always available. */
  href: string;
  /** Higher sorts first. */
  priority: number;
}

/** The stage a candidate moves to when advanced from the queue. */
const NEXT_STAGE: Partial<Record<PipelineStage, PipelineStage>> = {
  identified: "reviewing",
  reviewing: "contacted",
  contacted: "responded",
  responded: "advanced",
};

const STAGE_UNACTIONED: PipelineStage[] = ["identified", "reviewing"];

/**
 * The homepage's reason to exist: what to do next, and why.
 *
 * Ranked by priority rather than grouped by type, because a recruiter's next
 * action is whatever matters most — not whatever category they scroll to first.
 */
export function recommendedFocus(input: IntelInput, limit = 8): FocusItem[] {
  const items: FocusItem[] = [];
  const candidateById = new Map(input.candidates.map((c) => [c.id, c]));
  const reqById = new Map(input.reqs.map((r) => [r.id, r]));
  const briefByReq = new Map(input.briefs.map((b) => [b.reqId, b]));

  // Strong candidates nobody has moved on. The clearest wasted opportunity:
  // work already done, sitting idle.
  for (const rc of input.reqCandidates) {
    if (!STAGE_UNACTIONED.includes(rc.stage)) continue;
    if ((rc.fitScore ?? 0) < 70) continue;
    const c = candidateById.get(rc.candidateId);
    const req = reqById.get(rc.reqId);
    if (!c || !req) continue;
    const next = NEXT_STAGE[rc.stage];
    items.push({
      id: `strong-${rc.id}`,
      kind: "strong-candidate",
      title: c.fullName,
      reason: `${rc.fitScore}% fit for ${req.code} — still ${rc.stage}`,
      action: next
        ? { type: "advance", reqCandidateId: rc.id, to: next, label: `Move to ${next}` }
        : { type: "navigate", href: `/reqs/${req.id}`, label: "Open pipeline" },
      secondary: { type: "reject", reqCandidateId: rc.id, label: "Not a fit" },
      href: `/reqs/${req.id}`,
      priority: 100 + (rc.fitScore ?? 0),
    });
  }

  // Close calls, newest first. These decay: a near-miss is only useful while
  // the recruiter still remembers the search.
  for (const req of input.reqs) {
    for (const s of closeCallsFor(req.id, input.sessions).slice(0, 3)) {
      items.push({
        id: `close-${s.id}`,
        kind: "close-call",
        title: s.name,
        reason: `Close call on ${req.code}${s.reason ? ` — ${s.reason}` : ""}`,
        // Capturing needs the full form, so this one genuinely leaves home.
        action: { type: "navigate", href: `/reqs/${req.id}/session`, label: "Revisit in session" },
        href: `/reqs/${req.id}/session`,
        priority: 80,
      });
    }
  }

  // Duplicate/reuse opportunities. Surfaced before they become two people.
  const seenPairs = new Set<string>();
  for (const c of input.candidates) {
    for (const dup of findPossibleDuplicates(c, input.candidates)) {
      const key = [c.id, dup.id].sort().join("|");
      if (seenPairs.has(key)) continue;
      seenPairs.add(key);
      items.push({
        id: `dup-${key}`,
        kind: "reuse-opportunity",
        title: `${c.fullName} · possible duplicate`,
        reason: `Matches ${dup.fullName} already in the pool — merge or keep separate`,
        action: { type: "navigate", href: `/candidates/${c.id}`, label: "Compare" },
        href: `/candidates/${c.id}`,
        priority: 75,
      });
    }
  }

  // Open reqs with thin pipelines. The question "which searches need work?"
  for (const req of input.reqs) {
    if (req.status !== "open") continue;
    const counts = stageCounts(req.id, input.reqCandidates);
    const live = counts.identified + counts.reviewing + counts.contacted + counts.responded;
    if (live >= 3) continue;
    items.push({
      id: `thin-${req.id}`,
      kind: "thin-pipeline",
      title: req.title || req.code,
      reason:
        live === 0
          ? `${req.code} has nobody in the pipeline`
          : `${req.code} has only ${live} live ${live === 1 ? "candidate" : "candidates"}`,
      action: { type: "start-session", reqId: req.id, label: "Start sourcing" },
      href: `/reqs/${req.id}/session`,
      priority: 90 - live * 5,
    });
  }

  // Sessions where most reviewed people were skipped.
  //
  // The wording is deliberately neutral about cause. A low capture rate can come
  // from a brief that is too narrow, search criteria that are off, or a sourcing
  // approach that needs changing — and sometimes from a genuinely thin market,
  // where the session was correct and nothing needs fixing. Asserting the brief
  // is at fault would put a guess in front of the recruiter as a finding. The
  // action links to the brief because that is the most common place to look
  // first, not because it is the diagnosis.
  for (const s of input.sessions) {
    const c = sessionCounts(s);
    if (c.reviewed < 5 || c.captureRate === null || c.captureRate >= 25) continue;
    const req = reqById.get(s.reqId);
    if (!req) continue;
    items.push({
      id: `weak-${s.id}`,
      kind: "weak-session",
      title: `${req.code} — ${c.captureRate}% capture rate`,
      reason:
        `${c.captured} of ${c.reviewed} reviewed were added. ` +
        `Review the brief, search criteria, or sourcing approach.`,
      action: { type: "navigate", href: `/reqs/${req.id}/edit`, label: "Review brief" },
      href: `/reqs/${req.id}/edit`,
      priority: 70,
    });
  }

  // Evaluations made against a superseded brief.
  for (const rc of input.reqCandidates) {
    const b = briefByReq.get(rc.reqId);
    const req = reqById.get(rc.reqId);
    const c = candidateById.get(rc.candidateId);
    if (!b || !req || !c || rc.briefVersion >= b.version) continue;
    items.push({
      id: `stale-${rc.id}`,
      kind: "stale-evaluation",
      title: c.fullName,
      reason: `Assessed against ${req.code} brief v${rc.briefVersion}, now v${b.version}`,
      // Reassessment is a judgement against the new bar — it needs the pipeline.
      action: { type: "navigate", href: `/reqs/${req.id}`, label: "Reassess" },
      href: `/reqs/${req.id}`,
      priority: 60,
    });
  }

  return items.sort((a, b) => b.priority - a.priority).slice(0, limit);
}

// ---------------------------------------------------------------------------
// 3. Talent intelligence — concentration as navigation
// ---------------------------------------------------------------------------

export type ConcentrationDimension = "company" | "location" | "title" | "skill";

export interface ConcentrationRow {
  label: string;
  count: number;
  /** Share of the pool, 0-100. */
  share: number;
}

/**
 * Concentration across a dimension.
 *
 * `company` delegates to the existing `talentConcentration` rather than
 * reimplementing it — that function is the tested definition of "where are
 * our people", and a second implementation could drift from it.
 */
export function concentration(
  candidates: Candidate[],
  dimension: ConcentrationDimension,
  limit = 6,
): ConcentrationRow[] {
  const total = candidates.length;
  const withShare = (rows: Array<{ label: string; count: number }>): ConcentrationRow[] =>
    rows
      .slice(0, limit)
      .map((r) => ({ ...r, share: total === 0 ? 0 : Math.round((r.count / total) * 100) }));

  if (dimension === "company") {
    return withShare(
      talentConcentration(candidates).map((r) => ({ label: r.company, count: r.count })),
    );
  }

  const counts = new Map<string, number>();
  const bump = (value: string) => {
    const v = value.trim();
    if (v) counts.set(v, (counts.get(v) ?? 0) + 1);
  };

  for (const c of candidates) {
    if (dimension === "location") bump(c.location);
    else if (dimension === "title") bump(currentRole(c)?.title ?? "");
    else for (const s of c.skills) bump(s);
  }

  return withShare(
    [...counts.entries()]
      .map(([label, count]) => ({ label, count }))
      .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label)),
  );
}

/** Does this candidate match a concentration row? Powers click-to-filter. */
export function matchesDimension(
  candidate: Candidate,
  dimension: ConcentrationDimension,
  label: string,
): boolean {
  const l = label.toLowerCase();
  if (dimension === "company") return currentCompany(candidate).toLowerCase() === l;
  if (dimension === "location") return candidate.location.toLowerCase() === l;
  if (dimension === "title") return (currentRole(candidate)?.title ?? "").toLowerCase() === l;
  return candidate.skills.some((s) => s.toLowerCase() === l);
}

// ---------------------------------------------------------------------------
// 4. Activity — one merged timeline
// ---------------------------------------------------------------------------

export type ActivityKind = "capture" | "evaluation" | "skip";

export interface ActivityEntry {
  id: string;
  kind: ActivityKind;
  at: string;
  title: string;
  detail: string;
  href: string;
}

/**
 * Recent activity across captures, stage moves, and skips.
 *
 * Merged rather than three lists: the recruiter's question is "did anything
 * move?", which three separate feeds make harder to answer, not easier.
 */
export function recentActivity(input: IntelInput, limit = 12): ActivityEntry[] {
  const candidateById = new Map(input.candidates.map((c) => [c.id, c]));
  const reqById = new Map(input.reqs.map((r) => [r.id, r]));
  const out: ActivityEntry[] = [];

  for (const rc of input.reqCandidates) {
    const c = candidateById.get(rc.candidateId);
    const req = reqById.get(rc.reqId);
    if (!c || !req) continue;

    out.push({
      id: `add-${rc.id}`,
      kind: "capture",
      at: rc.addedAt,
      title: c.fullName,
      detail: `added to ${req.code}`,
      href: `/candidates/${c.id}`,
    });

    // Stage moves after the initial add are the real evaluation signal.
    for (const h of rc.history.slice(1)) {
      out.push({
        id: `mv-${rc.id}-${h.at}-${h.to}`,
        kind: "evaluation",
        at: h.at,
        title: c.fullName,
        detail: `${h.to} on ${req.code}${h.reason ? ` — ${h.reason}` : ""}`,
        href: `/reqs/${req.id}`,
      });
    }
  }

  for (const s of input.sessions) {
    const req = reqById.get(s.reqId);
    for (const skip of s.skipped ?? []) {
      out.push({
        id: `skip-${skip.id}`,
        kind: "skip",
        at: skip.at,
        title: skip.name,
        detail: `skipped on ${req?.code ?? "a req"}${skip.closeCall ? " — close call" : ""}`,
        href: req ? `/reqs/${req.id}/session` : "/",
      });
    }
  }

  return out.sort((a, b) => b.at.localeCompare(a.at)).slice(0, limit);
}

// ---------------------------------------------------------------------------
// 5. Talent pool — enriched rows for the table
// ---------------------------------------------------------------------------

export interface PoolRow {
  candidate: Candidate;
  company: string;
  title: string;
  /** How many requisitions this person has been evaluated for. */
  reqCount: number;
  /** Best fit score across their evaluations, if any were scored. */
  bestFit: number | null;
  /** Stage on their most recently touched evaluation. Null if never on a req. */
  latestStage: PipelineStage | null;
  lastActivity: string;
}

export function poolRows(input: IntelInput): PoolRow[] {
  return input.candidates
    .map((candidate) => {
      const history = reqHistoryFor(candidate.id, input.reqCandidates);
      const scored = history.map((h) => h.fitScore).filter((s): s is number => s !== null);
      return {
        candidate,
        company: currentCompany(candidate),
        title: currentRole(candidate)?.title ?? "",
        reqCount: history.length,
        bestFit: scored.length ? Math.max(...scored) : null,
        latestStage: history[0]?.stage ?? null,
        lastActivity: history[0]?.updatedAt ?? candidate.updatedAt,
      };
    })
    .sort((a, b) => b.lastActivity.localeCompare(a.lastActivity));
}

/** Saved views — named filters over the pool, not stored queries. */
export type SavedView = "all" | "multi-req" | "scored" | "unevaluated" | "supervised";

export const SAVED_VIEWS: ReadonlyArray<{ id: SavedView; label: string; hint: string }> = [
  { id: "all", label: "Everyone", hint: "The whole pool" },
  { id: "multi-req", label: "Reusable", hint: "Seen on more than one req" },
  { id: "scored", label: "Scored", hint: "Has a fit score" },
  { id: "unevaluated", label: "Not yet on a req", hint: "In the pool, never evaluated" },
  { id: "supervised", label: "From sourcing", hint: "Captured in a supervised session" },
];

export function applyView(rows: PoolRow[], view: SavedView): PoolRow[] {
  switch (view) {
    case "multi-req":
      return rows.filter((r) => r.reqCount > 1);
    case "scored":
      return rows.filter((r) => r.bestFit !== null);
    case "unevaluated":
      return rows.filter((r) => r.reqCount === 0);
    case "supervised":
      return rows.filter((r) => r.candidate.origin === "supervised-linkedin");
    default:
      return rows;
  }
}

/** CSV of the visible rows. Quotes every field so commas cannot corrupt it. */
export function toCsv(rows: PoolRow[]): string {
  const esc = (v: string | number | null) =>
    `"${String(v ?? "").replace(/"/g, '""')}"`;
  const header = ["Name", "Headline", "Company", "Title", "Location", "Skills", "Reqs", "Best fit", "Origin"];
  const body = rows.map((r) =>
    [
      r.candidate.fullName,
      r.candidate.headline,
      r.company,
      r.title,
      r.candidate.location,
      r.candidate.skills.join("; "),
      r.reqCount,
      r.bestFit,
      r.candidate.origin,
    ]
      .map(esc)
      .join(","),
  );
  return [header.map(esc).join(","), ...body].join("\n");
}

/** Rollup of every session, for the intelligence header. */
export function sourcingTotals(sessions: SourcingSession[]) {
  return aggregateCounts(sessions);
}

/** All close calls across every req, newest first. */
export function allCloseCalls(input: IntelInput): SkippedCandidate[] {
  return input.reqs
    .flatMap((r) => closeCallsFor(r.id, input.sessions))
    .sort((a, b) => b.at.localeCompare(a.at));
}

// ---------------------------------------------------------------------------
// Dashboard — requisition rows and sourcing performance
// ---------------------------------------------------------------------------

export interface ReqDashboardRow {
  req: Req;
  /** 0-100 authoring completeness, from the existing readiness model. */
  completeness: number;
  sourcingReady: boolean;
  /** Candidates still moving: identified through responded. */
  live: number;
  /** Live candidates scoring >= the strong threshold. */
  strong: number;
  advanced: number;
  rejected: number;
  sessions: number;
  activeSession: SourcingSession | null;
  captureRate: number | null;
  lastActivity: string;
}

/** Fit at or above this counts as a strong candidate. See ADR-014 appendix. */
export const STRONG_FIT = 70;

const LIVE_STAGES: PipelineStage[] = ["identified", "reviewing", "contacted", "responded"];

/**
 * One row per requisition for the dashboard.
 *
 * Every field is derived: completeness from `evaluateReadiness`, counts from
 * `stageCounts`, sourcing figures from `aggregateCounts` over that req's
 * sessions. Nothing is stored, so a row can never disagree with the records it
 * summarises.
 */
export function reqDashboard(input: IntelInput): ReqDashboardRow[] {
  const briefByReq = new Map(input.briefs.map((b) => [b.reqId, b]));

  return sortForWorkspace(input.reqs).map((req) => {
    const brief = briefByReq.get(req.id);
    const readiness = evaluateReadiness(req, brief);
    const counts = stageCounts(req.id, input.reqCandidates);
    const mine = input.reqCandidates.filter((rc) => rc.reqId === req.id);
    const sessions = sessionsForReq(req.id, input.sessions);

    const lastActivity = [
      req.updatedAt,
      ...mine.map((rc) => rc.updatedAt),
      ...sessions.map((s) => s.endedAt ?? s.startedAt),
    ].sort().reverse()[0] ?? req.updatedAt;

    return {
      req,
      completeness: readiness.completeness,
      sourcingReady: readiness.sourcingReady,
      live: LIVE_STAGES.reduce((sum, st) => sum + counts[st], 0),
      strong: mine.filter(
        (rc) => LIVE_STAGES.includes(rc.stage) && (rc.fitScore ?? 0) >= STRONG_FIT,
      ).length,
      advanced: counts.advanced,
      rejected: counts.rejected,
      sessions: sessions.length,
      activeSession: activeSessionFor(req.id, input.sessions),
      captureRate: aggregateCounts(sessions).captureRate,
      lastActivity,
    };
  });
}

export interface SessionPerformanceRow {
  session: SourcingSession;
  reqCode: string;
  counts: SessionCounts;
}

export interface SourcingPerformance {
  totals: SessionCounts;
  sessionCount: number;
  activeCount: number;
  /** Sessions with enough signal to rank, best capture rate first. */
  ranked: SessionPerformanceRow[];
  strongest: SessionPerformanceRow | null;
  weakest: SessionPerformanceRow | null;
}

/**
 * Sourcing performance across every session.
 *
 * Strongest and weakest are drawn only from sessions that reviewed at least
 * `MIN_RANKABLE_REVIEWED` people. A session that looked at two profiles and
 * captured one is not a 50% performer — it is a session with no signal, and
 * ranking it would put noise at the top of the board.
 */
export const MIN_RANKABLE_REVIEWED = 3;

export function sourcingPerformance(input: IntelInput): SourcingPerformance {
  const reqCode = new Map(input.reqs.map((r) => [r.id, r.code]));

  const rows: SessionPerformanceRow[] = input.sessions.map((session) => ({
    session,
    reqCode: reqCode.get(session.reqId) ?? "—",
    counts: sessionCounts(session),
  }));

  const rankable = rows
    .filter((r) => r.counts.reviewed >= MIN_RANKABLE_REVIEWED && r.counts.captureRate !== null)
    .sort((a, b) => (b.counts.captureRate ?? 0) - (a.counts.captureRate ?? 0));

  return {
    totals: aggregateCounts(input.sessions),
    sessionCount: input.sessions.length,
    activeCount: input.sessions.filter(isActive).length,
    ranked: rankable,
    strongest: rankable[0] ?? null,
    weakest: rankable.length > 1 ? rankable[rankable.length - 1] : null,
  };
}

/** People evaluated for more than one requisition, most-evaluated first. */
export interface ReusableCandidate {
  candidate: Candidate;
  reqCount: number;
  reqCodes: string[];
  bestFit: number | null;
}

export function reusableTalent(input: IntelInput, limit = 6): ReusableCandidate[] {
  const reqCode = new Map(input.reqs.map((r) => [r.id, r.code]));
  return input.candidates
    .map((candidate) => {
      const history = reqHistoryFor(candidate.id, input.reqCandidates);
      const scored = history.map((h) => h.fitScore).filter((f): f is number => f !== null);
      return {
        candidate,
        reqCount: history.length,
        reqCodes: history.map((h) => reqCode.get(h.reqId) ?? "—"),
        bestFit: scored.length ? Math.max(...scored) : null,
      };
    })
    .filter((r) => r.reqCount > 1)
    .sort((a, b) => b.reqCount - a.reqCount || (b.bestFit ?? -1) - (a.bestFit ?? -1))
    .slice(0, limit);
}
