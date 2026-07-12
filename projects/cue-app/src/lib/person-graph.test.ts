import { describe, it, expect } from "vitest";
import type { CueEvent, Guest, GuestRole } from "./types";
import { newGuest } from "./guests-select";
import { buildPeople } from "./people";
import {
  coAttendees,
  networkCompanies,
  sharedOrganizations,
  personNetwork,
} from "./person-graph";

function mkEvent(id: string, startsAt: string): CueEvent {
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
  };
}

let seq = 0;
function guest(eventId: string, email: string, over: Partial<Guest> = {}, roles: GuestRole[] = []): Guest {
  seq += 1;
  const base = newGuest(eventId, `g-${seq}`, "2026-05-01T00:00:00Z");
  const [first, last] = email.split("@")[0].split(".");
  return {
    ...base,
    identity: { firstName: first ?? "A", lastName: last ?? "B", email },
    professional: { company: over.professional?.company },
    roles,
    ...over,
  };
}

const e1 = mkEvent("e1", "2026-06-01T18:00:00Z");
const e2 = mkEvent("e2", "2026-07-01T18:00:00Z");
const e3 = mkEvent("e3", "2026-08-01T18:00:00Z");

describe("coAttendees", () => {
  it("ranks people by number of shared events, strongest first", () => {
    const guests = [
      guest("e1", "ava.chen@x.com"),
      guest("e2", "ava.chen@x.com"),
      guest("e3", "ava.chen@x.com"),
      // Ben shares e1 + e2 with Ava (2), Cara shares only e1 (1).
      guest("e1", "ben.ford@x.com"),
      guest("e2", "ben.ford@x.com"),
      guest("e1", "cara.diaz@x.com"),
    ];
    const people = buildPeople(guests, [e1, e2, e3]);
    const ava = people.find((p) => p.email === "ava.chen@x.com")!;
    const ties = coAttendees(ava, people);
    expect(ties.map((t) => t.person.email)).toEqual(["ben.ford@x.com", "cara.diaz@x.com"]);
    expect(ties[0].count).toBe(2);
    expect(ties[0].sharedEventIds.sort()).toEqual(["e1", "e2"]);
  });

  it("excludes the person themselves and people who share no events", () => {
    const guests = [
      guest("e1", "ava.chen@x.com"),
      guest("e3", "zoe.kim@x.com"), // no overlap with Ava
    ];
    const people = buildPeople(guests, [e1, e3]);
    const ava = people.find((p) => p.email === "ava.chen@x.com")!;
    expect(coAttendees(ava, people)).toHaveLength(0);
  });
});

describe("networkCompanies", () => {
  it("counts companies across the co-attendee set, most-common first", () => {
    const guests = [
      guest("e1", "ava.chen@x.com"),
      guest("e1", "ben.ford@x.com", { professional: { company: "Stripe" } }),
      guest("e1", "cara.diaz@x.com", { professional: { company: "Stripe" } }),
      guest("e1", "dan.eng@x.com", { professional: { company: "Ramp" } }),
    ];
    const people = buildPeople(guests, [e1]);
    const ava = people.find((p) => p.email === "ava.chen@x.com")!;
    const cos = networkCompanies(coAttendees(ava, people));
    expect(cos[0]).toEqual({ name: "Stripe", count: 2 });
    expect(cos.map((c) => c.name)).toContain("Ramp");
  });
});

describe("sharedOrganizations", () => {
  it("returns organizations the person shares with their network", () => {
    const guests = [
      guest("e1", "ava.chen@x.com", {}, ["founder"]),
      guest("e1", "ben.ford@x.com", {}, ["founder"]), // shares Founders Circle
      guest("e1", "cara.diaz@x.com", {}, ["press"]), // Press Corps, not shared
    ];
    const people = buildPeople(guests, [e1]);
    const ava = people.find((p) => p.email === "ava.chen@x.com")!;
    const shared = sharedOrganizations(ava, coAttendees(ava, people));
    expect(shared).toContain("Founders Circle");
    expect(shared).not.toContain("Press Corps");
  });
});

describe("personNetwork", () => {
  it("assembles co-attendees, companies, own events (recent first), and shared orgs", () => {
    const guests = [
      guest("e1", "ava.chen@x.com", {}, ["founder"]),
      guest("e2", "ava.chen@x.com", {}, ["founder"]),
      guest("e1", "ben.ford@x.com", { professional: { company: "Stripe" } }, ["founder"]),
    ];
    const people = buildPeople(guests, [e1, e2]);
    const ava = people.find((p) => p.email === "ava.chen@x.com")!;
    const net = personNetwork(ava, people);
    expect(net.coAttendees.map((t) => t.person.email)).toEqual(["ben.ford@x.com"]);
    expect(net.companies.map((c) => c.name)).toEqual(["Stripe"]);
    expect(net.events.map((a) => a.eventId)).toEqual(["e2", "e1"]); // most recent first
    expect(net.organizations).toContain("Founders Circle");
  });
});
