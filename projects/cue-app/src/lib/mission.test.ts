import { describe, it, expect } from "vitest";
import type { CueEvent, Guest, GuestRole, RsvpStatus } from "./types";
import { newGuest } from "./guests-select";
import {
  isFlagship,
  liveEvents,
  flagshipEvent,
  upcomingEvents,
  orgMetrics,
  attendanceHealth,
  isSpeaker,
  vipsAwaiting,
  speakersAwaiting,
  capacitySignal,
  followUps,
  quickActions,
  rollCallPath,
  eventTabPath,
  aiBriefing,
  upcomingMilestones,
} from "./mission";

const NOW = Date.parse("2026-07-08T19:00:00.000Z");
const MIN = 60 * 1000;
const DAY = 24 * 60 * 60 * 1000;

function makeEvent(over: Partial<CueEvent> & { id: string }): CueEvent {
  return {
    title: "Untitled",
    classification: "portfolio",
    summary: "",
    startsAt: "2026-07-08T18:00:00.000Z",
    timezone: "America/Los_Angeles",
    status: "upcoming",
    venue: "Venue",
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

interface GOpts {
  eventId?: string;
  rsvp?: RsvpStatus;
  vip?: boolean;
  roles?: GuestRole[];
  tags?: string[];
  checkedInMinsAgo?: number;
  waitlisted?: boolean;
  thanked?: boolean;
}

let seq = 0;
function g(opts: GOpts = {}): Guest {
  seq += 1;
  const base = newGuest(opts.eventId ?? "evt-1", `g-${seq}`, "2026-07-01T00:00:00.000Z");
  return {
    ...base,
    identity: { firstName: `First${seq}`, lastName: `Last${seq}`, email: `p${seq}@x.com` },
    roles: opts.roles ?? [],
    vip: opts.vip ?? false,
    tags: opts.tags ?? [],
    attendance: {
      ...base.attendance,
      rsvp: opts.rsvp ?? "confirmed",
      waitlisted: opts.waitlisted ?? false,
      checkedIn: opts.checkedInMinsAgo !== undefined,
      checkedInAt:
        opts.checkedInMinsAgo !== undefined
          ? new Date(NOW - opts.checkedInMinsAgo * MIN).toISOString()
          : undefined,
    },
    communication: { ...base.communication, thankYouSent: opts.thanked ?? false },
  };
}

// --- Event selection --------------------------------------------------------

describe("event selection", () => {
  const flagship = makeEvent({ id: "flag", status: "live", tags: ["flagship"], startsAt: "2026-07-08T17:00:00Z" });
  const otherLive = makeEvent({ id: "other", status: "live", startsAt: "2026-07-08T16:00:00Z" });
  const soon = makeEvent({ id: "soon", status: "upcoming", startsAt: "2026-07-10T18:00:00Z" });
  const later = makeEvent({ id: "later", status: "upcoming", startsAt: "2026-08-01T18:00:00Z" });
  const draft = makeEvent({ id: "draft", status: "draft" });
  const done = makeEvent({ id: "done", status: "done" });
  const all = [done, later, otherLive, draft, flagship, soon];

  it("isFlagship keys off the flagship tag", () => {
    expect(isFlagship(flagship)).toBe(true);
    expect(isFlagship(otherLive)).toBe(false);
  });

  it("liveEvents floats the flagship above other live events", () => {
    expect(liveEvents(all).map((e) => e.id)).toEqual(["flag", "other"]);
  });

  it("flagshipEvent prefers the tagged flagship over an earlier live event", () => {
    expect(flagshipEvent(all)?.id).toBe("flag");
  });

  it("flagshipEvent falls back to the first live event when none is tagged", () => {
    expect(flagshipEvent([otherLive, soon])?.id).toBe("other");
  });

  it("flagshipEvent is undefined when nothing is live", () => {
    expect(flagshipEvent([soon, draft, done])).toBeUndefined();
  });

  it("upcomingEvents returns upcoming only, soonest first, capped", () => {
    expect(upcomingEvents(all, 1).map((e) => e.id)).toEqual(["soon"]);
    expect(upcomingEvents(all).map((e) => e.id)).toEqual(["soon", "later"]);
  });
});

// --- Org metrics ------------------------------------------------------------

describe("orgMetrics", () => {
  it("counts events by status and sums confirmed guests", () => {
    const events = [
      makeEvent({ id: "a", status: "live", confirmedGuests: 100 }),
      makeEvent({ id: "b", status: "upcoming", confirmedGuests: 20 }),
      makeEvent({ id: "c", status: "done", confirmedGuests: 40 }),
    ];
    const m = orgMetrics(events);
    expect(m).toMatchObject({ totalEvents: 3, liveCount: 1, upcomingCount: 1, confirmedGuests: 160 });
  });

  it("averages fill only over active capped events (excludes done + uncapped)", () => {
    const events = [
      makeEvent({ id: "half", status: "live", confirmedGuests: 50, capacity: { maxAttendees: 100, rsvpEnabled: true, waitlistEnabled: false } }),
      makeEvent({ id: "full", status: "upcoming", confirmedGuests: 100, capacity: { maxAttendees: 100, rsvpEnabled: true, waitlistEnabled: false } }),
      makeEvent({ id: "past", status: "done", confirmedGuests: 0, capacity: { maxAttendees: 100, rsvpEnabled: true, waitlistEnabled: false } }),
      makeEvent({ id: "uncapped", status: "live", confirmedGuests: 9, capacity: { maxAttendees: null, rsvpEnabled: false, waitlistEnabled: false } }),
    ];
    // (0.5 + 1.0) / 2 = 75%
    expect(orgMetrics(events).avgFill).toBe(75);
  });

  it("reports zero avg fill when there are no active capped events", () => {
    expect(orgMetrics([makeEvent({ id: "d", status: "done" })]).avgFill).toBe(0);
  });
});

// --- Attendance health ------------------------------------------------------

describe("attendanceHealth", () => {
  it("rolls up checked-in and expected across every live event", () => {
    const live1 = makeEvent({ id: "e1", status: "live" });
    const live2 = makeEvent({ id: "e2", status: "live" });
    const upcoming = makeEvent({ id: "e3", status: "upcoming" });
    const roster = [
      g({ eventId: "e1", checkedInMinsAgo: 10 }),
      g({ eventId: "e1" }), // expected, not arrived
      g({ eventId: "e2", vip: true, checkedInMinsAgo: 5 }),
      g({ eventId: "e3", checkedInMinsAgo: 5 }), // not live → ignored
    ];
    const h = attendanceHealth([live1, live2, upcoming], roster, NOW);
    expect(h).toMatchObject({
      liveEventCount: 2,
      checkedIn: 2,
      expected: 3,
      remaining: 1,
      vipCheckedIn: 1,
      vipTotal: 1,
    });
    expect(h.pct).toBe(67);
  });

  it("returns zeroes (no divide-by-zero) when nothing is live", () => {
    const h = attendanceHealth([makeEvent({ id: "e", status: "upcoming" })], [], NOW);
    expect(h).toMatchObject({ liveEventCount: 0, checkedIn: 0, expected: 0, pct: 0 });
  });
});

// --- Alerts -----------------------------------------------------------------

describe("operational alerts", () => {
  it("isSpeaker keys off speaker/keynote tags", () => {
    expect(isSpeaker(g({ tags: ["keynote"] }))).toBe(true);
    expect(isSpeaker(g({ tags: ["speaker", "ai"] }))).toBe(true);
    expect(isSpeaker(g({ tags: ["press"] }))).toBe(false);
  });

  it("vipsAwaiting lists confirmed, un-arrived VIPs on live events only", () => {
    const live = makeEvent({ id: "L", status: "live" });
    const upcoming = makeEvent({ id: "U", status: "upcoming" });
    const roster = [
      g({ eventId: "L", vip: true }), // awaited VIP
      g({ eventId: "L", vip: true, checkedInMinsAgo: 5 }), // already in
      g({ eventId: "L", vip: false }), // not a VIP
      g({ eventId: "U", vip: true }), // not live
    ];
    expect(vipsAwaiting([live, upcoming], roster)).toHaveLength(1);
  });

  it("speakersAwaiting lists confirmed, un-arrived speakers on live events", () => {
    const live = makeEvent({ id: "L", status: "live" });
    const roster = [
      g({ eventId: "L", tags: ["speaker"] }), // awaited speaker
      g({ eventId: "L", tags: ["keynote"], checkedInMinsAgo: 3 }), // already in
      g({ eventId: "L", tags: [] }), // not a speaker
    ];
    expect(speakersAwaiting([live], roster)).toHaveLength(1);
  });

  it("capacitySignal surfaces the worst live capped room and flags risk", () => {
    const tight = makeEvent({ id: "tight", status: "live", capacity: { maxAttendees: 10, rsvpEnabled: true, waitlistEnabled: false } });
    const roomy = makeEvent({ id: "roomy", status: "live", capacity: { maxAttendees: 100, rsvpEnabled: true, waitlistEnabled: false } });
    const roster = [
      ...Array.from({ length: 9 }, () => g({ eventId: "tight", checkedInMinsAgo: 5 })),
      ...Array.from({ length: 10 }, () => g({ eventId: "roomy", checkedInMinsAgo: 5 })),
    ];
    const sig = capacitySignal([tight, roomy], roster, NOW);
    expect(sig.eventId).toBe("tight");
    expect(sig.pct).toBe(90);
    expect(sig.atRisk).toBe(true);
  });

  it("capacitySignal is null-and-clear when no live capped event exists", () => {
    const uncapped = makeEvent({ id: "u", status: "live", capacity: { maxAttendees: null, rsvpEnabled: false, waitlistEnabled: false } });
    const sig = capacitySignal([uncapped], [g({ eventId: "u", checkedInMinsAgo: 1 })], NOW);
    expect(sig.pct).toBeNull();
    expect(sig.atRisk).toBe(false);
  });

  it("followUps counts thank-yous owed and waitlist decisions", () => {
    const done = makeEvent({ id: "D", status: "done" });
    const live = makeEvent({ id: "L", status: "live" });
    const roster = [
      g({ eventId: "D", checkedInMinsAgo: 100, thanked: false }), // owed thanks
      g({ eventId: "D", checkedInMinsAgo: 100, thanked: true }), // already thanked
      g({ eventId: "D" }), // never arrived → nothing owed
      g({ eventId: "L", rsvp: "waitlisted", waitlisted: true }), // waitlist decision
    ];
    expect(followUps([done, live], roster)).toEqual({ total: 2, thankYous: 1, waitlist: 1 });
  });
});

// --- Quick actions / routing ------------------------------------------------

describe("quickActions routing", () => {
  const flagship = makeEvent({ id: "evt-2041", status: "live", tags: ["flagship"] });

  it("wires the four actions to the right routes off the flagship", () => {
    const actions = quickActions(flagship);
    expect(actions.map((a) => a.id)).toEqual(["create", "rollcall", "invite", "agenda"]);
    expect(actions.find((a) => a.id === "create")?.to).toBe("/events/new");
    expect(actions.find((a) => a.id === "rollcall")?.to).toBe("/events/evt-2041/rollcall");
    expect(actions.find((a) => a.id === "invite")?.to).toBe("/events/evt-2041?tab=guests");
    expect(actions.find((a) => a.id === "agenda")?.to).toBe("/events/evt-2041?tab=assistant");
    expect(actions.every((a) => a.enabled)).toBe(true);
  });

  it("links the live event to the Roll Call Command Center", () => {
    expect(rollCallPath(flagship)).toBe("/events/evt-2041/rollcall");
  });

  it("builds tab deep-links for the detail page", () => {
    expect(eventTabPath(flagship, "guests")).toBe("/events/evt-2041?tab=guests");
  });

  it("disables live-only actions and falls back to /events when nothing is live", () => {
    const actions = quickActions(undefined);
    expect(actions.find((a) => a.id === "create")?.enabled).toBe(true);
    for (const id of ["rollcall", "invite", "agenda"] as const) {
      const a = actions.find((x) => x.id === id)!;
      expect(a.enabled).toBe(false);
      expect(a.to).toBe("/events");
    }
  });
});

// --- AI briefing ------------------------------------------------------------

describe("aiBriefing", () => {
  it("recommends a greeter when VIPs are still expected at the flagship", () => {
    const flagship = makeEvent({ id: "F", status: "live", tags: ["flagship"], title: "Summit" });
    const recs = aiBriefing([flagship], [g({ eventId: "F", vip: true })], NOW);
    const rec = recs.find((r) => r.id === "vip-greeter");
    expect(rec).toBeDefined();
    expect(rec?.action?.to).toBe("/events/F/rollcall");
  });

  it("flags an under-subscribed upcoming event within the nudge horizon", () => {
    const soft = makeEvent({
      id: "S",
      status: "upcoming",
      title: "LP Briefing",
      startsAt: new Date(NOW + 20 * DAY).toISOString(),
      confirmedGuests: 20,
      capacity: { maxAttendees: 100, rsvpEnabled: true, waitlistEnabled: false },
    });
    const rec = aiBriefing([soft], [], NOW).find((r) => r.id === "second-wave");
    expect(rec).toBeDefined();
    expect(rec?.action?.to).toBe("/events/S?tab=guests");
  });

  it("does not nudge a well-subscribed or far-off upcoming event", () => {
    const full = makeEvent({ id: "full", status: "upcoming", startsAt: new Date(NOW + 10 * DAY).toISOString(), confirmedGuests: 90, capacity: { maxAttendees: 100, rsvpEnabled: true, waitlistEnabled: false } });
    const farOff = makeEvent({ id: "far", status: "upcoming", startsAt: new Date(NOW + 90 * DAY).toISOString(), confirmedGuests: 1, capacity: { maxAttendees: 100, rsvpEnabled: true, waitlistEnabled: false } });
    expect(aiBriefing([full, farOff], [], NOW).some((r) => r.id === "second-wave")).toBe(false);
  });

  it("falls back to an on-track note when a live event has no open issues", () => {
    const flagship = makeEvent({ id: "F", status: "live", tags: ["flagship"] });
    const recs = aiBriefing([flagship], [g({ eventId: "F", checkedInMinsAgo: 10 })], NOW);
    expect(recs.map((r) => r.id)).toContain("on-track");
  });

  it("caps the briefing to the requested number of items", () => {
    const flagship = makeEvent({ id: "F", status: "live", tags: ["flagship"], title: "Summit" });
    const done = makeEvent({ id: "D", status: "done" });
    const roster = [
      g({ eventId: "F", vip: true }),
      g({ eventId: "F", tags: ["speaker"] }),
      g({ eventId: "D", checkedInMinsAgo: 100, thanked: false }),
    ];
    expect(aiBriefing([flagship, done], roster, NOW, 2)).toHaveLength(2);
  });
});

// --- Milestones -------------------------------------------------------------

describe("upcomingMilestones", () => {
  it("collects wraps, doors, and drafts as future items, soonest first", () => {
    const live = makeEvent({ id: "L", status: "live", startsAt: new Date(NOW - 60 * MIN).toISOString(), endsAt: new Date(NOW + 3 * 60 * MIN).toISOString() });
    const upcoming = makeEvent({ id: "U", status: "upcoming", startsAt: new Date(NOW + DAY).toISOString() });
    const draft = makeEvent({ id: "D", status: "draft", startsAt: new Date(NOW + 5 * DAY).toISOString() });
    const past = makeEvent({ id: "P", status: "done", startsAt: new Date(NOW - DAY).toISOString() });
    const milestones = upcomingMilestones([draft, past, upcoming, live], NOW);
    expect(milestones.map((m) => m.id)).toEqual(["L-wrap", "U-doors", "D-draft"]);
    expect(milestones.map((m) => m.kind)).toEqual(["wrap", "doors", "draft"]);
  });

  it("drops milestones already in the past and respects the limit", () => {
    const soon = makeEvent({ id: "A", status: "upcoming", startsAt: new Date(NOW + DAY).toISOString() });
    const later = makeEvent({ id: "B", status: "upcoming", startsAt: new Date(NOW + 2 * DAY).toISOString() });
    const gone = makeEvent({ id: "C", status: "upcoming", startsAt: new Date(NOW - DAY).toISOString() });
    expect(upcomingMilestones([soon, later, gone], NOW, 1).map((m) => m.eventId)).toEqual(["A"]);
  });
});
