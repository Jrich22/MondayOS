import { Link } from "react-router-dom";
import type { CueEvent } from "@/lib/types";
import { relativeDay, formatEventDate, fillRatio, isUncapped } from "@/lib/format";
import { ArrowRightIcon } from "@/components/icons";

/**
 * Upcoming events — a compact, scannable list beneath the live section. Each row
 * shows when it opens, where it is, and how the guest list is filling, and links
 * through to the event's detail page.
 */
export function UpcomingList({ events }: { events: CueEvent[] }) {
  return (
    <section className="card p-5">
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-ink">Upcoming events</p>
        <Link
          to="/events"
          className="focus-ring inline-flex items-center gap-1 rounded-md text-xs font-medium text-ink-muted hover:text-ink"
        >
          All events
          <ArrowRightIcon width={13} height={13} />
        </Link>
      </div>

      {events.length === 0 ? (
        <p className="mt-3 text-xs text-ink-muted">Nothing scheduled yet.</p>
      ) : (
        <ul className="mt-2 divide-y divide-line">
          {events.map((e) => {
            const uncapped = isUncapped(e);
            const pct = Math.round(fillRatio(e) * 100);
            const location = [e.venue, e.city].filter(Boolean).join(" · ");
            return (
              <li key={e.id}>
                <Link
                  to={`/events/${e.id}`}
                  className="focus-ring group flex items-center gap-4 rounded-lg px-1.5 py-3 transition-colors hover:bg-white/[0.03]"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-ink">{e.title}</p>
                    <p className="mt-0.5 truncate text-xs text-ink-faint">
                      {formatEventDate(e.startsAt)}
                      {location ? ` · ${location}` : ""}
                    </p>
                  </div>
                  <div className="hidden shrink-0 text-right sm:block">
                    <p className="text-xs font-medium text-ink">
                      {e.confirmedGuests}
                      {uncapped ? " confirmed" : `/${e.capacity.maxAttendees}`}
                    </p>
                    {!uncapped && (
                      <p className="text-[11px] text-ink-faint">{pct}% full</p>
                    )}
                  </div>
                  <span className="shrink-0 rounded-md bg-white/[0.04] px-2 py-0.5 text-[11px] font-medium text-ink-muted">
                    {relativeDay(e.startsAt)}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
