import { describe, it, expect } from "vitest";
import type { CueEvent, Guest } from "./types";
import { newGuest } from "./guests-select";
import { guestTimeline, nextStep, type TimelineEntry, type TimelineKind } from "./comms-history";

function last(entries: TimelineEntry[]): TimelineEntry {
  return entries[entries.length - 1];
}

const START = "2026-08-01T18:00:00.000Z";
const NOW = Date.parse("2026-08-01T19:00:00.000Z"); // an hour into the event

const event: CueEvent = {
  id: "evt-1",
  title: "Summit",
  classification: "founder",
  summary: "",
  startsAt: START,
  timezone: "America/Los_Angeles",
  status: "live",
  venue: "Pier 27",
  city: "SF",
  host: "Dana",
  capacity: { maxAttendees: 500, rsvpEnabled: true, waitlistEnabled: true },
  confirmedGuests: 1,
  invitedGuests: 1,
  branding: { theme: "indigo" },
  portfolio: [],
  tags: [],
  createdAt: "2026-07-01T00:00:00.000Z",
};

function make(over: Partial<Guest>): Guest {
  const g = newGuest("evt-1", over.id ?? "g", "2026-07-01T00:00:00.000Z");
  return {
    ...g,
    ...over,
    attendance: { ...g.attendance, ...(over.attendance ?? {}) },
    communication: { ...g.communication, ...(over.communication ?? {}) },
  };
}

function kinds(entries: { kind: TimelineKind }[]): TimelineKind[] {
  return entries.map((e) => e.kind);
}

describe("guestTimeline", () => {
  it("a fully-engaged attendee has the whole chain, marked done", () => {
    const g = make({
      id: "g1",
      vip: true,
      attendance: { ...make({}).attendance, rsvp: "confirmed", checkedIn: true, checkedInAt: "2026-08-01T18:20:00.000Z" },
      communication: { invitationSent: true, reminderSent: true, thankYouSent: true },
    });
    const tl = guestTimeline(g, event, NOW);
    expect(kinds(tl)).toEqual(
      expect.arrayContaining(["invitation", "opened", "clicked", "rsvp", "reminder", "checked-in", "thank-you"]),
    );
    // Everything sent should be done; after thank-you the next step is a survey.
    expect(tl.filter((e) => e.kind !== "survey").every((e) => e.done)).toBe(true);
    expect(last(tl).kind).toBe("survey");
    expect(last(tl).done).toBe(false);
  });

  it("an un-invited guest surfaces the send-invitation next step", () => {
    const g = make({ id: "g2", communication: { invitationSent: false, reminderSent: false, thankYouSent: false } });
    const tl = guestTimeline(g, event, NOW);
    expect(tl[0].kind).toBe("invitation");
    expect(tl[0].done).toBe(false);
    // The single pending action is to send the invitation.
    expect(tl.some((e) => !e.done && e.label === "Send invitation")).toBe(true);
  });

  it("an invited-but-silent guest shows no opened/clicked and is owed a reminder", () => {
    const g = make({
      id: "g3",
      attendance: { ...make({}).attendance, rsvp: "invited" },
      communication: { invitationSent: true, reminderSent: false, thankYouSent: false },
    });
    const tl = guestTimeline(g, event, NOW);
    expect(kinds(tl)).toContain("invitation");
    expect(kinds(tl)).not.toContain("opened");
    expect(kinds(tl)).not.toContain("rsvp");
    expect(last(tl).kind).toBe("reminder");
    expect(last(tl).done).toBe(false);
  });

  it("check-in uses the real timestamp for a relative label", () => {
    const g = make({
      id: "g4",
      attendance: { ...make({}).attendance, rsvp: "confirmed", checkedIn: true, checkedInAt: "2026-08-01T18:40:00.000Z" },
      communication: { invitationSent: true, reminderSent: false, thankYouSent: false },
    });
    const tl = guestTimeline(g, event, NOW);
    const checkin = tl.find((e) => e.kind === "checked-in");
    expect(checkin?.when).toBe("20m ago");
  });
});

describe("nextStep", () => {
  const base = make({ id: "n" });

  it("owes an invitation when none was sent", () => {
    expect(nextStep(make({ communication: { invitationSent: false, reminderSent: false, thankYouSent: false } }))?.label).toBe(
      "Send invitation",
    );
  });

  it("owes a reminder to a silent invitee", () => {
    const g = make({
      attendance: { ...base.attendance, rsvp: "invited" },
      communication: { invitationSent: true, reminderSent: false, thankYouSent: false },
    });
    expect(nextStep(g)?.label).toBe("Send RSVP reminder");
  });

  it("owes a thank-you to an attendee who wasn't thanked", () => {
    const g = make({
      attendance: { ...base.attendance, rsvp: "confirmed", checkedIn: true },
      communication: { invitationSent: true, reminderSent: true, thankYouSent: false },
    });
    expect(nextStep(g)?.label).toBe("Send thank-you");
  });

  it("owes a survey to a thanked attendee", () => {
    const g = make({
      attendance: { ...base.attendance, rsvp: "confirmed", checkedIn: true },
      communication: { invitationSent: true, reminderSent: true, thankYouSent: true },
    });
    expect(nextStep(g)?.label).toBe("Send feedback survey");
  });

  it("owes nothing to a confirmed guest who hasn't attended yet and was reminded", () => {
    const g = make({
      attendance: { ...base.attendance, rsvp: "confirmed", checkedIn: false },
      communication: { invitationSent: true, reminderSent: true, thankYouSent: false },
    });
    expect(nextStep(g)).toBeNull();
  });
});
