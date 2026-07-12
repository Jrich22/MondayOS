import type { Campaign, CampaignStage } from "./comms";
import { projectMetrics, emptyMetrics } from "./comms";

/**
 * Seed data for the Communications Center (TASK-0043): reusable templates and a
 * walkable set of campaigns across the event lifecycle. Like lib/data (events)
 * and lib/guests-data (roster), this stands in for the API/persistence layer so
 * the workspace is fully walkable on day one — the flagship Summit has campaigns
 * in every state (sent with real metrics, scheduled, and draft), a wrapped event
 * has its thank-you + survey, and a live dinner has its invitations out.
 *
 * Timestamps are computed relative to load time so nothing reads as stale.
 */

const MIN = 60 * 1000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;
const NOW = Date.now();

function iso(offsetMs: number): string {
  return new Date(NOW + offsetMs).toISOString();
}

// ---------------------------------------------------------------------------
// Templates — eight seed campaigns spanning the kinds of events Cue runs
// ---------------------------------------------------------------------------

export interface CommsTemplate {
  id: string;
  name: string;
  /** One-line description shown on the template card. */
  description: string;
  /** The lifecycle stage this template is written for. */
  stage: CampaignStage;
  subject: string;
  message: string;
  /** Short tags surfaced on the card. */
  tags: string[];
}

export const seedTemplates: CommsTemplate[] = [
  {
    id: "tpl-founder-dinner",
    name: "Founder Dinner",
    description: "Intimate, personal invite for a small curated dinner.",
    stage: "invitations",
    subject: "A seat at the table — {{event_name}}",
    message:
      `Hi {{first_name}},\n\n` +
      `We're pulling together a small dinner for a handful of founders we admire, and we'd love for you to be one of them. No agenda, no panel — just good people, honest conversation, and a proper meal.\n\n` +
      `It's {{event_date}} at {{venue}}. The table is intentionally small, so we're only reaching out to a few.\n\n` +
      `Can you make it? Just reply and we'll save your seat.\n\n` +
      `Warmly,\n{{host}}`,
    tags: ["dinner", "intimate", "founders"],
  },
  {
    id: "tpl-board-meeting",
    name: "Board Meeting",
    description: "Crisp, formal notice with agenda and materials.",
    stage: "invitations",
    subject: "Board Meeting — {{event_name}}",
    message:
      `Dear {{first_name}},\n\n` +
      `This is a formal notice of the upcoming board meeting for {{event_name}}, to be held {{event_date}} at {{venue}}.\n\n` +
      `Agenda:\n  1. Approval of prior minutes\n  2. CEO & financial review\n  3. Key decisions and approvals\n  4. Forward plan and next steps\n\n` +
      `Board materials will be circulated 72 hours in advance. Please confirm your attendance at your earliest convenience.\n\n` +
      `Kind regards,\n{{host}}`,
    tags: ["governance", "formal"],
  },
  {
    id: "tpl-investor-summit",
    name: "Investor Summit",
    description: "Polished invite for LPs and co-investors.",
    stage: "invitations",
    subject: "You're invited: {{event_name}}",
    message:
      `Dear {{first_name}},\n\n` +
      `We would be delighted to have you join us for {{event_name}}, our gathering for limited partners and co-investors, on {{event_date}} at {{venue}}.\n\n` +
      `The program brings together the people building the portfolio and the people backing it, with candid updates, direct access to founders, and time to connect.\n\n` +
      `We hope you'll join us. Please RSVP below.\n\n` +
      `Warm regards,\n{{host}}`,
    tags: ["LPs", "investors", "polished"],
  },
  {
    id: "tpl-customer-advisory",
    name: "Customer Advisory Board",
    description: "Partnership-toned invite for your most strategic customers.",
    stage: "invitations",
    subject: "Join our Customer Advisory Board — {{event_name}}",
    message:
      `Hi {{first_name}},\n\n` +
      `You're one of the customers whose perspective has shaped where we're headed — which is exactly why we'd love to have you at {{event_name}} on {{event_date}}.\n\n` +
      `This is a working session, not a pitch: a small group of leaders helping us set direction, pressure-test the roadmap, and share what's working (and what isn't).\n\n` +
      `Would you join us? Your seat is reserved.\n\n` +
      `Thank you,\n{{host}}`,
    tags: ["customers", "advisory", "strategic"],
  },
  {
    id: "tpl-executive-offsite",
    name: "Executive Offsite",
    description: "Focused, logistics-forward note for a leadership offsite.",
    stage: "invitations",
    subject: "Leadership Offsite — {{event_name}}",
    message:
      `Hi {{first_name}},\n\n` +
      `Details for our leadership offsite, {{event_name}}, are set: {{event_date}} at {{venue}}.\n\n` +
      `Two days to step back from the day-to-day, align on the year ahead, and do the deep work we never get to in the calendar's cracks. Come ready to think and to disagree well.\n\n` +
      `Travel and lodging details to follow. Please confirm you're in.\n\n` +
      `{{host}}`,
    tags: ["leadership", "offsite", "planning"],
  },
  {
    id: "tpl-conference",
    name: "Conference",
    description: "Energetic, at-scale invite with a clear call to register.",
    stage: "invitations",
    subject: "{{event_name}} — save your spot",
    message:
      `Hi {{first_name}},\n\n` +
      `{{event_name}} is coming to {{venue}} on {{event_date}}, and you're on the list.\n\n` +
      `Expect a full program of talks, hands-on sessions, and the kind of hallway conversations that are worth the ticket on their own. Speakers, agenda, and workshops are live now.\n\n` +
      `Spots are limited — register below to lock yours in.\n\n` +
      `See you there,\n{{host}}`,
    tags: ["conference", "at-scale"],
  },
  {
    id: "tpl-alumni",
    name: "University Alumni",
    description: "Warm, nostalgic reconnect note for an alumni gathering.",
    stage: "invitations",
    subject: "Come back for a night — {{event_name}}",
    message:
      `Hi {{first_name}},\n\n` +
      `It's been too long. We're bringing the community back together for {{event_name}} on {{event_date}} at {{venue}}, and it wouldn't be the same without you.\n\n` +
      `Old friends, new faces, and a chance to pick up conversations right where they left off. Bring your stories.\n\n` +
      `We'd love to see you there — RSVP below.\n\n` +
      `Warmly,\n{{host}}`,
    tags: ["alumni", "reunion", "warm"],
  },
  {
    id: "tpl-nonprofit-gala",
    name: "Nonprofit Gala",
    description: "Elegant, mission-forward invite for a fundraising gala.",
    stage: "invitations",
    subject: "An evening for a cause — {{event_name}}",
    message:
      `Dear {{first_name}},\n\n` +
      `We warmly invite you to {{event_name}}, an evening in support of the work we care about most, on {{event_date}} at {{venue}}.\n\n` +
      `Join us for dinner, a program celebrating this year's impact, and the chance to help write the next chapter. Every seat makes a difference.\n\n` +
      `We would be honored to have you with us. Please RSVP below.\n\n` +
      `With gratitude,\n{{host}}`,
    tags: ["gala", "fundraising", "elegant"],
  },
];

// ---------------------------------------------------------------------------
// Campaigns — a walkable lifecycle across three events
// ---------------------------------------------------------------------------

interface CampaignSpec {
  id: string;
  eventId: string;
  stage: CampaignStage;
  title: string;
  audience: Campaign["audience"];
  audienceTag?: string;
  subject: string;
  message: string;
  status: Campaign["status"];
  /** Recipients for a sent campaign; drives the projected metrics. */
  recipients?: number;
  /** Offset from now (ms) for sentAt/scheduledAt. */
  timeOffsetMs: number;
  /** Offset from now (ms) for createdAt/updatedAt (defaults near timeOffset). */
  createdOffsetMs?: number;
}

function build(spec: CampaignSpec): Campaign {
  const created = spec.createdOffsetMs ?? spec.timeOffsetMs - 2 * HOUR;
  const metrics =
    spec.status === "sent" && spec.recipients
      ? projectMetrics(spec.recipients, spec.stage, spec.id)
      : emptyMetrics();
  return {
    id: spec.id,
    eventId: spec.eventId,
    stage: spec.stage,
    title: spec.title,
    audience: spec.audience,
    audienceTag: spec.audienceTag,
    subject: spec.subject,
    message: spec.message,
    status: spec.status,
    scheduledAt: spec.status === "scheduled" ? iso(spec.timeOffsetMs) : undefined,
    sentAt: spec.status === "sent" ? iso(spec.timeOffsetMs) : undefined,
    metrics,
    createdAt: iso(created),
    updatedAt: iso(spec.timeOffsetMs),
  };
}

const SUMMIT = "evt-2041";
const ROUNDTABLE = "evt-2042";
const CELEBRATION = "evt-2045";

const specs: CampaignSpec[] = [
  // --- Q3 Founder Summit (flagship, live) — a full funnel ---
  {
    id: "cmp-2041-std",
    eventId: SUMMIT,
    stage: "save-the-date",
    title: "Summit — Save the Date",
    audience: "everyone",
    subject: "Save the date: Q3 Founder Summit",
    message:
      `Hi {{first_name}},\n\nHold the date — the Q3 Founder Summit is coming to Pier 27 in San Francisco. A full day with the founders building across the portfolio. The formal invite is on its way; for now, just save the date.\n\nMore soon,\nDana`,
    status: "sent",
    recipients: 481,
    timeOffsetMs: -40 * DAY,
  },
  {
    id: "cmp-2041-inv",
    eventId: SUMMIT,
    stage: "invitations",
    title: "Summit — Official Invitation",
    audience: "everyone",
    subject: "You're invited: Q3 Founder Summit",
    message:
      `Hi {{first_name}},\n\nWe'd love for you to join us for the Q3 Founder Summit at Pier 27 Pavilion — a full day built for Series A+ founders across the portfolio. Fireside conversations, working roundtables, and the room you actually want to be in.\n\nSpace is limited. RSVP below to save your spot.\n\nWarmly,\nDana`,
    status: "sent",
    recipients: 481,
    timeOffsetMs: -28 * DAY,
  },
  {
    id: "cmp-2041-rem",
    eventId: SUMMIT,
    stage: "rsvp-reminders",
    title: "Summit — RSVP Reminder",
    audience: "pending",
    subject: "Still hoping to see you at the Summit",
    message:
      `Hi {{first_name}},\n\nWe haven't heard back yet and wanted to make sure this didn't slip past you — we're holding a spot for you at the Q3 Founder Summit. A quick yes or no helps us plan the room.\n\nRSVP here whenever you have a moment.\n\nThanks,\nDana`,
    status: "sent",
    recipients: 161,
    timeOffsetMs: -14 * DAY,
  },
  {
    id: "cmp-2041-vip",
    eventId: SUMMIT,
    stage: "vip-outreach",
    title: "Summit — VIP Personal Notes",
    audience: "vips",
    subject: "A personal note ahead of the Summit",
    message:
      `Hi {{first_name}},\n\nA quick personal note — I'd really love to have you in the room at the Summit. We've reserved a seat near the founders' table for you, and there are a couple of people I'd like to make sure you meet.\n\nLet me know if there's anything that would make the day work better for you.\n\nDana`,
    status: "sent",
    recipients: 42,
    timeOffsetMs: -10 * DAY,
  },
  {
    id: "cmp-2041-upd",
    eventId: SUMMIT,
    stage: "event-updates",
    title: "Summit — Agenda & Speakers Update",
    audience: "confirmed",
    subject: "What's new for the Q3 Founder Summit",
    message:
      `Hi {{first_name}},\n\nA few updates before the Summit: the agenda and speaker lineup are now final, doors open at 8:30am for coffee, and we've added an afternoon roundtable block by request.\n\nFull details are on the event page. See you soon.\n\nDana`,
    status: "scheduled",
    timeOffsetMs: 1 * DAY,
  },
  {
    id: "cmp-2041-day",
    eventId: SUMMIT,
    stage: "day-before",
    title: "Summit — Day Before Logistics",
    audience: "confirmed",
    subject: "See you tomorrow at the Summit",
    message:
      `Hi {{first_name}},\n\nThe Summit is tomorrow. Doors at 8:30am at Pier 27 Pavilion, The Embarcadero. Parking is limited — rideshare is easiest. Bring a layer; the pavilion runs cool in the morning.\n\nLooking forward to it.\n\nDana`,
    status: "draft",
    timeOffsetMs: -1 * HOUR,
  },
  {
    id: "cmp-2041-thanks",
    eventId: SUMMIT,
    stage: "thank-you",
    title: "Summit — Thank You (draft)",
    audience: "confirmed",
    subject: "Thank you for joining the Summit",
    message: "",
    status: "draft",
    timeOffsetMs: -30 * MIN,
  },

  // --- AI Infra Roundtable (live dinner) ---
  {
    id: "cmp-2042-inv",
    eventId: ROUNDTABLE,
    stage: "invitations",
    title: "Roundtable — Dinner Invitation",
    audience: "everyone",
    subject: "A seat at the table — AI Infra Roundtable",
    message:
      `Hi {{first_name}},\n\nWe're pulling together a small dinner on scaling inference — technical founders only, no slides. We'd love for you to join us at The Battery.\n\nThe table is intentionally small. Can you make it?\n\nPriya`,
    status: "sent",
    recipients: 26,
    timeOffsetMs: -12 * DAY,
  },
  {
    id: "cmp-2042-day",
    eventId: ROUNDTABLE,
    stage: "day-before",
    title: "Roundtable — Final Details",
    audience: "confirmed",
    subject: "See you tomorrow — AI Infra Roundtable",
    message:
      `Hi {{first_name}},\n\nDinner is tomorrow at The Battery, Library room, 7pm. Come hungry and bring the hard questions.\n\nPriya`,
    status: "sent",
    recipients: 22,
    timeOffsetMs: -1 * DAY,
  },

  // --- Meridian Bio Series B Celebration (wrapped) — post-event ---
  {
    id: "cmp-2045-thanks",
    eventId: CELEBRATION,
    stage: "thank-you",
    title: "Celebration — Thank You",
    audience: "confirmed",
    subject: "Thank you for celebrating with us",
    message:
      `Hi {{first_name}},\n\nThank you for coming to celebrate Meridian's Series B — it meant a lot to have you there. Onward to the next chapter.\n\nWith gratitude,\nPriya`,
    status: "sent",
    recipients: 38,
    timeOffsetMs: -3 * DAY,
  },
  {
    id: "cmp-2045-survey",
    eventId: CELEBRATION,
    stage: "survey",
    title: "Celebration — Feedback Survey",
    audience: "confirmed",
    subject: "Two minutes on the celebration?",
    message:
      `Hi {{first_name}},\n\nWe'd love your quick read on the evening — what worked, what we'd change, and anyone you'd like an intro to. Two minutes, promise.\n\nThank you,\nPriya`,
    status: "sent",
    recipients: 38,
    timeOffsetMs: -2 * DAY,
  },
];

export const seedCampaigns: Campaign[] = specs.map(build);
