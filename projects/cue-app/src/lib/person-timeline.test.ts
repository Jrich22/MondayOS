import { describe, it, expect } from "vitest";
import type { CueEvent, Guest } from "./types";
import { newGuest } from "./guests-select";
import { buildPeople } from "./people";
import { personTimeline } from "./person-timeline";

function mkEvent(id: string, startsAt: string, status: CueEvent["status"] = "done"): CueEvent {
  return {
    id,
    title: `Event ${id}`,
    classification: "portfolio",
    summary: "",
    startsAt,
    timezone: "America/Los_Angeles",
    status,
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
function guest(eventId: string, email: string, over: Partial<Guest> = {}): Guest {
  seq += 1;
  const base = newGuest(eventId, `g-${seq}`, "2026-05-01T00:00:00Z");
  const [first, last] = email.split("@")[0].split(".");
  return {
    ...base,
    identity: { firstName: first ?? "A", lastName: last ?? "B", email },
    ...over,
  };
}

const e1 = mkEvent("e1", "2026-06-01T18:00:00Z");
const e2 = mkEvent("e2", "2026-07-01T18:00:00Z");

describe("personTimeline", () => {
  it("emits the funnel for a checked-in VIP speaker at a wrapped event", () => {
    const g = guest("e1", "ava.chen@x.com", {
      vip: true,
      tags: ["keynote"],
      attendance: {
        rsvp: "confirmed",
        checkedIn: true,
        checkedInAt: "2026-06-01T18:40:00Z",
        noShow: false,
        waitlisted: false,
        plusOnes: 0,
      },
      communication: { invitationSent: true, reminderSent: true, thankYouSent: true },
    });
    const people = buildPeople([g], [e1]);
    const t = personTimeline(people[0]);
    const kinds = t.map((e) => e.kind);
    // Funnel order within the event (no co-attendee, so no "met").
    expect(kinds).toEqual(["invited", "rsvp", "checkedin", "vip", "speaker", "thankyou", "survey"]);
    expect(t.find((e) => e.kind === "rsvp")?.label).toBe("RSVP'd — Confirmed");
  });

  it("orders newest event first while keeping each event's funnel intact", () => {
    const guests = [
      guest("e1", "ava.chen@x.com", {
        attendance: { rsvp: "confirmed", checkedIn: true, checkedInAt: "2026-06-01T19:00:00Z", noShow: false, waitlisted: false, plusOnes: 0 },
      }),
      guest("e2", "ava.chen@x.com", {
        attendance: { rsvp: "confirmed", checkedIn: false, noShow: false, waitlisted: false, plusOnes: 0 },
      }),
    ];
    const people = buildPeople(guests, [e1, e2]);
    const t = personTimeline(people[0]);
    // e2 (newest) block first, then e1.
    const firstEventId = t[0].eventId;
    expect(firstEventId).toBe("e2");
    // Within e1, invited precedes checkedin even though checkedInAt is later than start.
    const e1Kinds = t.filter((e) => e.eventId === "e1").map((e) => e.kind);
    expect(e1Kinds.indexOf("invited")).toBeLessThan(e1Kinds.indexOf("checkedin"));
  });

  it("adds a 'Met' entry naming a real co-attendee at a shared event", () => {
    const guests = [
      guest("e1", "ava.chen@x.com", {
        attendance: { rsvp: "confirmed", checkedIn: true, checkedInAt: "2026-06-01T18:30:00Z", noShow: false, waitlisted: false, plusOnes: 0 },
      }),
      guest("e1", "ben.ford@x.com", { vip: true }),
    ];
    const people = buildPeople(guests, [e1]);
    const ava = people.find((p) => p.email === "ava.chen@x.com")!;
    const met = personTimeline(ava, people).find((e) => e.kind === "met");
    // Fixture names derive from lowercase emails, so match case-insensitively.
    expect(met?.label.toLowerCase()).toContain("ben ford");
  });

  it("omits a survey entry for a future event", () => {
    const upcoming = mkEvent("eu", "2026-09-01T18:00:00Z", "upcoming");
    const g = guest("eu", "ava.chen@x.com", {
      attendance: { rsvp: "confirmed", checkedIn: true, checkedInAt: "2026-09-01T18:30:00Z", noShow: false, waitlisted: false, plusOnes: 0 },
    });
    const people = buildPeople([g], [upcoming]);
    expect(personTimeline(people[0]).some((e) => e.kind === "survey")).toBe(false);
  });
});
