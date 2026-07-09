import type { CueEvent, EventClassification } from "./types";
import { classificationLabel } from "./classification";
import { formatEventDate } from "./format";

/**
 * AI assist for the create-event flow (TASK-0033).
 *
 * Product stance: Cue is an event-operations platform *first*; AI enhances the
 * workflow but never replaces it. So this is strictly optional and additive —
 * the user asks for a draft, reviews it, and chooses to insert it.
 *
 * In the MVP there is no live model wired into the client. These deterministic,
 * offline template generators stand in for a real model so the UX is complete
 * and testable now; when a provider is wired in later, only `runAssist` changes
 * and the callers/UI stay the same.
 */

export type AssistKind =
  | "description"
  | "agenda"
  | "invite"
  | "followup"
  | "summary"
  | "attendees";

export interface AssistInput {
  title: string;
  classification: EventClassification;
  customClassification?: string;
  venue?: string;
  city?: string;
  /** Optional operational context used by summary/follow-up drafts. */
  confirmedGuests?: number;
  invitedGuests?: number;
  dateLabel?: string;
}

/** Build assist input from a full event (used by the detail AI panel). */
export function assistInputFromEvent(event: CueEvent): AssistInput {
  return {
    title: event.title,
    classification: event.classification,
    customClassification: event.customClassification,
    venue: event.venue,
    city: event.city,
    confirmedGuests: event.confirmedGuests,
    invitedGuests: event.invitedGuests,
    dateLabel: formatEventDate(event.startsAt),
  };
}

const audienceFor: Record<EventClassification, string> = {
  portfolio: "portfolio founders and operators",
  internal: "the firm team",
  investor: "limited partners and co-investors",
  founder: "founders across the community",
  recruiting: "candidates and hiring teams",
  community: "the broader ecosystem",
  "demo-day": "investors, press, and the cohort",
  "board-meeting": "board members and observers",
  customer: "customers and go-to-market partners",
  custom: "your guests",
};

function eventName(input: AssistInput): string {
  return input.title.trim() || "this event";
}

function locationClause(input: AssistInput): string {
  const parts = [input.venue?.trim(), input.city?.trim()].filter(Boolean);
  return parts.length ? ` at ${parts.join(", ")}` : "";
}

/** A polished 2–3 sentence description draft. */
export function draftDescription(input: AssistInput): string {
  const name = eventName(input);
  const audience = audienceFor[input.classification];
  const kind = classificationLabel(input.classification, input.customClassification).toLowerCase();
  return (
    `${name} is a ${kind} gathering bringing together ${audience}${locationClause(input)}. ` +
    `Expect focused conversation, genuine connection, and a program designed to be worth the calendar hold. ` +
    `Come ready to trade notes, meet peers, and leave with something you can use.`
  );
}

/** A sensible starter agenda as ordered line items. */
export function suggestAgenda(input: AssistInput): string[] {
  const kind = input.classification;
  const base = [
    "Arrivals & welcome reception",
    "Opening remarks from the host",
  ];
  const middle =
    kind === "demo-day"
      ? ["Cohort presentations", "Investor Q&A", "Breakout meetings"]
      : kind === "board-meeting"
        ? ["Business review", "Key decisions & approvals", "Forward plan"]
        : kind === "recruiting"
          ? ["Company lightning intros", "Open networking with hiring teams", "1:1 candidate conversations"]
          : ["Fireside conversation", "Facilitated roundtables", "Open networking"];
  return [...base, ...middle, "Closing & next steps"];
}

/** Warm, on-brand invite copy. */
export function suggestInviteCopy(input: AssistInput): string {
  const name = eventName(input);
  const audience = audienceFor[input.classification];
  return (
    `You're invited to ${name}.\n\n` +
    `We're bringing together ${audience}${locationClause(input)} for an evening built around real conversation and connection. ` +
    `Space is limited and curated — we'd love for you to join us.\n\n` +
    `Hit RSVP below to save your spot.`
  );
}

/** A follow-up / thank-you email draft for after the event. */
export function draftFollowup(input: AssistInput): string {
  const name = eventName(input);
  return (
    `Subject: Thank you for joining ${name}\n\n` +
    `Thanks so much for coming to ${name}${locationClause(input)} — it was better for having you there. ` +
    `A few of the connections and conversations from the room are worth continuing, and we'd love to help.\n\n` +
    `We'll share the recap and any materials shortly. In the meantime, reply here if there's someone you'd like an introduction to.\n\n` +
    `Until next time,\nThe team`
  );
}

/** A crisp operational summary of the event's current state. */
export function eventSummary(input: AssistInput): string {
  const name = eventName(input);
  const kind = classificationLabel(input.classification, input.customClassification);
  const when = input.dateLabel ? ` on ${input.dateLabel}` : "";
  const rsvp =
    input.confirmedGuests != null && input.invitedGuests != null
      ? ` So far ${input.confirmedGuests} of ${input.invitedGuests} invited guests have confirmed.`
      : "";
  return (
    `${name} is a ${kind} event${when}${locationClause(input)}.${rsvp} ` +
    `The program is on track; next steps are to finalize the guest list, confirm the run-of-show, and send reminders to pending RSVPs.`
  );
}

/** Portfolio-aware suggestions for who to invite. */
export function suggestAttendees(input: AssistInput): string[] {
  const base = [
    "Founders from portfolio companies matched to the theme",
    "Operators in relevant functions (eng, GTM, finance)",
    "Co-investors and syndicate partners on shared deals",
    "High-signal prospects the team is tracking",
  ];
  if (input.classification === "recruiting") {
    return ["Senior candidates in active searches", "Hiring managers across the portfolio", ...base.slice(2)];
  }
  if (input.classification === "investor") {
    return ["Limited partners", "Prospective LPs", "Co-investors", "Portfolio CEOs presenting"];
  }
  return base;
}

/** Render the requested draft to a single string of text. */
export function assistText(kind: AssistKind, input: AssistInput): string {
  switch (kind) {
    case "description":
      return draftDescription(input);
    case "agenda":
      return suggestAgenda(input)
        .map((item, i) => `${i + 1}. ${item}`)
        .join("\n");
    case "invite":
      return suggestInviteCopy(input);
    case "followup":
      return draftFollowup(input);
    case "summary":
      return eventSummary(input);
    case "attendees":
      return suggestAttendees(input)
        .map((a) => `• ${a}`)
        .join("\n");
  }
}

/**
 * Async wrapper the UI calls, with a short delay so the "generating" state
 * reads as real work. Kept separate from the pure builders so those stay
 * synchronously testable.
 */
export function runAssist(kind: AssistKind, input: AssistInput): Promise<string> {
  return new Promise((resolve) => {
    setTimeout(() => resolve(assistText(kind, input)), 550);
  });
}
