import type { CueEvent } from "./types";
import { fillRatio, isUncapped } from "./format";

/**
 * Pure derivations for the Event Detail command center (TASK-0034). Kept out of
 * the components so the operational math — RSVP pacing, roll-call attendance,
 * health signals — is unit-testable and lives in one place.
 */

export interface RsvpSummary {
  confirmed: number;
  invited: number;
  /** Invited but not yet confirmed. */
  pending: number;
  /** Confirmed ÷ invited, as a 0–100 int (0 when nobody is invited yet). */
  ratePct: number;
}

export function rsvpSummary(event: CueEvent): RsvpSummary {
  const confirmed = Math.max(0, event.confirmedGuests);
  const invited = Math.max(0, event.invitedGuests);
  const pending = Math.max(0, invited - confirmed);
  const ratePct = invited === 0 ? 0 : Math.round((confirmed / invited) * 100);
  return { confirmed, invited, pending, ratePct };
}

export interface RollCallStats {
  checkedIn: number;
  /** Who we expect in the room (confirmed RSVPs). */
  expected: number;
  /** Expected minus checked-in (never negative). */
  remaining: number;
  /** Confirmed who have not checked in once the event is over. */
  noShows: number;
  /** Room capacity, or null when uncapped. */
  capacity: number | null;
  /** checkedIn ÷ expected, 0–100 int. */
  attendancePct: number;
}

/**
 * Roll-call attendance. `checkedIn` defaults to 0 (the Sprint-1 shell has no
 * live check-in yet); `ended` distinguishes remaining-to-arrive from no-shows.
 */
export function rollCallStats(
  event: CueEvent,
  checkedIn = 0,
  ended = event.status === "done",
): RollCallStats {
  const expected = Math.max(0, event.confirmedGuests);
  const safeChecked = Math.min(Math.max(0, checkedIn), expected);
  const remaining = Math.max(0, expected - safeChecked);
  return {
    checkedIn: safeChecked,
    expected,
    remaining: ended ? 0 : remaining,
    noShows: ended ? remaining : 0,
    capacity: isUncapped(event) ? null : (event.capacity.maxAttendees as number),
    attendancePct: expected === 0 ? 0 : Math.round((safeChecked / expected) * 100),
  };
}

export type HealthTone = "good" | "warn" | "neutral";

export interface HealthCard {
  label: string;
  value: string;
  hint: string;
  tone: HealthTone;
}

const DAY = 24 * 60 * 60 * 1000;

/** Whole days from `now` until the event start (negative once past). */
export function daysUntil(event: CueEvent, now: number): number {
  return Math.round((new Date(event.startsAt).getTime() - now) / DAY);
}

/**
 * A small set of at-a-glance health signals for the Overview tab. Each is
 * derived from the event's real state so the cards mean something on day one.
 */
export function eventHealth(event: CueEvent, now: number): HealthCard[] {
  const days = daysUntil(event, now);
  const { pending, ratePct } = rsvpSummary(event);
  const fill = Math.round(fillRatio(event) * 100);

  // Countdown
  const countdown: HealthCard =
    event.status === "live"
      ? { label: "Timing", value: "Live now", hint: "Happening today", tone: "good" }
      : event.status === "done"
        ? { label: "Timing", value: "Wrapped", hint: "Event has ended", tone: "neutral" }
        : {
            label: "Timing",
            value: days <= 0 ? "Today" : `${days}d to go`,
            hint: days <= 7 ? "Final stretch" : "On the calendar",
            tone: days <= 3 && event.status !== "draft" ? "warn" : "neutral",
          };

  // RSVP pace
  const rsvp: HealthCard = {
    label: "RSVP rate",
    value: `${ratePct}%`,
    hint: pending > 0 ? `${pending} pending` : "All responded",
    tone: ratePct >= 60 ? "good" : pending > 0 && days <= 7 ? "warn" : "neutral",
  };

  // Capacity
  const capacity: HealthCard = isUncapped(event)
    ? { label: "Capacity", value: "No cap", hint: "Unlimited room", tone: "neutral" }
    : {
        label: "Capacity",
        value: `${fill}% full`,
        hint: `${event.confirmedGuests} of ${event.capacity.maxAttendees}`,
        tone: fill >= 60 ? "good" : fill < 30 && event.status !== "draft" ? "warn" : "neutral",
      };

  // Readiness (Sprint-1 shell: agenda/guests not wired yet)
  const readiness: HealthCard = {
    label: "Readiness",
    value: event.status === "draft" ? "Draft" : "In progress",
    hint: event.status === "draft" ? "Not published" : "Agenda & guests pending",
    tone: event.status === "draft" ? "warn" : "neutral",
  };

  return [countdown, rsvp, capacity, readiness];
}
