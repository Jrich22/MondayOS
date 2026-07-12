import { describe, it, expect } from "vitest";
import type { CueEvent, Guest } from "./types";
import { newGuest, withCheckIn } from "./guests-select";
import { walkInGuest, canCreateWalkIn, scanMeta, shouldCheckIn } from "./checkin";
import { buildBadge } from "./badge";
import { validateScan, qrPayload } from "./qr";
import { liveMetrics, recentArrivals } from "./rollcall";
import { attendanceHealth } from "./mission";
import { buildPeople } from "./people";
import { personTimeline } from "./person-timeline";

const NOW = Date.parse("2026-07-08T19:00:00.000Z");
const NOW_ISO = new Date(NOW).toISOString();

function makeEvent(over: Partial<CueEvent> = {}): CueEvent {
  return {
    id: "evt-1",
    title: "Founders Summit",
    classification: "founder",
    summary: "",
    startsAt: "2026-07-08T18:00:00.000Z",
    endsAt: "2026-07-08T22:00:00.000Z",
    timezone: "America/Los_Angeles",
    status: "live",
    venue: "The Pavilion",
    city: "SF",
    host: "Cue",
    capacity: { maxAttendees: 100, rsvpEnabled: true, waitlistEnabled: false },
    confirmedGuests: 0,
    invitedGuests: 0,
    branding: { theme: "emerald" },
    portfolio: [],
    tags: [],
    createdAt: "2026-07-01T00:00:00.000Z",
    ...over,
  };
}

function guest(id: string, over: Partial<Guest> = {}): Guest {
  const base = newGuest("evt-1", id, "2026-07-01T00:00:00.000Z");
  return {
    ...base,
    identity: { firstName: "Ava", lastName: "Chen", email: `${id}@x.com` },
    professional: { company: "Lattice", jobTitle: "CEO" },
    roles: ["founder"],
    attendance: { ...base.attendance, rsvp: "confirmed" },
    communication: { ...base.communication, invitationSent: true },
    ...over,
  };
}

describe("walk-in flow", () => {
  it("creates an attendee that is a walk-in, checked in, and QR-addressable", () => {
    const w = walkInGuest(
      makeEvent(),
      { firstName: "Dana", lastName: "Lee", company: "Northwind", vip: true, roles: ["investor"] },
      "gst-walk",
      NOW_ISO,
    );
    expect(w.identity).toMatchObject({ firstName: "Dana", lastName: "Lee" });
    expect(w.attendance.checkedIn).toBe(true);
    expect(w.attendance.checkedInAt).toBe(NOW_ISO);
    expect(w.attendance.rsvp).toBe("invited"); // never a confirmed RSVP
    expect(w.vip).toBe(true);
    expect(w.tags).toContain("walk-in");
    // A stable QR identity resolves right back to this attendee.
    const res = validateScan(qrPayload(w), { event: makeEvent(), guests: [w], now: NOW });
    // Already checked in on creation, so a re-scan reads already-in — the identity resolved.
    expect(res.guest?.id).toBe("gst-walk");
    expect(res.status).toBe("already-in");
  });

  it("guards against creating an empty walk-in", () => {
    expect(canCreateWalkIn({ firstName: "", lastName: "" })).toBe(false);
    expect(canCreateWalkIn({ firstName: "Dana", lastName: "" })).toBe(true);
  });
});

describe("badge rendering", () => {
  it("projects preferred name, company, title, role, VIP, org color, and QR", () => {
    const badge = buildBadge(
      guest("gst-1", { identity: { firstName: "Robert", lastName: "Chen", preferredName: "Rob" }, vip: true }),
      makeEvent(),
    );
    expect(badge.name).toBe("Rob Chen");
    expect(badge.company).toBe("Lattice");
    expect(badge.title).toBe("CEO");
    expect(badge.role).toBe("Founder");
    expect(badge.vip).toBe(true);
    expect(badge.color).toBe("#10b981"); // emerald theme accent
    expect(badge.eventTitle).toBe("Founders Summit");
    expect(badge.qr).toBe(qrPayload(guest("gst-1")));
  });
});

describe("scan outcome presentation", () => {
  it("escalates a ready VIP to a celebration, plain guests to success", () => {
    expect(scanMeta("ready", true).tone).toBe("celebrate");
    expect(scanMeta("ready", false).tone).toBe("success");
    expect(scanMeta("wrong-event", false).tone).toBe("error");
    expect(scanMeta("duplicate", false).tone).toBe("warn");
  });

  it("only a ready scan performs the check-in mutation", () => {
    expect(shouldCheckIn("ready")).toBe(true);
    for (const s of ["already-in", "duplicate", "wrong-event", "expired", "invalid"] as const) {
      expect(shouldCheckIn(s)).toBe(false);
    }
  });
});

/**
 * One source of truth: a scan resolves a guest, and applying the SAME
 * `withCheckIn` seam Roll Call uses updates every downstream surface — Roll Call
 * metrics, Mission Control health, and the relationship timeline — with no
 * duplicate state. These tests exercise that shared pipeline end to end.
 */
describe("check-in is one source of truth", () => {
  function scanAndCheckIn(roster: Guest[], id: string): Guest[] {
    const res = validateScan(qrPayloadFor(id), { event: makeEvent(), guests: roster, now: NOW });
    expect(res.status).toBe("ready");
    const updated = withCheckIn(res.guest!, true, NOW_ISO);
    return roster.map((g) => (g.id === updated.id ? updated : g));
  }
  // Local helper: qrPayloadFor via the guest's known ids.
  function qrPayloadFor(id: string): string {
    return qrPayload(guest(id));
  }

  it("Roll Call: a scan increments checked-in and surfaces in recent arrivals", () => {
    const before = [guest("gst-1"), guest("gst-2")];
    expect(liveMetrics(before, makeEvent(), NOW).checkedIn).toBe(0);
    const after = scanAndCheckIn(before, "gst-1");
    expect(liveMetrics(after, makeEvent(), NOW).checkedIn).toBe(1);
    expect(recentArrivals(after).map((g) => g.id)).toContain("gst-1");
  });

  it("Mission Control: attendance health reflects the same check-in", () => {
    const events = [makeEvent()];
    const before = [guest("gst-1"), guest("gst-2")];
    expect(attendanceHealth(events, before, NOW).checkedIn).toBe(0);
    const after = scanAndCheckIn(before, "gst-1");
    expect(attendanceHealth(events, after, NOW).checkedIn).toBe(1);
  });

  it("Relationship Intelligence: a Checked In entry appends to the person's timeline", () => {
    const events = [makeEvent()];
    const before = [guest("gst-1")];
    const personBefore = buildPeople(before, events)[0];
    expect(personTimeline(personBefore).some((e) => e.kind === "checkedin")).toBe(false);

    const after = scanAndCheckIn(before, "gst-1");
    const personAfter = buildPeople(after, events)[0];
    const arrival = personTimeline(personAfter).find((e) => e.kind === "checkedin");
    expect(arrival).toBeTruthy();
    expect(arrival?.label).toBe("Checked in");
    expect(arrival?.at).toBe(NOW_ISO);
  });
});
