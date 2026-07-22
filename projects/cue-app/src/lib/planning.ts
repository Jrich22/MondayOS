/**
 * Event Planning — domain model and pure logic (slice 1: Planning Workspace &
 * Readiness Foundation).
 *
 * Implements the approved product records:
 *   • DEC-0008  — Event Planning is Cue's active next increment.
 *   • DOC-0015  — Event Planning Product Increment Definition.
 *   • DEC-0009  — operating semantics and boundaries.
 *
 * Design rules honored here (DEC-0009):
 *   §1 Ownership   — a lightweight `WorkspaceMember` behind the existing
 *                    mock-session seam (lib/session), DISTINCT from Person/Guest.
 *   §2 Readiness   — explainable category *states* (never a weighted %), each
 *                    carrying its contributing evidence and required next action,
 *                    derived ONLY from capabilities Cue currently supports.
 *   §3 Capacity    — capacity is a hard constraint; invited/confirmed/waitlisted/
 *                    projected are demand signals kept SEPARATE (never redefines
 *                    capacity as an RSVP count — see DEC-0005).
 *   §4 Handoff     — planning reads the CANONICAL event + guest state (lib/store,
 *                    lib/guests); it keeps no separate copy of guest/event data.
 *
 * Planning-owned records (days, milestones, responsibilities, risks) live as
 * their own collection keyed by `eventId` per the CueEvent future-compatibility
 * contract — they are never nested inside CueEvent. This module is React-free
 * and pure so the rules are unit-testable in one place.
 */
import type { CueEvent, Guest } from "./types";
import { currentUser } from "./session";
import { guestSummary } from "./guests-select";
import { CAPACITY_RISK_PCT } from "./mission";

// ---------------------------------------------------------------------------
// Ownership (DEC-0009 §1) — behind the mock-session seam; not a Person/Guest.
// ---------------------------------------------------------------------------

export interface WorkspaceMember {
  id: string;
  name: string;
  role: string;
  initials: string;
}

/** The current app user as a WorkspaceMember (reads the existing session seam). */
export function currentMember(): WorkspaceMember {
  return {
    id: "wm-current",
    name: currentUser.name,
    role: currentUser.role,
    initials: currentUser.initials,
  };
}

// ---------------------------------------------------------------------------
// Planning-owned records (collection keyed by eventId; never nested in CueEvent)
// ---------------------------------------------------------------------------

/** A structured day of a (possibly multi-day) event. */
export interface PlanningDay {
  id: string;
  date: string; // yyyy-mm-dd
  label: string;
}

export type MilestoneStatus = "todo" | "in-progress" | "done";

export interface Milestone {
  id: string;
  title: string;
  dueDate?: string; // yyyy-mm-dd
  status: MilestoneStatus;
  ownerId?: string; // WorkspaceMember.id
}

/** An area of the event a WorkspaceMember is accountable for. */
export interface Responsibility {
  id: string;
  area: string;
  ownerId?: string; // WorkspaceMember.id
}

export type RiskSeverity = "low" | "medium" | "high";
export type RiskStatus = "open" | "mitigating" | "resolved";

export interface Risk {
  id: string;
  title: string;
  severity: RiskSeverity;
  status: RiskStatus;
  /** A blocker gates readiness (drives a Blocked state). */
  blocker: boolean;
  mitigation?: string;
}

/** The full planning collection for one event (keyed by eventId). */
export interface EventPlan {
  eventId: string;
  /** Primary planning owner (WorkspaceMember.id), or null if unassigned. */
  ownerId: string | null;
  /** Members available to own responsibilities/milestones. */
  members: WorkspaceMember[];
  days: PlanningDay[];
  milestones: Milestone[];
  responsibilities: Responsibility[];
  risks: Risk[];
  updatedAt: string;
}

/** Calendar days (inclusive, date-only) spanned by an event's window. */
export function eventDayCount(event: CueEvent): number {
  const start = event.startsAt.slice(0, 10);
  const end = (event.endsAt ?? event.startsAt).slice(0, 10);
  const ms = Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`);
  return Math.max(1, Math.floor(ms / 86_400_000) + 1);
}

/** Add `n` days to a yyyy-mm-dd date (UTC), returning yyyy-mm-dd. */
function addDays(date: string, n: number): string {
  return new Date(Date.parse(`${date}T00:00:00Z`) + n * 86_400_000)
    .toISOString()
    .slice(0, 10);
}

/**
 * A fresh plan seeded from the canonical event: one PlanningDay per calendar day
 * of the event window, owned by the current member. Deterministic (ids derived
 * from eventId + index) so it is testable and stable across reloads.
 */
export function emptyPlan(event: CueEvent, now: number): EventPlan {
  const me = currentMember();
  const startDate = event.startsAt.slice(0, 10);
  const days: PlanningDay[] = Array.from(
    { length: eventDayCount(event) },
    (_, i) => ({
      id: `day-${event.id}-${i + 1}`,
      date: addDays(startDate, i),
      label: `Day ${i + 1}`,
    }),
  );
  return {
    eventId: event.id,
    ownerId: me.id,
    members: [me],
    days,
    milestones: [],
    responsibilities: [],
    risks: [],
    updatedAt: new Date(now).toISOString(),
  };
}

// ---------------------------------------------------------------------------
// Capacity vs. demand (DEC-0009 §3) — kept strictly separate.
// ---------------------------------------------------------------------------

export interface CapacityDemand {
  /** Hard operational constraint. `null` = uncapped (not applicable). */
  capacity: number | null;
  // Demand signals — never fold into capacity.
  invited: number;
  confirmed: number;
  waitlisted: number;
  /** Confirmed head count incl. plus-ones (reuses guestSummary.expectedHeadcount). */
  projected: number;
  /** projected / capacity, or null when uncapped. */
  utilization: number | null;
  state: ReadinessState;
  warning: string | null;
}

/**
 * Compare canonical guest demand against the event's capacity. Reuses
 * `guestSummary` for confirmed/projected (no duplicated calculation) and counts
 * the remaining demand states directly. Capacity is read from the event and is
 * never redefined as an RSVP count.
 */
export function capacityDemand(event: CueEvent, guests: Guest[]): CapacityDemand {
  const summary = guestSummary(guests); // reuse shared demand calc
  const invited = guests.filter((g) => g.attendance.rsvp === "invited").length;
  const waitlisted = guests.filter(
    (g) => g.attendance.waitlisted || g.attendance.rsvp === "waitlisted",
  ).length;
  const capacity = event.capacity.maxAttendees;
  const projected = summary.expectedHeadcount;

  if (capacity === null) {
    return {
      capacity, invited, confirmed: summary.confirmed, waitlisted, projected,
      utilization: null, state: "not-applicable", warning: null,
    };
  }

  const utilization = capacity > 0 ? projected / capacity : 0;
  let state: ReadinessState = "complete";
  let warning: string | null = null;
  if (projected > capacity) {
    state = "blocked";
    warning = `Projected attendance (${projected}) exceeds capacity (${capacity}). Reduce confirmations or raise capacity.`;
  } else if (utilization * 100 >= CAPACITY_RISK_PCT) {
    state = "attention";
    warning = `Projected attendance (${projected}) is nearing capacity (${capacity}). Watch confirmations and the waitlist.`;
  }
  return { capacity, invited, confirmed: summary.confirmed, waitlisted, projected, utilization, state, warning };
}

// ---------------------------------------------------------------------------
// Operational readiness (DEC-0009 §2) — explainable category states.
// ---------------------------------------------------------------------------

export type ReadinessState = "complete" | "attention" | "blocked" | "not-applicable";

export interface ReadinessCategory {
  key: string;
  label: string;
  state: ReadinessState;
  /** Contributing evidence (required for every state). */
  evidence: string;
  /** The required next action (required for every state). */
  nextAction: string;
}

export interface ReadinessReport {
  categories: ReadinessCategory[];
  /** Worst applicable state across categories (blocked > attention > complete). */
  overall: ReadinessState;
}

const STATE_RANK: Record<ReadinessState, number> = {
  "not-applicable": 0,
  complete: 1,
  attention: 2,
  blocked: 3,
};

function worst(states: ReadinessState[]): ReadinessState {
  const applicable = states.filter((s) => s !== "not-applicable");
  if (applicable.length === 0) return "not-applicable";
  return applicable.reduce((a, b) => (STATE_RANK[b] > STATE_RANK[a] ? b : a));
}

function todayIso(now: number): string {
  return new Date(now).toISOString().slice(0, 10);
}

/**
 * Derive explainable readiness across categories from the canonical event, its
 * canonical guest roster, and the planning collection. Every category returns
 * its state plus evidence and a next action. Uses only capabilities Cue supports
 * today (no Registration/Sessions/Speakers signals).
 */
export function readiness(
  event: CueEvent,
  guests: Guest[],
  plan: EventPlan,
  now: number,
): ReadinessReport {
  const today = todayIso(now);
  const categories: ReadinessCategory[] = [];

  // 1. Schedule — multi-day structure.
  categories.push(
    plan.days.length === 0
      ? { key: "schedule", label: "Schedule", state: "attention",
          evidence: "No days structured yet.",
          nextAction: "Add at least one planning day to structure the event." }
      : { key: "schedule", label: "Schedule", state: "complete",
          evidence: `${plan.days.length} day${plan.days.length === 1 ? "" : "s"} structured.`,
          nextAction: "Refine per-day labels and milestones as planning firms up." },
  );

  // 2. Ownership — a planning owner and owned responsibilities.
  const unowned = plan.responsibilities.filter((r) => !r.ownerId).length;
  if (!plan.ownerId) {
    categories.push({ key: "ownership", label: "Ownership", state: "attention",
      evidence: "No planning owner assigned.",
      nextAction: "Assign a planning owner to hold accountability." });
  } else if (plan.responsibilities.length === 0) {
    categories.push({ key: "ownership", label: "Ownership", state: "attention",
      evidence: "An owner is set but no responsibilities are defined.",
      nextAction: "Define responsibility areas (e.g. catering, AV, comms)." });
  } else if (unowned > 0) {
    categories.push({ key: "ownership", label: "Ownership", state: "attention",
      evidence: `${unowned} of ${plan.responsibilities.length} responsibilities are unassigned.`,
      nextAction: "Assign an owner to every responsibility area." });
  } else {
    categories.push({ key: "ownership", label: "Ownership", state: "complete",
      evidence: `Owner assigned; ${plan.responsibilities.length} responsibilities all owned.`,
      nextAction: "Keep owners current as the team changes." });
  }

  // 3. Milestones — defined, with none overdue.
  const overdue = plan.milestones.filter(
    (m) => m.status !== "done" && m.dueDate !== undefined && m.dueDate < today,
  );
  const done = plan.milestones.filter((m) => m.status === "done").length;
  if (plan.milestones.length === 0) {
    categories.push({ key: "milestones", label: "Milestones", state: "attention",
      evidence: "No milestones defined.",
      nextAction: "Add the key planning milestones and their due dates." });
  } else if (overdue.length > 0) {
    categories.push({ key: "milestones", label: "Milestones", state: "attention",
      evidence: `${overdue.length} milestone${overdue.length === 1 ? "" : "s"} overdue (${done}/${plan.milestones.length} done).`,
      nextAction: "Re-plan or complete the overdue milestones." });
  } else {
    categories.push({ key: "milestones", label: "Milestones", state: "complete",
      evidence: `${done}/${plan.milestones.length} milestones done, none overdue.`,
      nextAction: "Advance the remaining milestones on schedule." });
  }

  // 4. Risks & blockers — an open blocker gates readiness.
  const openBlockers = plan.risks.filter((r) => r.blocker && r.status !== "resolved");
  const openRisks = plan.risks.filter((r) => !r.blocker && r.status === "open");
  if (openBlockers.length > 0) {
    categories.push({ key: "risks", label: "Risks & blockers", state: "blocked",
      evidence: `${openBlockers.length} open blocker${openBlockers.length === 1 ? "" : "s"}.`,
      nextAction: "Resolve the blocker(s) before the event can proceed." });
  } else if (openRisks.length > 0) {
    categories.push({ key: "risks", label: "Risks & blockers", state: "attention",
      evidence: `${openRisks.length} open risk${openRisks.length === 1 ? "" : "s"}, no blockers.`,
      nextAction: "Add a mitigation for each open risk." });
  } else {
    categories.push({ key: "risks", label: "Risks & blockers", state: "complete",
      evidence: plan.risks.length === 0 ? "No risks logged." : "All risks mitigated or resolved.",
      nextAction: "Keep watching for new risks as planning progresses." });
  }

  // 5. Guests — canonical roster (reuses guestSummary; no separate copy).
  const gs = guestSummary(guests);
  const invited = guests.length;
  if (invited === 0) {
    categories.push({ key: "guests", label: "Guests", state: "attention",
      evidence: "No guests on the event yet.",
      nextAction: "Invite guests to build the audience." });
  } else {
    categories.push({ key: "guests", label: "Guests", state: "complete",
      evidence: `${invited} on the roster, ${gs.confirmed} confirmed.`,
      nextAction: "Chase outstanding RSVPs to firm up the headcount." });
  }

  // 6. Capacity — reuse the capacity/demand model (state + warning).
  const cd = capacityDemand(event, guests);
  categories.push({
    key: "capacity", label: "Capacity", state: cd.state,
    evidence:
      cd.capacity === null
        ? "Uncapped event — capacity is not a constraint."
        : `Projected ${cd.projected} against capacity ${cd.capacity}.`,
    nextAction:
      cd.warning ??
      (cd.capacity === null
        ? "Set a capacity if the venue imposes a hard limit."
        : "Headroom is healthy; keep tracking confirmations."),
  });

  return { categories, overall: worst(categories.map((c) => c.state)) };
}
