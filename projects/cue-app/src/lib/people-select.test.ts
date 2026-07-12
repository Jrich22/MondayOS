import { describe, it, expect } from "vitest";
import type { CueEvent, Guest, GuestRole } from "./types";
import { newGuest } from "./guests-select";
import { buildPeople } from "./people";
import {
  emptyPeopleFilters,
  hasActivePeopleFilters,
  matchesPersonQuery,
  selectPeople,
  peopleFacets,
  peopleSummary,
} from "./people-select";

function mkEvent(id: string, startsAt: string, over: Partial<CueEvent> = {}): CueEvent {
  return {
    id,
    title: `Event ${id}`,
    classification: "portfolio",
    summary: "",
    startsAt,
    timezone: "America/Los_Angeles",
    status: "done",
    venue: "V",
    city: "SF",
    host: "H",
    capacity: { maxAttendees: 100, rsvpEnabled: true, waitlistEnabled: false },
    confirmedGuests: 0,
    invitedGuests: 0,
    branding: { theme: "indigo" },
    portfolio: [],
    tags: [],
    createdAt: "2026-05-01T00:00:00Z",
    ...over,
  };
}

let seq = 0;
function guest(
  eventId: string,
  first: string,
  last: string,
  over: { company?: string; roles?: GuestRole[]; vip?: boolean; tags?: string[]; checkedIn?: boolean; portfolio?: string } = {},
): Guest {
  seq += 1;
  const base = newGuest(eventId, `g-${seq}`, "2026-05-01T00:00:00Z");
  return {
    ...base,
    identity: { firstName: first, lastName: last, email: `${first}.${last}@x.com`.toLowerCase() },
    professional: { company: over.company, portfolioCompanyId: over.portfolio },
    roles: over.roles ?? [],
    vip: over.vip ?? false,
    tags: over.tags ?? [],
    attendance: { ...base.attendance, rsvp: "confirmed", checkedIn: over.checkedIn ?? false },
  };
}

const e1 = mkEvent("e1", "2026-06-01T18:00:00Z", {
  portfolio: [{ id: "co-lattice", name: "Lattice AI", stage: "Seed" }],
});
const e2 = mkEvent("e2", "2026-07-01T18:00:00Z");

function directory() {
  return buildPeople(
    [
      guest("e1", "Ava", "Chen", { company: "Lattice AI", roles: ["founder"], vip: true, portfolio: "co-lattice", checkedIn: true, tags: ["ai"] }),
      guest("e2", "Ava", "Chen", { company: "Lattice AI", roles: ["founder"], vip: true, portfolio: "co-lattice", checkedIn: true }),
      guest("e1", "Ben", "Ford", { company: "Stripe", roles: ["investor"] }),
      guest("e2", "Cara", "Diaz", { company: "Ramp", roles: ["founder"], tags: ["keynote"], checkedIn: true }),
    ],
    [e1, e2],
  );
}

describe("matchesPersonQuery", () => {
  it("matches across name, company, tags and event history", () => {
    const [ava] = buildPeople([guest("e1", "Ava", "Chen", { company: "Lattice AI", tags: ["ai"] })], [e1]);
    expect(matchesPersonQuery(ava, "")).toBe(true);
    expect(matchesPersonQuery(ava, "lattice")).toBe(true);
    expect(matchesPersonQuery(ava, "Event e1")).toBe(true);
    expect(matchesPersonQuery(ava, "zzz")).toBe(false);
  });
});

describe("selectPeople", () => {
  it("filters by role, company, vip, and attended", () => {
    const people = directory();
    const f = emptyPeopleFilters();
    expect(selectPeople(people, { ...f, role: "founder" }, "name").map((p) => p.firstName)).toEqual(["Ava", "Cara"]);
    expect(selectPeople(people, { ...f, company: "Stripe" }, "name").map((p) => p.firstName)).toEqual(["Ben"]);
    expect(selectPeople(people, { ...f, vipOnly: true }, "name").map((p) => p.firstName)).toEqual(["Ava"]);
    expect(selectPeople(people, { ...f, speakerOnly: true }, "name").map((p) => p.firstName)).toEqual(["Cara"]);
    expect(selectPeople(people, { ...f, attendedOnly: true }, "name").map((p) => p.firstName).sort()).toEqual(["Ava", "Cara"]);
  });

  it("filters by portfolio tie", () => {
    const people = directory();
    const got = selectPeople(people, { ...emptyPeopleFilters(), portfolioCompanyId: "co-lattice" }, "name");
    expect(got.map((p) => p.firstName)).toEqual(["Ava"]);
  });

  it("sorts by connections (appearance count) then name", () => {
    const people = directory();
    // Ava has 2 appearances, others 1 → Ava first under 'connections'.
    expect(selectPeople(people, emptyPeopleFilters(), "connections")[0].firstName).toBe("Ava");
  });
});

describe("hasActivePeopleFilters", () => {
  it("is false for empty filters and true once anything is set", () => {
    expect(hasActivePeopleFilters(emptyPeopleFilters())).toBe(false);
    expect(hasActivePeopleFilters({ ...emptyPeopleFilters(), vipOnly: true })).toBe(true);
    expect(hasActivePeopleFilters({ ...emptyPeopleFilters(), query: "a" })).toBe(true);
  });
});

describe("peopleFacets", () => {
  it("collects distinct sorted options and resolves portfolio names", () => {
    const facets = peopleFacets(directory());
    expect(facets.companies).toEqual(["Lattice AI", "Ramp", "Stripe"]);
    expect(facets.portfolio).toEqual([{ id: "co-lattice", name: "Lattice AI" }]);
    expect(facets.organizations).toContain("Founders Circle");
    expect(facets.interests).toContain("AI Infrastructure");
  });
});

describe("peopleSummary", () => {
  it("counts total, attended, vips, and recurring people", () => {
    const s = peopleSummary(directory());
    expect(s.total).toBe(3); // Ava, Ben, Cara
    expect(s.attended).toBe(2); // Ava, Cara checked in
    expect(s.vips).toBe(1); // Ava
    expect(s.recurring).toBe(1); // Ava on 2 events
  });
});
