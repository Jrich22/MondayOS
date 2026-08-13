/**
 * Active requisitions board.
 *
 * One card per requisition: what it is, how ready it is, what is moving, and
 * the one action worth taking next. Cards rather than table rows because a req
 * carries three unrelated dimensions — authoring completeness, pipeline health,
 * sourcing activity — and columns force them into a false comparison.
 *
 * The primary action is chosen from state: a thin open req says "Start
 * sourcing", a live session says "Resume session", an unready draft says
 * "Finish setup". Showing all three on every card would make none of them read
 * as the next step.
 */
import type { FC } from "react";
import { Link } from "react-router-dom";
import type { ReqDashboardRow } from "@/lib/intel";
import { Card, EmptyState, ReqStatusBadge, cn } from "@/components/ui/Primitives";
import { relativeTime } from "@/lib/format";

const Metric: FC<{ label: string; value: string | number; tone?: string }> = ({
  label,
  value,
  tone,
}) => (
  <div>
    <dt className="text-[10px] uppercase tracking-wide text-ink-faint">{label}</dt>
    <dd className={cn("mt-0.5 text-lg font-semibold tabular-nums text-ink", tone)}>{value}</dd>
  </div>
);

function primaryAction(row: ReqDashboardRow): { label: string; href: string; strong: boolean } {
  const { req, activeSession, sourcingReady, live } = row;
  if (activeSession) {
    return { label: "Resume session", href: `/reqs/${req.id}/session`, strong: true };
  }
  if (req.status === "draft" || !sourcingReady) {
    return { label: "Finish setup", href: `/reqs/${req.id}/edit`, strong: !sourcingReady };
  }
  if (req.status === "open" && live < 3) {
    return { label: "Start sourcing", href: `/reqs/${req.id}/session`, strong: true };
  }
  return { label: "Open pipeline", href: `/reqs/${req.id}`, strong: false };
}

export const ReqBoard: FC<{ rows: ReqDashboardRow[] }> = ({ rows }) => {
  if (rows.length === 0) {
    return (
      <EmptyState
        title="No requisitions yet"
        body="A requisition is the unit of work — briefs, sessions and evaluations all hang off one."
      />
    );
  }

  return (
    <ul className="grid gap-3 lg:grid-cols-2">
      {rows.map((row) => {
        const action = primaryAction(row);
        return (
          <li key={row.req.id}>
            <Card className="h-full p-4 transition-colors hover:border-line-strong">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs text-ink-faint">{row.req.code}</span>
                    <ReqStatusBadge status={row.req.status} />
                    {row.activeSession && (
                      <span className="inline-flex items-center gap-1 rounded-full border border-brand-500/30 bg-brand-500/10 px-2 py-0.5 text-[10px] text-brand-200">
                        <span className="h-1.5 w-1.5 rounded-full bg-brand-400" aria-hidden />
                        {row.activeSession.status === "paused" ? "paused" : "sourcing"}
                      </span>
                    )}
                  </div>
                  <Link
                    to={`/reqs/${row.req.id}`}
                    className="mt-1 block truncate text-base font-medium text-ink hover:text-brand-200"
                  >
                    {row.req.title || "Untitled requisition"}
                  </Link>
                  <p className="truncate text-xs text-ink-faint">
                    {row.req.team} · {row.req.location} · {row.req.hiringManager || "no HM"}
                  </p>
                </div>
                <Link
                  to={action.href}
                  className={cn(
                    "shrink-0 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors",
                    action.strong
                      ? "bg-brand-600 text-white hover:bg-brand-500"
                      : "border border-line text-ink-muted hover:border-line-strong hover:text-ink",
                  )}
                >
                  {action.label}
                </Link>
              </div>

              <dl className="mt-4 grid grid-cols-4 gap-3">
                <Metric label="Live" value={row.live} />
                <Metric
                  label="Strong"
                  value={row.strong}
                  tone={row.strong > 0 ? "text-stage-advanced" : undefined}
                />
                <Metric label="Sessions" value={row.sessions} />
                <Metric
                  label="Capture"
                  value={row.captureRate === null ? "—" : `${row.captureRate}%`}
                />
              </dl>

              <div className="mt-3 flex items-center gap-2">
                <div
                  className="h-1 flex-1 overflow-hidden rounded-full bg-line"
                  role="progressbar"
                  aria-valuenow={row.completeness}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-label={`${row.req.code} setup ${row.completeness}% complete`}
                >
                  <div
                    className={cn(
                      "h-full rounded-full transition-all duration-500",
                      row.sourcingReady ? "bg-stage-advanced" : "bg-oversight",
                    )}
                    style={{ width: `${Math.max(row.completeness, 2)}%` }}
                  />
                </div>
                <span className="shrink-0 text-[11px] tabular-nums text-ink-faint">
                  {row.completeness}% {row.sourcingReady ? "ready" : "setup"}
                </span>
              </div>

              <p className="mt-2 text-[11px] text-ink-faint">
                Last activity {relativeTime(row.lastActivity)}
              </p>
            </Card>
          </li>
        );
      })}
    </ul>
  );
};
