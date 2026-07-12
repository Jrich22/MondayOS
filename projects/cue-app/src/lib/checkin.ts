import type { CueEvent, Guest, GuestRole } from "./types";
import { newGuest, withCheckIn } from "./guests-select";
import type { ScanStatus } from "./qr";

/**
 * Kiosk-flow logic for the Check-In surface (TASK-0045): the walk-in builder and
 * the presentation metadata for a scan outcome. Pure and store-free — the kiosk
 * component supplies id + clock and commits through the guest store, so a scan
 * and a walk-in flow through the exact same seam Roll Call uses.
 */

export interface WalkInInput {
  firstName: string;
  lastName: string;
  company?: string;
  jobTitle?: string;
  vip?: boolean;
  roles?: GuestRole[];
}

/**
 * Build a walk-in attendee captured at the door: a fresh Guest who — by
 * definition — never held a confirmed RSVP, with the invite path marked so a
 * credential is on file, tagged "walk-in" for later reporting, and already
 * checked in at `nowIso`. Its stable QR identity comes for free from the id (see
 * lib/qr), so "assign QR identity" needs no extra field. Pure: the caller
 * supplies the id and clock, then adds the record to the store.
 */
export function walkInGuest(
  event: CueEvent,
  input: WalkInInput,
  id: string,
  nowIso: string,
): Guest {
  const base = newGuest(event.id, id, nowIso);
  const captured: Guest = {
    ...base,
    identity: { firstName: input.firstName.trim(), lastName: input.lastName.trim() },
    professional: {
      company: input.company?.trim() || undefined,
      jobTitle: input.jobTitle?.trim() || undefined,
    },
    roles: input.roles ?? [],
    vip: input.vip ?? false,
    attendance: { ...base.attendance, rsvp: "invited" },
    communication: { ...base.communication, invitationSent: true },
    tags: ["walk-in"],
  };
  return withCheckIn(captured, true, nowIso);
}

/** Whether a walk-in draft has the minimum needed to create the attendee. */
export function canCreateWalkIn(input: WalkInInput): boolean {
  return input.firstName.trim() !== "" || input.lastName.trim() !== "";
}

// --- Scan outcome presentation ---------------------------------------------

export type ScanTone = "success" | "celebrate" | "warn" | "error";

export interface ScanMeta {
  tone: ScanTone;
  /** Headline shown large on the result card. */
  title: string;
}

/**
 * Map a scan status (and whether the attendee is a VIP) to the result card's
 * tone and headline. VIP readiness escalates to a celebration so the host gets a
 * cue to greet them. Kept here so the outcome copy lives in one auditable place.
 */
export function scanMeta(status: ScanStatus, vip: boolean): ScanMeta {
  switch (status) {
    case "ready":
      return vip
        ? { tone: "celebrate", title: "VIP arrival" }
        : { tone: "success", title: "Checked in" };
    case "already-in":
      return { tone: "warn", title: "Already checked in" };
    case "duplicate":
      return { tone: "warn", title: "Duplicate scan" };
    case "wrong-event":
      return { tone: "error", title: "Wrong event" };
    case "expired":
      return { tone: "error", title: "Badge expired" };
    case "invalid":
    default:
      return { tone: "error", title: "Invalid badge" };
  }
}

/** Whether a scan status should trigger the actual check-in mutation. */
export function shouldCheckIn(status: ScanStatus): boolean {
  return status === "ready";
}
