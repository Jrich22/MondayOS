import { Link } from "react-router-dom";
import type { CueEvent } from "@/lib/types";
import { useGuests } from "@/lib/guests";
import { liveMetrics } from "@/lib/rollcall";
import { StatTile } from "@/components/detail/Panel";
import { Button } from "@/components/ui/Button";
import { CheckCircleIcon } from "@/components/icons";

/**
 * Roll Call — the launch pad into the full-screen Command Center (TASK-0040).
 *
 * The command center is a dedicated, full-viewport operational mode, so inside
 * the tabbed detail page this tab is a briefing + entry point: it reads the live
 * roster for an at-a-glance status and hands off to the real surface. Keeping the
 * heavy live surface on its own route is what lets it own the whole screen.
 */
export function RollCallTab({ event }: { event: CueEvent }) {
  const guests = useGuests(event.id);
  const m = liveMetrics(guests, event, Date.now());
  const isLive = event.status === "live";

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatTile label="Checked in" value={String(m.checkedIn)} accent="text-status-live" />
        <StatTile label="Remaining" value={String(m.remaining)} sub="expected to arrive" />
        <StatTile label="Attendance" value={`${m.attendancePct}%`} accent="text-brand-400" sub={`${m.checkedIn}/${m.expected}`} />
        <StatTile label="VIPs in" value={`${m.vipCheckedIn}/${m.vipTotal}`} accent="text-status-draft" />
      </div>

      <section className="card overflow-hidden">
        <div className="flex flex-col items-start gap-5 p-6 sm:flex-row sm:items-center sm:justify-between sm:p-8">
          <div className="flex items-start gap-4">
            <span className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-brand-sheen text-brand-400">
              <CheckCircleIcon width={24} height={24} />
            </span>
            <div>
              <h3 className="text-lg font-semibold text-ink">Roll Call Command Center</h3>
              <p className="mt-1 max-w-md text-sm text-ink-muted">
                The live control center for running the door: instant search, one-tap
                check-in, live attendance, arrival feeds, alerts, and an AI copilot — a
                focused full-screen mode built to run {m.total > 0 ? `all ${m.total}` : "every"} attendees
                without leaving it.
              </p>
            </div>
          </div>
          <Link to={`/events/${event.id}/rollcall`} className="shrink-0">
            <Button>
              {isLive ? "Enter Command Center" : "Open Command Center"}
            </Button>
          </Link>
        </div>
      </section>
    </div>
  );
}
