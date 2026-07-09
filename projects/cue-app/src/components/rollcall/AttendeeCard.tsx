import { memo } from "react";
import type { CueEvent, Guest } from "@/lib/types";
import {
  displayName,
  initials,
  guestCompany,
  RSVP_META,
  ROLE_META,
} from "@/lib/guests-select";
import { qrReady, arrivalTime } from "@/lib/rollcall";
import { StarIcon, CheckIcon, QrIcon } from "@/components/icons";
import { cn } from "@/lib/cn";

/**
 * The attendee card — the atomic unit of the command center's left panel. It
 * surfaces everything the operator needs at a glance (name, company, title, org
 * role, RSVP, VIP, portfolio tie, QR-ready, checked-in state) and the WHOLE card
 * is the check-in target: one click toggles check-in, no modal, no confirmation.
 * That single-action design is what makes 500 check-ins without leaving the
 * screen possible.
 *
 * Memoized because the roster can hold hundreds of these; only the toggled card
 * re-renders on a check-in.
 */
export const AttendeeCard = memo(function AttendeeCard({
  guest,
  event,
  onCheckIn,
}: {
  guest: Guest;
  event: CueEvent;
  onCheckIn: (g: Guest) => void;
}) {
  const checkedIn = guest.attendance.checkedIn;
  const rsvp = RSVP_META[guest.attendance.rsvp];
  const company = guestCompany(guest, event.portfolio);
  const isPortfolio = Boolean(
    guest.professional.portfolioCompanyId &&
      event.portfolio.some((c) => c.id === guest.professional.portfolioCompanyId),
  );
  const ready = qrReady(guest);
  const title = guest.professional.jobTitle;

  return (
    <button
      type="button"
      onClick={() => onCheckIn(guest)}
      aria-pressed={checkedIn}
      aria-label={checkedIn ? `Undo check-in for ${displayName(guest)}` : `Check in ${displayName(guest)}`}
      className={cn(
        "focus-ring group flex w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition-colors",
        checkedIn
          ? "border-status-live/25 bg-status-live/[0.06]"
          : "border-line bg-canvas-raised hover:border-line-strong hover:bg-white/[0.03] active:bg-white/[0.05]",
      )}
    >
      {/* Avatar */}
      <span
        className={cn(
          "grid h-11 w-11 shrink-0 place-items-center rounded-full text-sm font-semibold transition-colors",
          checkedIn
            ? "bg-status-live/15 text-status-live ring-2 ring-status-live/40"
            : "bg-white/[0.06] text-ink",
        )}
      >
        {initials(guest)}
      </span>

      {/* Identity + meta */}
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <span className="truncate font-semibold text-ink">{displayName(guest)}</span>
          {guest.vip && (
            <span className="inline-flex shrink-0 items-center gap-1 rounded-md bg-status-draft/15 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-status-draft">
              <StarIcon width={10} height={10} />
              VIP
            </span>
          )}
          {isPortfolio && (
            <span className="hidden shrink-0 rounded-md bg-brand-500/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-brand-300 sm:inline">
              Portfolio
            </span>
          )}
          {guest.attendance.plusOnes > 0 && (
            <span className="shrink-0 rounded bg-white/[0.06] px-1 text-[10px] font-semibold text-ink-muted">
              +{guest.attendance.plusOnes}
            </span>
          )}
        </span>
        <span className="mt-0.5 block truncate text-xs text-ink-muted">
          {company || "—"}
          {title ? <span className="text-ink-faint"> · {title}</span> : null}
        </span>
        <span className="mt-1 flex items-center gap-2 text-[11px]">
          <span className={cn("inline-flex items-center gap-1 font-medium", rsvp.tone)}>
            <span className={cn("h-1.5 w-1.5 rounded-full", rsvp.dot)} />
            {rsvp.label}
          </span>
          {guest.roles.slice(0, 1).map((r) => (
            <span key={r} className="text-ink-faint">
              {ROLE_META[r].label}
            </span>
          ))}
          <span
            className={cn("inline-flex items-center gap-0.5", ready ? "text-ink-faint" : "text-ink-faint/40")}
            title={ready ? "QR credential ready" : "No QR credential"}
          >
            <QrIcon width={12} height={12} />
          </span>
        </span>
      </span>

      {/* Check-in state — the action affordance */}
      <span className="flex shrink-0 flex-col items-end gap-0.5">
        <span
          className={cn(
            "grid h-9 w-9 place-items-center rounded-full border transition-colors",
            checkedIn
              ? "border-status-live bg-status-live text-canvas"
              : "border-line-strong text-transparent group-hover:border-brand-400 group-hover:text-brand-400",
          )}
        >
          <CheckIcon width={18} height={18} />
        </span>
        <span
          className={cn(
            "text-[10px] font-medium tabular-nums",
            checkedIn ? "text-status-live" : "text-ink-faint opacity-0 group-hover:opacity-100",
          )}
        >
          {checkedIn ? arrivalTime(guest) : "Check in"}
        </span>
      </span>
    </button>
  );
});
