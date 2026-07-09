import { describe, it, expect } from "vitest";
import { selectEvents, sortEvents, matchesQuery, type EventFilter } from "./select";
import type { CueEvent, EventStatus } from "./types";

/** Minimal event factory so each test states only the fields it cares about. */
function makeEvent(over: Partial<CueEvent> & { id: string }): CueEvent {
  return {
    title: "Untitled",
    classification: "portfolio",
    summary: "",
    startsAt: "2026-07-10T18:00:00.000Z",
    timezone: "America/Los_Angeles",
    status: "upcoming",
    venue: "Somewhere",
    city: "San Francisco",
    host: "Host",
    capacity: { maxAttendees: 100, rsvpEnabled: true, waitlistEnabled: false },
    confirmedGuests: 0,
    invitedGuests: 0,
    branding: { theme: "indigo" },
    portfolio: [],
    tags: [],
    createdAt: "2026-07-01T00:00:00.000Z",
    ...over,
  };
}

describe("sortEvents", () => {
  it("orders by operational urgency: live → upcoming → draft → done", () => {
    const list = [
      makeEvent({ id: "d", status: "done" }),
      makeEvent({ id: "u", status: "upcoming" }),
      makeEvent({ id: "l", status: "live" }),
      makeEvent({ id: "f", status: "draft" }),
    ];
    expect(sortEvents(list).map((e) => e.id)).toEqual(["l", "u", "f", "d"]);
  });

  it("breaks ties within a status by start time (earliest first)", () => {
    const list = [
      makeEvent({ id: "late", status: "upcoming", startsAt: "2026-08-01T18:00:00Z" }),
      makeEvent({ id: "soon", status: "upcoming", startsAt: "2026-07-11T18:00:00Z" }),
    ];
    expect(sortEvents(list).map((e) => e.id)).toEqual(["soon", "late"]);
  });

  it("does not mutate the input array", () => {
    const list = [makeEvent({ id: "a", status: "done" }), makeEvent({ id: "b", status: "live" })];
    const before = list.map((e) => e.id);
    sortEvents(list);
    expect(list.map((e) => e.id)).toEqual(before);
  });
});

describe("matchesQuery", () => {
  const event = makeEvent({
    id: "e",
    title: "Founder Summit",
    venue: "Pier 27",
    city: "San Francisco",
    tags: ["flagship"],
    portfolio: [{ id: "c", name: "Northwind", stage: "Series A" }],
  });

  it("matches an empty query", () => {
    expect(matchesQuery(event, "")).toBe(true);
    expect(matchesQuery(event, "   ")).toBe(true);
  });

  it("matches case-insensitively across title, venue, tags and portfolio", () => {
    expect(matchesQuery(event, "founder")).toBe(true);
    expect(matchesQuery(event, "PIER")).toBe(true);
    expect(matchesQuery(event, "flagship")).toBe(true);
    expect(matchesQuery(event, "northwind")).toBe(true);
  });

  it("returns false when nothing matches", () => {
    expect(matchesQuery(event, "zzz-nope")).toBe(false);
  });
});

describe("selectEvents", () => {
  const list: CueEvent[] = [
    makeEvent({ id: "live", status: "live", title: "AI Roundtable" }),
    makeEvent({ id: "up", status: "upcoming", title: "LP Briefing" }),
    makeEvent({ id: "done", status: "done", title: "Recap Dinner" }),
  ];

  it("filters by status and keeps sort order", () => {
    const filters: EventFilter[] = ["upcoming"];
    for (const f of filters) {
      const out = selectEvents(list, f, "");
      expect(out.every((e) => e.status === (f as EventStatus))).toBe(true);
    }
  });

  it("returns everything for the 'all' filter", () => {
    expect(selectEvents(list, "all", "")).toHaveLength(3);
  });

  it("combines filter and query", () => {
    expect(selectEvents(list, "all", "roundtable").map((e) => e.id)).toEqual(["live"]);
    expect(selectEvents(list, "done", "roundtable")).toHaveLength(0);
  });
});
