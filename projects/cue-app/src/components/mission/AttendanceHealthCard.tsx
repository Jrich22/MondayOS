import type { AttendanceHealth } from "@/lib/mission";
import { cn } from "@/lib/cn";
import { UsersIcon, StarIcon, TrendingUpIcon } from "@/components/icons";

/**
 * Attendance health rolled up across every live event. A single "health"
 * read — how full the rooms are, VIP coverage, and momentum — so the organizer
 * knows if the live moment is tracking well without opening each event.
 */
export function AttendanceHealthCard({ health }: { health: AttendanceHealth }) {
  const vipPct =
    health.vipTotal === 0 ? 100 : Math.round((health.vipCheckedIn / health.vipTotal) * 100);
  const tone =
    health.pct >= 80 ? "text-status-live" : health.pct >= 50 ? "text-status-draft" : "text-ink";

  return (
    <section className="card p-5">
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-ink">Attendance health</p>
        <span className="text-[11px] text-ink-faint">
          {health.liveEventCount} live event{health.liveEventCount === 1 ? "" : "s"}
        </span>
      </div>

      <div className="mt-4 flex items-end gap-3">
        <p className={cn("text-4xl font-semibold tabular-nums", tone)}>{health.pct}%</p>
        <p className="pb-1.5 text-xs text-ink-muted">
          <span className="font-medium text-ink">{health.checkedIn}</span> of {health.expected}{" "}
          expected checked in
        </p>
      </div>

      <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-white/[0.06]">
        <div
          className="h-full rounded-full bg-gradient-to-r from-brand-500 to-status-live transition-[width] duration-500"
          style={{ width: `${Math.min(100, health.pct)}%` }}
          role="progressbar"
          aria-valuenow={Math.min(100, health.pct)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Overall attendance"
        />
      </div>

      <dl className="mt-4 grid grid-cols-3 gap-3 text-center">
        <Stat icon={<UsersIcon width={15} height={15} />} value={String(health.remaining)} label="To arrive" />
        <Stat icon={<StarIcon width={15} height={15} />} value={`${vipPct}%`} label="VIPs in" accent />
        <Stat
          icon={<TrendingUpIcon width={15} height={15} />}
          value={health.arrivalRate > 0 ? `${health.arrivalRate}/hr` : "—"}
          label="Pace"
        />
      </dl>
    </section>
  );
}

function Stat({
  icon,
  value,
  label,
  accent,
}: {
  icon: React.ReactNode;
  value: string;
  label: string;
  accent?: boolean;
}) {
  return (
    <div className="rounded-xl border border-line bg-canvas/40 py-2.5">
      <span className={cn("mx-auto flex justify-center", accent ? "text-brand-400" : "text-ink-faint")}>
        {icon}
      </span>
      <p className="mt-1 text-sm font-semibold tabular-nums text-ink">{value}</p>
      <p className="text-[10px] uppercase tracking-wide text-ink-faint">{label}</p>
    </div>
  );
}
