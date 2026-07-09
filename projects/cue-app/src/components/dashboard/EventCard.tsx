import { Link } from "react-router-dom";
import type { CueEvent } from "@/lib/types";
import { formatEventDate, relativeDay, fillRatio, isUncapped } from "@/lib/format";
import { classificationLabel } from "@/lib/classification";
import { StatusBadge } from "./StatusBadge";
import { Badge } from "@/components/ui/Badge";
import { CalendarIcon, MapPinIcon, UsersIcon } from "@/components/icons";

/**
 * One event, rendered as a rich card: status, title, when/where, the portfolio
 * companies it serves, and a confirmed-guest fill bar. This is the primary unit
 * of the dashboard — everything a host needs to triage an event at a glance.
 */
export function EventCard({ event }: { event: CueEvent }) {
  const uncapped = isUncapped(event);
  const pct = Math.round(fillRatio(event) * 100);
  const locationParts = [event.venue.trim(), event.city.trim()].filter(Boolean);

  return (
    <article className="card group relative flex flex-col p-5 transition-all duration-200 hover:-translate-y-0.5 hover:border-line-strong hover:shadow-glow focus-within:border-line-strong">
      <header className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <StatusBadge status={event.status} />
          <Badge>{classificationLabel(event.classification, event.customClassification)}</Badge>
        </div>
        <span className="text-xs font-medium text-ink-faint">
          {relativeDay(event.startsAt)}
        </span>
      </header>

      <h3 className="mt-3 text-lg font-semibold leading-snug text-ink">
        <Link to={`/events/${event.id}`} className="focus-ring rounded outline-none">
          <span className="absolute inset-0" aria-hidden />
          {event.title}
        </Link>
      </h3>
      <p className="mt-1 line-clamp-2 text-sm text-ink-muted">{event.summary}</p>

      <dl className="mt-4 space-y-1.5 text-sm text-ink-muted">
        <div className="flex items-center gap-2">
          <CalendarIcon width={16} height={16} className="text-ink-faint" />
          <dd>{formatEventDate(event.startsAt)}</dd>
        </div>
        <div className="flex items-center gap-2">
          <MapPinIcon width={16} height={16} className="text-ink-faint" />
          <dd>{locationParts.length ? locationParts.join(" · ") : "Location TBD"}</dd>
        </div>
      </dl>

      {event.portfolio.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-1.5">
          {event.portfolio.slice(0, 3).map((c) => (
            <Badge key={c.id} className="border-brand-500/25 bg-brand-500/5 text-brand-200">
              {c.name}
            </Badge>
          ))}
          {event.portfolio.length > 3 && (
            <Badge>+{event.portfolio.length - 3}</Badge>
          )}
        </div>
      )}

      <div className="mt-auto pt-5">
        <div className="flex items-center justify-between text-xs text-ink-muted">
          <span className="inline-flex items-center gap-1.5">
            <UsersIcon width={14} height={14} className="text-ink-faint" />
            <span className="font-medium text-ink">{event.confirmedGuests}</span>
            {uncapped
              ? "confirmed · no cap"
              : `confirmed of ${event.capacity.maxAttendees}`}
          </span>
          {!uncapped && (
            <span className="tabular-nums font-medium text-ink">{pct}%</span>
          )}
        </div>
        {!uncapped && (
          <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-white/[0.06]">
            <div
              className="h-full rounded-full bg-gradient-to-r from-brand-500 to-status-live transition-[width] duration-500"
              style={{ width: `${pct}%` }}
              role="progressbar"
              aria-valuenow={pct}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={`${event.confirmedGuests} of ${event.capacity.maxAttendees} confirmed`}
            />
          </div>
        )}
      </div>
    </article>
  );
}
