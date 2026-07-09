import type { CueEvent, Guest, PortfolioCompany } from "./types";
import { isUncapped } from "./format";
import { displayName, guestCompany } from "./guests-select";

/**
 * Pure derivations for the Roll Call Command Center (TASK-0040) — the live
 * operational control center for running an event.
 *
 * Everything here is computed off the *live roster* (`Guest[]`), which is the
 * source of truth during an event, rather than the aggregate counts on the
 * event record. That is the whole point of the command center: the numbers on
 * screen are the room, right now. Keeping the math here (out of the components)
 * means the operational rules — walk-ins, no-shows, arrival pace, alerts — are
 * unit-testable and live in one auditable place, the same split the rest of the
 * app uses (see lib/detail, lib/guests-select).
 *
 * NOTHING here adds storage. Per the Guest model's future-compatibility
 * contract, "walk-in", "QR ready", arrival pace, and the arrival feeds are all
 * *derived* from fields that already exist (`attendance.checkedInAt`, `rsvp`,
 * `vip`, `communication.invitationSent`, identity) — Roll Call layers on top of
 * the attendee CRM without reshaping it.
 */

const HOUR_MS = 60 * 60 * 1000;

/** A confirmed RSVP — someone we expect in the room. */
export function isExpected(g: Guest): boolean {
  return g.attendance.rsvp === "confirmed";
}

/**
 * A walk-in: physically arrived without a confirmed RSVP. Derived, never stored
 * — this catches both a tentative/invited guest who shows up and a brand-new
 * attendee captured at the door (who starts life un-confirmed).
 */
export function isWalkIn(g: Guest): boolean {
  return g.attendance.checkedIn && g.attendance.rsvp !== "confirmed";
}

/**
 * Whether a scannable QR credential is ready for this attendee. Derived from
 * `id` + identity per the model contract: a credential is "ready" once the
 * invitation went out to a real email address. Walk-ins added at the door have
 * neither yet, which is exactly when the indicator should read "not ready".
 */
export function qrReady(g: Guest): boolean {
  return g.communication.invitationSent && Boolean(g.identity.email?.trim());
}

/** Parse a check-in timestamp to millis, or null if absent/unparseable. */
function checkedInAtMs(g: Guest): number | null {
  const at = g.attendance.checkedInAt;
  if (!at) return null;
  const ms = Date.parse(at);
  return Number.isNaN(ms) ? null : ms;
}

export interface LiveMetrics {
  /** Roster size (everyone on the list, any RSVP state). */
  total: number;
  /** Confirmed RSVPs — who we expect. */
  expected: number;
  /** Physically in the room. */
  checkedIn: number;
  /** Confirmed guests who have not arrived yet (never negative). */
  remaining: number;
  /** Room capacity, or null when uncapped. */
  capacity: number | null;
  /** checkedIn ÷ expected, as a 0-based int (can exceed 100 with walk-ins). */
  attendancePct: number;
  /** checkedIn ÷ capacity, 0–100, or null when uncapped. */
  capacityPct: number | null;
  /** Arrived without a confirmed RSVP. */
  walkIns: number;
  /** Confirmed but not arrived once the event has ended (0 while live). */
  noShows: number;
  /** VIPs physically in the room. */
  vipCheckedIn: number;
  /** VIPs we still expect (confirmed, not yet arrived). */
  vipExpected: number;
  /** Total VIPs on the roster. */
  vipTotal: number;
  /** Average arrivals per hour since the first check-in (0 if nobody in yet). */
  arrivalRate: number;
}

/**
 * The live metric board. Single pass over the roster so it stays cheap even at
 * 500 attendees. `now` is injected (not read from the clock) so this is pure and
 * testable; `ended` distinguishes remaining-to-arrive from no-shows.
 */
export function liveMetrics(
  guests: Guest[],
  event: CueEvent,
  now: number,
  ended = event.status === "done",
): LiveMetrics {
  let expected = 0;
  let checkedIn = 0;
  let walkIns = 0;
  let vipCheckedIn = 0;
  let vipExpected = 0;
  let vipTotal = 0;
  let earliestArrival = Infinity;

  for (const g of guests) {
    const confirmed = isExpected(g);
    const isIn = g.attendance.checkedIn;
    if (confirmed) expected += 1;
    if (isIn) {
      checkedIn += 1;
      if (!confirmed) walkIns += 1;
      const at = checkedInAtMs(g);
      if (at !== null && at < earliestArrival) earliestArrival = at;
    }
    if (g.vip) {
      vipTotal += 1;
      if (isIn) vipCheckedIn += 1;
      else if (confirmed) vipExpected += 1;
    }
  }

  const remaining = Math.max(0, expected - (checkedIn - walkIns));
  const capacity = isUncapped(event) ? null : (event.capacity.maxAttendees as number);

  // Arrivals per hour, measured from the first check-in to now. Floor the window
  // at one minute so a burst of opening-bell arrivals doesn't divide to infinity.
  let arrivalRate = 0;
  if (checkedIn > 0 && earliestArrival !== Infinity) {
    const elapsedH = Math.max(now - earliestArrival, 60 * 1000) / HOUR_MS;
    arrivalRate = Math.round(checkedIn / elapsedH);
  }

  return {
    total: guests.length,
    expected,
    checkedIn,
    remaining: ended ? 0 : remaining,
    capacity,
    attendancePct: expected === 0 ? 0 : Math.round((checkedIn / expected) * 100),
    capacityPct: capacity && capacity > 0 ? Math.round((checkedIn / capacity) * 100) : null,
    walkIns,
    noShows: ended ? remaining : 0,
    vipCheckedIn,
    vipExpected,
    vipTotal,
    arrivalRate,
  };
}

// --- Arrival feeds ----------------------------------------------------------

/** Checked-in guests, most-recent arrival first. */
export function recentArrivals(guests: Guest[], limit = 12): Guest[] {
  return guests
    .filter((g) => g.attendance.checkedIn)
    .sort((a, b) => (checkedInAtMs(b) ?? 0) - (checkedInAtMs(a) ?? 0))
    .slice(0, limit);
}

/** Checked-in VIPs, most-recent arrival first — the host's greet queue. */
export function vipArrivals(guests: Guest[], limit = 8): Guest[] {
  return recentArrivals(
    guests.filter((g) => g.vip),
    limit,
  );
}

/** Confirmed guests who have not arrived, VIPs first then alphabetical. */
export function notArrived(guests: Guest[]): Guest[] {
  return guests
    .filter((g) => isExpected(g) && !g.attendance.checkedIn)
    .sort(
      (a, b) =>
        Number(b.vip) - Number(a.vip) ||
        a.identity.lastName.localeCompare(b.identity.lastName),
    );
}

// --- Alerts -----------------------------------------------------------------

export type AlertTone = "greet" | "warn" | "info";

export interface RollCallAlert {
  id: string;
  tone: AlertTone;
  title: string;
  detail: string;
}

/** Minutes since a guest checked in, or Infinity if not applicable. */
function minsSinceArrival(g: Guest, now: number): number {
  const at = checkedInAtMs(g);
  return at === null ? Infinity : (now - at) / 60000;
}

/**
 * The live alert stack — a small, deterministic set of operational signals the
 * team should act on, ordered most-urgent first. Pure over the roster + clock.
 */
export function rollCallAlerts(
  guests: Guest[],
  event: CueEvent,
  now: number,
): RollCallAlert[] {
  const m = liveMetrics(guests, event, now);
  const alerts: RollCallAlert[] = [];

  // Capacity pressure.
  if (m.capacity !== null) {
    if (m.checkedIn >= m.capacity) {
      alerts.push({
        id: "at-capacity",
        tone: "warn",
        title: "At capacity",
        detail: `${m.checkedIn} of ${m.capacity} checked in — hold new walk-ins.`,
      });
    } else if (m.checkedIn >= 0.9 * m.capacity) {
      alerts.push({
        id: "near-capacity",
        tone: "warn",
        title: "Approaching capacity",
        detail: `${m.checkedIn} of ${m.capacity} seats filled.`,
      });
    }
  }

  // A VIP just walked in — greet them.
  const freshVip = vipArrivals(guests, 1).find((g) => minsSinceArrival(g, now) <= 10);
  if (freshVip) {
    const co = guestCompany(freshVip, event.portfolio);
    alerts.push({
      id: "vip-arrived",
      tone: "greet",
      title: "VIP just arrived",
      detail: `Greet ${displayName(freshVip)}${co ? ` · ${co}` : ""}.`,
    });
  }

  // VIPs still expected at the door.
  if (m.vipExpected > 0) {
    alerts.push({
      id: "vip-expected",
      tone: "info",
      title: `${m.vipExpected} VIP${m.vipExpected === 1 ? "" : "s"} still expected`,
      detail: "Watch the door and flag arrivals to the host.",
    });
  }

  // Post-event no-shows.
  if (m.noShows > 0) {
    alerts.push({
      id: "no-shows",
      tone: "info",
      title: `${m.noShows} no-show${m.noShows === 1 ? "" : "s"}`,
      detail: "Confirmed guests who never checked in.",
    });
  }

  return alerts;
}

// --- Small presentation helpers (pure) --------------------------------------

/** "6:42 PM" for an arrival timestamp; "" when absent. */
const arrivalTimeFmt = new Intl.DateTimeFormat("en-US", {
  hour: "numeric",
  minute: "2-digit",
});

export function arrivalTime(g: Guest): string {
  const at = checkedInAtMs(g);
  return at === null ? "" : arrivalTimeFmt.format(new Date(at));
}

/** Compact relative arrival, e.g. "just now", "3m ago", "1h ago". */
export function arrivalAgo(g: Guest, now: number): string {
  const mins = minsSinceArrival(g, now);
  if (!Number.isFinite(mins)) return "";
  if (mins < 1) return "just now";
  if (mins < 60) return `${Math.round(mins)}m ago`;
  return `${Math.round(mins / 60)}h ago`;
}

export type { PortfolioCompany };
