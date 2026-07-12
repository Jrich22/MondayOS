import type { CueEvent } from "./types";
import type { CampaignStage } from "./comms";
import { classificationLabel } from "./classification";
import { formatEventDate } from "./format";

/**
 * The Communications AI Assistant (TASK-0043) — offline/mock only.
 *
 * Same stance as lib/ai (create flow) and lib/rollcall-ai (roll call): Cue is an
 * operations tool first, so the assistant is deterministic, offline, and fully
 * unit-testable. It drafts and reshapes campaign copy from templates + the event
 * context; there is no network and no model wired in. When a real model lands,
 * only `runAssist` changes — the actions, callers, and UI stay put.
 *
 * Actions split into two shapes:
 *   • Generators (invitation, follow-up, thank-you, survey) produce a subject +
 *     message from scratch, using the event and lifecycle stage.
 *   • Transforms (professional, friendly, shorten, expand) reshape the copy the
 *     user already has, so the assistant augments their draft rather than
 *     replacing it.
 *   • Subject lines returns several options to choose from.
 */

export type AssistAction =
  | "generate-invitation"
  | "rewrite-professional"
  | "rewrite-friendly"
  | "shorten"
  | "expand"
  | "subject-lines"
  | "generate-follow-up"
  | "generate-thank-you"
  | "generate-survey";

export interface AssistMeta {
  action: AssistAction;
  label: string;
  /** Short verb-phrase for the button hint. */
  hint: string;
  /** Generators need no existing copy; transforms operate on the current draft. */
  kind: "generate" | "transform" | "subjects";
}

/** The assistant's actions, in the order the panel lists them. */
export const ASSIST_ACTIONS: AssistMeta[] = [
  { action: "generate-invitation", label: "Generate invitation", hint: "Draft a full invite", kind: "generate" },
  { action: "rewrite-professional", label: "Rewrite professionally", hint: "More formal tone", kind: "transform" },
  { action: "rewrite-friendly", label: "Rewrite friendly", hint: "Warmer, more personal", kind: "transform" },
  { action: "shorten", label: "Shorten", hint: "Tighten the copy", kind: "transform" },
  { action: "expand", label: "Expand", hint: "Add detail and warmth", kind: "transform" },
  { action: "subject-lines", label: "Generate subject lines", hint: "Five options", kind: "subjects" },
  { action: "generate-follow-up", label: "Generate follow-up", hint: "Nudge non-responders", kind: "generate" },
  { action: "generate-thank-you", label: "Generate thank-you", hint: "Post-event note", kind: "generate" },
  { action: "generate-survey", label: "Generate survey", hint: "Feedback request", kind: "generate" },
];

export interface AssistContext {
  event: CueEvent;
  stage: CampaignStage;
  /** The current draft subject, for transforms. */
  subject: string;
  /** The current draft message, for transforms. */
  message: string;
  /** Human audience label, for personalizing generated copy. */
  audienceLabel: string;
}

/**
 * The assistant's reply. `subject`/`message` are set by generators and
 * transforms; `subjects` is set only by the subject-lines action. The UI applies
 * whichever fields are present.
 */
export interface AssistResult {
  subject?: string;
  message?: string;
  subjects?: string[];
  /** One-line summary of what the assistant did, shown above the result. */
  note: string;
}

function eventName(event: CueEvent): string {
  return event.title.trim() || "our event";
}

function whenClause(event: CueEvent): string {
  return event.startsAt ? ` on ${formatEventDate(event.startsAt)}` : "";
}

function whereClause(event: CueEvent): string {
  const parts = [event.venue?.trim(), event.city?.trim()].filter(Boolean);
  return parts.length ? ` at ${parts.join(", ")}` : "";
}

function kindLabel(event: CueEvent): string {
  return classificationLabel(event.classification, event.customClassification).toLowerCase();
}

// --- Generators -------------------------------------------------------------

function generateInvitation(ctx: AssistContext): AssistResult {
  const name = eventName(ctx.event);
  return {
    subject: `You're invited: ${name}`,
    message:
      `Hi {{first_name}},\n\n` +
      `We'd love for you to join us for ${name}, a ${kindLabel(ctx.event)} gathering${whenClause(ctx.event)}${whereClause(ctx.event)}.\n\n` +
      `It's a curated room built for real conversation and genuine connection — the kind of evening worth holding the calendar for. Space is limited, so we're reaching out to the people we most want in the room.\n\n` +
      `Will you join us? Tap below to RSVP.\n\n` +
      `Warmly,\n${ctx.event.host}`,
    note: "Drafted a full invitation from the event details.",
  };
}

function generateFollowUp(ctx: AssistContext): AssistResult {
  const name = eventName(ctx.event);
  return {
    subject: `A quick nudge on ${name}`,
    message:
      `Hi {{first_name}},\n\n` +
      `We haven't heard back yet and wanted to make sure this didn't slip past you — we're holding a spot for you at ${name}${whenClause(ctx.event)}.\n\n` +
      `The list is filling up, so a quick yes or no helps us plan the room. It would be great to have you there.\n\n` +
      `RSVP here whenever you have a moment.\n\n` +
      `Thanks,\n${ctx.event.host}`,
    note: "Drafted a follow-up for guests who haven't responded.",
  };
}

function generateThankYou(ctx: AssistContext): AssistResult {
  const name = eventName(ctx.event);
  return {
    subject: `Thank you for joining ${name}`,
    message:
      `Hi {{first_name}},\n\n` +
      `Thank you for coming to ${name} — it was a better evening for having you in the room. ` +
      `The conversations were exactly the kind we hoped for, and a lot of that was down to who showed up.\n\n` +
      `We'll share a short recap and any materials shortly. In the meantime, if there's someone you'd like an introduction to, just reply — we're happy to help.\n\n` +
      `Until next time,\n${ctx.event.host}`,
    note: "Drafted a warm post-event thank-you.",
  };
}

function generateSurvey(ctx: AssistContext): AssistResult {
  const name = eventName(ctx.event);
  return {
    subject: `Two minutes on ${name}?`,
    message:
      `Hi {{first_name}},\n\n` +
      `We're always trying to make these better, and your read on ${name} would genuinely help.\n\n` +
      `If you have two minutes, we'd love to know:\n` +
      `  1. How would you rate the event overall (1–5)?\n` +
      `  2. What was the most valuable part for you?\n` +
      `  3. What's one thing we should change next time?\n` +
      `  4. Anyone you'd like us to introduce you to?\n\n` +
      `Tap below to share your feedback — thank you.\n\n` +
      `${ctx.event.host}`,
    note: "Drafted a short post-event feedback survey.",
  };
}

// --- Transforms -------------------------------------------------------------

/** Fallback copy when the user runs a transform on an empty draft. */
function seedDraft(ctx: AssistContext): string {
  return (
    `You're invited to ${eventName(ctx.event)}${whenClause(ctx.event)}${whereClause(ctx.event)}. ` +
    `We'd love for you to join us.`
  );
}

function rewriteProfessional(ctx: AssistContext): AssistResult {
  const body = ctx.message.trim() || seedDraft(ctx);
  const cleaned = body
    .replace(/!+/g, ".")
    .replace(/\bwanna\b/gi, "would like to")
    .replace(/\bgonna\b/gi, "going to")
    .replace(/\bhey\b/gi, "Hello")
    .replace(/\bthanks\b/gi, "Thank you");
  return {
    message:
      `Dear {{first_name}},\n\n` +
      `${cleaned}\n\n` +
      `We would be delighted to have you join us and hope you are able to attend.\n\n` +
      `Kind regards,\n${ctx.event.host}`,
    note: "Rewrote the message in a more formal, professional register.",
  };
}

function rewriteFriendly(ctx: AssistContext): AssistResult {
  const body = ctx.message.trim() || seedDraft(ctx);
  const cleaned = body
    .replace(/\bDear\b/gi, "Hi")
    .replace(/\bKind regards\b/gi, "Cheers")
    .replace(/\bWe would be delighted\b/gi, "We'd love");
  return {
    message:
      `Hey {{first_name}},\n\n` +
      `${cleaned}\n\n` +
      `Would genuinely love to see you there — it won't be the same without you. 🙌\n\n` +
      `Talk soon,\n${ctx.event.host.split(" ")[0]}`,
    note: "Warmed up the tone to something friendlier and more personal.",
  };
}

function shorten(ctx: AssistContext): AssistResult {
  const body = ctx.message.trim() || seedDraft(ctx);
  // Keep the first two sentences, drop the rest — a believable "tighten".
  const sentences = body.replace(/\n+/g, " ").split(/(?<=[.!?])\s+/).filter(Boolean);
  const kept = sentences.slice(0, 2).join(" ");
  return {
    message: `${kept}\n\nRSVP below — hope to see you there.`,
    note: `Tightened the copy from ${sentences.length} sentences to the essentials.`,
  };
}

function expand(ctx: AssistContext): AssistResult {
  const body = ctx.message.trim() || seedDraft(ctx);
  return {
    message:
      `${body}\n\n` +
      `A little more on what to expect: we keep these intentionally small so the ` +
      `conversation stays real, with time to actually meet the people around you rather ` +
      `than work a room. Come as you are, bring what's on your mind, and leave with a few ` +
      `connections worth keeping.\n\n` +
      `If you have any questions before then, just reply to this note — we read every one.`,
    note: "Expanded the draft with detail and a warmer close.",
  };
}

function subjectLines(ctx: AssistContext): AssistResult {
  const name = eventName(ctx.event);
  const first = name.split(/\s+/)[0];
  const perStage: Record<CampaignStage, string[]> = {
    "save-the-date": [
      `Save the date: ${name}`,
      `Hold the date — ${name} is coming`,
      `Before your calendar fills up: ${name}`,
      `Mark your calendar for ${name}`,
      `A date worth holding — ${name}`,
    ],
    invitations: [
      `You're invited: ${name}`,
      `An invitation to ${name}`,
      `We'd love to see you at ${name}`,
      `A seat for you at ${name}`,
      `Join us for ${name}`,
    ],
    "rsvp-reminders": [
      `Still hoping to see you at ${name}`,
      `A quick nudge on ${name}`,
      `Have you had a chance to RSVP?`,
      `We're holding your spot for ${name}`,
      `Last call to RSVP for ${name}`,
    ],
    "vip-outreach": [
      `A personal invite to ${name}`,
      `We'd really like you in the room, ${first}`,
      `Reserving a seat for you at ${name}`,
      `Would love to have you at ${name}`,
      `A note about ${name}`,
    ],
    "event-updates": [
      `An update on ${name}`,
      `What's new for ${name}`,
      `A few details ahead of ${name}`,
      `${name}: the latest`,
      `Quick update before ${name}`,
    ],
    "day-before": [
      `See you tomorrow at ${name}`,
      `${name} is tomorrow — the details`,
      `Everything you need for ${name} tomorrow`,
      `Tomorrow: ${name}`,
      `Final details for ${name}`,
    ],
    "morning-of": [
      `Today's the day — ${name}`,
      `See you tonight at ${name}`,
      `${name} is today — here's where to go`,
      `A few notes for ${name} today`,
      `Looking forward to tonight, ${first}`,
    ],
    "thank-you": [
      `Thank you for joining ${name}`,
      `It was great to see you at ${name}`,
      `Thanks for being part of ${name}`,
      `A note of thanks — ${name}`,
      `That was special — thank you`,
    ],
    survey: [
      `Two minutes on ${name}?`,
      `How was ${name} for you?`,
      `Your take on ${name}`,
      `Help us make the next one better`,
      `A quick favor after ${name}`,
    ],
  };
  return {
    subjects: perStage[ctx.stage],
    note: "Generated five subject-line options for this stage.",
  };
}

/** Compute the assistant's reply synchronously (pure — the tested seam). */
export function assist(action: AssistAction, ctx: AssistContext): AssistResult {
  switch (action) {
    case "generate-invitation":
      return generateInvitation(ctx);
    case "generate-follow-up":
      return generateFollowUp(ctx);
    case "generate-thank-you":
      return generateThankYou(ctx);
    case "generate-survey":
      return generateSurvey(ctx);
    case "rewrite-professional":
      return rewriteProfessional(ctx);
    case "rewrite-friendly":
      return rewriteFriendly(ctx);
    case "shorten":
      return shorten(ctx);
    case "expand":
      return expand(ctx);
    case "subject-lines":
      return subjectLines(ctx);
  }
}

/**
 * Async wrapper the UI calls, with a short delay so the "thinking" state reads as
 * real work. Kept separate from the pure `assist` so that stays synchronously
 * testable.
 */
export function runAssist(action: AssistAction, ctx: AssistContext): Promise<AssistResult> {
  return new Promise((resolve) => {
    setTimeout(() => resolve(assist(action, ctx)), 600);
  });
}
