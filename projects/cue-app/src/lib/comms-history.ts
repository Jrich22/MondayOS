import type { CueEvent, Guest } from "./types";

/**
 * Communication history (TASK-0043) — the per-guest lifecycle timeline.
 *
 * "Every guest eventually has" a chain of comms touchpoints: Invitation →
 * Opened → Clicked → RSVP → Reminder → Checked In → Thank You → Survey. For
 * Sprint 2 this is a *timeline only* (no editing), derived deterministically from
 * the guest record the rest of the app already maintains — their communication
 * flags, RSVP status, and check-in — so the history always agrees with Guest
 * Management and Roll Call rather than duplicating state.
 *
 * Timestamps are synthesized relative to the event start (and the real
 * `checkedInAt` when present) so the timeline reads as a plausible sequence. When
 * a real messaging log lands later, only this derivation changes.
 */

export type TimelineKind =
  | "invitation"
  | "opened"
  | "clicked"
  | "rsvp"
  | "reminder"
  | "checked-in"
  | "thank-you"
  | "survey";

export interface TimelineEntry {
  kind: TimelineKind;
  /** What happened, e.g. "Invitation sent" or "RSVP'd — Confirmed". */
  label: string;
  /** Human relative time, e.g. "3 weeks before" or "12m ago". */
  when: string;
  /** Whether this touchpoint has happened (vs. a pending next-step). */
  done: boolean;
  /** Icon key resolved by the UI. */
  icon: string;
}

const MIN = 60 * 1000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

/** Relative label anchored to the event start: "3 weeks before", "2h after". */
function relToEvent(offsetMs: number, startMs: number): string {
  const t = startMs + offsetMs;
  const delta = t - startMs;
  const abs = Math.abs(delta);
  const dir = delta <= 0 ? "before" : "after";

  if (abs < HOUR) {
    const m = Math.max(1, Math.round(abs / MIN));
    return `${m}m ${dir}`;
  }
  if (abs < DAY) {
    const h = Math.round(abs / HOUR);
    return `${h}h ${dir}`;
  }
  const days = Math.round(abs / DAY);
  if (days < 14) return `${days}d ${dir}`;
  const weeks = Math.round(days / 7);
  return `${weeks} week${weeks === 1 ? "" : "s"} ${dir}`;
}

/** "12m ago" style, for touchpoints anchored to a real timestamp like check-in. */
function ago(fromMs: number, now: number): string {
  const abs = Math.max(0, now - fromMs);
  if (abs < HOUR) return `${Math.max(1, Math.round(abs / MIN))}m ago`;
  if (abs < DAY) return `${Math.round(abs / HOUR)}h ago`;
  return `${Math.round(abs / DAY)}d ago`;
}

const RSVP_LABEL: Record<Guest["attendance"]["rsvp"], string> = {
  confirmed: "Confirmed",
  invited: "No response yet",
  tentative: "Tentative",
  declined: "Declined",
  waitlisted: "Waitlisted",
};

/**
 * The ordered communication timeline for one guest. Entries that have happened
 * are marked `done`; the single most relevant pending next-step is appended so
 * the timeline doubles as a "what's owed" cue. Pure over its inputs.
 */
export function guestTimeline(guest: Guest, event: CueEvent, now: number): TimelineEntry[] {
  const startMs = Date.parse(event.startsAt);
  const entries: TimelineEntry[] = [];
  const comm = guest.communication;
  const att = guest.attendance;
  const rsvp = att.rsvp;

  // Invitation — the anchor. If nothing has been sent, the whole chain is
  // pending, so we surface that as the one actionable next step.
  if (comm.invitationSent) {
    entries.push({
      kind: "invitation",
      label: "Invitation sent",
      when: relToEvent(-28 * DAY, startMs),
      done: true,
      icon: "mail",
    });
    // Opens/clicks are a plausible consequence of an engaged guest (anyone who
    // responded or checked in must have opened it).
    const engaged = rsvp !== "invited" || att.checkedIn;
    if (engaged) {
      entries.push({
        kind: "opened",
        label: "Opened the invitation",
        when: relToEvent(-27 * DAY + 4 * HOUR, startMs),
        done: true,
        icon: "eye",
      });
      if (rsvp === "confirmed" || att.checkedIn) {
        entries.push({
          kind: "clicked",
          label: "Clicked through to RSVP",
          when: relToEvent(-27 * DAY + 5 * HOUR, startMs),
          done: true,
          icon: "cursor",
        });
      }
    }
    // RSVP response.
    if (rsvp !== "invited") {
      entries.push({
        kind: "rsvp",
        label: `RSVP'd — ${RSVP_LABEL[rsvp]}`,
        when: relToEvent(-26 * DAY, startMs),
        done: true,
        icon: rsvp === "declined" ? "close" : "check",
      });
    }
  } else {
    entries.push({
      kind: "invitation",
      label: "Invitation not sent yet",
      when: "Pending",
      done: false,
      icon: "mail",
    });
  }

  // Reminder.
  if (comm.reminderSent) {
    entries.push({
      kind: "reminder",
      label: "RSVP reminder sent",
      when: relToEvent(-14 * DAY, startMs),
      done: true,
      icon: "bell",
    });
  }

  // Check-in — anchored to the real timestamp when present.
  if (att.checkedIn && att.checkedInAt) {
    entries.push({
      kind: "checked-in",
      label: "Checked in at the door",
      when: ago(Date.parse(att.checkedInAt), now),
      done: true,
      icon: "checkin",
    });
  }

  // Thank-you.
  if (comm.thankYouSent) {
    entries.push({
      kind: "thank-you",
      label: "Thank-you sent",
      when: relToEvent(1 * DAY, startMs),
      done: true,
      icon: "heart",
    });
  }

  // The single most relevant pending next-step, appended if the chain isn't done.
  const next = nextStep(guest);
  if (next) entries.push(next);

  return entries;
}

/**
 * The one comms action a guest is "owed" next, or null if the chain is complete
 * for their status. Drives the pending row in the timeline and the "needs
 * attention" hints — a small worklist derived from the same rules.
 */
export function nextStep(guest: Guest): TimelineEntry | null {
  const comm = guest.communication;
  const att = guest.attendance;
  const rsvp = att.rsvp;

  if (!comm.invitationSent) {
    return { kind: "invitation", label: "Send invitation", when: "Next", done: false, icon: "mail" };
  }
  // Awaiting an RSVP and not yet reminded → reminder is owed.
  if ((rsvp === "invited" || rsvp === "tentative") && !comm.reminderSent) {
    return { kind: "reminder", label: "Send RSVP reminder", when: "Next", done: false, icon: "bell" };
  }
  // Attended but not thanked → thank-you is owed.
  if (att.checkedIn && !comm.thankYouSent) {
    return { kind: "thank-you", label: "Send thank-you", when: "Next", done: false, icon: "heart" };
  }
  // Thanked attendee → survey is the natural close.
  if (att.checkedIn && comm.thankYouSent) {
    return { kind: "survey", label: "Send feedback survey", when: "Next", done: false, icon: "clipboard" };
  }
  return null;
}
