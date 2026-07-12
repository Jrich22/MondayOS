import { describe, it, expect } from "vitest";
import type { CueEvent, Guest, GuestRole } from "./types";
import { newGuest } from "./guests-select";
import { buildPeople, type Person } from "./people";
import { personInsights, personSummary, recommendedEvents } from "./person-ai";

function mkEvent(over: Partial<CueEvent> & { id: string }): CueEvent {
  return {
    title: `Event ${over.id}`,
    classification: "portfolio",
    summary: "",
    startsAt: "2026-06-01T18:00:00Z",
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
  email: string,
  over: {
    company?: string;
    title?: string;
    portfolio?: string;
    roles?: GuestRole[];
    vip?: boolean;
    checkedIn?: boolean;
    rsvp?: Guest["attendance"]["rsvp"];
  } = {},
): Guest {
  seq += 1;
  const base = newGuest(eventId, `g-${seq}`, "2026-05-01T00:00:00Z");
  const [first, last] = email.split("@")[0].split(".");
  return {
    ...base,
    identity: { firstName: first ?? "A", lastName: last ?? "B", email },
    professional: { company: over.company, jobTitle: over.title, portfolioCompanyId: over.portfolio },
    roles: over.roles ?? [],
    vip: over.vip ?? false,
    attendance: {
      ...base.attendance,
      rsvp: over.rsvp ?? "confirmed",
      checkedIn: over.checkedIn ?? true,
      checkedInAt: (over.checkedIn ?? true) ? "2026-06-01T18:30:00Z" : undefined,
    },
  };
}

const e1 = mkEvent({ id: "e1", classification: "founder", startsAt: "2026-06-01T18:00:00Z" });
const e2 = mkEvent({ id: "e2", classification: "founder", startsAt: "2026-07-01T18:00:00Z" });

describe("personInsights", () => {
  it("reports attendance history and reliability", () => {
    const people = buildPeople(
      [
        guest("e1", "ava.chen@x.com", { checkedIn: true }),
        guest("e2", "ava.chen@x.com", { checkedIn: true }),
      ],
      [e1, e2],
    );
    const ins = personInsights(people[0], people);
    expect(ins.find((i) => i.id === "history")?.text).toContain("2 events");
    expect(ins.find((i) => i.id === "reliable")?.text).toContain("all 2");
  });

  it("surfaces founder-circuit affinity and network sector", () => {
    const people = buildPeople(
      [
        guest("e1", "ava.chen@x.com", { roles: ["founder"], portfolio: "co-meridian", checkedIn: true }),
        guest("e2", "ava.chen@x.com", { roles: ["founder"], portfolio: "co-meridian", checkedIn: true }),
        // a healthcare co-attendee at both events → network sector = Healthcare
        guest("e1", "ben.bio@x.com", { portfolio: "co-meridian" }),
        guest("e2", "ben.bio@x.com", { portfolio: "co-meridian" }),
      ],
      [e1, e2],
    );
    const ava = people.find((p) => p.email === "ava.chen@x.com")!;
    const ins = personInsights(ava, people);
    expect(ins.find((i) => i.id === "founder-dinners")?.text.toLowerCase()).toContain("healthcare");
    expect(ins.find((i) => i.id === "network-sector")?.text.toLowerCase()).toContain("healthcare");
  });

  it("describes the relationship to the current organizer", () => {
    const people = buildPeople(
      [
        guest("e1", "ava.chen@x.com"),
        guest("e2", "ava.chen@x.com"),
        guest("e1", "dana.host@x.com"),
        guest("e2", "dana.host@x.com"),
      ],
      [e1, e2],
    );
    const ava = people.find((p) => p.email === "ava.chen@x.com")!;
    const dana = people.find((p) => p.email === "dana.host@x.com")!;
    const rel = personInsights(ava, people, dana).find((i) => i.id === "relationship");
    expect(rel?.text).toContain("Strong relationship");
    expect(rel?.text).toContain(dana.firstName);
  });

  it("caps the number of insights", () => {
    const people = buildPeople([guest("e1", "ava.chen@x.com", { vip: true })], [e1]);
    expect(personInsights(people[0], people, undefined, 1)).toHaveLength(1);
  });
});

describe("personSummary", () => {
  it("opens with role and company and reads as one paragraph", () => {
    const people = buildPeople(
      [guest("e1", "ava.chen@x.com", { company: "Lattice AI", title: "CEO", vip: true })],
      [e1],
    );
    const s = personSummary(people[0], people);
    // Fixture names derive from lowercase emails; assert case-insensitively.
    expect(s.toLowerCase()).toContain("ava is ceo at lattice ai.");
    expect(s).toContain("VIP");
  });
});

describe("recommendedEvents", () => {
  function healthcarePerson(): { person: Person; all: Person[] } {
    const people = buildPeople(
      [guest("e1", "ava.chen@x.com", { roles: ["founder"], portfolio: "co-meridian" })],
      [e1],
    );
    return { person: people[0], all: people };
  }

  it("recommends an upcoming event matching portfolio sector, with a reason", () => {
    const { person } = healthcarePerson();
    const upcoming = mkEvent({
      id: "up",
      status: "upcoming",
      classification: "founder",
      startsAt: "2026-09-01T18:00:00Z",
      portfolio: [{ id: "co-meridian", name: "Meridian Bio", stage: "Series B" }],
    });
    const recs = recommendedEvents(person, [e1, upcoming]);
    expect(recs).toHaveLength(1);
    expect(recs[0].event.id).toBe("up");
    expect(recs[0].reason.toLowerCase()).toContain("healthcare");
  });

  it("excludes events the person is already on and past events", () => {
    const { person } = healthcarePerson();
    // e1 is in the past AND the person is already on it → never recommended.
    const recs = recommendedEvents(person, [e1]);
    expect(recs).toHaveLength(0);
  });
});
