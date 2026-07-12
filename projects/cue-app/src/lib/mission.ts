import type { CueEvent, Guest } from "./types";
import { liveMetrics, notArrived } from "./rollcall";
import { fillRatio, isUncapped, relativeDay } from "./format";
import { displayName, guestCompany } from "./guests-select";

/**
 * Pure derivations for Mission Control (TASK-0042) — the organizer's home
 * screen. Where the Roll Call Command Center (lib/rollcall) reasons about a
 * single live event, Mission Control reasons across the *whole portfolio of
 * events at once*: which are live, how attendance is tracking, what needs a
 * human right now, and what is coming up.
 *
 * Everything here is a pure function over the event list + the flat guest
 * roster + an injected `now`, so the page is presentation-only and the
 * operational rules (flagship selection, cross-event alerts, the AI briefing,
 * routing targets) live in one auditable, unit-testable place — the same split
 * the rest of the app uses (lib/select, lib/rollcall, lib/guests-select).
 *
 * It designedly composes the per-event math already proven in lib/rollcall
 * (`liveMetrics`, `notArrived`) rather than re-deriving attendance, so the
 * numbers Mission Control shows always agree with the numbers inside a running
 * event.
 */

/** Tag that marks the firm's headline event (see lib/data seed). */
const FLAGSHIP_TAG = "flagship";
/** Tags that mark an attendee as a speaker/keynote for the "speaker" alert. */
const SPEAKER_TAGS = ["speaker", "keynote"];
/** Checked-in ÷ capacity at or above this reads as capacity risk. */
export const CAPACITY_RISK_PCT = 85;
/** An upcoming event this far out (days) or nearer is close enough to nudge. */
const NUDGE_HORIZON_DAYS = 30;
/** Below this confirmed-fill an upcoming event is "under-subscribed". */
const UNDER_SUBSCRIBED_FILL = 0.5;

const DAY_MS = 24 * 60 * 60 * 1000;

/** Guests belonging to one event, from the flat roster. */
function rosterFor(eventId: string, allGuests: Guest[]): Guest[] {
  return allGuests.filter((g) => g.eventId === eventId);
}

// --- Event selection --------------------------------------------------------

/** The firm's headline event, by tag. */
export function isFlagship(event: CueEvent): boolean {
  return event.tags.includes(FLAGSHIP_TAG);
}

/**
 * Live events, flagship first, then earliest-started first. The flagship floats
 * to the top so Mission Control always leads with the headline event.
 */
export function liveEvents(events: CueEvent[]): CueEvent[] {
  return events
    .filter((e) => e.status === "live")
    .sort(
      (a, b) =>
        Number(isFlagship(b)) - Number(isFlagship(a)) ||
        new Date(a.startsAt).getTime() - new Date(b.startsAt).getTime(),
    );
}

/**
 * The event Mission Control leads with: the tagged flagship if it is live, else
 * the first live event, else undefined (nothing running right now).
 */
export function flagshipEvent(events: CueEvent[]): CueEvent | undefined {
  const live = liveEvents(events);
  return live.find(isFlagship) ?? live[0];
}

/** Upcoming (scheduled, not yet live) events, soonest first. */
export function upcomingEvents(events: CueEvent[], limit = 4): CueEvent[] {
  return events
    .filter((e) => e.status === "upcoming")
    .sort((a, b) => new Date(a.startsAt).getTime() - new Date(b.startsAt).getTime())
    .slice(0, limit);
}

// --- Organization-level metrics --------------------------------------------

export interface OrgMetrics {
  totalEvents: number;
  liveCount: number;
  upcomingCount: number;
  /** Confirmed RSVPs summed across every event (the invite funnel, not arrivals). */
  confirmedGuests: number;
  /** Average confirmed-fill across active, capped events (0–100). */
  avgFill: number;
}

/** Portfolio-wide counts for the header KPI row. */
export function orgMetrics(events: CueEvent[]): OrgMetrics {
  const active = events.filter((e) => e.status !== "done" && !isUncapped(e));
  const avgFill =
    active.length === 0
      ? 0
      : Math.round(
          (active.reduce((n, e) => n + fillRatio(e), 0) / active.length) * 100,
        );
  return {
    totalEvents: events.length,
    liveCount: events.filter((e) => e.status === "live").length,
    upcomingCount: events.filter((e) => e.status === "upcoming").length,
    confirmedGuests: events.reduce((n, e) => n + e.confirmedGuests, 0),
    avgFill,
  };
}

// --- Attendance health (aggregated across live events) ----------------------

export interface AttendanceHealth {
  /** Number of events contributing (live right now). */
  liveEventCount: number;
  checkedIn: number;
  expected: number;
  /** Confirmed guests not yet arrived across live events. */
  remaining: number;
  /** checkedIn ÷ expected, 0–100 (0 when nothing expected). */
  pct: number;
  vipCheckedIn: number;
  vipTotal: number;
  /** Summed arrivals/hour across live events (a coarse room-momentum signal). */
  arrivalRate: number;
}

/**
 * Live attendance rolled up across every live event, composed from the same
 * `liveMetrics` a single Roll Call screen shows. Pure over the roster + clock.
 */
export function attendanceHealth(
  events: CueEvent[],
  allGuests: Guest[],
  now: number,
): AttendanceHealth {
  const live = liveEvents(events);
  let checkedIn = 0;
  let expected = 0;
  let remaining = 0;
  let vipCheckedIn = 0;
  let vipTotal = 0;
  let arrivalRate = 0;

  for (const e of live) {
    const m = liveMetrics(rosterFor(e.id, allGuests), e, now);
    checkedIn += m.checkedIn;
    expected += m.expected;
    remaining += m.remaining;
    vipCheckedIn += m.vipCheckedIn;
    vipTotal += m.vipTotal;
    arrivalRate += m.arrivalRate;
  }

  return {
    liveEventCount: live.length,
    checkedIn,
    expected,
    remaining,
    vipCheckedIn,
    vipTotal,
    arrivalRate,
    pct: expected === 0 ? 0 : Math.round((checkedIn / expected) * 100),
  };
}

// --- Operational alerts -----------------------------------------------------

/** A speaker/keynote attendee (tag-driven; VIP is separate, see Guest.vip). */
export function isSpeaker(g: Guest): boolean {
  return g.tags.some((t) => SPEAKER_TAGS.includes(t));
}

/** Confirmed VIPs across live events who have not checked in, VIP-priority order. */
export function vipsAwaiting(events: CueEvent[], allGuests: Guest[]): Guest[] {
  return liveEvents(events).flatMap((e) =>
    notArrived(rosterFor(e.id, allGuests)).filter((g) => g.vip),
  );
}

/** Confirmed speakers across live events who have not checked in. */
export function speakersAwaiting(events: CueEvent[], allGuests: Guest[]): Guest[] {
  return liveEvents(events).flatMap((e) =>
    notArrived(rosterFor(e.id, allGuests)).filter(isSpeaker),
  );
}

export interface CapacitySignal {
  eventId?: string;
  eventTitle?: string;
  /** checkedIn ÷ capacity, 0–100, or null when no live capped event exists. */
  pct: number | null;
  checkedIn: number;
  capacity: number | null;
  atRisk: boolean;
}

/**
 * The most-pressured live capped event. Surfaces the single worst capacity
 * ratio so the capacity card speaks to the room most at risk of filling up.
 */
export function capacitySignal(
  events: CueEvent[],
  allGuests: Guest[],
  now: number,
): CapacitySignal {
  let worst: CapacitySignal = {
    pct: null,
    checkedIn: 0,
    capacity: null,
    atRisk: false,
  };
  for (const e of liveEvents(events)) {
    const m = liveMetrics(rosterFor(e.id, allGuests), e, now);
    if (m.capacityPct === null) continue;
    if (worst.pct === null || m.capacityPct > worst.pct) {
      worst = {
        eventId: e.id,
        eventTitle: e.title,
        pct: m.capacityPct,
        checkedIn: m.checkedIn,
        capacity: m.capacity,
        atRisk: m.capacityPct >= CAPACITY_RISK_PCT,
      };
    }
  }
  return worst;
}

export interface FollowUps {
  total: number;
  /** Attended a wrapped event but no thank-you sent yet. */
  thankYous: number;
  /** Waitlisted on a still-active event — a promote/decline decision. */
  waitlist: number;
}

/**
 * Outstanding post-event and roster housekeeping across the portfolio: thank-you
 * notes owed for wrapped events, and waitlist decisions on active events.
 */
export function followUps(events: CueEvent[], allGuests: Guest[]): FollowUps {
  let thankYous = 0;
  let waitlist = 0;
  for (const e of events) {
    const roster = rosterFor(e.id, allGuests);
    if (e.status === "done") {
      thankYous += roster.filter(
        (g) => g.attendance.checkedIn && !g.communication.thankYouSent,
      ).length;
    } else {
      waitlist += roster.filter(
        (g) => g.attendance.waitlisted || g.attendance.rsvp === "waitlisted",
      ).length;
    }
  }
  return { total: thankYous + waitlist, thankYous, waitlist };
}

// --- Quick actions / routing ------------------------------------------------

export type QuickActionId = "create" | "rollcall" | "invite" | "agenda";

export interface QuickAction {
  id: QuickActionId;
  label: string;
  /** In-app route the action navigates to (hash-router path). */
  to: string;
  /** False when the action needs a live event that doesn't exist. */
  enabled: boolean;
}

/** Deep link to an event's Roll Call Command Center. */
export function rollCallPath(event: CueEvent): string {
  return `/events/${event.id}/rollcall`;
}

/** Deep link to a tab on an event's detail page. */
export function eventTabPath(event: CueEvent, tab: string): string {
  return `/events/${event.id}?tab=${tab}`;
}

/**
 * The four Mission Control quick actions, wired to routes. The three that act on
 * a running event target the flagship; without one they fall back to the events
 * list and are marked disabled so the UI can dim them rather than dead-link.
 */
export function quickActions(flagship: CueEvent | undefined): QuickAction[] {
  const hasLive = Boolean(flagship);
  return [
    { id: "create", label: "Create event", to: "/events/new", enabled: true },
    {
      id: "rollcall",
      label: "Launch roll call",
      to: flagship ? rollCallPath(flagship) : "/events",
      enabled: hasLive,
    },
    {
      id: "invite",
      label: "Invite guests",
      to: flagship ? eventTabPath(flagship, "guests") : "/events",
      enabled: hasLive,
    },
    {
      id: "agenda",
      label: "Generate agenda",
      to: flagship ? eventTabPath(flagship, "assistant") : "/events",
      enabled: hasLive,
    },
  ];
}

// --- AI briefing (contextual recommendations) -------------------------------

export interface Recommendation {
  id: string;
  headline: string;
  detail: string;
  /** Optional in-app action link. */
  action?: { label: string; to: string };
}

/**
 * The AI Briefing: a deterministic, priority-ordered set of "do this next"
 * recommendations derived entirely from the live state — no model, no network
 * (the same offline stance as lib/ai and lib/rollcall-ai). Each rule fires only
 * when its condition holds, so the briefing is always true for the portfolio as
 * it stands. Capped to the top handful so it reads as a briefing, not a backlog.
 */
export function aiBriefing(
  events: CueEvent[],
  allGuests: Guest[],
  now: number,
  limit = 4,
): Recommendation[] {
  const recs: Recommendation[] = [];
  const flagship = flagshipEvent(events);
  const vips = vipsAwaiting(events, allGuests);
  const speakers = speakersAwaiting(events, allGuests);
  const cap = capacitySignal(events, allGuests, now);
  const health = attendanceHealth(events, allGuests, now);
  const tasks = followUps(events, allGuests);

  if (speakers.length > 0) {
    const top = speakers[0];
    const co = flagship ? guestCompany(top, flagship.portfolio) : "";
    recs.push({
      id: "speaker-eta",
      headline:
        speakers.length === 1
          ? `Confirm ${displayName(top)}'s ETA`
          : `${speakers.length} speakers not yet arrived`,
      detail:
        speakers.length === 1
          ? `${displayName(top)}${co ? ` · ${co}` : ""} is confirmed but hasn't checked in — chase an arrival time before they're on.`
          : `${displayName(top)}${co ? ` · ${co}` : ""} and ${speakers.length - 1} other${speakers.length - 1 === 1 ? "" : "s"} are confirmed but not in the room yet.`,
      action: flagship
        ? { label: "Open roll call", to: rollCallPath(flagship) }
        : undefined,
    });
  }

  if (vips.length > 0 && flagship) {
    recs.push({
      id: "vip-greeter",
      headline: `Station a greeter at ${flagship.title}`,
      detail: `${vips.length} VIP${vips.length === 1 ? "" : "s"} confirmed but not yet checked in — brief the host so no one arrives unnoticed.`,
      action: { label: "See who's expected", to: rollCallPath(flagship) },
    });
  }

  if (cap.atRisk && cap.eventId && cap.eventTitle) {
    recs.push({
      id: "capacity-hold",
      headline: `Hold walk-ins at ${cap.eventTitle}`,
      detail: `${cap.checkedIn} of ${cap.capacity} checked in (${cap.pct}%) — the room is close to full.`,
      action: { label: "Open roll call", to: `/events/${cap.eventId}/rollcall` },
    });
  }

  const soft = upcomingEvents(events, 10).find((e) => {
    const days = Math.round((new Date(e.startsAt).getTime() - now) / DAY_MS);
    return !isUncapped(e) && days <= NUDGE_HORIZON_DAYS && fillRatio(e) < UNDER_SUBSCRIBED_FILL;
  });
  if (soft) {
    recs.push({
      id: "second-wave",
      headline: `Push a second-wave invite for ${soft.title}`,
      detail: `${soft.confirmedGuests} of ${soft.capacity.maxAttendees} confirmed ${relativeDay(soft.startsAt).toLowerCase()} — there's room and time to fill it.`,
      action: { label: "Manage guests", to: eventTabPath(soft, "guests") },
    });
  }

  if (tasks.total > 0) {
    const parts = [
      tasks.waitlist > 0 ? `${tasks.waitlist} waitlist decision${tasks.waitlist === 1 ? "" : "s"}` : "",
      tasks.thankYous > 0 ? `${tasks.thankYous} thank-you${tasks.thankYous === 1 ? "" : "s"} to send` : "",
    ].filter(Boolean);
    recs.push({
      id: "follow-ups",
      headline: `Clear ${tasks.total} follow-up${tasks.total === 1 ? "" : "s"}`,
      detail: parts.join(" · ") + ".",
    });
  }

  if (recs.length === 0 && health.liveEventCount > 0) {
    recs.push({
      id: "on-track",
      headline: "Everything's on track",
      detail: `${health.checkedIn} of ${health.expected} expected are in${health.arrivalRate > 0 ? `, arrivals steady at ~${health.arrivalRate}/hr` : ""}. Nothing needs you right now.`,
    });
  }

  return recs.slice(0, limit);
}

// --- Upcoming milestones ----------------------------------------------------

export type MilestoneKind = "wrap" | "doors" | "draft";

export interface Milestone {
  id: string;
  eventId: string;
  eventTitle: string;
  /** Short verb phrase, e.g. "Doors open", "Wraps up". */
  label: string;
  kind: MilestoneKind;
  /** ISO timestamp the milestone occurs. */
  at: string;
}

/**
 * The next things on the clock across the portfolio: live events wrapping up,
 * upcoming events opening their doors, and drafts still needing details. Future
 * only, soonest first. Pure over the event list + clock.
 */
export function upcomingMilestones(
  events: CueEvent[],
  now: number,
  limit = 5,
): Milestone[] {
  const items: Milestone[] = [];
  for (const e of events) {
    if (e.status === "live" && e.endsAt) {
      items.push({
        id: `${e.id}-wrap`,
        eventId: e.id,
        eventTitle: e.title,
        label: "Wraps up",
        kind: "wrap",
        at: e.endsAt,
      });
    } else if (e.status === "upcoming") {
      items.push({
        id: `${e.id}-doors`,
        eventId: e.id,
        eventTitle: e.title,
        label: "Doors open",
        kind: "doors",
        at: e.startsAt,
      });
    } else if (e.status === "draft") {
      items.push({
        id: `${e.id}-draft`,
        eventId: e.id,
        eventTitle: e.title,
        label: "Finalize details",
        kind: "draft",
        at: e.startsAt,
      });
    }
  }
  return items
    .filter((m) => Date.parse(m.at) >= now)
    .sort((a, b) => Date.parse(a.at) - Date.parse(b.at))
    .slice(0, limit);
}
