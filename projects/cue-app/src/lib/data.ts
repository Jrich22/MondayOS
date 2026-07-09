import type { CueEvent, PortfolioCompany } from "./types";

/**
 * Seed data for the MVP foundation. This stands in for the API/persistence
 * layer that later sprints add. TASK-0037 (seed demo data) will expand this so
 * every surface is walkable end-to-end; the event store (lib/store) layers any
 * user-created events on top of this seed.
 *
 * Dates are computed relative to "now" at module load so the dashboard always
 * shows a realistic mix of upcoming/live/past events rather than dates that
 * silently rot.
 */

export const companies: Record<string, PortfolioCompany> = {
  northwind: { id: "co-northwind", name: "Northwind", stage: "Series A" },
  lattice: { id: "co-lattice", name: "Lattice AI", stage: "Seed" },
  meridian: { id: "co-meridian", name: "Meridian Bio", stage: "Series B" },
  cobalt: { id: "co-cobalt", name: "Cobalt", stage: "Growth" },
  fathom: { id: "co-fathom", name: "Fathom", stage: "Seed" },
  arclight: { id: "co-arclight", name: "Arclight", stage: "Series A" },
};

const DAY = 24 * 60 * 60 * 1000;
const TZ = "America/Los_Angeles";

/** Build an ISO timestamp offset from now by a number of days (+/-). */
function daysFromNow(days: number, hour = 18): string {
  const d = new Date(Date.now() + days * DAY);
  d.setHours(hour, 0, 0, 0);
  return d.toISOString();
}

/** End time N hours after a start ISO. */
function plusHours(iso: string, hours: number): string {
  return new Date(new Date(iso).getTime() + hours * 60 * 60 * 1000).toISOString();
}

export const seedEvents: CueEvent[] = [
  {
    id: "evt-2041",
    title: "Q3 Founder Summit",
    classification: "founder",
    summary: "Full-day gathering for Series A+ founders across the portfolio.",
    // Live now — doors opened ~90 min ago, so the Roll Call Command Center has a
    // real event to run and arrivals fall inside the arrival-pacing window.
    startsAt: new Date(Date.now() - 90 * 60 * 1000).toISOString(),
    endsAt: new Date(Date.now() + 4 * 60 * 60 * 1000).toISOString(),
    timezone: TZ,
    status: "live",
    venue: "Pier 27 Pavilion",
    address: "Pier 27, The Embarcadero",
    city: "San Francisco",
    host: "Dana Whitfield",
    capacity: { maxAttendees: 500, rsvpEnabled: true, waitlistEnabled: true },
    confirmedGuests: 320,
    invitedGuests: 481,
    branding: { theme: "indigo" },
    portfolio: [companies.northwind, companies.meridian, companies.cobalt],
    tags: ["summit", "founders", "flagship"],
    createdAt: daysFromNow(-30),
  },
  {
    id: "evt-2042",
    title: "AI Infra Roundtable",
    classification: "portfolio",
    summary: "Intimate dinner on scaling inference — technical founders only.",
    startsAt: daysFromNow(0, new Date().getHours() + 1),
    endsAt: plusHours(daysFromNow(0, new Date().getHours() + 1), 3),
    timezone: TZ,
    status: "live",
    venue: "The Battery — Library",
    address: "717 Battery St",
    city: "San Francisco",
    host: "Priya Anand",
    capacity: { maxAttendees: 24, rsvpEnabled: true, waitlistEnabled: false },
    confirmedGuests: 22,
    invitedGuests: 26,
    branding: { theme: "emerald" },
    portfolio: [companies.lattice, companies.fathom],
    tags: ["roundtable", "ai", "dinner"],
    createdAt: daysFromNow(-14),
  },
  {
    id: "evt-2043",
    title: "LP Winter Briefing",
    classification: "investor",
    summary: "Annual limited-partner update and portfolio showcase.",
    startsAt: daysFromNow(21),
    endsAt: plusHours(daysFromNow(21), 3),
    timezone: TZ,
    status: "upcoming",
    venue: "Fairmont — Gold Room",
    address: "950 Mason St",
    city: "San Francisco",
    host: "Marcus Lee",
    capacity: { maxAttendees: 120, rsvpEnabled: true, waitlistEnabled: true },
    confirmedGuests: 41,
    invitedGuests: 110,
    branding: { theme: "slate" },
    portfolio: [companies.cobalt, companies.arclight],
    tags: ["lp", "briefing"],
    createdAt: daysFromNow(-20),
  },
  {
    id: "evt-2044",
    title: "Hiring Fair: Portfolio × Talent",
    classification: "recruiting",
    summary: "Candidates meet 12 hiring portfolio companies in one afternoon.",
    startsAt: daysFromNow(34),
    timezone: "America/New_York",
    status: "draft",
    venue: "TBD",
    city: "New York",
    host: "Dana Whitfield",
    capacity: { maxAttendees: 300, rsvpEnabled: true, waitlistEnabled: true },
    confirmedGuests: 0,
    invitedGuests: 0,
    branding: { theme: "amber" },
    portfolio: [companies.northwind, companies.lattice, companies.arclight, companies.fathom],
    tags: ["talent", "hiring", "portfolio"],
    createdAt: daysFromNow(-3),
  },
  {
    id: "evt-2045",
    title: "Meridian Bio Series B Celebration",
    classification: "portfolio",
    summary: "Close dinner marking Meridian's Series B round.",
    startsAt: daysFromNow(-4),
    endsAt: plusHours(daysFromNow(-4), 3),
    timezone: TZ,
    status: "done",
    venue: "Angler",
    address: "132 The Embarcadero",
    city: "San Francisco",
    host: "Priya Anand",
    capacity: { maxAttendees: 40, rsvpEnabled: true, waitlistEnabled: false },
    confirmedGuests: 38,
    invitedGuests: 40,
    branding: { theme: "rose" },
    portfolio: [companies.meridian],
    tags: ["celebration", "dinner"],
    createdAt: daysFromNow(-25),
  },
  {
    id: "evt-2046",
    title: "Founder Onboarding Breakfast",
    classification: "founder",
    summary: "Welcome breakfast for the two newest portfolio founders.",
    startsAt: daysFromNow(-12, 9),
    endsAt: plusHours(daysFromNow(-12, 9), 2),
    timezone: TZ,
    status: "done",
    venue: "Sightglass Coffee",
    address: "270 7th St",
    city: "San Francisco",
    host: "Marcus Lee",
    capacity: { maxAttendees: 16, rsvpEnabled: true, waitlistEnabled: false },
    confirmedGuests: 14,
    invitedGuests: 16,
    branding: { theme: "emerald" },
    portfolio: [companies.fathom, companies.lattice],
    tags: ["onboarding", "breakfast"],
    createdAt: daysFromNow(-40),
  },
];
