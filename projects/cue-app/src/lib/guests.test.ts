import { describe, it, expect, beforeEach } from "vitest";
import { addGuest, getGuests, updateGuest, removeGuest, __resetGuests } from "./guests";
import { newGuest } from "./guests-select";
import { seedGuests } from "./guests-data";

const NOW = "2026-07-08T18:00:00.000Z";

function guest(eventId: string, id: string, firstName = "Test") {
  const g = newGuest(eventId, id, NOW);
  return { ...g, identity: { ...g.identity, firstName, lastName: "Guest" } };
}

describe("guest store", () => {
  beforeEach(() => __resetGuests());

  it("exposes the seed roster scoped by event", () => {
    const seededEventIds = new Set(seedGuests.map((g) => g.eventId));
    for (const eventId of seededEventIds) {
      const roster = getGuests(eventId);
      expect(roster.length).toBeGreaterThan(0);
      expect(roster.every((g) => g.eventId === eventId)).toBe(true);
    }
  });

  it("does not leak guests across events", () => {
    addGuest(guest("evt-x", "gst-x1"));
    expect(getGuests("evt-x").map((g) => g.id)).toEqual(["gst-x1"]);
    expect(getGuests("evt-y")).toHaveLength(0);
  });

  it("updates an attendee by id", () => {
    addGuest(guest("evt-x", "gst-x1", "Before"));
    const original = getGuests("evt-x")[0];
    updateGuest({ ...original, identity: { ...original.identity, firstName: "After" } });
    expect(getGuests("evt-x")[0].identity.firstName).toBe("After");
  });

  it("removes an attendee by id", () => {
    addGuest(guest("evt-x", "gst-x1"));
    removeGuest("gst-x1");
    expect(getGuests("evt-x")).toHaveLength(0);
  });

  it("reset restores the seed roster", () => {
    addGuest(guest("evt-x", "gst-x1"));
    __resetGuests();
    expect(getGuests("evt-x")).toHaveLength(0);
    expect(getGuests(seedGuests[0].eventId).length).toBeGreaterThan(0);
  });
});
