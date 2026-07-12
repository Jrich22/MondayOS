import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { CueEvent } from "@/lib/types";
import type { LiveMetrics } from "@/lib/rollcall";
import { ArrowLeftIcon, ScanIcon } from "@/components/icons";
import { cn } from "@/lib/cn";

/**
 * Live wall-clock time. Self-ticking each second in its own state so only this
 * node re-renders — the attendee roster never repaints just because the clock
 * advanced.
 */
function LiveClock() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <span className="tabular-nums">
      {new Date(now).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}
    </span>
  );
}

/**
 * The command center's own top bar (TASK-0040): event identity + LIVE badge on
 * the left, the headline operational numbers across the right. This replaces the
 * app chrome entirely — the operator is in a focused, full-screen mode.
 */
export function CommandBar({
  event,
  metrics,
}: {
  event: CueEvent;
  metrics: LiveMetrics;
}) {
  const isLive = event.status === "live";

  return (
    <header className="flex flex-wrap items-center gap-x-6 gap-y-3 border-b border-line bg-canvas-raised/80 px-4 py-3 backdrop-blur-xl sm:px-6">
      {/* Identity + exit */}
      <div className="flex min-w-0 items-center gap-3">
        <Link
          to={`/events/${event.id}`}
          aria-label="Exit command center"
          className="focus-ring grid h-9 w-9 shrink-0 place-items-center rounded-xl border border-line text-ink-muted transition-colors hover:text-ink"
        >
          <ArrowLeftIcon width={18} height={18} />
        </Link>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="truncate text-base font-semibold tracking-tight text-ink">
              {event.title}
            </h1>
            {isLive && (
              <span className="relative inline-flex shrink-0 items-center gap-1.5 rounded-full bg-status-live/12 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-status-live">
                <span className="relative flex h-1.5 w-1.5">
                  <span className="absolute inline-flex h-full w-full animate-pulse-ring rounded-full bg-status-live/60" />
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-status-live" />
                </span>
                Live
              </span>
            )}
          </div>
          <p className="truncate text-xs text-ink-faint">
            {event.venue} · Roll Call Command Center
          </p>
        </div>
      </div>

      {/* Headline metrics */}
      <div className="ml-auto flex items-center gap-x-5 gap-y-2 overflow-x-auto sm:gap-x-7">
        <BarStat label="Attendance" value={`${metrics.attendancePct}%`} accent="text-brand-400" primary />
        <BarStat label="Checked in" value={metrics.checkedIn} accent="text-status-live" />
        <BarStat label="Remaining" value={metrics.remaining} />
        <BarStat label="Walk-ins" value={metrics.walkIns} accent="text-status-draft" />
        <Link
          to={`/events/${event.id}/checkin`}
          className="focus-ring hidden shrink-0 items-center gap-1.5 rounded-xl border border-line-strong px-3 py-2 text-xs font-medium text-ink-muted hover:text-ink sm:inline-flex"
        >
          <ScanIcon width={15} height={15} />
          Check-in kiosk
        </Link>
        <div className="hidden items-center gap-2 border-l border-line pl-5 text-sm font-medium text-ink-muted sm:flex">
          <LiveClock />
        </div>
      </div>
    </header>
  );
}

function BarStat({
  label,
  value,
  accent,
  primary,
}: {
  label: string;
  value: string | number;
  accent?: string;
  primary?: boolean;
}) {
  return (
    <div className="shrink-0 text-right sm:text-left">
      <p className="text-[10px] font-medium uppercase tracking-wide text-ink-faint">{label}</p>
      <p
        className={cn(
          "tabular-nums font-semibold leading-tight",
          primary ? "text-xl" : "text-lg",
          accent ?? "text-ink",
        )}
      >
        {value}
      </p>
    </div>
  );
}
