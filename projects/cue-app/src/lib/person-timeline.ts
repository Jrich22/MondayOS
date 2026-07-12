import type { Person, PersonAppearance } from "./people";

/**
 * The relationship timeline (TASK-0044) — a person's chronological history
 * across every event, folded from their appearances. Each event contributes a
 * run of entries following the interaction funnel (Invited → RSVP → Checked In →
 * VIP → Speaker → Met someone → Thank-you → Survey), and future interactions
 * simply append as new events land on their record.
 *
 * Pure over the projected `Person` (+ the wider people set, so "Met someone" can
 * name a real co-attendee). No timestamps are invented beyond the ones already
 * on the record: entries anchor to the event start (or the actual check-in time),
 * and a stable phase rank orders the funnel within a single event.
 */

export type TimelineKind =
  | "invited"
  | "rsvp"
  | "checkedin"
  | "vip"
  | "speaker"
  | "met"
  | "thankyou"
  | "survey";

export interface TimelineEntry {
  id: string;
  eventId: string;
  eventTitle: string;
  /** ISO anchor (event start, or check-in time for the arrival entry). */
  at: string;
  kind: TimelineKind;
  label: string;
  detail?: string;
}

/** Funnel order within a single event, so a day reads top-to-bottom. */
const PHASE_RANK: Record<TimelineKind, number> = {
  invited: 0,
  rsvp: 1,
  checkedin: 2,
  vip: 3,
  speaker: 4,
  met: 5,
  thankyou: 6,
  survey: 7,
};

const RSVP_LABEL: Record<PersonAppearance["rsvp"], string> = {
  confirmed: "RSVP'd — Confirmed",
  invited: "Invitation pending",
  tentative: "RSVP'd — Tentative",
  waitlisted: "Waitlisted",
  declined: "RSVP'd — Declined",
};

/**
 * The most notable other attendee `person` could have met at an event: the
 * highest-signal co-attendee (VIP first, then most-connected) who was also on
 * that event. Returns undefined when nobody else was there.
 */
function metAt(
  eventId: string,
  person: Person,
  people: Person[],
): Person | undefined {
  const candidates = people.filter(
    (p) => p.id !== person.id && p.appearances.some((a) => a.eventId === eventId),
  );
  if (candidates.length === 0) return undefined;
  candidates.sort(
    (a, b) =>
      Number(b.vip) - Number(a.vip) ||
      b.eventsInvited - a.eventsInvited ||
      a.lastName.localeCompare(b.lastName),
  );
  return candidates[0];
}

/**
 * Build a person's full timeline, most recent first. `people` is used only to
 * name a plausible "Met someone" connection per event; pass an empty array to
 * skip those entries.
 */
export function personTimeline(person: Person, people: Person[] = []): TimelineEntry[] {
  const entries: TimelineEntry[] = [];

  for (const a of person.appearances) {
    const base = { eventId: a.eventId, eventTitle: a.eventTitle };

    // Being on an event's roster at all means they were invited.
    entries.push({
      ...base,
      id: `${a.eventId}-invited`,
      at: a.startsAt,
      kind: "invited",
      label: "Invited",
    });

    entries.push({
      ...base,
      id: `${a.eventId}-rsvp`,
      at: a.startsAt,
      kind: "rsvp",
      label: RSVP_LABEL[a.rsvp],
    });

    if (a.checkedIn) {
      entries.push({
        ...base,
        id: `${a.eventId}-checkedin`,
        at: a.checkedInAt ?? a.startsAt,
        kind: "checkedin",
        label: "Checked in",
      });
    }

    if (a.vip) {
      entries.push({
        ...base,
        id: `${a.eventId}-vip`,
        at: a.startsAt,
        kind: "vip",
        label: "Hosted as VIP",
      });
    }

    if (a.isSpeaker) {
      entries.push({
        ...base,
        id: `${a.eventId}-speaker`,
        at: a.startsAt,
        kind: "speaker",
        label: "Spoke at the event",
      });
    }

    if (a.checkedIn) {
      const met = metAt(a.eventId, person, people);
      if (met) {
        entries.push({
          ...base,
          id: `${a.eventId}-met`,
          at: a.startsAt,
          kind: "met",
          label: `Connected with ${met.displayName}`,
          detail: met.company || undefined,
        });
      }
    }

    if (a.thankYouSent) {
      entries.push({
        ...base,
        id: `${a.eventId}-thankyou`,
        at: a.startsAt,
        kind: "thankyou",
        label: "Thank-you sent",
      });
    }

    // Post-event survey follow-up for wrapped events they attended.
    if (a.eventStatus === "done" && a.checkedIn) {
      entries.push({
        ...base,
        id: `${a.eventId}-survey`,
        at: a.startsAt,
        kind: "survey",
        label: "Post-event survey",
      });
    }
  }

  // Newest event first; within an event, funnel order. Sort on the event start
  // (not each entry's own `at`) so a later check-in time never reorders the
  // funnel within its own event.
  const startById = new Map(person.appearances.map((a) => [a.eventId, a.startsAt]));
  entries.sort((a, b) => {
    const ea = Date.parse(startById.get(a.eventId) ?? a.at);
    const eb = Date.parse(startById.get(b.eventId) ?? b.at);
    if (ea !== eb) return eb - ea;
    return PHASE_RANK[a.kind] - PHASE_RANK[b.kind];
  });
  return entries;
}
