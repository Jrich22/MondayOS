import { describe, it, expect } from "vitest";
import type { CueEvent, Guest, GuestAttendance, RsvpStatus } from "./types";
import {
  emptyPlan,
  eventDayCount,
  capacityDemand,
  readiness,
  currentMember,
  type EventPlan,
  type Milestone,
  type Responsibility,
  type Risk,
} from "./planning";

const NOW = Date.parse("2026-08-15T00:00:00Z");

function makeEvent(over: Partial<CueEvent> = {}): CueEvent {
  return {
    id: "evt-1",
    title: "Portfolio Summit",
    classification: "portfolio",
    summary: "",
    startsAt: "2026-09-01T18:00:00.000Z",
    endsAt: "2026-09-01T21:00:00.000Z",
    timezone: "America/Los_Angeles",
    status: "upcoming",
    venue: "HQ",
    city: "SF",
    host: "Dana",
    capacity: { maxAttendees: null, rsvpEnabled: true, waitlistEnabled: false },
    confirmedGuests: 0,
    invitedGuests: 0,
    branding: { theme: "default" },
    portfolio: [],
    tags: [],
    createdAt: "2026-08-01T00:00:00.000Z",
    ...over,
  };
}

let gid = 0;
function makeGuest(rsvp: RsvpStatus, extra: Partial<GuestAttendance> = {}): Guest {
  gid += 1;
  return {
    id: `g-${gid}`,
    eventId: "evt-1",
    identity: { firstName: "A", lastName: `${gid}` },
    professional: {},
    roles: [],
    vip: false,
    attendance: {
      rsvp,
      checkedIn: false,
      noShow: false,
      waitlisted: rsvp === "waitlisted",
      plusOnes: 0,
      ...extra,
    },
    preferences: { hotelNeeded: false, transportationNeeded: false },
    communication: { invitationSent: false, reminderSent: false, thankYouSent: false },
    notes: {},
    tags: [],
    seat: null,
    createdAt: "",
    updatedAt: "",
  };
}

function confirmed(n: number, plusOnes = 0): Guest[] {
  return Array.from({ length: n }, () => makeGuest("confirmed", { plusOnes }));
}

function basePlan(over: Partial<EventPlan> = {}): EventPlan {
  return { ...emptyPlan(makeEvent(), NOW), ...over };
}

function cat(report: ReturnType<typeof readiness>, key: string) {
  const c = report.categories.find((x) => x.key === key);
  if (!c) throw new Error(`missing category ${key}`);
  return c;
}

// ---------------------------------------------------------------------------
// Plan derivation & multi-day
// ---------------------------------------------------------------------------

describe("emptyPlan / eventDayCount", () => {
  it("seeds a single day for a same-day event, owned by the current member", () => {
    const plan = emptyPlan(makeEvent(), NOW);
    expect(plan.days).toHaveLength(1);
    expect(plan.days[0].date).toBe("2026-09-01");
    expect(plan.ownerId).toBe(currentMember().id);
    expect(plan.members).toHaveLength(1);
  });

  it("seeds one PlanningDay per calendar day for a multi-day event", () => {
    const ev = makeEvent({ startsAt: "2026-09-01T18:00:00Z", endsAt: "2026-09-03T02:00:00Z" });
    expect(eventDayCount(ev)).toBe(3);
    const plan = emptyPlan(ev, NOW);
    expect(plan.days.map((d) => d.date)).toEqual(["2026-09-01", "2026-09-02", "2026-09-03"]);
    expect(plan.days.map((d) => d.label)).toEqual(["Day 1", "Day 2", "Day 3"]);
  });

  it("is deterministic (stable ids seeded from eventId)", () => {
    expect(emptyPlan(makeEvent(), NOW).days[0].id).toBe(emptyPlan(makeEvent(), NOW + 5000).days[0].id);
  });
});

// ---------------------------------------------------------------------------
// Capacity vs. demand (DEC-0009 §3)
// ---------------------------------------------------------------------------

describe("capacityDemand", () => {
  it("is not-applicable and never warns when uncapped", () => {
    const cd = capacityDemand(makeEvent({ capacity: { maxAttendees: null, rsvpEnabled: true, waitlistEnabled: false } }), confirmed(50));
    expect(cd.capacity).toBeNull();
    expect(cd.state).toBe("not-applicable");
    expect(cd.warning).toBeNull();
  });

  it("keeps invited/confirmed/waitlisted/projected as separate demand signals", () => {
    const guests = [
      ...confirmed(3, 1), // 3 confirmed, each +1 => projected 6
      makeGuest("invited"),
      makeGuest("invited"),
      makeGuest("waitlisted"),
    ];
    const cd = capacityDemand(makeEvent({ capacity: { maxAttendees: 100, rsvpEnabled: true, waitlistEnabled: true } }), guests);
    expect(cd.confirmed).toBe(3);
    expect(cd.invited).toBe(2);
    expect(cd.waitlisted).toBe(1);
    expect(cd.projected).toBe(6); // capacity is NOT an RSVP count
    expect(cd.capacity).toBe(100);
  });

  it("is complete with healthy headroom", () => {
    const cd = capacityDemand(makeEvent({ capacity: { maxAttendees: 10, rsvpEnabled: true, waitlistEnabled: false } }), confirmed(5));
    expect(cd.state).toBe("complete");
    expect(cd.warning).toBeNull();
  });

  it("flags attention when projected demand nears capacity (>=85%)", () => {
    const cd = capacityDemand(makeEvent({ capacity: { maxAttendees: 10, rsvpEnabled: true, waitlistEnabled: false } }), confirmed(9));
    expect(cd.state).toBe("attention");
    expect(cd.warning).toMatch(/nearing capacity/);
  });

  it("blocks when projected demand exceeds capacity", () => {
    const cd = capacityDemand(makeEvent({ capacity: { maxAttendees: 10, rsvpEnabled: true, waitlistEnabled: false } }), confirmed(12));
    expect(cd.state).toBe("blocked");
    expect(cd.warning).toMatch(/exceeds capacity/);
  });
});

// ---------------------------------------------------------------------------
// Readiness category states (DEC-0009 §2)
// ---------------------------------------------------------------------------

describe("readiness — every category carries evidence and a next action", () => {
  it("all categories always have non-empty evidence and nextAction", () => {
    const report = readiness(makeEvent(), [], basePlan(), NOW);
    for (const c of report.categories) {
      expect(c.evidence.trim().length).toBeGreaterThan(0);
      expect(c.nextAction.trim().length).toBeGreaterThan(0);
      expect(["complete", "attention", "blocked", "not-applicable"]).toContain(c.state);
    }
  });
});

describe("readiness — schedule", () => {
  it("attention when no days are structured", () => {
    const r = readiness(makeEvent(), [], basePlan({ days: [] }), NOW);
    expect(cat(r, "schedule").state).toBe("attention");
  });
  it("complete when days exist", () => {
    expect(cat(readiness(makeEvent(), [], basePlan(), NOW), "schedule").state).toBe("complete");
  });
});

describe("readiness — ownership", () => {
  const resp = (ownerId?: string): Responsibility => ({ id: `r-${Math.random()}`, area: "Catering", ownerId });
  it("attention with no owner", () => {
    expect(cat(readiness(makeEvent(), [], basePlan({ ownerId: null }), NOW), "ownership").state).toBe("attention");
  });
  it("attention when a responsibility is unassigned", () => {
    const r = readiness(makeEvent(), [], basePlan({ responsibilities: [resp()] }), NOW);
    expect(cat(r, "ownership").state).toBe("attention");
    expect(cat(r, "ownership").evidence).toMatch(/unassigned/);
  });
  it("complete when owner set and all responsibilities owned", () => {
    const r = readiness(makeEvent(), [], basePlan({ responsibilities: [resp("wm-current")] }), NOW);
    expect(cat(r, "ownership").state).toBe("complete");
  });
});

describe("readiness — milestones", () => {
  const ms = (over: Partial<Milestone>): Milestone => ({ id: `m-${Math.random()}`, title: "Book venue", status: "todo", ...over });
  it("attention when none defined", () => {
    expect(cat(readiness(makeEvent(), [], basePlan({ milestones: [] }), NOW), "milestones").state).toBe("attention");
  });
  it("attention when a milestone is overdue", () => {
    const r = readiness(makeEvent(), [], basePlan({ milestones: [ms({ dueDate: "2026-08-01", status: "todo" })] }), NOW);
    expect(cat(r, "milestones").state).toBe("attention");
    expect(cat(r, "milestones").evidence).toMatch(/overdue/);
  });
  it("complete when milestones exist and none are overdue", () => {
    const r = readiness(makeEvent(), [], basePlan({ milestones: [ms({ dueDate: "2026-09-01", status: "in-progress" })] }), NOW);
    expect(cat(r, "milestones").state).toBe("complete");
  });
});

describe("readiness — risks & blockers", () => {
  const risk = (over: Partial<Risk>): Risk => ({ id: `k-${Math.random()}`, title: "Weather", severity: "medium", status: "open", blocker: false, ...over });
  it("blocked when an open blocker exists", () => {
    const r = readiness(makeEvent(), [], basePlan({ risks: [risk({ blocker: true, status: "open" })] }), NOW);
    expect(cat(r, "risks").state).toBe("blocked");
    expect(r.overall).toBe("blocked");
  });
  it("attention with an open non-blocking risk", () => {
    expect(cat(readiness(makeEvent(), [], basePlan({ risks: [risk({ status: "open" })] }), NOW), "risks").state).toBe("attention");
  });
  it("complete when no open risks", () => {
    expect(cat(readiness(makeEvent(), [], basePlan({ risks: [risk({ status: "resolved" })] }), NOW), "risks").state).toBe("complete");
  });
  it("a resolved blocker no longer blocks", () => {
    expect(cat(readiness(makeEvent(), [], basePlan({ risks: [risk({ blocker: true, status: "resolved" })] }), NOW), "risks").state).toBe("complete");
  });
});

describe("readiness — guests & capacity reuse", () => {
  it("attention when the roster is empty", () => {
    expect(cat(readiness(makeEvent(), [], basePlan(), NOW), "guests").state).toBe("attention");
  });
  it("complete once guests are present", () => {
    expect(cat(readiness(makeEvent(), confirmed(3), basePlan(), NOW), "guests").state).toBe("complete");
  });
  it("capacity category mirrors capacityDemand (blocked when over)", () => {
    const ev = makeEvent({ capacity: { maxAttendees: 5, rsvpEnabled: true, waitlistEnabled: false } });
    const r = readiness(ev, confirmed(8), basePlan(), NOW);
    expect(cat(r, "capacity").state).toBe("blocked");
  });
});

describe("readiness — overall", () => {
  it("is the worst applicable category state", () => {
    // Clean plan with guests and a capped-but-healthy event => complete overall.
    const ev = makeEvent({ capacity: { maxAttendees: 100, rsvpEnabled: true, waitlistEnabled: false } });
    const plan = basePlan({
      responsibilities: [{ id: "r1", area: "AV", ownerId: "wm-current" }],
      milestones: [{ id: "m1", title: "Venue", status: "done" }],
    });
    expect(readiness(ev, confirmed(10), plan, NOW).overall).toBe("complete");
  });
});
