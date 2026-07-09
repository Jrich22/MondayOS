import type { CueEvent, EventStatus } from "./types";

/** A dashboard filter is any status, or "all". */
export type EventFilter = "all" | EventStatus;

/**
 * Operational sort order for the dashboard: the most time-sensitive events
 * first (live → upcoming → draft → past), then by start time within a status.
 */
const STATUS_ORDER: Record<EventStatus, number> = {
  live: 0,
  upcoming: 1,
  draft: 2,
  done: 3,
};

export function sortEvents(list: CueEvent[]): CueEvent[] {
  return [...list].sort((a, b) => {
    if (STATUS_ORDER[a.status] !== STATUS_ORDER[b.status]) {
      return STATUS_ORDER[a.status] - STATUS_ORDER[b.status];
    }
    return new Date(a.startsAt).getTime() - new Date(b.startsAt).getTime();
  });
}

/** True if the event matches a free-text query across its searchable fields. */
export function matchesQuery(event: CueEvent, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (
    event.title.toLowerCase().includes(q) ||
    event.venue.toLowerCase().includes(q) ||
    event.city.toLowerCase().includes(q) ||
    event.tags.some((t) => t.toLowerCase().includes(q)) ||
    event.portfolio.some((c) => c.name.toLowerCase().includes(q))
  );
}

/**
 * Apply the status filter + search query, then sort. Pure over its inputs so the
 * dashboard component is just presentation and this stays unit-testable.
 */
export function selectEvents(
  list: CueEvent[],
  filter: EventFilter,
  query: string,
): CueEvent[] {
  const filtered = list.filter((e) => {
    if (filter !== "all" && e.status !== filter) return false;
    return matchesQuery(e, query);
  });
  return sortEvents(filtered);
}
