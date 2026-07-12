import { useSyncExternalStore, useMemo } from "react";
import type { Campaign } from "./comms";
import { seedCampaigns } from "./comms-data";

/**
 * Campaign store for the Communications Center (TASK-0043).
 *
 * Mirrors the attendee store (lib/guests): campaigns are fully mutable (create,
 * edit, schedule, send, delete), so rather than overlaying user changes on an
 * immutable seed we materialize the demo campaigns into localStorage on first run
 * and treat storage as the source of truth thereafter. Clearing storage restores
 * the demo cleanly. This is the single seam every comms surface reads through;
 * when a real messaging backend lands, only this module changes.
 */

const STORAGE_KEY = "cue.campaigns.v1";

let campaigns: Campaign[] = load();
const listeners = new Set<() => void>();

/** Stable snapshot reference so useSyncExternalStore only re-renders on change. */
let snapshot: Campaign[] = campaigns;

function load(): Campaign[] {
  if (typeof localStorage === "undefined") return [...seedCampaigns];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      persist(seedCampaigns);
      return [...seedCampaigns];
    }
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as Campaign[]) : [...seedCampaigns];
  } catch {
    // Corrupt storage must not crash the app — fall back to the demo campaigns.
    return [...seedCampaigns];
  }
}

function persist(list: Campaign[]): void {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
  } catch {
    // Storage may be full/blocked; the in-memory copy still works this session.
  }
}

function commit(next: Campaign[]): void {
  campaigns = next;
  snapshot = next;
  persist(next);
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): Campaign[] {
  return snapshot;
}

/** React hook: the full, reactive campaign list across every event. */
export function useCampaigns(): Campaign[] {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

/** React hook: the reactive campaign list for a single event. */
export function useEventCampaigns(eventId: string): Campaign[] {
  const all = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  return useMemo(() => all.filter((c) => c.eventId === eventId), [all, eventId]);
}

/** Add a new campaign (newest first) and notify subscribers. */
export function addCampaign(campaign: Campaign): Campaign {
  commit([campaign, ...campaigns]);
  return campaign;
}

/** Replace a campaign by id. No-op if the id is unknown. */
export function updateCampaign(next: Campaign): void {
  let changed = false;
  const updated = campaigns.map((c) => {
    if (c.id === next.id) {
      changed = true;
      return next;
    }
    return c;
  });
  if (changed) commit(updated);
}

/** Remove a campaign by id. */
export function removeCampaign(id: string): void {
  const remaining = campaigns.filter((c) => c.id !== id);
  if (remaining.length !== campaigns.length) commit(remaining);
}

/** Test/dev helper: restore the seed campaigns. */
export function __resetCampaigns(): void {
  commit([...seedCampaigns]);
}
