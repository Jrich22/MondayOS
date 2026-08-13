import type { CueEvent, Guest } from "./types";

/**
 * QR identity + scan validation for the Check-In kiosk (TASK-0045).
 *
 * The whole surface hangs off one idea: a *stable QR identity per guest+event*.
 * The payload encodes ONLY three things — the event id, the guest id, and a
 * checksum that binds them — and NEVER email, phone, notes, dietary needs, or any
 * other PII (the credential is printed on a badge anyone can photograph). Because
 * it is a pure function of two stable ids, the same guest at the same event always
 * produces the same payload, so a badge is reprintable and a scan is verifiable.
 *
 * The scanner resolves a raw payload through `validateScan`, a single pure
 * function that returns one of the required outcomes — Ready, Already Checked In,
 * Expired, Wrong Event, Invalid, Duplicate — off the live roster and the clock.
 * It never mutates: the kiosk applies the check-in through the SAME `withCheckIn`
 * seam Roll Call uses (see lib/guests-select), so there is one source of truth.
 */

export const QR_PREFIX = "CUE1";
const SEP = "|";

/** djb2 → unsigned base36 — the same stable hash style used for person ids.
 *  Exported so the Invite & RSVP token (lib/invitation) reuses the one hash. */
export function hash36(s: string): string {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = (Math.imul(h, 33) + s.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}

/**
 * The checksum that binds a credential to its ids. A hand-edited payload (swapped
 * guest id, forged event) fails this check and resolves as Invalid — the QR
 * carries no secret, but it can't be trivially spoofed into a valid attendee.
 */
function signature(eventId: string, guestId: string): string {
  return hash36(`${eventId}${SEP}${guestId}${SEP}cue-badge-v1`);
}

/** The stable QR payload for explicit ids (used before a guest is in the store). */
export function qrPayloadFor(eventId: string, guestId: string): string {
  return [QR_PREFIX, eventId, guestId, signature(eventId, guestId)].join(SEP);
}

/** The stable QR identity for one attendee at one event. Pure, PII-free. */
export function qrPayload(guest: Guest): string {
  return qrPayloadFor(guest.eventId, guest.id);
}

export interface QrCredential {
  eventId: string;
  guestId: string;
  sig: string;
}

/** Parse a raw scan into its fields, or null if it isn't a Cue credential. */
export function parseQrPayload(raw: string): QrCredential | null {
  const parts = raw.trim().split(SEP);
  if (parts.length !== 4) return null;
  const [prefix, eventId, guestId, sig] = parts;
  if (prefix !== QR_PREFIX || !eventId || !guestId || !sig) return null;
  return { eventId, guestId, sig };
}

/** Whether a parsed credential's checksum matches its ids (tamper check). */
export function isSignatureValid(cred: QrCredential): boolean {
  return cred.sig === signature(cred.eventId, cred.guestId);
}

// --- Scan validation --------------------------------------------------------

export type ScanStatus =
  | "ready"
  | "already-in"
  | "wrong-event"
  | "expired"
  | "invalid"
  | "duplicate";

export interface ScanResult {
  status: ScanStatus;
  /** The resolved attendee, when the payload named a real guest on this event. */
  guest?: Guest;
  /** Short human explanation for the result card. */
  reason: string;
}

export interface ScanContext {
  event: CueEvent;
  /** The current event's live roster — the source of truth. */
  guests: Guest[];
  now: number;
  /** The immediately-previous scan, for double-fire (duplicate) suppression. */
  last?: { payload: string; at: number } | null;
}

/** A repeat of the exact same code within this window reads as a duplicate. */
export const DUPLICATE_WINDOW_MS = 4000;

/**
 * Resolve a raw scan against the live roster. Pure over (payload, roster, clock,
 * last-scan) so every branch is unit-testable and the kiosk stays presentation.
 * The order matters: format/tamper first, then event match, then attendee match,
 * then lifecycle, then the session double-fire guard, then check-in state.
 */
export function validateScan(raw: string, ctx: ScanContext): ScanResult {
  const cred = parseQrPayload(raw);
  if (!cred || !isSignatureValid(cred)) {
    return { status: "invalid", reason: "This code isn't a valid Cue badge." };
  }
  if (cred.eventId !== ctx.event.id) {
    return { status: "wrong-event", reason: "This badge belongs to a different event." };
  }
  const guest = ctx.guests.find((g) => g.id === cred.guestId);
  if (!guest) {
    return { status: "invalid", reason: "No attendee on this event matches the badge." };
  }
  if (ctx.event.status === "done") {
    return { status: "expired", guest, reason: "This event has already ended." };
  }
  const isDoubleFire =
    ctx.last != null &&
    ctx.last.payload === raw &&
    ctx.now - ctx.last.at < DUPLICATE_WINDOW_MS;
  if (isDoubleFire) {
    return { status: "duplicate", guest, reason: "Just scanned — one moment." };
  }
  if (guest.attendance.checkedIn) {
    return { status: "already-in", guest, reason: "Already checked in." };
  }
  return { status: "ready", guest, reason: "Welcome — checking in." };
}

// --- Visual QR glyph --------------------------------------------------------

/** Hash-seeded PRNG (mulberry32) — deterministic modules per payload. */
function seeded(s: string): () => number {
  let a = 0;
  for (let i = 0; i < s.length; i++) a = Math.imul(a ^ s.charCodeAt(i), 2654435761) >>> 0;
  return function () {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Stamp a 7×7 QR finder pattern (+1 quiet separator) at a corner. */
function stampFinder(m: boolean[][], r0: number, c0: number): void {
  const n = m.length;
  for (let r = -1; r <= 7; r++) {
    for (let c = -1; c <= 7; c++) {
      const rr = r0 + r;
      const cc = c0 + c;
      if (rr < 0 || cc < 0 || rr >= n || cc >= n) continue;
      const inBox = r >= 0 && r <= 6 && c >= 0 && c <= 6;
      const ring = r === 0 || r === 6 || c === 0 || c === 6;
      const center = r >= 2 && r <= 4 && c >= 2 && c <= 4;
      m[rr][cc] = inBox && (ring || center); // outside the box = quiet separator
    }
  }
}

/**
 * A deterministic module matrix for rendering a QR-style glyph on the badge.
 * This is a *visual credential*, not a spec-compliant QR — the MVP has no camera
 * to read one — but it is stable per payload and carries the three finder squares
 * a real QR has, so it reads unmistakably as "scan me". Same payload → same glyph.
 */
export function qrMatrix(payload: string, size = 25): boolean[][] {
  const rnd = seeded(payload);
  const m: boolean[][] = Array.from({ length: size }, () =>
    Array.from({ length: size }, () => rnd() > 0.5),
  );
  stampFinder(m, 0, 0);
  stampFinder(m, 0, size - 7);
  stampFinder(m, size - 7, 0);
  return m;
}
