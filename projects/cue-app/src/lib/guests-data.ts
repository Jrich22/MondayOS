import type { Guest, GuestRole, RsvpStatus } from "./types";
import { newGuest } from "./guests-select";

/**
 * Seed attendee rosters for the MVP (TASK-0035). Like lib/data (events), this
 * stands in for the API/persistence layer. Two events get rich rosters so the
 * attendee workspace is walkable on day one — the live roundtable (with people
 * already checked in) and the flagship summit — while other events start empty
 * so the empty state is reachable too. TASK-0037 may expand this.
 *
 * Timestamps are computed relative to load time so check-ins never read as stale.
 */

const NOW = Date.now();
const NOW_ISO = new Date(NOW).toISOString();
const MIN = 60 * 1000;

function minsAgo(mins: number): string {
  return new Date(NOW - mins * MIN).toISOString();
}

interface Spec {
  first: string;
  last: string;
  preferred?: string;
  email?: string;
  phone?: string;
  company?: string;
  title?: string;
  dept?: string;
  /** Portfolio company id (see lib/data companies), e.g. "co-lattice". */
  portfolio?: string;
  roles?: GuestRole[];
  vip?: boolean;
  rsvp?: RsvpStatus;
  /** When set, the guest is checked in this many minutes ago. */
  checkedInMinsAgo?: number;
  noShow?: boolean;
  waitlisted?: boolean;
  plusOnes?: number;
  dietary?: string;
  accessibility?: string;
  hotel?: boolean;
  transport?: boolean;
  seatingPref?: string;
  invited?: boolean;
  reminded?: boolean;
  thanked?: boolean;
  internalNote?: string;
  publicNote?: string;
  tags?: string[];
  seat?: string;
}

function build(eventId: string, index: number, s: Spec): Guest {
  const g = newGuest(eventId, `gst-${eventId.replace("evt-", "")}-${index + 1}`, NOW_ISO);
  const email =
    s.email ?? `${s.first}.${s.last}`.toLowerCase().replace(/[^a-z.]/g, "") + "@example.com";
  return {
    ...g,
    identity: {
      firstName: s.first,
      lastName: s.last,
      preferredName: s.preferred,
      email,
      phone: s.phone,
    },
    professional: {
      company: s.company,
      jobTitle: s.title,
      department: s.dept,
      portfolioCompanyId: s.portfolio,
    },
    roles: s.roles ?? [],
    vip: s.vip ?? false,
    attendance: {
      rsvp: s.rsvp ?? "invited",
      checkedIn: s.checkedInMinsAgo !== undefined,
      checkedInAt: s.checkedInMinsAgo !== undefined ? minsAgo(s.checkedInMinsAgo) : undefined,
      noShow: s.noShow ?? false,
      waitlisted: s.waitlisted ?? false,
      plusOnes: s.plusOnes ?? 0,
    },
    preferences: {
      dietary: s.dietary,
      accessibility: s.accessibility,
      hotelNeeded: s.hotel ?? false,
      transportationNeeded: s.transport ?? false,
      seatingPreference: s.seatingPref,
    },
    communication: {
      invitationSent: s.invited ?? true,
      reminderSent: s.reminded ?? false,
      thankYouSent: s.thanked ?? false,
    },
    notes: { internal: s.internalNote, public: s.publicNote },
    tags: s.tags ?? [],
    seat: s.seat ?? null,
    createdAt: g.createdAt,
    updatedAt: g.updatedAt,
  };
}

function roster(eventId: string, specs: Spec[]): Guest[] {
  return specs.map((s, i) => build(eventId, i, s));
}

// AI Infra Roundtable — live now, intimate technical dinner. Several already in
// the room so Roll Call / check-in reads as real.
const roundtable = roster("evt-2042", [
  {
    first: "Ava",
    last: "Chen",
    title: "Co-founder & CEO",
    company: "Lattice AI",
    portfolio: "co-lattice",
    roles: ["founder"],
    vip: true,
    rsvp: "confirmed",
    checkedInMinsAgo: 38,
    dietary: "Vegetarian",
    invited: true,
    reminded: true,
    tags: ["speaker", "inference"],
    internalNote: "Keen to compare notes on serving costs — seat near Priya.",
    seat: "Head of table",
  },
  {
    first: "Devansh",
    last: "Rao",
    preferred: "Dev",
    title: "CTO",
    company: "Fathom",
    portfolio: "co-fathom",
    roles: ["founder"],
    rsvp: "confirmed",
    checkedInMinsAgo: 33,
    invited: true,
    reminded: true,
    tags: ["inference"],
  },
  {
    first: "Priya",
    last: "Anand",
    title: "Partner",
    company: "Cue Ventures",
    roles: ["internal"],
    vip: true,
    rsvp: "confirmed",
    checkedInMinsAgo: 52,
    invited: true,
    internalNote: "Host. Owns the room.",
  },
  {
    first: "Marcus",
    last: "Reyes",
    title: "Head of ML Platform",
    company: "Northwind",
    portfolio: "co-northwind",
    roles: ["founder"],
    rsvp: "confirmed",
    checkedInMinsAgo: 21,
    accessibility: "Step-free access needed",
    transport: true,
    invited: true,
    reminded: true,
  },
  {
    first: "Sofia",
    last: "Martinez",
    title: "Staff Engineer",
    company: "Cobalt",
    portfolio: "co-cobalt",
    rsvp: "confirmed",
    invited: true,
    reminded: true,
    dietary: "Gluten-free",
    tags: ["inference"],
  },
  {
    first: "Elena",
    last: "Petrova",
    title: "Reporter",
    company: "The Information",
    roles: ["press"],
    rsvp: "tentative",
    invited: true,
    internalNote: "Off the record only. Confirm before quoting founders.",
    tags: ["press"],
  },
  {
    first: "Tom",
    last: "Becker",
    title: "VP Engineering",
    company: "Arclight",
    portfolio: "co-arclight",
    rsvp: "confirmed",
    plusOnes: 1,
    invited: true,
    reminded: true,
  },
  {
    first: "Hana",
    last: "Kim",
    title: "Founding Engineer",
    company: "Lattice AI",
    portfolio: "co-lattice",
    rsvp: "declined",
    invited: true,
    internalNote: "Travel conflict — wants the recap.",
  },
]);

// Q3 Founder Summit — flagship, upcoming. A broad funnel: confirmed, invited,
// waitlisted, VIPs, a sponsor and press.
const summit = roster("evt-2041", [
  {
    first: "Dana",
    last: "Whitfield",
    title: "General Partner",
    company: "Cue Ventures",
    roles: ["internal"],
    vip: true,
    rsvp: "confirmed",
    checkedInMinsAgo: 2,
    invited: true,
    reminded: true,
    internalNote: "Opening remarks at 9am.",
  },
  {
    first: "Nadia",
    last: "Osei",
    title: "Founder & CEO",
    company: "Meridian Bio",
    portfolio: "co-meridian",
    roles: ["founder"],
    vip: true,
    rsvp: "confirmed",
    checkedInMinsAgo: 14,
    invited: true,
    reminded: true,
    hotel: true,
    dietary: "Pescatarian",
    tags: ["keynote", "series-b"],
  },
  {
    first: "Liam",
    last: "Foster",
    title: "Founder",
    company: "Northwind",
    portfolio: "co-northwind",
    roles: ["founder"],
    rsvp: "confirmed",
    checkedInMinsAgo: 48,
    plusOnes: 1,
    invited: true,
    reminded: true,
    hotel: true,
    transport: true,
  },
  {
    first: "Grace",
    last: "Liu",
    title: "COO",
    company: "Cobalt",
    portfolio: "co-cobalt",
    roles: ["founder"],
    rsvp: "invited",
    invited: true,
  },
  {
    first: "Owen",
    last: "Nakamura",
    title: "Managing Director",
    company: "Summit Peak Capital",
    roles: ["investor"],
    vip: true,
    rsvp: "confirmed",
    checkedInMinsAgo: 35,
    invited: true,
    reminded: true,
    seatingPref: "Near the founders' table",
  },
  {
    first: "Rachel",
    last: "Green",
    title: "Partnerships Lead",
    company: "Stripe",
    roles: ["sponsor"],
    rsvp: "confirmed",
    checkedInMinsAgo: 70,
    invited: true,
    reminded: true,
    tags: ["sponsor"],
    internalNote: "Sponsoring the coffee bar. Wants a logo placement photo.",
  },
  {
    first: "Marco",
    last: "Bianchi",
    title: "Senior Editor",
    company: "TechCrunch",
    roles: ["press"],
    rsvp: "tentative",
    invited: true,
    tags: ["press"],
  },
  {
    first: "Yuki",
    last: "Tanaka",
    title: "Founder",
    company: "Fathom",
    portfolio: "co-fathom",
    roles: ["founder"],
    rsvp: "waitlisted",
    waitlisted: true,
    invited: true,
    internalNote: "Waitlist — promote if a founder drops.",
  },
  {
    first: "Isabel",
    last: "Cruz",
    title: "Head of Talent",
    company: "Arclight",
    portfolio: "co-arclight",
    rsvp: "confirmed",
    checkedInMinsAgo: 60,
    invited: true,
    reminded: true,
    accessibility: "Sign-language interpreter requested",
  },
  {
    first: "Ben",
    last: "Sable",
    title: "Founder",
    company: "Lattice AI",
    portfolio: "co-lattice",
    roles: ["founder"],
    rsvp: "declined",
    invited: true,
    internalNote: "Fundraising travel. Follow up in Q4.",
  },
]);

// Meridian Series B Celebration — a wrapped event, so its roster is a record of
// who actually attended (checked in) for post-event analytics later.
const celebration = roster("evt-2045", [
  {
    first: "Nadia",
    last: "Osei",
    title: "Founder & CEO",
    company: "Meridian Bio",
    portfolio: "co-meridian",
    roles: ["founder"],
    vip: true,
    rsvp: "confirmed",
    checkedInMinsAgo: 6 * 24 * 60,
    invited: true,
    reminded: true,
    thanked: true,
  },
  {
    first: "Priya",
    last: "Anand",
    title: "Partner",
    company: "Cue Ventures",
    roles: ["internal"],
    rsvp: "confirmed",
    checkedInMinsAgo: 6 * 24 * 60 + 15,
    invited: true,
    thanked: true,
  },
  {
    first: "Carlos",
    last: "Mendez",
    title: "CFO",
    company: "Meridian Bio",
    portfolio: "co-meridian",
    rsvp: "confirmed",
    noShow: true,
    invited: true,
    reminded: true,
    internalNote: "Confirmed but never arrived — flight delayed.",
  },
]);

// ---------------------------------------------------------------------------
// Large live roster for the Roll Call Command Center (TASK-0040)
// ---------------------------------------------------------------------------
//
// The command center's flagship promise is checking in ~500 attendees at speed,
// so the live Summit needs a roster at that scale to be walkable and to exercise
// search, infinite scroll, arrival pacing, and the live metric board. This is
// generated with a seeded PRNG so the roster is *stable across reloads* (a demo
// that reshuffles on every refresh reads as broken) and so the numbers stay
// deterministic. The curated anchors above (Dana, Nadia, Owen…) sit at the front
// for named VIP colour; procedural attendees fill the room behind them.

/** Deterministic PRNG (mulberry32) — stable roster without Math.random. */
function mulberry32(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const GEN_FIRST = [
  "Aisha", "Alex", "Amara", "Andre", "Anika", "Ben", "Bianca", "Caleb", "Carla", "Chen",
  "Daniel", "Deepa", "Diego", "Elena", "Emeka", "Fatima", "Felix", "Gabriel", "Grace", "Hana",
  "Hugo", "Ines", "Isaac", "Jamal", "Jana", "Jae", "Kaito", "Kira", "Lars", "Leah",
  "Lucas", "Maya", "Mateo", "Mei", "Naomi", "Nikhil", "Nora", "Omar", "Priya", "Quinn",
  "Rafael", "Ravi", "Rosa", "Sam", "Sana", "Sofia", "Tariq", "Tessa", "Uma", "Viktor",
  "Wei", "Yara", "Yusuf", "Zoe",
];

const GEN_LAST = [
  "Abara", "Adeyemi", "Ahmed", "Alvarez", "Bauer", "Bell", "Cho", "Costa", "Dixon", "Duarte",
  "Ferreira", "Fischer", "Ghosh", "Gupta", "Hansen", "Ibrahim", "Ito", "Jensen", "Kaur", "Khan",
  "Kim", "Kovac", "Larsen", "Lopez", "Malik", "Mehta", "Meyer", "Nakamura", "Novak", "Okafor",
  "Ortiz", "Park", "Patel", "Petrov", "Reyes", "Rossi", "Sato", "Silva", "Singh", "Sorensen",
  "Tan", "Torres", "Vega", "Wang", "Weber", "Yamamoto", "Zhang", "Ziegler",
];

/** Non-portfolio companies attendees come from. */
const GEN_COMPANIES = [
  "Anthropic", "Stripe", "Ramp", "Figma", "Notion", "Vercel", "Linear", "Retool", "Airtable",
  "Rippling", "Brex", "Scale", "Databricks", "Snowflake", "Confluent", "HashiCorp", "Sequoia",
  "a16z", "Greylock", "Benchmark", "Index Ventures", "First Round", "Kleiner Perkins",
  "Mayfield", "Craft Ventures", "The Information", "Bloomberg", "TechCrunch", "Independent",
  "Angel Investor",
];

/** Portfolio companies, by id, so generated guests carry real portfolio ties. */
const GEN_PORTFOLIO: { id: string; name: string }[] = [
  { id: "co-northwind", name: "Northwind" },
  { id: "co-lattice", name: "Lattice AI" },
  { id: "co-meridian", name: "Meridian Bio" },
  { id: "co-cobalt", name: "Cobalt" },
  { id: "co-fathom", name: "Fathom" },
  { id: "co-arclight", name: "Arclight" },
];

const GEN_TITLES = [
  "Founder & CEO", "Co-founder & CTO", "VP Engineering", "Head of Product", "COO", "CFO",
  "Head of Growth", "Partner", "Principal", "Managing Director", "General Partner",
  "Head of Talent", "Staff Engineer", "Chief of Staff", "Head of GTM", "VP Sales",
  "Head of Design", "Operating Partner", "Analyst", "Reporter",
];

const GEN_TAGS = [
  "keynote", "speaker", "series-a", "series-b", "ai", "infra", "gtm", "hiring",
  "first-time", "returning", "sponsor", "press", "vip-guest", "founder-track",
];

/**
 * Generate `count` procedural attendees for the live Summit. Deterministic given
 * the seed. Roughly: 66% confirmed / 16% invited / rest tentative-waitlisted-
 * declined; ~35% carry a portfolio tie; ~55% of confirmed have already arrived,
 * staggered across the last ~95 minutes (so arrival rate, recent arrivals, and
 * the VIP greet queue all populate); a thin slice of un-confirmed guests have
 * checked in as walk-ins.
 */
function generatedRoster(eventId: string, count: number, seed: number): Guest[] {
  const rand = mulberry32(seed);
  const pick = <T,>(arr: T[]): T => arr[Math.floor(rand() * arr.length)];
  const specs: Spec[] = [];

  for (let i = 0; i < count; i++) {
    const first = pick(GEN_FIRST);
    const last = pick(GEN_LAST);

    const rsvpRoll = rand();
    let rsvp: RsvpStatus =
      rsvpRoll < 0.66 ? "confirmed"
      : rsvpRoll < 0.82 ? "invited"
      : rsvpRoll < 0.9 ? "tentative"
      : rsvpRoll < 0.95 ? "waitlisted"
      : "declined";

    // ~8% VIPs, always confirmed so they belong to the greet queue.
    const vip = rand() < 0.08;
    if (vip) rsvp = "confirmed";

    // Portfolio tie or an outside company.
    let company: string;
    let portfolio: string | undefined;
    if (rand() < 0.35) {
      const co = pick(GEN_PORTFOLIO);
      company = co.name;
      portfolio = co.id;
    } else {
      company = pick(GEN_COMPANIES);
    }

    // Role, founder-weighted for a founder summit.
    const roleRoll = rand();
    const roles: GuestRole[] =
      roleRoll < 0.5 ? ["founder"]
      : roleRoll < 0.7 ? ["investor"]
      : roleRoll < 0.85 ? ["internal"]
      : roleRoll < 0.93 ? ["sponsor"]
      : ["press"];

    // Arrivals: staggered over the last ~95 minutes. VIPs arrive a touch more
    // reliably so the VIP feed and greet alert are lively.
    let checkedInMinsAgo: number | undefined;
    if (rsvp === "confirmed" && rand() < (vip ? 0.6 : 0.55)) {
      checkedInMinsAgo = 1 + Math.floor(rand() * 95);
    } else if ((rsvp === "invited" || rsvp === "tentative") && rand() < 0.06) {
      checkedInMinsAgo = 1 + Math.floor(rand() * 25); // walk-in
    }

    specs.push({
      first,
      last,
      company,
      portfolio,
      title: pick(GEN_TITLES),
      roles,
      vip,
      rsvp,
      checkedInMinsAgo,
      waitlisted: rsvp === "waitlisted",
      plusOnes: rand() < 0.12 ? 1 : 0,
      invited: true,
      reminded: rand() < 0.7,
      tags: rand() < 0.4 ? [pick(GEN_TAGS)] : undefined,
    });
  }

  // Offset generated ids past the curated anchors so ids stay unique per event.
  return specs.map((s, i) => build(eventId, summit.length + i, s));
}

const summitCrowd = generatedRoster("evt-2041", 470, 0x5c0e);

export const seedGuests: Guest[] = [
  ...roundtable,
  ...summit,
  ...summitCrowd,
  ...celebration,
];
