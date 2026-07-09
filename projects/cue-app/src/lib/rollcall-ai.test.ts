import { describe, it, expect } from "vitest";
import type { CueEvent, Guest, RsvpStatus, GuestRole } from "./types";
import { newGuest } from "./guests-select";
import { answerCopilot, COPILOT_PROMPTS } from "./rollcall-ai";

const NOW = Date.parse("2026-07-08T19:00:00.000Z");
const MIN = 60 * 1000;

function makeEvent(): CueEvent {
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
  };
}

let seq = 0;
function g(opts: {
  first?: string;
  rsvp?: RsvpStatus;
  vip?: boolean;
  roles?: GuestRole[];
  checkedInMinsAgo?: number;
} = {}): Guest {
  seq += 1;
  const base = newGuest("evt-1", `g-${seq}`, "2026-07-01T00:00:00.000Z");
  return {
    ...base,
    identity: { firstName: opts.first ?? `First${seq}`, lastName: `Last${seq}`, email: `p${seq}@x.com` },
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
  };
}

const ctx = (guests: Guest[]) => ({ guests, event: makeEvent(), now: NOW });

describe("COPILOT_PROMPTS", () => {
  it("exposes the five specified suggested prompts", () => {
    expect(COPILOT_PROMPTS.map((p) => p.id)).toEqual([
      "not-arrived",
      "remaining-vips",
      "founders-here",
      "greet-next",
      "summary",
    ]);
  });
});

describe("answerCopilot", () => {
  it("who hasn't arrived — counts confirmed absentees and names them", () => {
    const a = answerCopilot("not-arrived", ctx([
      g({ rsvp: "confirmed", checkedInMinsAgo: 5 }),
      g({ rsvp: "confirmed", vip: true }),
      g({ rsvp: "confirmed" }),
    ]));
    expect(a.headline).toContain("2 confirmed guests haven't arrived");
    expect(a.headline).toContain("1 VIP");
    expect(a.guests).toHaveLength(2);
  });

  it("who hasn't arrived — celebrates an empty door", () => {
    const a = answerCopilot("not-arrived", ctx([g({ checkedInMinsAgo: 1 })]));
    expect(a.headline).toContain("Everyone expected is here");
    expect(a.guests).toHaveLength(0);
  });

  it("remaining VIPs — only un-arrived VIPs", () => {
    const a = answerCopilot("remaining-vips", ctx([
      g({ vip: true, checkedInMinsAgo: 1 }),
      g({ vip: true }),
      g({ vip: false }),
    ]));
    expect(a.headline).toContain("1 VIP still to arrive");
    expect(a.guests).toHaveLength(1);
  });

  it("founders here — counts checked-in founders against expected", () => {
    const a = answerCopilot("founders-here", ctx([
      g({ roles: ["founder"], checkedInMinsAgo: 3 }),
      g({ roles: ["founder"], checkedInMinsAgo: 1 }),
      g({ roles: ["founder"] }),
      g({ roles: ["investor"], checkedInMinsAgo: 1 }),
    ]));
    expect(a.headline).toContain("2 founders are here of 3 expected");
  });

  it("greet next — points at the most recent VIP arrival", () => {
    const a = answerCopilot("greet-next", ctx([
      g({ first: "Ada", vip: true, checkedInMinsAgo: 20 }),
      g({ first: "Grace", vip: true, checkedInMinsAgo: 2 }),
    ]));
    expect(a.headline).toContain("Greet Grace");
    expect(a.guests[0].identity.firstName).toBe("Grace");
  });

  it("summary — reports the live board in prose", () => {
    const a = answerCopilot("summary", ctx([
      g({ rsvp: "confirmed", checkedInMinsAgo: 30 }),
      g({ rsvp: "confirmed" }),
    ]));
    expect(a.headline).toContain("1 of 2 expected checked in (50%)");
  });
});
