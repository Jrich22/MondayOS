import type { CueEvent, Guest, GuestRole } from "./types";
import { displayName, guestCompany, initials, ROLE_META } from "./guests-select";
import { themeByKey } from "./branding";
import { qrPayload } from "./qr";

/**
 * Badge model for the Check-In kiosk (TASK-0045). A badge is a *derivation*, not
 * a stored record: everything on it is projected from the Guest + Event through
 * the same display helpers the rest of the app uses (preferred name, resolved
 * company, portfolio-aware org color), plus the stable QR identity. Nothing new
 * is persisted, so a reprint is always current with the attendee record.
 *
 * The badge carries only presentational identity — name, company, title, role,
 * VIP, organization color, and the QR glyph. It never surfaces email, phone,
 * dietary, or notes: those are operator-facing CRM fields, not badge fields.
 */

export interface BadgeData {
  /** Preferred-name-aware display name — the large line on the badge. */
  name: string;
  initials: string;
  company: string;
  title?: string;
  vip: boolean;
  /** The single most defining role, for the badge role line. */
  role?: string;
  /** All role labels (compact badge shows the primary; large may show more). */
  roles: string[];
  /** Organization accent color (hex), from the event's brand theme. */
  color: string;
  /** Event title, shown as the badge footer. */
  eventTitle: string;
  /** The stable, PII-free QR identity payload for the glyph. */
  qr: string;
}

/** Which role leads on the badge when an attendee holds several. */
const ROLE_PRIORITY: GuestRole[] = ["founder", "investor", "sponsor", "press", "internal"];

/** Project a Guest + Event into the data a badge renders. Pure. */
export function buildBadge(guest: Guest, event: CueEvent): BadgeData {
  const primary = ROLE_PRIORITY.find((r) => guest.roles.includes(r));
  return {
    name: displayName(guest),
    initials: initials(guest),
    company: guestCompany(guest, event.portfolio),
    title: guest.professional.jobTitle,
    vip: guest.vip,
    role: primary ? ROLE_META[primary].label : undefined,
    roles: guest.roles.map((r) => ROLE_META[r].label),
    color: themeByKey(event.branding.theme).accent,
    eventTitle: event.title,
    qr: qrPayload(guest),
  };
}
