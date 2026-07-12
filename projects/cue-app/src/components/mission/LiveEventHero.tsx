import { Link } from "react-router-dom";
import type { CueEvent, Guest } from "@/lib/types";
import { liveMetrics } from "@/lib/rollcall";
import { rollCallPath, isFlagship } from "@/lib/mission";
import { formatTimeRange } from "@/lib/format";
import { classificationLabel } from "@/lib/classification";
import { Button } from "@/components/ui/Button";
import {
  MapPinIcon,
  UsersIcon,
  StarIcon,
  TrendingUpIcon,
  FlagIcon,
  ArrowRightIcon,
} from "@/components/icons";

/**
 * The Live Event hero — Mission Control's centerpiece. It reads the live roster
 * (via the same `liveMetrics` the Roll Call Command Center uses) so the numbers
 * are the room right now, and its primary action drops the organizer straight
 * into Roll Call for this event (task requirement: link the live card to the
 * command center).
 */
export function LiveEventHero({
  event,
  guests,
  now,
}: {
  event: CueEvent;
  guests: Guest[];
  now: number;
}) {
  const m = liveMetrics(guests, event, now);
  const flagship = isFlagship(event);
  const location = [event.venue, event.city].filter(Boolean).join(" · ");
  const capacityPct = m.capacityPct ?? m.attendancePct;

  return (
    <article className="card relative overflow-hidden p-6 sm:p-7">
      {/* Ambient live glow */}
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-br from-status-live/[0.08] via-transparent to-brand-500/[0.06]" />
      <div className="pointer-events-none absolute -right-16 -top-16 h-56 w-56 rounded-full bg-status-live/10 blur-3xl" />

      <div className="relative">
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-status-live/10 px-2.5 py-1 text-xs font-semibold text-status-live ring-1 ring-status-live/30">
            <span className="h-1.5 w-1.5 animate-pulse-ring rounded-full bg-status-live" />
            Live now
          </span>
          {flagship && (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-brand-500/10 px-2.5 py-1 text-xs font-semibold text-brand-200 ring-1 ring-brand-500/25">
              <FlagIcon width={13} height={13} />
              Flagship
            </span>
          )}
          <span className="text-xs font-medium text-ink-faint">
            {classificationLabel(event.classification, event.customClassification)}
          </span>
        </div>

        <h2 className="mt-4 text-2xl font-semibold tracking-tight text-ink sm:text-[1.75rem]">
          {event.title}
        </h2>
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-ink-muted">
          <span className="inline-flex items-center gap-1.5">
            <MapPinIcon width={16} height={16} className="text-ink-faint" />
            {location || "Location TBD"}
          </span>
          <span className="inline-flex items-center gap-1.5">
            {formatTimeRange(event.startsAt, event.endsAt)}
          </span>
        </div>

        {/* Live metric strip */}
        <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Metric
            label="Checked in"
            value={String(m.checkedIn)}
            sub={`of ${m.expected} expected`}
            icon={<UsersIcon width={16} height={16} />}
          />
          <Metric
            label="VIPs in room"
            value={`${m.vipCheckedIn}/${m.vipTotal}`}
            sub={m.vipExpected > 0 ? `${m.vipExpected} expected` : "all here"}
            icon={<StarIcon width={16} height={16} />}
            accent
          />
          <Metric
            label="Arrival pace"
            value={m.arrivalRate > 0 ? `${m.arrivalRate}` : "—"}
            sub={m.arrivalRate > 0 ? "per hour" : "no arrivals yet"}
            icon={<TrendingUpIcon width={16} height={16} />}
          />
          <Metric
            label={m.capacity !== null ? "Capacity" : "Attendance"}
            value={`${capacityPct}%`}
            sub={m.capacity !== null ? `${m.checkedIn} of ${m.capacity}` : `${m.attendancePct}% of expected`}
            icon={<UsersIcon width={16} height={16} />}
          />
        </div>

        {/* Fill bar */}
        <div className="mt-5">
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/[0.06]">
            <div
              className="h-full rounded-full bg-gradient-to-r from-brand-500 to-status-live transition-[width] duration-500"
              style={{ width: `${Math.min(100, capacityPct)}%` }}
              role="progressbar"
              aria-valuenow={Math.min(100, capacityPct)}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={`${m.checkedIn} checked in`}
            />
          </div>
        </div>

        <div className="mt-6 flex flex-wrap items-center gap-3">
          <Link to={rollCallPath(event)}>
            <Button>
              Open Roll Call
              <ArrowRightIcon width={16} height={16} />
            </Button>
          </Link>
          <Link to={`/events/${event.id}`}>
            <Button variant="outline">Event details</Button>
          </Link>
          {m.remaining > 0 && (
            <span className="text-sm text-ink-muted">
              <span className="font-medium text-ink">{m.remaining}</span> still to arrive
            </span>
          )}
        </div>
      </div>
    </article>
  );
}

function Metric({
  label,
  value,
  sub,
  icon,
  accent,
}: {
  label: string;
  value: string;
  sub: string;
  icon: React.ReactNode;
  accent?: boolean;
}) {
  return (
    <div className="rounded-xl border border-line bg-canvas/40 px-3.5 py-3">
      <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-faint">
        <span className={accent ? "text-brand-400" : "text-ink-faint"}>{icon}</span>
        {label}
      </div>
      <p className="mt-1.5 text-xl font-semibold tabular-nums text-ink">{value}</p>
      <p className="text-[11px] text-ink-muted">{sub}</p>
    </div>
  );
}
