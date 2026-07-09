import { describe, it, expect } from "vitest";
import type { CueEvent, Guest, RsvpStatus, GuestRole } from "./types";
import { newGuest } from "./guests-select";
import {
  liveMetrics,
  isWalkIn,
  qrReady,
  recentArrivals,
  vipArrivals,
  notArrived,
  rollCallAlerts,
} from "./rollcall";

const NOW = Date.parse("2026-07-08T19:00:00.000Z");
const MIN = 60 * 1000;

function makeEvent(over: Partial<CueEvent> = {}): CueEvent {
  return {
    id: "evt-1",
    title: "Test",
    classification: "founder",
    summary: "",
    startsAt: "2026-07-08T18:00:00.000Z",
    timezone: "America/Los_Angeles",
    status: "live",
    venue: "",
    city: "",
    host: "",
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

interface GOpts {
  rsvp?: RsvpStatus;
  vip?: boolean;
  roles?: GuestRole[];
  checkedInMinsAgo?: number;
  email?: string;
  invited?: boolean;
}

let seq = 0;
function g(opts: GOpts = {}): Guest {
  seq += 1;
  const base = newGuest("evt-1", `g-${seq}`, "2026-07-01T00:00:00.000Z");
  return {
    ...base,
    identity: { firstName: `First${seq}`, lastName: `Last${seq}`, email: opts.email ?? `p${seq}@x.com` },
    roles: opts.roles ?? [],
    vip: opts.vip ?? false,
    attendance: {
      ...base.attendance,
      rsvp: opts.rsvp ?? "confirmed",
      checkedIn: opts.checkedInMinsAgo !== undefined,
      checkedInAt:
        opts.checkedInMinsAgo !== undefined
          ? new Date(NOW - opts.checkedInMinsAgo * MIN).toISOString()
          : undefined,
    },
    communication: { ...base.communication, invitationSent: opts.invited ?? true },
  };
}

describe("liveMetrics", () => {
  it("counts checked-in, remaining, and attendance off the live roster", () => {
    const roster = [
      g({ rsvp: "confirmed", checkedInMinsAgo: 30 }),
      g({ rsvp: "confirmed", checkedInMinsAgo: 10 }),
      g({ rsvp: "confirmed" }), // expected, not arrived
      g({ rsvp: "invited" }), // not expected, not arrived
    ];
    const m = liveMetrics(roster, makeEvent(), NOW);
    expect(m).toMatchObject({ expected: 3, checkedIn: 2, remaining: 1, total: 4 });
    expect(m.attendancePct).toBe(67);
  });

  it("treats an arrival without a confirmed RSVP as a walk-in", () => {
    const roster = [
      g({ rsvp: "confirmed", checkedInMinsAgo: 5 }),
      g({ rsvp: "invited", checkedInMinsAgo: 2 }), // walk-in
      g({ rsvp: "tentative", checkedInMinsAgo: 1 }), // walk-in
    ];
    const m = liveMetrics(roster, makeEvent(), NOW);
    expect(m.walkIns).toBe(2);
    expect(m.checkedIn).toBe(3);
    // remaining counts only confirmed-not-arrived, so walk-ins don't distort it
    expect(m.remaining).toBe(0);
  });

  it("converts remaining confirmed into no-shows once ended", () => {
    const roster = [
      g({ rsvp: "confirmed", checkedInMinsAgo: 40 }),
      g({ rsvp: "confirmed" }),
      g({ rsvp: "confirmed" }),
    ];
    const m = liveMetrics(roster, makeEvent({ status: "done" }), NOW, true);
    expect(m.remaining).toBe(0);
    expect(m.noShows).toBe(2);
  });

  it("tracks VIP arrivals separately from the general count", () => {
    const roster = [
      g({ vip: true, checkedInMinsAgo: 5 }),
      g({ vip: true }), // expected VIP, not here
      g({ vip: false, checkedInMinsAgo: 5 }),
    ];
    const m = liveMetrics(roster, makeEvent(), NOW);
    expect(m).toMatchObject({ vipTotal: 2, vipCheckedIn: 1, vipExpected: 1 });
  });

  it("reports capacity fill and null for uncapped events", () => {
    const roster = [g({ checkedInMinsAgo: 1 }), g({ checkedInMinsAgo: 1 })];
    expect(liveMetrics(roster, makeEvent({ capacity: { maxAttendees: 10, rsvpEnabled: true, waitlistEnabled: false } }), NOW).capacityPct).toBe(20);
    expect(liveMetrics(roster, makeEvent({ capacity: { maxAttendees: null, rsvpEnabled: true, waitlistEnabled: false } }), NOW).capacity).toBeNull();
  });

  it("computes a sane arrival rate from the first check-in", () => {
    // 4 arrivals across the last hour → ~4/hr.
    const roster = [
      g({ checkedInMinsAgo: 60 }),
      g({ checkedInMinsAgo: 40 }),
      g({ checkedInMinsAgo: 20 }),
      g({ checkedInMinsAgo: 1 }),
    ];
    expect(liveMetrics(roster, makeEvent(), NOW).arrivalRate).toBe(4);
  });

  it("returns zeroes for an empty roster without dividing by zero", () => {
    const m = liveMetrics([], makeEvent(), NOW);
    expect(m).toMatchObject({ total: 0, expected: 0, checkedIn: 0, attendancePct: 0, arrivalRate: 0 });
  });
});

describe("derived flags", () => {
  it("isWalkIn is true only for un-confirmed arrivals", () => {
    expect(isWalkIn(g({ rsvp: "invited", checkedInMinsAgo: 1 }))).toBe(true);
    expect(isWalkIn(g({ rsvp: "confirmed", checkedInMinsAgo: 1 }))).toBe(false);
    expect(isWalkIn(g({ rsvp: "invited" }))).toBe(false);
  });

  it("qrReady requires an invitation to a real email", () => {
    expect(qrReady(g({ invited: true, email: "a@b.com" }))).toBe(true);
    expect(qrReady(g({ invited: false, email: "a@b.com" }))).toBe(false);
    expect(qrReady(g({ invited: true, email: "" }))).toBe(false);
  });
});

describe("arrival feeds", () => {
  it("recentArrivals is newest-first and only checked-in", () => {
    const older = g({ checkedInMinsAgo: 30 });
    const newer = g({ checkedInMinsAgo: 2 });
    const absent = g({});
    const feed = recentArrivals([older, absent, newer]);
    expect(feed.map((x) => x.id)).toEqual([newer.id, older.id]);
  });

  it("vipArrivals only surfaces checked-in VIPs", () => {
    const vipIn = g({ vip: true, checkedInMinsAgo: 3 });
    const vipOut = g({ vip: true });
    const plebIn = g({ vip: false, checkedInMinsAgo: 1 });
    expect(vipArrivals([vipIn, vipOut, plebIn]).map((x) => x.id)).toEqual([vipIn.id]);
  });

  it("notArrived lists confirmed absentees, VIPs first", () => {
    const vip = g({ vip: true, rsvp: "confirmed" });
    const reg = g({ vip: false, rsvp: "confirmed" });
    const here = g({ rsvp: "confirmed", checkedInMinsAgo: 1 });
    const list = notArrived([reg, here, vip]);
    expect(list.map((x) => x.id)).toEqual([vip.id, reg.id]);
  });
});

describe("rollCallAlerts", () => {
  it("raises a greet alert for a VIP who just arrived", () => {
    const alerts = rollCallAlerts([g({ vip: true, checkedInMinsAgo: 2 })], makeEvent(), NOW);
    expect(alerts.some((a) => a.id === "vip-arrived" && a.tone === "greet")).toBe(true);
  });

  it("warns when approaching capacity", () => {
    const roster = Array.from({ length: 19 }, () => g({ checkedInMinsAgo: 5 }));
    const alerts = rollCallAlerts(roster, makeEvent({ capacity: { maxAttendees: 20, rsvpEnabled: true, waitlistEnabled: false } }), NOW);
    expect(alerts.some((a) => a.id === "near-capacity")).toBe(true);
  });

  it("flags VIPs still expected", () => {
    const alerts = rollCallAlerts([g({ vip: true, rsvp: "confirmed" })], makeEvent(), NOW);
    expect(alerts.some((a) => a.id === "vip-expected")).toBe(true);
  });
});
