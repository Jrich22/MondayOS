/**
 * Invite & RSVP — Invitation domain and pure logic (slice 1: single-guest loop).
 *
 * Implements the approved product records:
 *   • DEC-0010 — Invite & RSVP operating semantics.
 *   • DEC-0011 — final resolutions (separate Invitation entity; per-invitation
 *                plus-one allowance default 0; FIFO waitlist; token rotation
 *                invalidates prior links; not production-secure).
 *   • DOC-0016 — product definition.
 *
 * Boundaries honored here:
 *   §3 Invitation is a SEPARATE lightweight entity referencing `eventId`/`guestId`.
 *      Guest stays canonical for RSVP response, attendance, preferences, and the
 *      ACTUAL plus-one count; no Event/Guest fields are duplicated here.
 *   §4 Accepted plus-ones count toward projected demand; the allowance does not.
 *      Capacity is a hard constraint reused from the canonical guest demand.
 *   §6 The token follows the client-side `lib/qr` signed-credential precedent and
 *      adds a VERSION so rotation/revocation invalidate all prior links. Tokens
 *      carry no PII and are explicitly NOT production-secure (no server secret).
 *
 * React-free and pure so every rule is unit-testable in one place.
 */
import type { CueEvent, Guest, RsvpStatus } from "./types";
import { hash36 } from "./qr";
import { guestSummary } from "./guests-select";

// ---------------------------------------------------------------------------
// Invitation entity (keyed by guestId; references eventId/guestId only)
// ---------------------------------------------------------------------------

export type InvitationStatus = "active" | "revoked";

export interface Invitation {
  id: string;
  eventId: string;
  guestId: string;
  /** Bumped on rotation; embedded in the token so prior links stop resolving. */
  tokenVersion: number;
  status: InvitationStatus;
  /** Per-invitation plus-one allowance (nonnegative; default 0). */
  plusOneAllowance: number;
  /** Simulated delivery state (no real email/provider). */
  delivered: boolean;
  createdAt: string;
  /** Latest token-issued time (updated on rotation). */
  issuedAt: string;
  deliveredAt?: string;
  revokedAt?: string;
  rotatedAt?: string;
  respondedAt?: string;
  updatedAt: string;
}

/** A fresh active invitation for a guest, allowance 0, token version 1. */
export function newInvitation(eventId: string, guestId: string, now: number): Invitation {
  const iso = new Date(now).toISOString();
  return {
    id: `inv-${eventId}-${guestId}`,
    eventId,
    guestId,
    tokenVersion: 1,
    status: "active",
    plusOneAllowance: 0,
    delivered: false,
    createdAt: iso,
    issuedAt: iso,
    updatedAt: iso,
  };
}

// ---------------------------------------------------------------------------
// Opaque, PII-free, versioned token (extends the lib/qr precedent)
// ---------------------------------------------------------------------------

const RSVP_PREFIX = "CUERSVP1";
const SEP = "|";

/** Checksum binding a token to (event, guest, version). No secret — spoof-
 *  resistant, NOT production-secure (DEC-0011 §6). */
function rsvpSignature(eventId: string, guestId: string, version: number): string {
  return hash36(`${eventId}${SEP}${guestId}${SEP}${version}${SEP}cue-rsvp-v1`);
}

export function rsvpToken(inv: Invitation): string {
  return [
    RSVP_PREFIX,
    inv.eventId,
    inv.guestId,
    String(inv.tokenVersion),
    rsvpSignature(inv.eventId, inv.guestId, inv.tokenVersion),
  ].join(SEP);
}

export interface RsvpTokenParts {
  eventId: string;
  guestId: string;
  version: number;
  sig: string;
}

export function parseRsvpToken(raw: string): RsvpTokenParts | null {
  const parts = raw.trim().split(SEP);
  if (parts.length !== 5) return null;
  const [prefix, eventId, guestId, v, sig] = parts;
  const version = Number(v);
  if (prefix !== RSVP_PREFIX || !eventId || !guestId || !Number.isInteger(version) || !sig) return null;
  return { eventId, guestId, version, sig };
}

export function isRsvpSignatureValid(p: RsvpTokenParts): boolean {
  return p.sig === rsvpSignature(p.eventId, p.guestId, p.version);
}

// ---------------------------------------------------------------------------
// Resolve a token against store state → a response-page status
// ---------------------------------------------------------------------------

export type RsvpResolution =
  | "ok"
  | "invalid"
  | "revoked"
  | "rotated"
  | "wrong-event"
  | "expired"
  | "event-started";

export interface RsvpResolveResult {
  status: RsvpResolution;
  event?: CueEvent;
  guest?: Guest;
  invitation?: Invitation;
  reason: string;
}

export interface RsvpResolveContext {
  event?: CueEvent;
  guest?: Guest;
  invitation?: Invitation;
  now: number;
}

/**
 * Pure resolution of a raw token against the current event/guest/invitation.
 * Order matters: format/tamper → existence → event match → guest match →
 * revoked → rotated (version) → event lifecycle. Never mutates.
 */
export function resolveRsvp(raw: string, ctx: RsvpResolveContext): RsvpResolveResult {
  const p = parseRsvpToken(raw);
  if (!p || !isRsvpSignatureValid(p)) {
    return { status: "invalid", reason: "This response link isn't valid." };
  }
  if (!ctx.event || !ctx.invitation || !ctx.guest) {
    return { status: "invalid", reason: "This invitation no longer exists." };
  }
  if (p.eventId !== ctx.event.id || ctx.invitation.eventId !== ctx.event.id) {
    return { status: "wrong-event", reason: "This link is for a different event." };
  }
  if (ctx.guest.id !== p.guestId || ctx.invitation.guestId !== p.guestId) {
    return { status: "invalid", reason: "This invitation no longer exists." };
  }
  const found = { event: ctx.event, guest: ctx.guest, invitation: ctx.invitation };
  if (ctx.invitation.status === "revoked") {
    return { status: "revoked", ...found, reason: "This invitation has been revoked." };
  }
  if (p.version !== ctx.invitation.tokenVersion) {
    return { status: "rotated", ...found, reason: "This link has been replaced by a newer one." };
  }
  if (ctx.event.status === "done") {
    return { status: "expired", ...found, reason: "This event has already ended." };
  }
  if (ctx.now >= new Date(ctx.event.startsAt).getTime()) {
    return { status: "event-started", ...found, reason: "This event has started — responses are closed." };
  }
  return { status: "ok", ...found, reason: "You can respond below." };
}

// ---------------------------------------------------------------------------
// Response decision — capacity / waitlist (reuses canonical demand)
// ---------------------------------------------------------------------------

export type RsvpChoice = "confirmed" | "tentative" | "declined";

export interface DesiredResponse {
  choice: RsvpChoice;
  plusOnes: number;
}

export type ResponseOutcome =
  | { kind: "accepted"; rsvp: RsvpStatus; plusOnes: number; waitlisted: boolean; message: string }
  | { kind: "blocked"; reason: string };

/** Clamp a requested plus-one count to [0, allowance]. */
export function clampPlusOnes(requested: number, allowance: number): number {
  return Math.max(0, Math.min(Math.floor(requested) || 0, Math.max(0, Math.floor(allowance) || 0)));
}

/**
 * Decide the canonical guest state for a desired response, honoring capacity.
 *
 * `otherGuests` is every guest on the event EXCEPT the responder, so demand is
 * counted once. Reuses `guestSummary` for projected demand (confirmed heads +
 * their plus-ones) — no duplicated calculation. Accepted plus-ones count toward
 * demand; the allowance does not. Over capacity → waitlist (if enabled) or block.
 */
export function decideResponse(
  event: CueEvent,
  otherGuests: Guest[],
  invitation: Invitation,
  desired: DesiredResponse,
): ResponseOutcome {
  const plusOnes = clampPlusOnes(desired.plusOnes, invitation.plusOneAllowance);

  if (desired.choice === "declined") {
    return { kind: "accepted", rsvp: "declined", plusOnes: 0, waitlisted: false,
      message: "You've declined. You can change this while the event hasn't started." };
  }
  if (desired.choice === "tentative") {
    return { kind: "accepted", rsvp: "tentative", plusOnes, waitlisted: false,
      message: "Marked tentative — plus-ones are held but not counted until you confirm." };
  }

  // confirmed — capacity is a hard constraint (DEC-0009 §3 / DEC-0011 §4).
  const capacity = event.capacity.maxAttendees;
  const othersProjected = guestSummary(otherGuests).expectedHeadcount; // reuse
  const thisContribution = 1 + plusOnes;
  if (capacity !== null && othersProjected + thisContribution > capacity) {
    if (event.capacity.waitlistEnabled) {
      return { kind: "accepted", rsvp: "waitlisted", plusOnes, waitlisted: true,
        message: `The event is at capacity (${capacity}). You've been added to the waitlist; the organizer promotes guests manually.` };
    }
    return { kind: "blocked",
      reason: `The event is at capacity (${capacity}) and the waitlist is closed, so your spot can't be confirmed right now.` };
  }
  return { kind: "accepted", rsvp: "confirmed", plusOnes, waitlisted: false,
    message: "You're confirmed. See you there!" };
}
