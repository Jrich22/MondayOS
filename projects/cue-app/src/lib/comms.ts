import type { Guest } from "./types";

/**
 * Communications Center domain + pure logic (TASK-0043).
 *
 * The Communications workspace is Cue's outbound-comms mission control: it spans
 * the *entire event lifecycle*, from Save the Date through the post-event Survey.
 * Following the app's convention (lib/select, lib/guests-select, lib/mission),
 * every operational rule lives here as a pure function so the workspace is
 * presentation-only and the rules — who a campaign reaches, how a sent campaign's
 * metrics are derived, what each lifecycle stage means — are unit-testable in one
 * auditable place.
 *
 * INTEGRATION SEAMS (per task): a Campaign references its `CueEvent` by id and
 * resolves its recipients from the shared Guest roster (Guest Management), never
 * nesting either. So audiences always agree with the roster the rest of the app
 * shows, and adding a real email provider later changes only the store's "send"
 * path — not this model.
 */

// ---------------------------------------------------------------------------
// Lifecycle stages — the left navigation, in event-timeline order
// ---------------------------------------------------------------------------

export type CampaignStage =
  | "save-the-date"
  | "invitations"
  | "rsvp-reminders"
  | "vip-outreach"
  | "event-updates"
  | "day-before"
  | "morning-of"
  | "thank-you"
  | "survey";

export interface StageMeta {
  /** Left-nav label. */
  label: string;
  /** One-line description of what this stage's campaigns do. */
  blurb: string;
  /** Icon key resolved by the UI (see components/comms/stage-icon). */
  icon: string;
  /** Default audience a new campaign in this stage targets. */
  defaultAudience: AudienceId;
  /** Where in the timeline this sends, relative to the event ("T-14d", "Live"). */
  timing: string;
}

/** Stages in lifecycle order — this array *is* the left-nav order. */
export const STAGE_ORDER: CampaignStage[] = [
  "save-the-date",
  "invitations",
  "rsvp-reminders",
  "vip-outreach",
  "event-updates",
  "day-before",
  "morning-of",
  "thank-you",
  "survey",
];

export const STAGE_META: Record<CampaignStage, StageMeta> = {
  "save-the-date": {
    label: "Save the Date",
    blurb: "Early heads-up so the calendar hold lands before the formal invite.",
    icon: "calendar",
    defaultAudience: "everyone",
    timing: "T‑6 weeks",
  },
  invitations: {
    label: "Invitations",
    blurb: "The formal invite with the RSVP ask — your highest-stakes send.",
    icon: "mail",
    defaultAudience: "everyone",
    timing: "T‑4 weeks",
  },
  "rsvp-reminders": {
    label: "RSVP Reminders",
    blurb: "Nudge the guests who haven't responded yet toward a decision.",
    icon: "bell",
    defaultAudience: "pending",
    timing: "T‑2 weeks",
  },
  "vip-outreach": {
    label: "VIP Outreach",
    blurb: "Personal, high-touch notes to the guests who move the room.",
    icon: "star",
    defaultAudience: "vips",
    timing: "T‑2 weeks",
  },
  "event-updates": {
    label: "Event Updates",
    blurb: "Agenda, speaker, and logistics changes for confirmed guests.",
    icon: "megaphone",
    defaultAudience: "confirmed",
    timing: "T‑1 week",
  },
  "day-before": {
    label: "Day Before",
    blurb: "Final logistics — timing, address, parking — the night before.",
    icon: "moon",
    defaultAudience: "confirmed",
    timing: "T‑1 day",
  },
  "morning-of": {
    label: "Morning Of",
    blurb: "The last-mile note that gets confirmed guests through the door.",
    icon: "sun",
    defaultAudience: "confirmed",
    timing: "Event day",
  },
  "thank-you": {
    label: "Thank You",
    blurb: "Warm follow-up to everyone who showed up — sent while it's fresh.",
    icon: "heart",
    defaultAudience: "confirmed",
    timing: "T+1 day",
  },
  survey: {
    label: "Survey Campaigns",
    blurb: "Structured feedback to turn this event into a better next one.",
    icon: "clipboard",
    defaultAudience: "confirmed",
    timing: "T+2 days",
  },
};

// ---------------------------------------------------------------------------
// Audiences — segments resolved live against the Guest roster
// ---------------------------------------------------------------------------

export type AudienceId =
  | "everyone"
  | "confirmed"
  | "pending"
  | "declined"
  | "vips"
  | "speakers"
  | "sponsors"
  | "portfolio"
  | "custom";

export interface AudienceMeta {
  label: string;
  /** One-line description of who this segment includes. */
  description: string;
}

export const AUDIENCE_ORDER: AudienceId[] = [
  "everyone",
  "confirmed",
  "pending",
  "declined",
  "vips",
  "speakers",
  "sponsors",
  "portfolio",
  "custom",
];

export const AUDIENCE_META: Record<AudienceId, AudienceMeta> = {
  everyone: { label: "Everyone", description: "Every guest on the list, regardless of status." },
  confirmed: { label: "Confirmed", description: "Guests who have RSVP'd yes." },
  pending: { label: "Pending RSVP", description: "Invited or tentative — no firm answer yet." },
  declined: { label: "Declined", description: "Guests who RSVP'd no." },
  vips: { label: "VIPs", description: "Everyone flagged as a VIP." },
  speakers: { label: "Speakers", description: "Guests tagged as speakers or keynotes." },
  sponsors: { label: "Sponsors", description: "Guests in a sponsor role." },
  portfolio: { label: "Portfolio Companies", description: "Guests tied to a portfolio company." },
  custom: { label: "Custom Tags", description: "Guests carrying a specific tag." },
};

/** Tags that mark a guest as a speaker for the Speakers audience. */
const SPEAKER_TAGS = ["speaker", "keynote"];

/** Whether a guest belongs to an audience segment. `tag` applies to "custom". */
export function matchesAudience(g: Guest, audience: AudienceId, tag?: string): boolean {
  switch (audience) {
    case "everyone":
      return true;
    case "confirmed":
      return g.attendance.rsvp === "confirmed";
    case "pending":
      return g.attendance.rsvp === "invited" || g.attendance.rsvp === "tentative";
    case "declined":
      return g.attendance.rsvp === "declined";
    case "vips":
      return g.vip;
    case "speakers":
      return g.tags.some((t) => SPEAKER_TAGS.includes(t.toLowerCase()));
    case "sponsors":
      return g.roles.includes("sponsor");
    case "portfolio":
      return Boolean(g.professional.portfolioCompanyId);
    case "custom":
      return tag ? g.tags.includes(tag) : false;
  }
}

/** The recipients a campaign reaches, resolved live from the roster. */
export function resolveAudience(
  guests: Guest[],
  audience: AudienceId,
  tag?: string,
): Guest[] {
  return guests.filter((g) => matchesAudience(g, audience, tag));
}

/** How many guests an audience reaches. */
export function audienceCount(guests: Guest[], audience: AudienceId, tag?: string): number {
  let n = 0;
  for (const g of guests) if (matchesAudience(g, audience, tag)) n += 1;
  return n;
}

/** A human label for a campaign's audience, folding in the custom tag. */
export function audienceLabel(audience: AudienceId, tag?: string): string {
  if (audience === "custom") return tag ? `#${tag}` : "Custom tag";
  return AUDIENCE_META[audience].label;
}

// ---------------------------------------------------------------------------
// Campaign model
// ---------------------------------------------------------------------------

export type CampaignStatus = "draft" | "scheduled" | "sent";

export interface CampaignMetrics {
  /** How many guests the send went to. */
  recipients: number;
  delivered: number;
  opened: number;
  clicked: number;
  /** RSVP / survey responses attributed to this campaign. */
  responded: number;
}

export interface Campaign {
  id: string;
  /** The event this campaign belongs to (`CueEvent.id`). */
  eventId: string;
  stage: CampaignStage;
  title: string;
  audience: AudienceId;
  /** Set only when `audience === "custom"`. */
  audienceTag?: string;
  subject: string;
  message: string;
  status: CampaignStatus;
  /** ISO send time for a scheduled campaign. */
  scheduledAt?: string;
  /** ISO time a sent campaign went out. */
  sentAt?: string;
  metrics: CampaignMetrics;
  createdAt: string;
  updatedAt: string;
}

export function emptyMetrics(): CampaignMetrics {
  return { recipients: 0, delivered: 0, opened: 0, clicked: 0, responded: 0 };
}

export const STATUS_META: Record<CampaignStatus, { label: string; dot: string; text: string }> = {
  draft: { label: "Draft", dot: "bg-status-draft", text: "text-status-draft" },
  scheduled: { label: "Scheduled", dot: "bg-status-upcoming", text: "text-status-upcoming" },
  sent: { label: "Sent", dot: "bg-status-live", text: "text-status-live" },
};

// --- Per-campaign rate helpers (guard divide-by-zero) -----------------------

function safeRatio(num: number, den: number): number {
  return den > 0 ? num / den : 0;
}

export function deliveryRate(m: CampaignMetrics): number {
  return safeRatio(m.delivered, m.recipients);
}
export function openRate(m: CampaignMetrics): number {
  return safeRatio(m.opened, m.delivered);
}
export function clickRate(m: CampaignMetrics): number {
  return safeRatio(m.clicked, m.opened);
}
export function responseRate(m: CampaignMetrics): number {
  return safeRatio(m.responded, m.delivered);
}

// ---------------------------------------------------------------------------
// Deterministic metric projection for a freshly "sent" campaign
// ---------------------------------------------------------------------------
//
// There is no email provider in the MVP, so a campaign that the user "sends"
// still needs believable engagement numbers. We derive them deterministically
// from the recipient count, the lifecycle stage (a VIP note opens far more than
// a bulk save-the-date), and a hash of the campaign id (so two campaigns of the
// same size read differently without Math.random, which is banned in this app's
// data layer for stable demos).

/** Baseline open rate per stage — high-touch stages open more. */
const STAGE_OPEN_RATE: Record<CampaignStage, number> = {
  "save-the-date": 0.58,
  invitations: 0.64,
  "rsvp-reminders": 0.52,
  "vip-outreach": 0.82,
  "event-updates": 0.56,
  "day-before": 0.61,
  "morning-of": 0.67,
  "thank-you": 0.71,
  survey: 0.49,
};

/** Share of openers who respond (RSVP for pre-event, feedback for survey). */
const STAGE_RESPONSE_RATE: Record<CampaignStage, number> = {
  "save-the-date": 0.22,
  invitations: 0.54,
  "rsvp-reminders": 0.48,
  "vip-outreach": 0.6,
  "event-updates": 0.12,
  "day-before": 0.08,
  "morning-of": 0.06,
  "thank-you": 0.14,
  survey: 0.56,
};

/** Small deterministic string hash (djb2) — stable variance without randomness. */
function hash(s: string): number {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

/**
 * Believable engagement metrics for a send. Deterministic given the same inputs,
 * so the demo never reshuffles on reload. Variance is small (±5 points) and
 * derived from the seed string (usually the campaign id).
 */
export function projectMetrics(
  recipients: number,
  stage: CampaignStage,
  seed: string,
): CampaignMetrics {
  if (recipients <= 0) return emptyMetrics();
  const jitter = (offset: number, span: number) =>
    ((hash(seed + offset) % (span * 2 + 1)) - span) / 100;

  const delivered = Math.round(recipients * clamp(0.985 + jitter(1, 1), 0.95, 1));
  const openR = clamp(STAGE_OPEN_RATE[stage] + jitter(2, 5), 0.15, 0.95);
  const opened = Math.round(delivered * openR);
  const clicked = Math.round(opened * clamp(0.42 + jitter(3, 6), 0.1, 0.85));
  const responded = Math.round(opened * clamp(STAGE_RESPONSE_RATE[stage] + jitter(4, 4), 0, 0.9));

  return {
    recipients,
    delivered: Math.min(delivered, recipients),
    opened: Math.min(opened, delivered),
    clicked: Math.min(clicked, opened),
    responded: Math.min(responded, opened),
  };
}

// ---------------------------------------------------------------------------
// Selection + workspace-level rollups
// ---------------------------------------------------------------------------

/** Campaigns for one event, newest-updated first. */
export function campaignsForEvent(campaigns: Campaign[], eventId: string): Campaign[] {
  return campaigns
    .filter((c) => c.eventId === eventId)
    .sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt));
}

/** Campaigns in one stage of one event, newest-updated first. */
export function campaignsForStage(
  campaigns: Campaign[],
  eventId: string,
  stage: CampaignStage,
): Campaign[] {
  return campaignsForEvent(campaigns, eventId).filter((c) => c.stage === stage);
}

/** How many campaigns each stage has, for the left-nav counts. */
export function stageCounts(
  campaigns: Campaign[],
  eventId: string,
): Record<CampaignStage, number> {
  const counts = Object.fromEntries(STAGE_ORDER.map((s) => [s, 0])) as Record<
    CampaignStage,
    number
  >;
  for (const c of campaigns) if (c.eventId === eventId) counts[c.stage] += 1;
  return counts;
}

export interface WorkspaceMetrics {
  /** Total messages delivered across sent campaigns. */
  sent: number;
  /** Number of campaigns waiting to go out. */
  scheduled: number;
  opened: number;
  clicked: number;
  responded: number;
  delivered: number;
  /** delivered ÷ recipients across sent campaigns (0–1). */
  deliveryRate: number;
  /** responded ÷ delivered across sent campaigns (0–1). */
  rsvpRate: number;
}

/**
 * Roll up the seven headline metrics across an event's campaigns. "Sent" counts
 * delivered messages (what actually reached inboxes); "Scheduled" counts pending
 * campaigns. Rates are pooled over the delivered base so a big campaign weighs
 * more than a tiny one.
 */
export function workspaceMetrics(campaigns: Campaign[], eventId: string): WorkspaceMetrics {
  let recipients = 0;
  let delivered = 0;
  let opened = 0;
  let clicked = 0;
  let responded = 0;
  let scheduled = 0;

  for (const c of campaigns) {
    if (c.eventId !== eventId) continue;
    if (c.status === "scheduled") scheduled += 1;
    if (c.status === "sent") {
      recipients += c.metrics.recipients;
      delivered += c.metrics.delivered;
      opened += c.metrics.opened;
      clicked += c.metrics.clicked;
      responded += c.metrics.responded;
    }
  }

  return {
    sent: delivered,
    scheduled,
    opened,
    clicked,
    responded,
    delivered,
    deliveryRate: safeRatio(delivered, recipients),
    rsvpRate: safeRatio(responded, delivered),
  };
}

// --- Formatting helpers -----------------------------------------------------

export function pct(ratio: number): string {
  return `${Math.round(ratio * 100)}%`;
}

/** Compact count: 1240 → "1.2k". */
export function compact(n: number): string {
  if (n < 1000) return String(n);
  return `${(n / 1000).toFixed(n % 1000 === 0 ? 0 : 1)}k`;
}

// ---------------------------------------------------------------------------
// Construction
// ---------------------------------------------------------------------------

/** A blank draft campaign for a stage — sensible defaults, nothing sent yet. */
export function newCampaign(
  id: string,
  eventId: string,
  stage: CampaignStage,
  nowIso: string,
): Campaign {
  return {
    id,
    eventId,
    stage,
    title: `${STAGE_META[stage].label} campaign`,
    audience: STAGE_META[stage].defaultAudience,
    subject: "",
    message: "",
    status: "draft",
    metrics: emptyMetrics(),
    createdAt: nowIso,
    updatedAt: nowIso,
  };
}
