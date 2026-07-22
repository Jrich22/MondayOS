import { describe, it, expect } from "vitest";
import type { CueEvent, Guest, GuestAttendance, RsvpStatus } from "./types";
import {
  emptyPlan,
  eventDayCount,
  capacityDemand,
  readiness,
  currentMember,
  withRiskChange,
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
    // Both 11:00 local (PDT); the local calendar spans 09-01..09-03 = 3 days.
    const ev = makeEvent({ startsAt: "2026-09-01T18:00:00Z", endsAt: "2026-09-03T18:00:00Z" });
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
  it("an empty, UNREVIEWED register is Attention (absence is not proof of safety)", () => {
    const r = readiness(makeEvent(), [], basePlan({ risks: [], risksReviewed: false }), NOW);
    expect(cat(r, "risks").state).toBe("attention");
    expect(cat(r, "risks").evidence).toMatch(/no risk review/i);
  });
  it("complete only once the risk review is confirmed and no risks are open", () => {
    expect(cat(readiness(makeEvent(), [], basePlan({ risks: [risk({ status: "resolved" })], risksReviewed: true }), NOW), "risks").state).toBe("complete");
  });
  it("an empty register becomes Complete after an explicit review", () => {
    expect(cat(readiness(makeEvent(), [], basePlan({ risks: [], risksReviewed: true }), NOW), "risks").state).toBe("complete");
  });
  it("a resolved blocker no longer blocks (with review confirmed)", () => {
    expect(cat(readiness(makeEvent(), [], basePlan({ risks: [risk({ blocker: true, status: "resolved" })], risksReviewed: true }), NOW), "risks").state).toBe("complete");
  });
});

describe("risk-review invalidation (finding #2)", () => {
  const risk = (over: Partial<Risk>): Risk => ({ id: `k-${Math.random()}`, title: "Weather", severity: "medium", status: "open", blocker: false, ...over });

  it("withRiskChange always resets risksReviewed to false", () => {
    const reviewed = basePlan({ risksReviewed: true });
    expect(withRiskChange(reviewed, [risk({ status: "resolved" })]).risksReviewed).toBe(false);
    expect(withRiskChange(reviewed, []).risksReviewed).toBe(false); // even a no-op removal re-opens review
  });

  it("a previously reviewed plan returns to Attention after its risk register changes", () => {
    const reviewed = basePlan({ risks: [], risksReviewed: true });
    expect(cat(readiness(makeEvent(), [], reviewed, NOW), "risks").state).toBe("complete");
    // Any material change (here: add a risk) invalidates the acknowledgement.
    const changed = withRiskChange(reviewed, [risk({ status: "resolved" })]);
    expect(changed.risksReviewed).toBe(false);
    expect(cat(readiness(makeEvent(), [], changed, NOW), "risks").state).toBe("attention");
  });

  it("resolving a newly added risk does NOT return to Complete until re-reviewed", () => {
    // Add an open risk (Attention), then resolve it — still Attention because the
    // register changed and the review acknowledgement was invalidated.
    let plan = withRiskChange(basePlan({ risksReviewed: true }), [risk({ id: "k1", status: "open" })]);
    expect(cat(readiness(makeEvent(), [], plan, NOW), "risks").state).toBe("attention");
    plan = withRiskChange(plan, plan.risks.map((r) => ({ ...r, status: "resolved" as const })));
    expect(plan.risksReviewed).toBe(false);
    expect(cat(readiness(makeEvent(), [], plan, NOW), "risks").state).toBe("attention");
    // Only an explicit re-review returns it to Complete.
    const reReviewed = { ...plan, risksReviewed: true };
    expect(cat(readiness(makeEvent(), [], reReviewed, NOW), "risks").state).toBe("complete");
  });

  it("an open blocker remains Blocked regardless of the acknowledgement value", () => {
    const blocker = risk({ blocker: true, status: "open" });
    expect(cat(readiness(makeEvent(), [], basePlan({ risks: [blocker], risksReviewed: true }), NOW), "risks").state).toBe("blocked");
    expect(cat(readiness(makeEvent(), [], basePlan({ risks: [blocker], risksReviewed: false }), NOW), "risks").state).toBe("blocked");
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
      risksReviewed: true,
    });
    expect(readiness(ev, confirmed(10), plan, NOW).overall).toBe("complete");
  });
});

// ---------------------------------------------------------------------------
// Timezone-correct multi-day structure (finding #2)
// ---------------------------------------------------------------------------

describe("timezone-correct planning days", () => {
  it("uses the event's LOCAL date when the UTC date is the next day", () => {
    // 02:00Z on 09-02 is 19:00 on 09-01 in Los Angeles (PDT, UTC-7).
    const ev = makeEvent({
      timezone: "America/Los_Angeles",
      startsAt: "2026-09-02T02:00:00Z",
      endsAt: "2026-09-02T05:00:00Z",
    });
    expect(eventDayCount(ev)).toBe(1);
    expect(emptyPlan(ev, NOW).days.map((d) => d.date)).toEqual(["2026-09-01"]);
  });

  it("enumerates local calendar dates for a multi-day New York event", () => {
    const ev = makeEvent({
      timezone: "America/New_York",
      startsAt: "2026-09-01T14:00:00Z", // 10:00 EDT, 09-01
      endsAt: "2026-09-03T14:00:00Z", // 10:00 EDT, 09-03
    });
    expect(eventDayCount(ev)).toBe(3);
    expect(emptyPlan(ev, NOW).days.map((d) => d.date)).toEqual(["2026-09-01", "2026-09-02", "2026-09-03"]);
  });

  it("spans a DST boundary correctly (New York fall-back, Nov 1 2026)", () => {
    // 10-31 (EDT) → 11-02 (EST); the local calendar still counts 3 days.
    const ev = makeEvent({
      timezone: "America/New_York",
      startsAt: "2026-10-31T18:00:00Z", // 14:00 EDT, 10-31
      endsAt: "2026-11-02T18:00:00Z", // 13:00 EST, 11-02
    });
    expect(eventDayCount(ev)).toBe(3);
    expect(emptyPlan(ev, NOW).days.map((d) => d.date)).toEqual(["2026-10-31", "2026-11-01", "2026-11-02"]);
  });

  it("is a single day when the event crosses UTC midnight but not local midnight", () => {
    // 23:00Z 09-01 → 03:00Z 09-02 is 16:00–20:00 on 09-01 in Los Angeles.
    const ev = makeEvent({
      timezone: "America/Los_Angeles",
      startsAt: "2026-09-01T23:00:00Z",
      endsAt: "2026-09-02T03:00:00Z",
    });
    expect(eventDayCount(ev)).toBe(1);
    expect(emptyPlan(ev, NOW).days.map((d) => d.date)).toEqual(["2026-09-01"]);
  });
});
