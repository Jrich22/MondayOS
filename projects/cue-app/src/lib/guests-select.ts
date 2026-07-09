import type { Guest, GuestRole, RsvpStatus, PortfolioCompany } from "./types";

/**
 * Pure logic for the attendee CRM (TASK-0035): display derivations, search,
 * filtering, sorting, and roster summaries. Kept out of the components so the
 * operational rules are unit-testable and live in one auditable place — the same
 * split the event dashboard uses (see lib/select).
 */

// --- Display metadata -------------------------------------------------------

export const RSVP_META: Record<RsvpStatus, { label: string; tone: string; dot: string }> = {
  confirmed: { label: "Confirmed", tone: "text-status-live", dot: "bg-status-live" },
  invited: { label: "Invited", tone: "text-status-upcoming", dot: "bg-status-upcoming" },
  tentative: { label: "Tentative", tone: "text-status-draft", dot: "bg-status-draft" },
  waitlisted: { label: "Waitlisted", tone: "text-ink-muted", dot: "bg-ink-faint" },
  declined: { label: "Declined", tone: "text-ink-faint", dot: "bg-ink-faint" },
};

/** RSVP options in funnel order, for pickers. */
export const RSVP_ORDER: RsvpStatus[] = [
  "confirmed",
  "invited",
  "tentative",
  "waitlisted",
  "declined",
];

export const ROLE_META: Record<GuestRole, { label: string }> = {
  investor: { label: "Investor" },
  founder: { label: "Founder" },
  internal: { label: "Internal" },
  sponsor: { label: "Sponsor" },
  press: { label: "Press" },
};

export const ALL_ROLES: GuestRole[] = ["investor", "founder", "internal", "sponsor", "press"];

// --- Name / identity helpers ------------------------------------------------

/** Legal full name: "Ava Chen". */
export function fullName(g: Guest): string {
  return `${g.identity.firstName} ${g.identity.lastName}`.trim();
}

/** What we show: preferred name when set, else first name, plus last name. */
export function displayName(g: Guest): string {
  const first = g.identity.preferredName?.trim() || g.identity.firstName;
  return `${first} ${g.identity.lastName}`.trim();
}

/** Up to two initials for the avatar. */
export function initials(g: Guest): string {
  const a = g.identity.firstName.trim()[0] ?? "";
  const b = g.identity.lastName.trim()[0] ?? "";
  return (a + b).toUpperCase() || "?";
}

/**
 * The company to show for a guest: the portfolio company it is tied to (resolved
 * by id) wins over the free-text company, so portfolio ties read consistently.
 */
export function guestCompany(g: Guest, portfolio: PortfolioCompany[]): string {
  if (g.professional.portfolioCompanyId) {
    const co = portfolio.find((c) => c.id === g.professional.portfolioCompanyId);
    if (co) return co.name;
  }
  return g.professional.company?.trim() ?? "";
}

// --- Search / filter / sort -------------------------------------------------

export type GuestSort = "name" | "company" | "checkin" | "vip" | "rsvp";

export interface GuestFilters {
  query: string;
  role: GuestRole | null;
  company: string | null;
  portfolioCompanyId: string | null;
  rsvp: RsvpStatus | null;
  tag: string | null;
  vipOnly: boolean;
  checkedInOnly: boolean;
}

export function emptyFilters(): GuestFilters {
  return {
    query: "",
    role: null,
    company: null,
    portfolioCompanyId: null,
    rsvp: null,
    tag: null,
    vipOnly: false,
    checkedInOnly: false,
  };
}

/** True when any filter beyond the (separately shown) search is active. */
export function hasActiveFilters(f: GuestFilters): boolean {
  return (
    f.role !== null ||
    f.company !== null ||
    f.portfolioCompanyId !== null ||
    f.rsvp !== null ||
    f.tag !== null ||
    f.vipOnly ||
    f.checkedInOnly ||
    f.query.trim() !== ""
  );
}

/** Free-text match across the attendee's most identifying fields. */
export function matchesGuestQuery(g: Guest, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const haystack = [
    fullName(g),
    g.identity.preferredName ?? "",
    g.identity.email ?? "",
    g.professional.company ?? "",
    g.professional.jobTitle ?? "",
    g.professional.department ?? "",
    ...g.tags,
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(q);
}

const RSVP_SORT_RANK: Record<RsvpStatus, number> = {
  confirmed: 0,
  invited: 1,
  tentative: 2,
  waitlisted: 3,
  declined: 4,
};

/**
 * Filter then sort a roster. Pure over its inputs so the tab is presentation and
 * this stays testable. `company` matches the free-text company field exactly
 * (the filter options are built from those values).
 */
export function selectGuests(
  guests: Guest[],
  filters: GuestFilters,
  sort: GuestSort,
): Guest[] {
  const filtered = guests.filter((g) => {
    if (!matchesGuestQuery(g, filters.query)) return false;
    if (filters.role && !g.roles.includes(filters.role)) return false;
    if (filters.company && (g.professional.company ?? "") !== filters.company) return false;
    if (
      filters.portfolioCompanyId &&
      g.professional.portfolioCompanyId !== filters.portfolioCompanyId
    )
      return false;
    if (filters.rsvp && g.attendance.rsvp !== filters.rsvp) return false;
    if (filters.tag && !g.tags.includes(filters.tag)) return false;
    if (filters.vipOnly && !g.vip) return false;
    if (filters.checkedInOnly && !g.attendance.checkedIn) return false;
    return true;
  });
  return sortGuests(filtered, sort);
}

export function sortGuests(guests: Guest[], sort: GuestSort): Guest[] {
  const byName = (a: Guest, b: Guest) =>
    a.identity.lastName.localeCompare(b.identity.lastName) ||
    a.identity.firstName.localeCompare(b.identity.firstName);

  return [...guests].sort((a, b) => {
    switch (sort) {
      case "company": {
        const c = (a.professional.company ?? "").localeCompare(b.professional.company ?? "");
        return c || byName(a, b);
      }
      case "checkin": {
        // Most recent check-in first; not-yet-checked-in sinks to the bottom.
        const at = a.attendance.checkedInAt ? Date.parse(a.attendance.checkedInAt) : -Infinity;
        const bt = b.attendance.checkedInAt ? Date.parse(b.attendance.checkedInAt) : -Infinity;
        if (at !== bt) return bt - at;
        return byName(a, b);
      }
      case "vip":
        if (a.vip !== b.vip) return a.vip ? -1 : 1;
        return byName(a, b);
      case "rsvp": {
        const r = RSVP_SORT_RANK[a.attendance.rsvp] - RSVP_SORT_RANK[b.attendance.rsvp];
        return r || byName(a, b);
      }
      case "name":
      default:
        return byName(a, b);
    }
  });
}

// --- Roster derivations -----------------------------------------------------

export interface GuestSummary {
  total: number;
  confirmed: number;
  checkedIn: number;
  vip: number;
  /** Confirmed head count including their plus-ones. */
  expectedHeadcount: number;
}

export function guestSummary(guests: Guest[]): GuestSummary {
  let confirmed = 0;
  let checkedIn = 0;
  let vip = 0;
  let expectedHeadcount = 0;
  for (const g of guests) {
    const isConfirmed = g.attendance.rsvp === "confirmed";
    if (isConfirmed) {
      confirmed += 1;
      expectedHeadcount += 1 + Math.max(0, g.attendance.plusOnes);
    }
    if (g.attendance.checkedIn) checkedIn += 1;
    if (g.vip) vip += 1;
  }
  return { total: guests.length, confirmed, checkedIn, vip, expectedHeadcount };
}

/** Distinct free-text company names present in a roster, sorted, for a filter. */
export function uniqueCompanies(guests: Guest[]): string[] {
  const set = new Set<string>();
  for (const g of guests) {
    const c = g.professional.company?.trim();
    if (c) set.add(c);
  }
  return [...set].sort((a, b) => a.localeCompare(b));
}

/** Distinct tags present in a roster, sorted, for a filter. */
export function uniqueTags(guests: Guest[]): string[] {
  const set = new Set<string>();
  for (const g of guests) for (const t of g.tags) set.add(t);
  return [...set].sort((a, b) => a.localeCompare(b));
}

// --- Construction / mutation (pure; the caller supplies id + clock) ---------

/** A blank attendee for the "add" flow. Sensible defaults, nothing sent yet. */
export function newGuest(eventId: string, id: string, nowIso: string): Guest {
  return {
    id,
    eventId,
    identity: { firstName: "", lastName: "" },
    professional: {},
    roles: [],
    vip: false,
    attendance: {
      rsvp: "invited",
      checkedIn: false,
      noShow: false,
      waitlisted: false,
      plusOnes: 0,
    },
    preferences: { hotelNeeded: false, transportationNeeded: false },
    communication: { invitationSent: false, reminderSent: false, thankYouSent: false },
    notes: {},
    tags: [],
    seat: null,
    createdAt: nowIso,
    updatedAt: nowIso,
  };
}


/**
 * Flip a guest's check-in state, stamping/clearing `checkedInAt`. Checking in
 * also clears a stale no-show flag. Returns a new record (never mutates).
 */
export function withCheckIn(g: Guest, checkedIn: boolean, nowIso: string): Guest {
  return {
    ...g,
    attendance: {
      ...g.attendance,
      checkedIn,
      checkedInAt: checkedIn ? (g.attendance.checkedInAt ?? nowIso) : undefined,
      noShow: checkedIn ? false : g.attendance.noShow,
    },
    updatedAt: nowIso,
  };
}
