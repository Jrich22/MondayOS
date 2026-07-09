import { describe, it, expect } from "vitest";
import { seedGuests } from "./guests-data";
import { seedEvents } from "./data";
import { liveMetrics, isWalkIn } from "./rollcall";

/**
 * Guards the flagship Roll Call demo (TASK-0040): the live Summit must ship a
 * large roster with a lively mix so the command center is never empty on open.
 * The roster is generated with a seeded PRNG, so these counts are deterministic.
 */
describe("live Summit demo roster", () => {
  const summit = seedGuests.filter((g) => g.eventId === "evt-2041");
  const event = seedEvents.find((e) => e.id === "evt-2041")!;

  it("is at command-center scale", () => {
    expect(summit.length).toBeGreaterThan(450);
  });

  it("has a healthy live mix of arrivals, VIPs, and walk-ins", () => {
    const m = liveMetrics(summit, event, Date.now());
    expect(m.checkedIn).toBeGreaterThan(80);
    expect(m.remaining).toBeGreaterThan(40);
    expect(m.vipTotal).toBeGreaterThan(5);
    expect(m.vipCheckedIn).toBeGreaterThan(0);
    expect(summit.some(isWalkIn)).toBe(true);
    expect(m.arrivalRate).toBeGreaterThan(0);
  });

  it("the live event it belongs to is actually marked live", () => {
    expect(event.status).toBe("live");
  });
});
