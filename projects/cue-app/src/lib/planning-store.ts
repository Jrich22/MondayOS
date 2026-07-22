import { useSyncExternalStore } from "react";
import type { CueEvent } from "./types";
import { emptyPlan, type EventPlan } from "./planning";

/**
 * Planning store — the single seam every planning surface reads/writes plans
 * through. Mirrors lib/store.ts: no backend in the MVP, so "persisted" means
 * localStorage. Plans are a collection keyed by `eventId` (per the CueEvent
 * future-compatibility contract); the canonical event + guest records live in
 * their own stores and are never copied here.
 *
 * A plan is created lazily from the canonical event the first time it is saved,
 * so an untouched event has no stored plan and reads its derived default.
 */

const STORAGE_KEY = "cue.plans.v1";

let plans: Record<string, EventPlan> = load();
const listeners = new Set<() => void>();
let snapshot: Record<string, EventPlan> = plans;

function load(): Record<string, EventPlan> {
  if (typeof localStorage === "undefined") return {};
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? (parsed as Record<string, EventPlan>) : {};
  } catch {
    return {};
  }
}

function persist(): void {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(plans));
  } catch {
    /* storage full/blocked — in-memory copy still works this session */
  }
}

function emit(): void {
  snapshot = { ...plans };
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): Record<string, EventPlan> {
  return snapshot;
}

/** Non-reactive: the stored plan for an event, or its derived default. */
export function getPlan(event: CueEvent, now: number = nowMs()): EventPlan {
  return snapshot[event.id] ?? emptyPlan(event, now);
}

/** React hook: the reactive plan for an event (stored or derived default). */
export function usePlan(event: CueEvent): EventPlan {
  const all = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  return all[event.id] ?? emptyPlan(event, nowMs());
}

/** Persist a plan (stamps updatedAt) and notify subscribers. */
export function savePlan(plan: EventPlan): EventPlan {
  const next: EventPlan = { ...plan, updatedAt: new Date(nowMs()).toISOString() };
  plans = { ...plans, [plan.eventId]: next };
  persist();
  emit();
  return next;
}

/** Whether an event has an explicitly saved plan (vs. the derived default). */
export function hasSavedPlan(eventId: string): boolean {
  return eventId in snapshot;
}

/** Stable id helper for new planning records (collision-resistant enough for the MVP). */
export function planId(prefix: string): string {
  return `${prefix}-${nowMs().toString(36)}-${Math.floor(Math.random() * 1e6).toString(36)}`;
}

/** Test/dev helper: wipe all saved plans. */
export function __resetPlans(): void {
  plans = {};
  persist();
  emit();
}

function nowMs(): number {
  return Date.now();
}
