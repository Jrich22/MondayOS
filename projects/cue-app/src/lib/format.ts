import type { CueEvent, EventStatus } from "./types";

/** Display metadata for each event status: label + the token color it maps to. */
export const STATUS_META: Record<
  EventStatus,
  { label: string; dot: string; text: string; ring: string; bg: string }
> = {
  live: {
    label: "Live now",
    dot: "bg-status-live",
    text: "text-status-live",
    ring: "ring-status-live/30",
    bg: "bg-status-live/10",
  },
  upcoming: {
    label: "Upcoming",
    dot: "bg-status-upcoming",
    text: "text-status-upcoming",
    ring: "ring-status-upcoming/30",
    bg: "bg-status-upcoming/10",
  },
  draft: {
    label: "Draft",
    dot: "bg-status-draft",
    text: "text-status-draft",
    ring: "ring-status-draft/30",
    bg: "bg-status-draft/10",
  },
  done: {
    label: "Past",
    dot: "bg-status-done",
    text: "text-status-done",
    ring: "ring-status-done/20",
    bg: "bg-status-done/10",
  },
};

const dateFmt = new Intl.DateTimeFormat("en-US", {
  weekday: "short",
  month: "short",
  day: "numeric",
});
const timeFmt = new Intl.DateTimeFormat("en-US", {
  hour: "numeric",
  minute: "2-digit",
});

/** "Wed, Jul 15 · 6:00 PM" (browser timezone). */
export function formatEventDate(iso: string): string {
  const d = new Date(iso);
  return `${dateFmt.format(d)} · ${timeFmt.format(d)}`;
}

/**
 * Event date/time rendered in the EVENT's own IANA timezone (not the browser's),
 * with the zone abbreviation — e.g. "Tue, Sep 1, 2026 · 7:00 PM PDT". Used on the
 * guest RSVP surface so a guest sees the event's local time regardless of device.
 */
export function formatEventDateTz(iso: string, timeZone: string): string {
  const d = new Date(iso);
  const date = new Intl.DateTimeFormat("en-US", {
    timeZone, weekday: "short", month: "short", day: "numeric", year: "numeric",
  }).format(d);
  const time = new Intl.DateTimeFormat("en-US", {
    timeZone, hour: "numeric", minute: "2-digit", timeZoneName: "short",
  }).format(d);
  return `${date} · ${time}`;
}

/** "6:00 PM – 9:00 PM" (or just the start time if there is no end). */
export function formatTimeRange(startIso: string, endIso?: string): string {
  const start = timeFmt.format(new Date(startIso));
  if (!endIso) return start;
  return `${start} – ${timeFmt.format(new Date(endIso))}`;
}

/** Human relative distance, e.g. "in 6 days", "today", "4 days ago". */
export function relativeDay(iso: string): string {
  const diffMs = new Date(iso).getTime() - Date.now();
  const days = Math.round(diffMs / (24 * 60 * 60 * 1000));
  if (days === 0) return "Today";
  if (days === 1) return "Tomorrow";
  if (days === -1) return "Yesterday";
  if (days > 1) return `in ${days} days`;
  return `${Math.abs(days)} days ago`;
}

/** True when the event has no attendee cap. */
export function isUncapped(event: CueEvent): boolean {
  const max = event.capacity.maxAttendees;
  return max === null || max <= 0;
}

/**
 * Confirmed-guest fill ratio (0–1). Returns 0 for uncapped events (there is no
 * meaningful fill against an unlimited capacity), guarding against divide-by-zero.
 */
export function fillRatio(event: CueEvent): number {
  if (isUncapped(event)) return 0;
  return Math.min(1, event.confirmedGuests / (event.capacity.maxAttendees as number));
}
