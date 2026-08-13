/**
 * Sourcing performance.
 *
 * A funnel and a ranked session list. No time-series chart: with a handful of
 * sessions a trend line is noise wearing the costume of insight, and the
 * honest question at this scale is "which searches worked?" — which a ranked
 * list answers directly and a sparkline does not.
 *
 * Strongest and weakest only appear when enough sessions have enough signal to
 * rank; below that the module says so rather than crowning a two-profile
 * session.
 */
import type { FC } from "react";
import { Link } from "react-router-dom";
import type { SourcingPerformance as Performance } from "@/lib/intel";
import { Card, cn } from "@/components/ui/Primitives";
import { relativeTime } from "@/lib/format";

const FunnelBar: FC<{ label: string; value: number; total: number; tone: string }> = ({
  label,
  value,
  total,
  tone,
}) => {
  const pct = total === 0 ? 0 : Math.round((value / total) * 100);
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs text-ink-muted">{label}</span>
        <span className="text-xs tabular-nums text-ink">
          {value}
          <span className="ml-1 text-ink-faint">{pct}%</span>
        </span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-line">
        <div
          className={cn("h-full rounded-full transition-all duration-500", tone)}
          style={{ width: `${Math.max(pct, value > 0 ? 3 : 0)}%` }}
        />
      </div>
    </div>
  );
};

const SessionRow: FC<{
  row: Performance["ranked"][number];
  badge: string;
  tone: string;
}> = ({ row, badge, tone }) => (
  <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line bg-canvas-overlay px-3 py-2">
    <div className="min-w-0">
      <div className="flex items-center gap-2">
        <span className={cn("rounded border px-1.5 py-0.5 text-[10px] font-medium", tone)}>
          {badge}
        </span>
        <span className="font-mono text-xs text-ink-faint">{row.reqCode}</span>
      </div>
      <p className="mt-0.5 truncate text-xs text-ink-muted">
        {row.session.operator} · {row.counts.captured} of {row.counts.reviewed} reviewed ·{" "}
        {relativeTime(row.session.endedAt ?? row.session.startedAt)}
      </p>
    </div>
    <Link
      to={`/reqs/${row.session.reqId}/session`}
      className="shrink-0 text-sm font-semibold tabular-nums text-ink hover:text-brand-200"
    >
      {row.counts.captureRate}%
    </Link>
  </div>
);

export const SourcingPerformancePanel: FC<{ performance: Performance }> = ({ performance }) => {
  const { totals, sessionCount, activeCount, strongest, weakest } = performance;

  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <Card className="p-4">
        <div className="mb-3 flex items-baseline justify-between gap-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
            Across all sourcing
          </p>
          <p className="text-xs text-ink-faint">
            {sessionCount} {sessionCount === 1 ? "session" : "sessions"}
            {activeCount > 0 && ` · ${activeCount} live`}
          </p>
        </div>

        {totals.reviewed === 0 ? (
          <p className="py-4 text-center text-xs text-ink-faint">
            No profiles reviewed yet. Start a supervised session to see performance.
          </p>
        ) : (
          <>
            <div className="mb-4 flex items-end gap-4">
              <div>
                <p className="text-3xl font-semibold tabular-nums text-ink">
                  {totals.captureRate}%
                </p>
                <p className="text-[10px] uppercase tracking-wide text-ink-faint">capture rate</p>
              </div>
              <p className="pb-1 text-xs text-ink-muted">
                {totals.captured} captured from {totals.reviewed} reviewed
              </p>
            </div>

            <div className="space-y-2.5">
              <FunnelBar label="Reviewed" value={totals.reviewed} total={totals.reviewed} tone="bg-brand-600/50" />
              <FunnelBar label="Captured" value={totals.captured} total={totals.reviewed} tone="bg-stage-advanced" />
              <FunnelBar label="Skipped" value={totals.skipped} total={totals.reviewed} tone="bg-ink-faint" />
              <FunnelBar label="Close calls" value={totals.closeCalls} total={totals.reviewed} tone="bg-oversight" />
            </div>
          </>
        )}
      </Card>

      <Card className="p-4">
        <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Session performance
        </p>

        {!strongest ? (
          <p className="py-4 text-center text-xs text-ink-faint">
            Not enough sessions with enough reviewed profiles to rank yet.
          </p>
        ) : (
          <div className="space-y-2">
            <SessionRow
              row={strongest}
              badge="Strongest"
              tone="border-stage-advanced/30 bg-stage-advanced/10 text-stage-advanced"
            />
            {weakest && (
              <SessionRow
                row={weakest}
                badge="Weakest"
                tone="border-oversight-line bg-oversight-soft text-oversight"
              />
            )}
            {weakest && (
              <p className="pt-1 text-[11px] text-ink-faint">
                A low rate can come from the brief, the search criteria, the approach — or a
                genuinely thin market. Worth a look, not a verdict.
              </p>
            )}
          </div>
        )}
      </Card>
    </div>
  );
};
