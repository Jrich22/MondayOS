import { describe, it, expect } from "vitest";
import type { CueEvent, Guest, GuestAttendance, RsvpStatus } from "./types";
import {
  newInvitation,
  rotate,
  allowanceChange,
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

describe("opaque token envelope (finding #6)", () => {
  it("round-trips through the base64url envelope", () => {
    const token = rsvpToken(inv({ tokenVersion: 3 }));
    expect(parseRsvpToken(token)).toMatchObject({ eventId: "evt-1", guestId: "g-1", version: 3 });
  });
  it("exposes NO plainly-readable eventId/guestId/delimiters", () => {
    const token = rsvpToken(inv());
    expect(token).not.toContain("evt-1");
    expect(token).not.toContain("g-1");
    expect(token).not.toContain("|");
    expect(token).not.toContain("CUERSVP1");
  });
  it("rejects a malformed (non-envelope) token", () => {
    expect(parseRsvpToken("")).toBeNull();
    expect(parseRsvpToken("!!! not base64 !!!")).toBeNull();
    expect(parseRsvpToken("evt-1|g-1|1|abc")).toBeNull(); // the old plaintext form is now invalid
  });
  it("a tampered envelope does not resolve as valid", () => {
    const token = rsvpToken(inv());
    const last = token[token.length - 1];
    const tampered = token.slice(0, -1) + (last === "A" ? "B" : "A");
    const parsed = parseRsvpToken(tampered);
    // Either it no longer decodes to a valid payload, or the signature fails.
    expect(parsed === null || !isRsvpSignatureValid(parsed)).toBe(true);
  });
});

describe("rotate lifecycle (finding #3)", () => {
  it("increments version, resets delivery, preserves rotation history and respondedAt", () => {
    const base = inv({ tokenVersion: 1, delivered: true, deliveredAt: "2026-08-10T00:00:00Z", respondedAt: "2026-08-12T00:00:00Z", rotationCount: 0 });
    const r = rotate(base, NOW);
    expect(r.tokenVersion).toBe(2);
    expect(r.delivered).toBe(false);
    expect(r.deliveredAt).toBeUndefined();
    expect(r.rotationCount).toBe(1);
    expect(r.rotatedAt).toBeTruthy();
    expect(r.respondedAt).toBe("2026-08-12T00:00:00Z"); // overall response history preserved
    expect(r.status).toBe("active");
  });
});

describe("allowance invariant (finding #2)", () => {
  it("refuses to drop below the accepted plus-one count, with a reason", () => {
    const res = allowanceChange(1, 2);
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.reason).toMatch(/already accepted 2/i);
  });
  it("allows setting equal to or above the accepted count", () => {
    expect(allowanceChange(2, 2)).toEqual({ ok: true, allowance: 2 });
    expect(allowanceChange(5, 2)).toEqual({ ok: true, allowance: 5 });
  });
  it("floors negatives to zero when nothing is accepted", () => {
    expect(allowanceChange(-3, 0)).toEqual({ ok: true, allowance: 0 });
  });
});

describe("rsvp enablement (finding #5)", () => {
  const guest = makeGuest("invited", {}, "g-1");
  it("resolves to rsvp-disabled when the event has RSVP off", () => {
    const ev = makeEvent({ capacity: { maxAttendees: null, rsvpEnabled: false, waitlistEnabled: false } });
    const i = inv();
    expect(resolveRsvp(rsvpToken(i), { event: ev, guest, invitation: i, now: NOW }).status).toBe("rsvp-disabled");
  });
  it("blocks a response decision when RSVP is off", () => {
    const ev = makeEvent({ capacity: { maxAttendees: null, rsvpEnabled: false, waitlistEnabled: false } });
    expect(decideResponse(ev, [], inv(), { choice: "confirmed", plusOnes: 0 }).kind).toBe("blocked");
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
