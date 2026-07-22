import { useSyncExternalStore } from "react";
import { newInvitation, rotate, allowanceChange, type Invitation } from "./invitation";
import { getGuests, updateGuest } from "./guests";

/**
 * Invitation store — the single seam every invitation surface reads/writes
 * through. Mirrors lib/store.ts and lib/planning-store.ts: no backend in the MVP,
 * so "persisted" means localStorage. Invitations are a collection keyed by
 * `guestId` (one invitation per guest+event; Guest.id is unique per event) and
 * reference the canonical event/guest records — they never copy them.
 *
 * Lifecycle mutations (issue, allowance, rotate, revoke, delivered, responded)
 * all go through here so token version and status stay consistent. Rotating or
 * revoking is immediate: the token embeds the version, so prior links stop
 * resolving the instant this store changes (see lib/invitation `resolveRsvp`).
 */

const STORAGE_KEY = "cue.invitations.v1";

let byGuest: Record<string, Invitation> = load();
const listeners = new Set<() => void>();
let snapshot: Record<string, Invitation> = byGuest;

function load(): Record<string, Invitation> {
  if (typeof localStorage === "undefined") return {};
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? (parsed as Record<string, Invitation>) : {};
  } catch {
    return {};
  }
}

function persist(): void {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(byGuest));
  } catch {
    /* storage full/blocked — in-memory copy still works this session */
  }
}

function emit(): void {
  snapshot = { ...byGuest };
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): Record<string, Invitation> {
  return snapshot;
}

function commit(next: Invitation): Invitation {
  const stamped: Invitation = { ...next, updatedAt: new Date(nowMs()).toISOString() };
  byGuest = { ...byGuest, [stamped.guestId]: stamped };
  persist();
  emit();
  return stamped;
}

/** Non-reactive lookup of the invitation for a guest, or undefined. */
export function getInvitation(guestId: string): Invitation | undefined {
  return snapshot[guestId];
}

/** React hook: the reactive invitation for a guest, or undefined. */
export function useInvitation(guestId: string | undefined): Invitation | undefined {
  const all = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  return guestId ? all[guestId] : undefined;
}

/** Issue a fresh active invitation for a guest (idempotent: returns existing). */
export function issueInvitation(eventId: string, guestId: string): Invitation {
  const existing = snapshot[guestId];
  if (existing) return existing;
  return commit(newInvitation(eventId, guestId, nowMs()));
}

export type AllowanceResult =
  | { ok: true; invitation: Invitation }
  | { ok: false; reason: string };

/**
 * Set the nonnegative plus-one allowance, enforcing the invariant that it never
 * drops below the guest's already-ACCEPTED plus-one count (domain logic — see
 * `allowanceChange`). Rejects with an explanation rather than silently clamping.
 */
export function setAllowance(guestId: string, allowance: number): AllowanceResult {
  const inv = snapshot[guestId];
  if (!inv) return { ok: false, reason: "No invitation exists for this guest." };
  const guest = getGuests(inv.eventId).find((g) => g.id === guestId);
  const accepted = guest ? Math.max(0, guest.attendance.plusOnes) : 0;
  const change = allowanceChange(allowance, accepted);
  if (!change.ok) return { ok: false, reason: change.reason };
  return { ok: true, invitation: commit({ ...inv, plusOneAllowance: change.allowance }) };
}

/** Rotate/reissue the token: new undelivered link; all prior links stop now. */
export function rotateInvitation(guestId: string): Invitation | undefined {
  const inv = snapshot[guestId];
  if (!inv) return undefined;
  return commit(rotate(inv, nowMs()));
}

/** Revoke the invitation: prior links stop working immediately. */
export function revokeInvitation(guestId: string): Invitation | undefined {
  const inv = snapshot[guestId];
  if (!inv) return undefined;
  return commit({ ...inv, status: "revoked", revokedAt: new Date(nowMs()).toISOString() });
}

/**
 * Simulated delivery — the single operation that keeps the two records in sync:
 * it updates BOTH the invitation delivery state/timestamp AND the canonical
 * `Guest.communication.invitationSent`, so they cannot silently diverge. Call
 * only on a genuine (successful) delivery, never on a failed clipboard attempt.
 */
export function recordSimulatedDelivery(guestId: string): Invitation | undefined {
  const inv = snapshot[guestId];
  if (!inv) return undefined;
  const guest = getGuests(inv.eventId).find((g) => g.id === guestId);
  if (guest && !guest.communication.invitationSent) {
    updateGuest({
      ...guest,
      communication: { ...guest.communication, invitationSent: true },
      updatedAt: new Date(nowMs()).toISOString(),
    });
  }
  return commit({ ...inv, delivered: true, deliveredAt: new Date(nowMs()).toISOString() });
}

/** Record that the guest has responded (organizer visibility). */
export function markResponded(guestId: string): Invitation | undefined {
  const inv = snapshot[guestId];
  if (!inv) return undefined;
  return commit({ ...inv, respondedAt: new Date(nowMs()).toISOString() });
}

/** Test/dev helper: wipe all invitations. */
export function __resetInvitations(): void {
  byGuest = {};
  persist();
  emit();
}

function nowMs(): number {
  return Date.now();
}
