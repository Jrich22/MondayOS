import { describe, it, expect } from "vitest";
import { rsvpSummary, rollCallStats, eventHealth, daysUntil } from "./detail";
import type { CueEvent } from "./types";

function cap(maxAttendees: number | null): CueEvent["capacity"] {
  return { maxAttendees, rsvpEnabled: true, waitlistEnabled: false };
}

function makeEvent(over: Partial<CueEvent>): CueEvent {
  return {
    id: "e",
    title: "Test",
    classification: "portfolio",
    summary: "",
    startsAt: "2026-07-20T18:00:00.000Z",
    timezone: "America/Los_Angeles",
    status: "upcoming",
    venue: "",
    city: "",
    host: "",
    capacity: cap(100),
    confirmedGuests: 50,
    invitedGuests: 80,
    branding: { theme: "indigo" },
    portfolio: [],
    tags: [],
    createdAt: "2026-07-01T00:00:00.000Z",
    ...over,
  };
}

describe("rsvpSummary", () => {
  it("computes pending and rate", () => {
    const s = rsvpSummary(makeEvent({ confirmedGuests: 40, invitedGuests: 100 }));
    expect(s).toMatchObject({ confirmed: 40, invited: 100, pending: 60, ratePct: 40 });
  });

  it("handles nobody invited without dividing by zero", () => {
    expect(rsvpSummary(makeEvent({ confirmedGuests: 0, invitedGuests: 0 }))).toMatchObject({
      pending: 0,
      ratePct: 0,
    });
  });

  it("never reports negative pending", () => {
    expect(rsvpSummary(makeEvent({ confirmedGuests: 90, invitedGuests: 80 })).pending).toBe(0);
  });
});

describe("rollCallStats", () => {
  it("defaults to zero checked-in with everyone remaining before the event", () => {
    const s = rollCallStats(makeEvent({ confirmedGuests: 30 }), 0, false);
    expect(s).toMatchObject({ checkedIn: 0, expected: 30, remaining: 30, noShows: 0 });
    expect(s.attendancePct).toBe(0);
  });

  it("converts remaining into no-shows once the event has ended", () => {
    const s = rollCallStats(makeEvent({ confirmedGuests: 30 }), 20, true);
    expect(s.remaining).toBe(0);
    expect(s.noShows).toBe(10);
    expect(s.attendancePct).toBe(67);
  });

  it("clamps check-ins to the expected count", () => {
    const s = rollCallStats(makeEvent({ confirmedGuests: 10 }), 999, false);
    expect(s.checkedIn).toBe(10);
    expect(s.attendancePct).toBe(100);
  });

  it("reports null capacity for uncapped events", () => {
    expect(rollCallStats(makeEvent({ capacity: cap(null) })).capacity).toBeNull();
  });
});

describe("eventHealth", () => {
  const now = Date.parse("2026-07-10T12:00:00Z");

  it("returns four cards", () => {
    expect(eventHealth(makeEvent({}), now)).toHaveLength(4);
  });

  it("marks a live event's timing as good", () => {
    const cards = eventHealth(makeEvent({ status: "live" }), now);
    expect(cards[0]).toMatchObject({ label: "Timing", value: "Live now", tone: "good" });
  });

  it("flags a draft's readiness as a warning", () => {
    const readiness = eventHealth(makeEvent({ status: "draft" }), now).find(
      (c) => c.label === "Readiness",
    );
    expect(readiness?.tone).toBe("warn");
  });
});

describe("daysUntil", () => {
  it("is positive before the event and negative after", () => {
    const now = Date.parse("2026-07-10T18:00:00Z");
    expect(daysUntil(makeEvent({ startsAt: "2026-07-20T18:00:00Z" }), now)).toBe(10);
    expect(daysUntil(makeEvent({ startsAt: "2026-07-05T18:00:00Z" }), now)).toBe(-5);
  });
});
