import { useSyncExternalStore } from "react";
import type { CueEvent } from "./types";
import { seedEvents } from "./data";

/**
 * Event store for the MVP.
 *
 * The MVP has no backend, so "persisted event record" (TASK-0033 objective)
 * means localStorage. This module is the single seam every surface reads events
 * through: it merges the built-in seed events with any the user has created and
 * notifies subscribers on change. When a real API lands, only this module
 * changes — components keep calling `useEvents()` / `createEvent()`.
 *
 * User-created events are stored under one key; the immutable seed events are
 * never written to storage, so clearing storage restores the demo cleanly.
 */

const STORAGE_KEY = "cue.events.v1";

let createdEvents: CueEvent[] = loadCreated();
const listeners = new Set<() => void>();

/** Cached snapshot so useSyncExternalStore sees a stable reference between changes. */
let snapshot: CueEvent[] = compose();

function loadCreated(): CueEvent[] {
  if (typeof localStorage === "undefined") return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as CueEvent[]) : [];
  } catch {
    // Corrupt storage must not crash the app — start from an empty overlay.
    return [];
  }
}

function persist(): void {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(createdEvents));
  } catch {
    // Storage may be full/blocked; the in-memory copy still works this session.
  }
}

/** User-created events first (newest work surfaces at the top of the store). */
function compose(): CueEvent[] {
  return [...createdEvents, ...seedEvents];
}

function emit(): void {
  snapshot = compose();
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): CueEvent[] {
  return snapshot;
}

/** React hook: the full, reactive list of events (created + seed). */
export function useEvents(): CueEvent[] {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

/** Look up a single event by id (non-reactive; fine for one-off lookups). */
export function getEvent(id: string): CueEvent | undefined {
  return snapshot.find((e) => e.id === id);
}

/** React hook: a single event by id, reactive to store changes. */
export function useEvent(id: string | undefined): CueEvent | undefined {
  const events = useEvents();
  return id ? events.find((e) => e.id === id) : undefined;
}

/** Persist a new event and notify subscribers. Returns the stored record. */
export function createEvent(event: CueEvent): CueEvent {
  createdEvents = [event, ...createdEvents];
  persist();
  emit();
  return event;
}

/** Test/dev helper: wipe user-created events and reset to seed. */
export function __resetStore(): void {
  createdEvents = [];
  persist();
  emit();
}
