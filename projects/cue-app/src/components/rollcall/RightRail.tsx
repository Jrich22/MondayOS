import type { CueEvent, Guest } from "@/lib/types";
import {
  liveMetrics,
  recentArrivals,
  vipArrivals,
  rollCallAlerts,
  arrivalTime,
  arrivalAgo,
  type LiveMetrics,
  type AlertTone,
} from "@/lib/rollcall";
import { displayName, guestCompany, initials } from "@/lib/guests-select";
import { CopilotPanel } from "./CopilotPanel";
import { BoltIcon, BellIcon, StarIcon, ClockIcon } from "@/components/icons";
import { cn } from "@/lib/cn";

/**
 * The command center's right panel: the live board (metrics), the arrival feeds
 * (recent + VIP), operational alerts, and the AI copilot — everything the ops
 * team, host, and execs watch while the door runs. All values derive from the
 * live roster via lib/rollcall, so this panel is pure presentation.
 */
export function RightRail({
  event,
  guests,
  now,
}: {
  event: CueEvent;
  guests: Guest[];
  now: number;
}) {
  const metrics = liveMetrics(guests, event, now);
  const recent = recentArrivals(guests, 6);
  const vips = vipArrivals(guests, 6);
  const alerts = rollCallAlerts(guests, event, now);

  return (
    <div className="space-y-4">
      <LiveMetricsBoard metrics={metrics} />
      {alerts.length > 0 && <AlertsCard alerts={alerts} />}
      <ArrivalsCard title="Recent arrivals" icon={<ClockIcon width={15} height={15} />} guests={recent} event={event} now={now} emptyLabel="No arrivals yet" />
      <ArrivalsCard title="VIP arrivals" icon={<StarIcon width={15} height={15} />} guests={vips} event={event} now={now} accent emptyLabel="No VIPs in the room yet" />
      <CopilotPanel event={event} guests={guests} now={now} />
    </div>
  );
}

// --- Live metrics -----------------------------------------------------------

function LiveMetricsBoard({ metrics: m }: { metrics: LiveMetrics }) {
  return (
    <section className="card p-4">
      <div className="mb-3 flex items-center gap-2">
        <span className="grid h-6 w-6 place-items-center rounded-md bg-status-live/12 text-status-live">
          <BoltIcon width={14} height={14} />
        </span>
        <h2 className="text-sm font-semibold text-ink">Live metrics</h2>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Metric label="Checked in" value={m.checkedIn} accent="text-status-live" />
        <Metric label="Remaining" value={m.remaining} />
        <Metric label="Attendance" value={`${m.attendancePct}%`} accent="text-brand-400" />
        <Metric
          label="Capacity"
          value={m.capacity === null ? "∞" : m.capacity}
          sub={m.capacityPct !== null ? `${m.capacityPct}% full` : "uncapped"}
        />
        <Metric label="Walk-ins" value={m.walkIns} accent="text-status-draft" />
        <Metric label="No-shows" value={m.noShows} />
        <Metric label="VIPs in" value={`${m.vipCheckedIn}/${m.vipTotal}`} accent="text-status-draft" />
        <Metric label="Arrival rate" value={m.arrivalRate > 0 ? `${m.arrivalRate}` : "—"} sub="per hour" />
      </div>
    </section>
  );
}

function Metric({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: string | number;
  sub?: string;
  accent?: string;
}) {
  return (
    <div className="rounded-xl border border-line bg-white/[0.02] px-3 py-2.5">
      <p className="text-[10px] font-medium uppercase tracking-wide text-ink-faint">{label}</p>
      <p className={cn("mt-0.5 text-xl font-semibold tabular-nums leading-none", accent ?? "text-ink")}>
        {value}
      </p>
      {sub && <p className="mt-1 text-[10px] text-ink-faint">{sub}</p>}
    </div>
  );
}

// --- Arrival feeds ----------------------------------------------------------

function ArrivalsCard({
  title,
  icon,
  guests,
  event,
  now,
  accent,
  emptyLabel,
}: {
  title: string;
  icon: React.ReactNode;
  guests: Guest[];
  event: CueEvent;
  now: number;
  accent?: boolean;
  emptyLabel: string;
}) {
  return (
    <section className="card p-4">
      <div className="mb-3 flex items-center gap-2">
        <span
          className={cn(
            "grid h-6 w-6 place-items-center rounded-md",
            accent ? "bg-status-draft/12 text-status-draft" : "bg-white/[0.05] text-ink-muted",
          )}
        >
          {icon}
        </span>
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
      </div>
      {guests.length === 0 ? (
        <p className="py-2 text-xs text-ink-faint">{emptyLabel}</p>
      ) : (
        <ul className="space-y-1.5">
          {guests.map((g) => (
            <li key={g.id} className="flex items-center gap-2.5">
              <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-white/[0.06] text-[11px] font-semibold text-ink">
                {initials(g)}
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5">
                  <span className="truncate text-sm font-medium text-ink">{displayName(g)}</span>
                  {g.vip && !accent && <StarIcon width={11} height={11} className="shrink-0 text-status-draft" />}
                </span>
                <span className="block truncate text-[11px] text-ink-faint">
                  {guestCompany(g, event.portfolio) || "—"}
                </span>
              </span>
              <span className="shrink-0 text-right text-[11px] tabular-nums text-ink-muted">
                <span className="block">{arrivalTime(g)}</span>
                <span className="block text-ink-faint">{arrivalAgo(g, now)}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// --- Alerts -----------------------------------------------------------------

const ALERT_STYLE: Record<AlertTone, { wrap: string; dot: string }> = {
  greet: { wrap: "border-status-live/25 bg-status-live/[0.06]", dot: "bg-status-live" },
  warn: { wrap: "border-status-draft/25 bg-status-draft/[0.06]", dot: "bg-status-draft" },
  info: { wrap: "border-line bg-white/[0.02]", dot: "bg-ink-faint" },
};

function AlertsCard({ alerts }: { alerts: ReturnType<typeof rollCallAlerts> }) {
  return (
    <section className="card p-4">
      <div className="mb-3 flex items-center gap-2">
        <span className="grid h-6 w-6 place-items-center rounded-md bg-white/[0.05] text-ink-muted">
          <BellIcon width={15} height={15} />
        </span>
        <h2 className="text-sm font-semibold text-ink">Alerts</h2>
      </div>
      <ul className="space-y-2">
        {alerts.map((a) => {
          const s = ALERT_STYLE[a.tone];
          return (
            <li key={a.id} className={cn("flex gap-2.5 rounded-xl border px-3 py-2", s.wrap)}>
              <span className={cn("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", s.dot)} />
              <span className="min-w-0">
                <span className="block text-sm font-medium text-ink">{a.title}</span>
                <span className="block text-[11px] text-ink-muted">{a.detail}</span>
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
