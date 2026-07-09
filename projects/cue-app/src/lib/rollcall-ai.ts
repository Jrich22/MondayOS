import type { CueEvent, Guest } from "./types";
import { displayName, guestCompany } from "./guests-select";
import {
  liveMetrics,
  notArrived,
  vipArrivals,
  isExpected,
  arrivalAgo,
} from "./rollcall";

/**
 * The Roll Call AI Copilot (TASK-0040) — offline templates only.
 *
 * Cue is an operations tool first: the copilot *understands the event* by
 * reading the live roster and answering deterministically, with no network and
 * no model wired in (the same stance as lib/ai for the create flow). Each answer
 * is computed from `Guest[]` + the clock, so it is always correct for the room
 * as it stands and is fully unit-testable. When a real model is wired in later,
 * only `answerCopilot` changes; the UI and the suggested prompts stay put.
 */

export type CopilotPromptId =
  | "not-arrived"
  | "remaining-vips"
  | "founders-here"
  | "greet-next"
  | "summary";

export interface CopilotPrompt {
  id: CopilotPromptId;
  label: string;
}

/** The suggested prompts, exactly as specified in the product requirements. */
export const COPILOT_PROMPTS: CopilotPrompt[] = [
  { id: "not-arrived", label: "Who hasn't arrived?" },
  { id: "remaining-vips", label: "Show remaining VIPs" },
  { id: "founders-here", label: "How many founders are here?" },
  { id: "greet-next", label: "Who should the host greet next?" },
  { id: "summary", label: "Summarize attendance" },
];

export interface CopilotContext {
  guests: Guest[];
  event: CueEvent;
  now: number;
}

/**
 * A structured answer: a one-line headline plus optional attendee references the
 * UI can render as tappable rows (so "who hasn't arrived" doubles as a worklist,
 * not just prose).
 */
export interface CopilotAnswer {
  headline: string;
  /** Guests the answer points at, in priority order (may be empty). */
  guests: Guest[];
  /** How many more guests exist beyond the ones listed. */
  overflow: number;
}

const MAX_NAMED = 6;

function withOverflow(headline: string, list: Guest[]): CopilotAnswer {
  return {
    headline,
    guests: list.slice(0, MAX_NAMED),
    overflow: Math.max(0, list.length - MAX_NAMED),
  };
}

/** Answer one of the suggested prompts from the live roster. */
export function answerCopilot(id: CopilotPromptId, ctx: CopilotContext): CopilotAnswer {
  const { guests, event, now } = ctx;

  switch (id) {
    case "not-arrived": {
      const pending = notArrived(guests);
      if (pending.length === 0) {
        return { headline: "Everyone expected is here — the room is complete.", guests: [], overflow: 0 };
      }
      const vipCount = pending.filter((g) => g.vip).length;
      const vipClause = vipCount > 0 ? ` (${vipCount} VIP${vipCount === 1 ? "" : "s"})` : "";
      return withOverflow(
        `${pending.length} confirmed guest${pending.length === 1 ? "" : "s"} haven't arrived yet${vipClause}.`,
        pending,
      );
    }

    case "remaining-vips": {
      const vips = notArrived(guests).filter((g) => g.vip);
      if (vips.length === 0) {
        return { headline: "All expected VIPs have checked in.", guests: [], overflow: 0 };
      }
      return withOverflow(
        `${vips.length} VIP${vips.length === 1 ? "" : "s"} still to arrive.`,
        vips,
      );
    }

    case "founders-here": {
      const founders = guests.filter(
        (g) => g.roles.includes("founder") && g.attendance.checkedIn,
      );
      const expectedFounders = guests.filter((g) => g.roles.includes("founder") && isExpected(g)).length;
      return withOverflow(
        `${founders.length} founder${founders.length === 1 ? "" : "s"} ${
          founders.length === 1 ? "is" : "are"
        } here${expectedFounders ? ` of ${expectedFounders} expected` : ""}.`,
        founders,
      );
    }

    case "greet-next": {
      const next = vipArrivals(guests, MAX_NAMED);
      if (next.length === 0) {
        return { headline: "No VIP arrivals yet — nobody waiting to be greeted.", guests: [], overflow: 0 };
      }
      const top = next[0];
      const co = guestCompany(top, event.portfolio);
      return {
        headline: `Greet ${displayName(top)}${co ? ` · ${co}` : ""} — arrived ${arrivalAgo(top, now)}.`,
        guests: next,
        overflow: 0,
      };
    }

    case "summary":
    default: {
      const m = liveMetrics(guests, event, now);
      const pace = m.arrivalRate > 0 ? ` Arrivals are running ~${m.arrivalRate}/hr.` : "";
      const walk = m.walkIns > 0 ? ` ${m.walkIns} walk-in${m.walkIns === 1 ? "" : "s"}.` : "";
      return {
        headline:
          `${m.checkedIn} of ${m.expected} expected checked in (${m.attendancePct}%). ` +
          `${m.remaining} still to arrive, ${m.vipCheckedIn}/${m.vipTotal} VIPs in the room.${pace}${walk}`,
        guests: vipArrivals(guests, 3),
        overflow: 0,
      };
    }
  }
}
