import { useSyncExternalStore } from "react";
import { newInvitation, type Invitation } from "./invitation";

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

/** Set the nonnegative plus-one allowance. No-op if no invitation exists. */
export function setAllowance(guestId: string, allowance: number): Invitation | undefined {
  const inv = snapshot[guestId];
  if (!inv) return undefined;
  return commit({ ...inv, plusOneAllowance: Math.max(0, Math.floor(allowance) || 0) });
}

/** Rotate the token: bump the version so all prior links stop working now. */
export function rotateInvitation(guestId: string): Invitation | undefined {
  const inv = snapshot[guestId];
  if (!inv) return undefined;
  const iso = new Date(nowMs()).toISOString();
  return commit({ ...inv, tokenVersion: inv.tokenVersion + 1, status: "active", issuedAt: iso, rotatedAt: iso });
}

/** Revoke the invitation: prior links stop working immediately. */
export function revokeInvitation(guestId: string): Invitation | undefined {
  const inv = snapshot[guestId];
  if (!inv) return undefined;
  return commit({ ...inv, status: "revoked", revokedAt: new Date(nowMs()).toISOString() });
}

/** Simulated delivery — updates canonical invitation-delivery state (no provider). */
export function markDelivered(guestId: string): Invitation | undefined {
  const inv = snapshot[guestId];
  if (!inv) return undefined;
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
