import { describe, it, expect } from "vitest";
import type { CueEvent, Guest, GuestRole, RsvpStatus } from "./types";
import { newGuest } from "./guests-select";
import {
  buildPeople,
  personKey,
  personId,
  personIdForGuest,
  deriveInterests,
  deriveOrganizations,
} from "./people";

const TZ = "America/Los_Angeles";

function mkEvent(over: Partial<CueEvent> & { id: string }): CueEvent {
  return {
    title: "Event",
    classification: "portfolio",
    summary: "",
    startsAt: "2026-06-01T18:00:00.000Z",
    timezone: TZ,
    status: "done",
    venue: "Venue",
    city: "SF",
    host: "Host",
    capacity: { maxAttendees: 100, rsvpEnabled: true, waitlistEnabled: false },
    confirmedGuests: 0,
    invitedGuests: 0,
    branding: { theme: "indigo" },
    portfolio: [],
    tags: [],
    createdAt: "2026-05-01T00:00:00.000Z",
    ...over,
  };
}

interface GOpts {
  id?: string;
  eventId: string;
  first?: string;
  last?: string;
  preferred?: string;
  email?: string;
  phone?: string;
  company?: string;
  title?: string;
  portfolio?: string;
  roles?: GuestRole[];
  vip?: boolean;
  rsvp?: RsvpStatus;
  checkedIn?: boolean;
  tags?: string[];
  thanked?: boolean;
  internalNote?: string;
  publicNote?: string;
}

let seq = 0;
function mkGuest(o: GOpts): Guest {
  seq += 1;
  const base = newGuest(o.eventId, o.id ?? `g-${seq}`, "2026-05-01T00:00:00.000Z");
  return {
    ...base,
    identity: {
      firstName: o.first ?? "Ava",
      lastName: o.last ?? "Chen",
      preferredName: o.preferred,
      email: o.email,
      phone: o.phone,
    },
    professional: {
      company: o.company,
      jobTitle: o.title,
      portfolioCompanyId: o.portfolio,
    },
    roles: o.roles ?? [],
    vip: o.vip ?? false,
    attendance: {
      ...base.attendance,
      rsvp: o.rsvp ?? "confirmed",
      checkedIn: o.checkedIn ?? false,
      checkedInAt: o.checkedIn ? "2026-06-01T18:30:00.000Z" : undefined,
    },
    communication: { ...base.communication, thankYouSent: o.thanked ?? false },
    notes: { internal: o.internalNote, public: o.publicNote },
    tags: o.tags ?? [],
  };
}

describe("identity resolution", () => {
  it("keys on email when present, falling back to normalized name", () => {
    expect(personKey(mkGuest({ eventId: "e", email: "Ava.Chen@X.com" }))).toBe("ava.chen@x.com");
    expect(personKey(mkGuest({ eventId: "e", first: "Ava", last: "Chen", email: undefined }))).toBe(
      "ava chen",
    );
  });

  it("produces a stable, url-safe id for a key", () => {
    const id = personId("ava.chen@x.com");
    expect(id).toMatch(/^psn-[a-z0-9]+$/);
    expect(personId("ava.chen@x.com")).toBe(id); // deterministic
    expect(personId("other@x.com")).not.toBe(id);
  });

  it("personIdForGuest matches personId(personKey(guest))", () => {
    const g = mkGuest({ eventId: "e", email: "x@y.com" });
    expect(personIdForGuest(g)).toBe(personId(personKey(g)));
  });
});

describe("buildPeople — folding appearances", () => {
  const e1 = mkEvent({ id: "e1", title: "Summit", startsAt: "2026-06-01T18:00:00Z", classification: "founder" });
  const e2 = mkEvent({ id: "e2", title: "Dinner", startsAt: "2026-07-01T18:00:00Z", classification: "portfolio" });

  it("merges the same email across events into one person", () => {
    const people = buildPeople(
      [
        mkGuest({ eventId: "e1", email: "ava@x.com", company: "Lattice", title: "CEO", checkedIn: true }),
        mkGuest({ eventId: "e2", email: "ava@x.com", company: "Lattice AI", title: "Founder", vip: true, checkedIn: true }),
      ],
      [e1, e2],
    );
    expect(people).toHaveLength(1);
    const p = people[0];
    expect(p.appearances).toHaveLength(2);
    expect(p.eventsAttended).toBe(2);
    expect(p.eventsInvited).toBe(2);
    // Current company/title come from the most recent appearance (e2).
    expect(p.company).toBe("Lattice AI");
    expect(p.title).toBe("Founder");
    expect(p.vip).toBe(true); // VIP on any event
  });

  it("orders appearances chronologically and sets first/last seen", () => {
    const people = buildPeople(
      [
        mkGuest({ eventId: "e2", email: "ava@x.com" }),
        mkGuest({ eventId: "e1", email: "ava@x.com" }),
      ],
      [e1, e2],
    );
    const p = people[0];
    expect(p.appearances.map((a) => a.eventId)).toEqual(["e1", "e2"]);
    expect(p.firstSeen).toBe("2026-06-01T18:00:00Z");
    expect(p.lastSeen).toBe("2026-07-01T18:00:00Z");
  });

  it("unions roles and tags across appearances", () => {
    const people = buildPeople(
      [
        mkGuest({ eventId: "e1", email: "ava@x.com", roles: ["founder"], tags: ["ai"] }),
        mkGuest({ eventId: "e2", email: "ava@x.com", roles: ["investor"], tags: ["keynote"] }),
      ],
      [e1, e2],
    );
    expect(people[0].roles.sort()).toEqual(["founder", "investor"]);
    expect(people[0].tags).toEqual(["ai", "keynote"]);
  });

  it("resolves the portfolio-tie company name via the event portfolio", () => {
    const ev = mkEvent({
      id: "ep",
      portfolio: [{ id: "co-lattice", name: "Lattice AI", stage: "Seed" }],
    });
    const people = buildPeople(
      [mkGuest({ eventId: "ep", email: "ava@x.com", company: "typo", portfolio: "co-lattice" })],
      [ev],
    );
    expect(people[0].company).toBe("Lattice AI");
    expect(people[0].portfolioCompanyIds).toEqual(["co-lattice"]);
  });

  it("derives isSpeaker from speaker/keynote tags", () => {
    const people = buildPeople(
      [mkGuest({ eventId: "e1", email: "s@x.com", tags: ["keynote"] })],
      [e1],
    );
    expect(people[0].isSpeaker).toBe(true);
  });

  it("collects event notes into the person, newest event first", () => {
    const people = buildPeople(
      [
        mkGuest({ eventId: "e1", email: "ava@x.com", internalNote: "old note" }),
        mkGuest({ eventId: "e2", email: "ava@x.com", internalNote: "new note", publicNote: "shareable" }),
      ],
      [e1, e2],
    );
    const notes = people[0].notes;
    expect(notes[0].text).toBe("new note");
    expect(notes.map((n) => n.text)).toContain("shareable");
    expect(notes.map((n) => n.text)).toContain("old note");
  });

  it("counts attendance only for checked-in appearances", () => {
    const people = buildPeople(
      [
        mkGuest({ eventId: "e1", email: "ava@x.com", checkedIn: false }),
        mkGuest({ eventId: "e2", email: "ava@x.com", checkedIn: true }),
      ],
      [e1, e2],
    );
    expect(people[0].eventsInvited).toBe(2);
    expect(people[0].eventsAttended).toBe(1);
  });

  it("keeps distinct people separate", () => {
    const people = buildPeople(
      [
        mkGuest({ eventId: "e1", email: "ava@x.com" }),
        mkGuest({ eventId: "e1", email: "ben@x.com" }),
      ],
      [e1],
    );
    expect(people).toHaveLength(2);
  });
});

describe("deriveInterests", () => {
  it("weights portfolio sector, classification, and tag themes by recurrence", () => {
    const e = mkEvent({ id: "e1", classification: "founder", startsAt: "2026-06-01T18:00:00Z" });
    const people = buildPeople(
      [
        mkGuest({ eventId: "e1", email: "ava@x.com", portfolio: "co-meridian", tags: ["series-b"] }),
      ],
      [e],
    );
    // Healthcare (portfolio), Founders (classification), Fundraising (tag).
    expect(people[0].interests).toEqual(expect.arrayContaining(["Healthcare", "Founders", "Fundraising"]));
  });

  it("is a pure function of appearances + event map", () => {
    const e = mkEvent({ id: "e1", classification: "investor" });
    const eventsById = new Map([[e.id, e]]);
    const people = buildPeople([mkGuest({ eventId: "e1", email: "a@x.com" })], [e]);
    const direct = deriveInterests(people[0].appearances, eventsById);
    expect(direct).toContain("Investing");
  });
});

describe("deriveOrganizations", () => {
  it("maps roles + portfolio ties to affiliations", () => {
    expect(deriveOrganizations(["founder"], ["co-lattice"])).toEqual(
      expect.arrayContaining(["Founders Circle", "Portfolio Network"]),
    );
    expect(deriveOrganizations(["press"], [])).toEqual(["Press Corps"]);
    expect(deriveOrganizations([], [])).toEqual([]);
  });
});
