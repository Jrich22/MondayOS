import type { ReactNode } from "react";

/**
 * A KPI tile for the dashboard header row. Shows one headline number with a
 * label, an icon, and an optional secondary hint (e.g. a trend or breakdown).
 */
export function StatCard({
  label,
  value,
  hint,
  icon,
}: {
  label: string;
  value: string;
  hint?: string;
  icon: ReactNode;
}) {
  return (
    <div className="card relative overflow-hidden p-5">
      <div className="absolute inset-0 bg-brand-sheen opacity-0 transition-opacity duration-300 hover:opacity-100" />
      <div className="relative flex items-start justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-ink-faint">
            {label}
          </p>
          <p className="mt-2 text-3xl font-semibold tabular-nums text-ink">
            {value}
          </p>
          {hint && <p className="mt-1 text-xs text-ink-muted">{hint}</p>}
        </div>
        <span className="grid h-10 w-10 place-items-center rounded-xl border border-line bg-white/[0.03] text-brand-400">
          {icon}
        </span>
      </div>
    </div>
  );
}
