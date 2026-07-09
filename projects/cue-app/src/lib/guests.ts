import { useSyncExternalStore, useMemo } from "react";
import type { Guest } from "./types";
import { seedGuests } from "./guests-data";

/**
 * Attendee store for the MVP (TASK-0035).
 *
 * Like the event store, this is the single seam every surface reads guests
 * through, backed by localStorage until an API lands. Guests differ from events
 * in one way: they are fully mutable (check-in, edit, tag, delete), so rather
 * than overlaying user changes on an immutable seed we *materialize* the demo
 * roster into storage on first run and treat storage as the source of truth
 * thereafter. Clearing storage still restores the demo cleanly (see `load`).
 *
 * The store keeps a flat, event-agnostic list; `useGuests(eventId)` derives the
 * per-event roster. This honors the future-compatibility contract: a guest
 * references its event by id and is never nested inside the event record.
 */

// v2: the Roll Call Command Center (TASK-0040) ships a large live Summit roster;
// bump the key so the richer demo roster materializes over any v1 seed in storage.
const STORAGE_KEY = "cue.guests.v2";

let guests: Guest[] = load();
const listeners = new Set<() => void>();

/** Stable snapshot reference so useSyncExternalStore only re-renders on change. */
let snapshot: Guest[] = guests;

function load(): Guest[] {
  if (typeof localStorage === "undefined") return [...seedGuests];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      // First run (or cleared storage): seed the demo roster so it is editable.
      persist(seedGuests);
      return [...seedGuests];
    }
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as Guest[]) : [...seedGuests];
  } catch {
    // Corrupt storage must not crash the app — fall back to the demo roster.
    return [...seedGuests];
  }
}

function persist(list: Guest[]): void {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
  } catch {
    // Storage may be full/blocked; the in-memory copy still works this session.
  }
}

function commit(next: Guest[]): void {
  guests = next;
  snapshot = next;
  persist(next);
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): Guest[] {
  return snapshot;
}

/** React hook: the reactive roster for a single event. */
export function useGuests(eventId: string): Guest[] {
  const all = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  // Derive per-event so the external snapshot stays a stable reference.
  return useMemo(() => all.filter((g) => g.eventId === eventId), [all, eventId]);
}

/** Non-reactive roster lookup for a single event. */
export function getGuests(eventId: string): Guest[] {
  return snapshot.filter((g) => g.eventId === eventId);
}

/** Add a new attendee (newest first) and notify subscribers. */
export function addGuest(guest: Guest): Guest {
  commit([guest, ...guests]);
  return guest;
}

/** Replace an attendee by id. No-op if the id is unknown. */
export function updateGuest(next: Guest): void {
  let changed = false;
  const updated = guests.map((g) => {
    if (g.id === next.id) {
      changed = true;
      return next;
    }
    return g;
  });
  if (changed) commit(updated);
}

/** Remove an attendee by id. */
export function removeGuest(id: string): void {
  const remaining = guests.filter((g) => g.id !== id);
  if (remaining.length !== guests.length) commit(remaining);
}

/** Test/dev helper: restore the seed roster. */
export function __resetGuests(): void {
  commit([...seedGuests]);
}
