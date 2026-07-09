/**
 * Cue domain types (MVP foundation).
 *
 * These interfaces are the shared shape every surface reads. They are
 * portfolio-aware by design (per the Cue Definition of Done): an event and its
 * guests carry the portfolio companies they relate to, since the differentiated
 * bet is portfolio-aware event operations — not generic event management.
 *
 * Scope note: there is intentionally NO ticketing/payment concept here. Guest
 * counts model RSVPs (confirmed vs. invited), not sold tickets (see DEC-0005).
 *
 * FUTURE-COMPATIBILITY CONTRACT (TASK-0033 product guidance): the CueEvent is
 * the *container* every later operations surface hangs off. Guest management,
 * roll call, seating, agenda, vendors, documents, and analytics are each their
 * own collection that references `CueEvent.id` — they are never nested inline,
 * so adding them is additive and needs no redesign of this model. The fields
 * defined here (stable id, classification, capacity policy, timezone-aware
 * start/end, branding) are the ones those surfaces read; everything they add
 * lives beside the event, keyed by its id.
 */

/** Lifecycle status of an event, driving the at-a-glance status indicator. */
export type EventStatus = "draft" | "upcoming" | "live" | "done";

/**
 * What an event is *for*. Drives filtering, invite tone, and later analytics
 * segmentation. "custom" pairs with `customClassification` free text.
 */
export type EventClassification =
  | "portfolio"
  | "internal"
  | "investor"
  | "founder"
  | "recruiting"
  | "community"
  | "demo-day"
  | "board-meeting"
  | "customer"
  | "custom";

/** A portfolio company the firm has invested in. */
export interface PortfolioCompany {
  id: string;
  name: string;
  /** Investment stage — light context, not a CRM record. */
  stage: "Seed" | "Series A" | "Series B" | "Growth";
}

/** Capacity + RSVP policy for an event. */
export interface EventCapacity {
  /** Maximum attendees. `null` = uncapped. */
  maxAttendees: number | null;
  /** Whether guests RSVP (vs. a private/managed list). */
  rsvpEnabled: boolean;
  /** Whether a waitlist opens once capacity is reached (requires rsvp). */
  waitlistEnabled: boolean;
}

/** Presentation/branding for an event's public and internal surfaces. */
export interface EventBranding {
  /** Accent theme key (see lib/branding). Drives banner + accent color. */
  theme: string;
  /** Optional uploaded banner image (data/URL). Placeholder in the MVP. */
  bannerUrl?: string;
  /** Optional uploaded logo (data/URL). Placeholder in the MVP. */
  logoUrl?: string;
}

/** A single event managed in Cue. */
export interface CueEvent {
  id: string;
  title: string;
  classification: EventClassification;
  /** Free-text label used only when `classification === "custom"`. */
  customClassification?: string;
  /** Short human summary / description shown on cards and detail. */
  summary: string;
  /** ISO date-time the event starts. */
  startsAt: string;
  /** ISO date-time the event ends. Optional for drafts. */
  endsAt?: string;
  /** IANA timezone the wall-clock times are expressed in. */
  timezone: string;
  status: EventStatus;
  venue: string;
  /** Street address / room. Optional (a venue name may be enough). */
  address?: string;
  city: string;
  /** Who owns the event inside the firm. */
  host: string;
  capacity: EventCapacity;
  /** RSVPs that have confirmed. */
  confirmedGuests: number;
  /** Total guests invited. */
  invitedGuests: number;
  branding: EventBranding;
  /** Portfolio companies this event is oriented around. */
  portfolio: PortfolioCompany[];
  /** Freeform tags for filtering/segmentation. */
  tags: string[];
  /** ISO timestamp the record was created. */
  createdAt: string;
}

// ---------------------------------------------------------------------------
// Attendee CRM (TASK-0035)
// ---------------------------------------------------------------------------
//
// A guest is not a name on a list — it is the attendee record every later
// operations surface reads. Per the future-compatibility contract above, a Guest
// references its event by id and is never nested inside CueEvent, so the roster
// is its own collection keyed by `eventId`. The seams below are defined now and
// surfaced later without reshaping the record:
//   • Roll Call            → attendance.checkedIn / attendance.checkedInAt
//   • Badges / QR / Name tags → derived from `id` + identity (no new storage)
//   • Seating assignments  → `seat`
//   • Networking / Analytics → derived over the guest collection
//   • Communications        → the `communication` flags feed the invite flow
// Each of those lands additively against the fields already defined here.

/**
 * RSVP lifecycle for an attendee — their *response* state, kept distinct from
 * `checkedIn` (physical arrival) so Roll Call layers on without losing the
 * invite funnel, and so analytics can read both independently.
 */
export type RsvpStatus =
  | "invited"
  | "confirmed"
  | "tentative"
  | "declined"
  | "waitlisted";

/**
 * A professional role an attendee plays at the event. A guest may hold several
 * (a founder who is also internal-facing). VIP is deliberately *not* a role — it
 * is a cross-cutting flag (see `Guest.vip`) any role can carry, which filtering
 * and sorting treat as first-class.
 */
export type GuestRole = "investor" | "founder" | "internal" | "sponsor" | "press";

/** Who the attendee is. */
export interface GuestIdentity {
  firstName: string;
  lastName: string;
  /** What they go by, if different from their first name. */
  preferredName?: string;
  email?: string;
  phone?: string;
}

/** Where the attendee works and how they relate to the portfolio. */
export interface GuestProfessional {
  company?: string;
  jobTitle?: string;
  department?: string;
  /**
   * Seam to `PortfolioCompany.id` — the portfolio-aware bet (task objective).
   * When set, the attendee is tied to a portfolio company the firm has invested
   * in, which drives portfolio filtering and later portfolio-reach analytics.
   */
  portfolioCompanyId?: string;
}

/** Attendance state across the funnel: RSVP → arrival → seating. */
export interface GuestAttendance {
  rsvp: RsvpStatus;
  checkedIn: boolean;
  /** ISO timestamp set when `checkedIn` flips true. The Roll Call seam. */
  checkedInAt?: string;
  noShow: boolean;
  waitlisted: boolean;
  /** Additional guests they bring (0 = none). */
  plusOnes: number;
}

/** Logistics and hospitality needs. */
export interface GuestPreferences {
  dietary?: string;
  accessibility?: string;
  hotelNeeded: boolean;
  transportationNeeded: boolean;
  seatingPreference?: string;
}

/** What outbound comms this attendee has received. Feeds the invite flow. */
export interface GuestCommunication {
  invitationSent: boolean;
  reminderSent: boolean;
  thankYouSent: boolean;
}

/** Freeform context — internal-only vs. shareable. */
export interface GuestNotes {
  internal?: string;
  public?: string;
}

/** A single attendee on an event — the atomic record of the attendee CRM. */
export interface Guest {
  id: string;
  /** The event this attendee belongs to (`CueEvent.id`). */
  eventId: string;
  identity: GuestIdentity;
  professional: GuestProfessional;
  roles: GuestRole[];
  /** Cross-cutting importance flag; drives the VIP filter + sort. */
  vip: boolean;
  attendance: GuestAttendance;
  preferences: GuestPreferences;
  communication: GuestCommunication;
  notes: GuestNotes;
  /** Unlimited freeform tags for segmentation. */
  tags: string[];
  /** Seating assignment (table/seat label). Null until seating is run. */
  seat?: string | null;
  createdAt: string;
  updatedAt: string;
}
