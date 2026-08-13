import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fillRatio, relativeDay, STATUS_META } from "./format";
import type { CueEvent } from "./types";

function cap(maxAttendees: number | null): CueEvent["capacity"] {
  return { maxAttendees, rsvpEnabled: true, waitlistEnabled: false };
}

function makeEvent(over: Partial<CueEvent>): CueEvent {
  return {
    id: "e",
    title: "",
    classification: "portfolio",
    summary: "",
    startsAt: "2026-07-10T18:00:00.000Z",
    timezone: "America/Los_Angeles",
    status: "upcoming",
    venue: "",
    city: "",
    host: "",
    capacity: cap(100),
    confirmedGuests: 0,
    invitedGuests: 0,
    branding: { theme: "indigo" },
    portfolio: [],
    tags: [],
    createdAt: "2026-07-01T00:00:00.000Z",
    ...over,
  };
}

describe("fillRatio", () => {
  it("computes confirmed / capacity", () => {
    expect(fillRatio(makeEvent({ capacity: cap(100), confirmedGuests: 25 }))).toBe(0.25);
  });

  it("clamps over-subscription to 1", () => {
    expect(fillRatio(makeEvent({ capacity: cap(10), confirmedGuests: 40 }))).toBe(1);
  });

  it("returns 0 for uncapped events (no divide-by-zero)", () => {
    expect(fillRatio(makeEvent({ capacity: cap(null), confirmedGuests: 5 }))).toBe(0);
    expect(fillRatio(makeEvent({ capacity: cap(0), confirmedGuests: 5 }))).toBe(0);
  });
});

describe("relativeDay", () => {
  beforeEach(() => {
    // Freeze "now" so relative phrasing is deterministic.
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-10T12:00:00.000Z"));
  });
  afterEach(() => vi.useRealTimers());

  it("labels today, tomorrow, yesterday", () => {
    expect(relativeDay("2026-07-10T20:00:00Z")).toBe("Today");
    expect(relativeDay("2026-07-11T12:00:00Z")).toBe("Tomorrow");
    expect(relativeDay("2026-07-09T12:00:00Z")).toBe("Yesterday");
  });

  it("labels multi-day distances in both directions", () => {
    expect(relativeDay("2026-07-16T12:00:00Z")).toBe("in 6 days");
    expect(relativeDay("2026-07-06T12:00:00Z")).toBe("4 days ago");
  });
});

describe("STATUS_META", () => {
  it("has an entry for every status", () => {
    for (const s of ["draft", "upcoming", "live", "done"] as const) {
      expect(STATUS_META[s]?.label).toBeTruthy();
    }
  });
});

import { formatEventDateTz } from "./format";

describe("formatEventDateTz (finding #4)", () => {
  it("renders the event's LOCAL date even when the UTC/browser date differs", () => {
    // 02:00Z on 09-02 is 19:00 on 09-01 in Los Angeles (PDT) → the guest must see Sep 1.
    const s = formatEventDateTz("2026-09-02T02:00:00Z", "America/Los_Angeles");
    expect(s).toMatch(/Sep 1, 2026/);
    expect(s).not.toMatch(/Sep 2/);
    expect(s).toMatch(/PDT/); // includes the zone label
  });
  it("uses the event timezone, not a fixed one", () => {
    const ny = formatEventDateTz("2026-01-15T18:00:00Z", "America/New_York"); // 1:00 PM EST
    expect(ny).toMatch(/EST/);
    const la = formatEventDateTz("2026-01-15T18:00:00Z", "America/Los_Angeles"); // 10:00 AM PST
    expect(la).toMatch(/PST/);
  });
});
