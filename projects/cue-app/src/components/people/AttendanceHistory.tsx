import { Link } from "react-router-dom";
import type { PersonAppearance } from "@/lib/people";
import { RSVP_META } from "@/lib/guests-select";
import { formatEventDate } from "@/lib/format";
import { cn } from "@/lib/cn";
import { CheckCircleIcon } from "@/components/icons";

/**
 * Attendance history — every event this person was on, most recent first, with
 * their RSVP and whether they actually showed. The factual backbone beneath the
 * timeline: one row per appearance, each linking to the event.
 */
export function AttendanceHistory({ appearances }: { appearances: PersonAppearance[] }) {
  const rows = [...appearances].sort((a, b) => Date.parse(b.startsAt) - Date.parse(a.startsAt));

  return (
    <ul className="divide-y divide-line">
      {rows.map((a) => {
        const rsvp = RSVP_META[a.rsvp];
        return (
          <li key={a.eventId}>
            <Link
              to={`/events/${a.eventId}`}
              className="focus-ring group flex items-center gap-3 py-3 transition-colors"
            >
              <span
                className={cn(
                  "grid h-8 w-8 shrink-0 place-items-center rounded-lg",
                  a.checkedIn ? "bg-status-live/10 text-status-live" : "bg-white/[0.04] text-ink-faint",
                )}
                title={a.checkedIn ? "Attended" : "Did not check in"}
              >
                <CheckCircleIcon width={16} height={16} />
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-ink group-hover:text-brand-300">
                  {a.eventTitle}
                </p>
                <p className="truncate text-[11px] text-ink-faint">
                  {formatEventDate(a.startsAt)}
                  {a.company ? ` · ${a.company}` : ""}
                </p>
              </div>
              <span className={cn("inline-flex shrink-0 items-center gap-1.5 text-xs font-medium", rsvp.tone)}>
                <span className={cn("h-1.5 w-1.5 rounded-full", rsvp.dot)} />
                {rsvp.label}
              </span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
