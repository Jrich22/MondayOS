import type { CueEvent, EventClassification, EventStatus } from "./types";
import { DEFAULT_THEME } from "./branding";

/**
 * The create-event form's working state and the pure logic that validates it
 * and turns it into a persisted `CueEvent`. Kept free of React so it is unit
 * testable and so the composition rules (how form fields map to the domain
 * model) live in one auditable place.
 */
export interface EventDraft {
  title: string;
  classification: EventClassification;
  customClassification: string;
  date: string; // yyyy-mm-dd
  startTime: string; // HH:MM (24h)
  endTime: string; // HH:MM (24h)
  timezone: string;
  venue: string;
  address: string;
  summary: string;
  /** Text input; "" means uncapped. */
  maxAttendees: string;
  rsvpEnabled: boolean;
  waitlistEnabled: boolean;
  theme: string;
}

/** A short list of common timezones for the picker (IANA ids). */
export const TIMEZONES: { value: string; label: string }[] = [
  { value: "America/Los_Angeles", label: "Pacific (PT)" },
  { value: "America/Denver", label: "Mountain (MT)" },
  { value: "America/Chicago", label: "Central (CT)" },
  { value: "America/New_York", label: "Eastern (ET)" },
  { value: "Europe/London", label: "London (GMT/BST)" },
  { value: "Europe/Berlin", label: "Central Europe (CET)" },
  { value: "Asia/Singapore", label: "Singapore (SGT)" },
];

/** Best-effort guess of the user's timezone, falling back to Pacific. */
function guessTimezone(): string {
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (TIMEZONES.some((t) => t.value === tz)) return tz;
  } catch {
    /* ignore */
  }
  return "America/Los_Angeles";
}

export function emptyDraft(): EventDraft {
  return {
    title: "",
    classification: "portfolio",
    customClassification: "",
    date: "",
    startTime: "18:00",
    endTime: "20:00",
    timezone: guessTimezone(),
    venue: "",
    address: "",
    summary: "",
    maxAttendees: "",
    rsvpEnabled: true,
    waitlistEnabled: false,
    theme: DEFAULT_THEME.key,
  };
}

export type DraftErrors = Partial<Record<keyof EventDraft, string>>;

/**
 * Validate a draft for creation. Drafts saved as "draft" are held to a lighter
 * bar (only a title), so `mode` controls strictness.
 */
export function validateDraft(
  draft: EventDraft,
  mode: "create" | "draft" = "create",
): DraftErrors {
  const errors: DraftErrors = {};

  if (!draft.title.trim()) {
    errors.title = "Give your event a name.";
  }

  if (draft.classification === "custom" && !draft.customClassification.trim()) {
    errors.customClassification = "Name your custom classification.";
  }

  if (draft.maxAttendees.trim()) {
    const n = Number(draft.maxAttendees);
    if (!Number.isInteger(n) || n <= 0) {
      errors.maxAttendees = "Capacity must be a positive whole number.";
    }
  }

  if (mode === "create") {
    if (!draft.date) errors.date = "Pick a date.";
    if (!draft.startTime) errors.startTime = "Set a start time.";
    if (
      draft.date &&
      draft.startTime &&
      draft.endTime &&
      draft.endTime <= draft.startTime
    ) {
      errors.endTime = "End time must be after the start time.";
    }
  }

  return errors;
}

/** Compose an ISO timestamp from a yyyy-mm-dd date and HH:MM time. */
export function composeIso(date: string, time: string): string {
  // Interpreted in the browser's local zone; adequate for the MVP. The chosen
  // IANA timezone is stored alongside for display and future correctness.
  return new Date(`${date}T${time || "00:00"}`).toISOString();
}

/** Derive lifecycle status from the event's time window relative to `now`. */
export function deriveStatus(startsAtMs: number, endsAtMs: number, now: number): EventStatus {
  if (now >= startsAtMs && now <= endsAtMs) return "live";
  if (startsAtMs > now) return "upcoming";
  return "done";
}

export interface ComposeOptions {
  id: string;
  now: number;
  /** "draft" forces draft status; "create" derives it from the time window. */
  mode: "create" | "draft";
  host: string;
}

/**
 * Turn a validated draft into a persistable `CueEvent`. Pure: callers supply
 * the id, clock, and host so this is deterministic and testable.
 */
export function draftToEvent(draft: EventDraft, opts: ComposeOptions): CueEvent {
  const startsAt = draft.date ? composeIso(draft.date, draft.startTime) : new Date(opts.now).toISOString();
  const endsAt =
    draft.date && draft.endTime ? composeIso(draft.date, draft.endTime) : undefined;

  const status: EventStatus =
    opts.mode === "draft"
      ? "draft"
      : deriveStatus(
          new Date(startsAt).getTime(),
          endsAt ? new Date(endsAt).getTime() : new Date(startsAt).getTime(),
          opts.now,
        );

  const maxAttendees = draft.maxAttendees.trim() ? Number(draft.maxAttendees) : null;

  return {
    id: opts.id,
    title: draft.title.trim(),
    classification: draft.classification,
    customClassification:
      draft.classification === "custom" ? draft.customClassification.trim() : undefined,
    summary: draft.summary.trim(),
    startsAt,
    endsAt,
    timezone: draft.timezone,
    status,
    venue: draft.venue.trim(),
    address: draft.address.trim() || undefined,
    // The MVP create form captures venue + address; city is derived later.
    city: "",
    host: opts.host,
    capacity: {
      maxAttendees,
      rsvpEnabled: draft.rsvpEnabled,
      // Waitlist only meaningful with RSVP + a cap.
      waitlistEnabled: draft.rsvpEnabled && maxAttendees !== null && draft.waitlistEnabled,
    },
    confirmedGuests: 0,
    invitedGuests: 0,
    branding: { theme: draft.theme },
    portfolio: [],
    tags: [],
    createdAt: new Date(opts.now).toISOString(),
  };
}
