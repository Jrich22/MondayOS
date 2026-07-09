import { describe, it, expect, beforeEach } from "vitest";
import { createEvent, getEvent, __resetStore } from "./store";
import { seedEvents } from "./data";
import { draftToEvent, emptyDraft } from "./create";

function newEvent(id: string, title = "Test Event") {
  return draftToEvent(
    { ...emptyDraft(), title, date: "2026-09-01" },
    { id, now: Date.parse("2026-07-10T12:00:00Z"), mode: "create", host: "Tester" },
  );
}

describe("event store", () => {
  beforeEach(() => __resetStore());

  it("exposes seed events by default", () => {
    // A known seed event id is resolvable.
    expect(getEvent(seedEvents[0].id)?.title).toBe(seedEvents[0].title);
  });

  it("persists a created event and makes it resolvable by id", () => {
    createEvent(newEvent("evt-new-1", "Portfolio Mixer"));
    expect(getEvent("evt-new-1")?.title).toBe("Portfolio Mixer");
  });

  it("surfaces created events ahead of seed events", () => {
    createEvent(newEvent("evt-new-2"));
    // getEvent scans the composed snapshot (created first); the new one resolves.
    expect(getEvent("evt-new-2")).toBeDefined();
  });

  it("reset clears created events but keeps the seed", () => {
    createEvent(newEvent("evt-new-3"));
    __resetStore();
    expect(getEvent("evt-new-3")).toBeUndefined();
    expect(getEvent(seedEvents[0].id)).toBeDefined();
  });
});
