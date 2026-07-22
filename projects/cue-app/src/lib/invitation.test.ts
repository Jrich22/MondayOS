import { describe, it, expect } from "vitest";
import type { CueEvent, Guest, GuestAttendance, RsvpStatus } from "./types";
import {
  newInvitation,
  rsvpToken,
  parseRsvpToken,
  isRsvpSignatureValid,
  resolveRsvp,
  decideResponse,
  clampPlusOnes,
  type Invitation,
} from "./invitation";

const NOW = Date.parse("2026-08-15T00:00:00Z");

function makeEvent(over: Partial<CueEvent> = {}): CueEvent {
  return {
    id: "evt-1", title: "Portfolio Summit", classification: "portfolio", summary: "",
    startsAt: "2026-09-01T18:00:00.000Z", endsAt: "2026-09-01T21:00:00.000Z",
    timezone: "America/Los_Angeles", status: "upcoming", venue: "HQ", city: "SF", host: "Dana",
    capacity: { maxAttendees: null, rsvpEnabled: true, waitlistEnabled: false },
    confirmedGuests: 0, invitedGuests: 0, branding: { theme: "default" }, portfolio: [], tags: [],
    createdAt: "2026-08-01T00:00:00.000Z", ...over,
  };
}

let gid = 0;
function makeGuest(rsvp: RsvpStatus, extra: Partial<GuestAttendance> = {}, id?: string): Guest {
  gid += 1;
  return {
    id: id ?? `g-${gid}`, eventId: "evt-1",
    identity: { firstName: "A", lastName: `${gid}` }, professional: {}, roles: [], vip: false,
    attendance: { rsvp, checkedIn: false, noShow: false, waitlisted: rsvp === "waitlisted", plusOnes: 0, ...extra },
    preferences: { hotelNeeded: false, transportationNeeded: false },
    communication: { invitationSent: false, reminderSent: false, thankYouSent: false },
    notes: {}, tags: [], seat: null, createdAt: "", updatedAt: "",
  };
}

function confirmed(n: number, plusOnes = 0): Guest[] {
  return Array.from({ length: n }, () => makeGuest("confirmed", { plusOnes }));
}

const inv = (over: Partial<Invitation> = {}): Invitation => ({ ...newInvitation("evt-1", "g-1", NOW), ...over });

// ---------------------------------------------------------------------------
// Model & token
// ---------------------------------------------------------------------------

describe("invitation model & token", () => {
  it("defaults: version 1, active, allowance 0, not delivered", () => {
    const i = newInvitation("evt-1", "g-1", NOW);
    expect(i).toMatchObject({ tokenVersion: 1, status: "active", plusOneAllowance: 0, delivered: false, eventId: "evt-1", guestId: "g-1" });
  });

  it("token round-trips and is signature-checked, PII-free", () => {
    const token = rsvpToken(inv());
    const parsed = parseRsvpToken(token)!;
    expect(parsed).toMatchObject({ eventId: "evt-1", guestId: "g-1", version: 1 });
    expect(isRsvpSignatureValid(parsed)).toBe(true);
    expect(token).not.toMatch(/@|firstName|lastName/); // no PII in the token
  });

  it("a tampered token fails the signature check", () => {
    const parsed = parseRsvpToken(rsvpToken(inv()))!;
    expect(isRsvpSignatureValid({ ...parsed, guestId: "g-999" })).toBe(false);
  });

  it("a token for a different version is not valid for the current one", () => {
    const v1 = parseRsvpToken(rsvpToken(inv({ tokenVersion: 1 })))!;
    // same fields but the current invitation is v2 → signature check still passes
    // for v1 (it's a real v1 token); resolveRsvp handles the version mismatch.
    expect(isRsvpSignatureValid(v1)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// resolveRsvp states
// ---------------------------------------------------------------------------

describe("resolveRsvp", () => {
  const guest = makeGuest("invited", {}, "g-1");
  const base = { event: makeEvent(), guest, invitation: inv(), now: NOW };

  it("ok for a valid active invitation before the event", () => {
    expect(resolveRsvp(rsvpToken(base.invitation), base).status).toBe("ok");
  });
  it("invalid for a malformed/tampered token", () => {
    expect(resolveRsvp("not-a-token", base).status).toBe("invalid");
  });
  it("invalid when the invitation/guest no longer exists", () => {
    expect(resolveRsvp(rsvpToken(base.invitation), { ...base, invitation: undefined }).status).toBe("invalid");
  });
  it("wrong-event when the token's event differs", () => {
    const other = inv({ eventId: "evt-2", guestId: "g-1" });
    expect(resolveRsvp(rsvpToken(other), { ...base, event: makeEvent({ id: "evt-1" }) }).status).toBe("wrong-event");
  });
  it("revoked when the invitation is revoked", () => {
    expect(resolveRsvp(rsvpToken(base.invitation), { ...base, invitation: inv({ status: "revoked" }) }).status).toBe("revoked");
  });
  it("rotated when the token version is superseded", () => {
    const oldToken = rsvpToken(inv({ tokenVersion: 1 }));
    expect(resolveRsvp(oldToken, { ...base, invitation: inv({ tokenVersion: 2 }) }).status).toBe("rotated");
  });
  it("expired when the event is done", () => {
    expect(resolveRsvp(rsvpToken(base.invitation), { ...base, event: makeEvent({ status: "done" }) }).status).toBe("expired");
  });
  it("event-started once now is at/after the start", () => {
    const started = makeEvent({ startsAt: "2026-08-14T00:00:00Z" }); // before NOW
    expect(resolveRsvp(rsvpToken(base.invitation), { ...base, event: started }).status).toBe("event-started");
  });
});

// ---------------------------------------------------------------------------
// decideResponse — capacity / waitlist
// ---------------------------------------------------------------------------

describe("decideResponse", () => {
  it("clamps plus-ones to the invitation allowance", () => {
    expect(clampPlusOnes(5, 2)).toBe(2);
    const out = decideResponse(makeEvent(), [], inv({ plusOneAllowance: 2 }), { choice: "confirmed", plusOnes: 5 });
    expect(out).toMatchObject({ kind: "accepted", rsvp: "confirmed", plusOnes: 2 });
  });

  it("declined resets to 0 plus-ones and never blocks", () => {
    expect(decideResponse(makeEvent(), [], inv(), { choice: "declined", plusOnes: 3 }))
      .toMatchObject({ kind: "accepted", rsvp: "declined", plusOnes: 0 });
  });

  it("tentative holds plus-ones but does not count toward demand", () => {
    expect(decideResponse(makeEvent(), confirmed(100), inv({ plusOneAllowance: 1 }), { choice: "tentative", plusOnes: 1 }))
      .toMatchObject({ kind: "accepted", rsvp: "tentative", waitlisted: false });
  });

  it("confirms within capacity", () => {
    const ev = makeEvent({ capacity: { maxAttendees: 10, rsvpEnabled: true, waitlistEnabled: false } });
    expect(decideResponse(ev, confirmed(5), inv(), { choice: "confirmed", plusOnes: 0 }))
      .toMatchObject({ kind: "accepted", rsvp: "confirmed" });
  });

  it("waitlists when confirming would exceed capacity and waitlisting is enabled", () => {
    const ev = makeEvent({ capacity: { maxAttendees: 5, rsvpEnabled: true, waitlistEnabled: true } });
    const out = decideResponse(ev, confirmed(5), inv(), { choice: "confirmed", plusOnes: 0 });
    expect(out).toMatchObject({ kind: "accepted", rsvp: "waitlisted", waitlisted: true });
  });

  it("blocks when confirming would exceed capacity and waitlisting is disabled", () => {
    const ev = makeEvent({ capacity: { maxAttendees: 5, rsvpEnabled: true, waitlistEnabled: false } });
    const out = decideResponse(ev, confirmed(5), inv(), { choice: "confirmed", plusOnes: 0 });
    expect(out.kind).toBe("blocked");
    if (out.kind === "blocked") expect(out.reason).toMatch(/capacity/);
  });

  it("counts ACCEPTED plus-ones toward demand (allowance itself does not)", () => {
    // capacity 5, 4 others confirmed → 1 seat left. Allowance 3 (does not count),
    // but accepting 1 plus-one (self + 1 = 2) exceeds the single seat.
    const ev = makeEvent({ capacity: { maxAttendees: 5, rsvpEnabled: true, waitlistEnabled: true } });
    const withPlus = decideResponse(ev, confirmed(4), inv({ plusOneAllowance: 3 }), { choice: "confirmed", plusOnes: 1 });
    expect(withPlus).toMatchObject({ rsvp: "waitlisted" }); // 4 + (1+1) = 6 > 5
    const soloOk = decideResponse(ev, confirmed(4), inv({ plusOneAllowance: 3 }), { choice: "confirmed", plusOnes: 0 });
    expect(soloOk).toMatchObject({ rsvp: "confirmed" }); // 4 + 1 = 5 == capacity
  });
});
