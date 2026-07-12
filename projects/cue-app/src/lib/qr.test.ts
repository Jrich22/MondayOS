import { describe, it, expect } from "vitest";
import type { CueEvent, Guest, RsvpStatus } from "./types";
import { newGuest } from "./guests-select";
import {
  qrPayload,
  qrPayloadFor,
  parseQrPayload,
  isSignatureValid,
  validateScan,
  qrMatrix,
  QR_PREFIX,
  DUPLICATE_WINDOW_MS,
} from "./qr";

const NOW = Date.parse("2026-07-08T19:00:00.000Z");

function makeEvent(over: Partial<CueEvent> = {}): CueEvent {
  return {
    id: "evt-1",
    title: "Summit",
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
    ...over,
  };
}

function g(id: string, over: { checkedIn?: boolean; rsvp?: RsvpStatus; eventId?: string } = {}): Guest {
  const base = newGuest(over.eventId ?? "evt-1", id, "2026-07-01T00:00:00.000Z");
  return {
    ...base,
    identity: { firstName: "Ava", lastName: "Chen" },
    attendance: {
      ...base.attendance,
      rsvp: over.rsvp ?? "confirmed",
      checkedIn: over.checkedIn ?? false,
      checkedInAt: over.checkedIn ? "2026-07-08T18:30:00.000Z" : undefined,
    },
  };
}

describe("qr identity", () => {
  it("is stable — the same guest+event always yields the same payload", () => {
    const guest = g("gst-1");
    expect(qrPayload(guest)).toBe(qrPayload(guest));
    expect(qrPayload(guest)).toBe(qrPayloadFor("evt-1", "gst-1"));
  });

  it("differs across guests and across events", () => {
    expect(qrPayload(g("gst-1"))).not.toBe(qrPayload(g("gst-2")));
    expect(qrPayloadFor("evt-1", "gst-1")).not.toBe(qrPayloadFor("evt-2", "gst-1"));
  });

  it("encodes only event id, guest id, and a checksum — no PII", () => {
    const guest: Guest = {
      ...g("gst-1"),
      identity: {
        firstName: "Ava",
        lastName: "Chen",
        email: "ava@secret.com",
        phone: "+1-555-0100",
      },
      notes: { internal: "sensitive" },
      preferences: { hotelNeeded: false, transportationNeeded: false, dietary: "vegan" },
    };
    const payload = qrPayload(guest);
    expect(payload.startsWith(QR_PREFIX)).toBe(true);
    for (const pii of ["ava@secret.com", "555-0100", "sensitive", "vegan"]) {
      expect(payload.includes(pii)).toBe(false);
    }
  });

  it("round-trips through parse with a valid signature", () => {
    const cred = parseQrPayload(qrPayload(g("gst-1")));
    expect(cred).toMatchObject({ eventId: "evt-1", guestId: "gst-1" });
    expect(isSignatureValid(cred!)).toBe(true);
  });

  it("rejects a tampered signature", () => {
    const forged = parseQrPayload(`${QR_PREFIX}|evt-1|gst-999|deadbeef`);
    expect(isSignatureValid(forged!)).toBe(false);
  });
});

describe("validateScan", () => {
  const ctx = (over: Partial<Parameters<typeof validateScan>[1]> = {}) => ({
    event: makeEvent(),
    guests: [g("gst-1"), g("gst-2", { checkedIn: true })],
    now: NOW,
    ...over,
  });

  it("returns ready for a valid, un-checked-in attendee on this event", () => {
    const res = validateScan(qrPayloadFor("evt-1", "gst-1"), ctx());
    expect(res.status).toBe("ready");
    expect(res.guest?.id).toBe("gst-1");
  });

  it("rejects a malformed payload as invalid", () => {
    expect(validateScan("not-a-code", ctx()).status).toBe("invalid");
    expect(validateScan("", ctx()).status).toBe("invalid");
  });

  it("rejects a tampered checksum as invalid", () => {
    expect(validateScan(`${QR_PREFIX}|evt-1|gst-1|000000`, ctx()).status).toBe("invalid");
  });

  it("rejects a credential minted for another event as wrong-event", () => {
    expect(validateScan(qrPayloadFor("evt-2", "gst-1"), ctx()).status).toBe("wrong-event");
  });

  it("rejects a valid code for an unknown attendee as invalid", () => {
    expect(validateScan(qrPayloadFor("evt-1", "gst-ghost"), ctx()).status).toBe("invalid");
  });

  it("reports already-in for an attendee already checked in", () => {
    const res = validateScan(qrPayloadFor("evt-1", "gst-2"), ctx());
    expect(res.status).toBe("already-in");
  });

  it("suppresses a double-fire of the same code as duplicate", () => {
    const payload = qrPayloadFor("evt-1", "gst-1");
    const res = validateScan(payload, ctx({ last: { payload, at: NOW - 500 } }));
    expect(res.status).toBe("duplicate");
    // Outside the window, the same code is processed normally again.
    const later = validateScan(payload, ctx({ last: { payload, at: NOW - DUPLICATE_WINDOW_MS - 1 } }));
    expect(later.status).toBe("ready");
  });

  it("marks a credential for a wrapped event as expired", () => {
    const res = validateScan(qrPayloadFor("evt-1", "gst-1"), ctx({ event: makeEvent({ status: "done" }) }));
    expect(res.status).toBe("expired");
  });
});

describe("qrMatrix", () => {
  it("is deterministic per payload and carries three finder patterns", () => {
    const payload = qrPayload(g("gst-1"));
    const a = qrMatrix(payload);
    const b = qrMatrix(payload);
    expect(a).toEqual(b);
    // A 7×7 finder square: outer ring on, the module just inside it off.
    const size = a.length;
    for (const [r0, c0] of [[0, 0], [0, size - 7], [size - 7, 0]] as const) {
      expect(a[r0][c0]).toBe(true); // corner of the ring
      expect(a[r0 + 1][c0 + 1]).toBe(false); // ring gap
      expect(a[r0 + 3][c0 + 3]).toBe(true); // center
    }
  });

  it("renders a different glyph for a different payload", () => {
    expect(qrMatrix(qrPayload(g("gst-1")))).not.toEqual(qrMatrix(qrPayload(g("gst-2"))));
  });
});
