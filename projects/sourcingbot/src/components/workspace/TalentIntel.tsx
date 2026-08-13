/**
 * Talent intelligence — concentration as navigation, not decoration.
 *
 * No charts. Each row is a bar and a click that FILTERS THE POOL below, so
 * "where is our talent concentrated?" and "show me those people" are the same
 * gesture. A pie chart would answer the first and abandon the second.
 */
import type { FC } from "react";
import type { ConcentrationDimension, ConcentrationRow } from "@/lib/intel";
import { Card, cn } from "@/components/ui/Primitives";

const DIMENSIONS: ReadonlyArray<{ id: ConcentrationDimension; label: string; question: string }> = [
  { id: "company", label: "Companies", question: "Where do our people work?" },
  { id: "location", label: "Locations", question: "Where are they?" },
  { id: "title", label: "Titles", question: "What do they do?" },
  { id: "skill", label: "Skills", question: "What can they do?" },
];

export const TalentIntel: FC<{
  dimension: ConcentrationDimension;
  rows: ConcentrationRow[];
  activeFilter: string | null;
  onDimension: (d: ConcentrationDimension) => void;
  onFilter: (label: string | null) => void;
}> = ({ dimension, rows, activeFilter, onDimension, onFilter }) => {
  const question = DIMENSIONS.find((d) => d.id === dimension)?.question ?? "";

  return (
    <Card className="p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-ink-faint">{question}</p>
        <div role="tablist" aria-label="Concentration dimension" className="flex gap-1">
          {DIMENSIONS.map((d) => (
            <button
              key={d.id}
              role="tab"
              type="button"
              aria-selected={dimension === d.id}
              onClick={() => {
                onDimension(d.id);
                onFilter(null);
              }}
              className={cn(
                "rounded-md px-2 py-1 text-xs transition-colors",
                dimension === d.id
                  ? "bg-brand-500/15 text-brand-200 ring-1 ring-brand-500/30"
                  : "text-ink-faint hover:bg-white/5 hover:text-ink-muted",
              )}
            >
              {d.label}
            </button>
          ))}
        </div>
      </div>

      {rows.length === 0 ? (
        <p className="py-4 text-center text-xs text-ink-faint">
          Not enough data yet — capture candidates to see concentration.
        </p>
      ) : (
        <ul className="space-y-1.5">
          {rows.map((r) => {
            const active = activeFilter?.toLowerCase() === r.label.toLowerCase();
            return (
              <li key={r.label}>
                <button
                  type="button"
                  aria-pressed={active}
                  onClick={() => onFilter(active ? null : r.label)}
                  className={cn(
                    "group w-full rounded-lg px-2 py-1.5 text-left transition-colors",
                    active ? "bg-brand-500/10 ring-1 ring-brand-500/25" : "hover:bg-white/5",
                  )}
                >
                  <div className="flex items-baseline justify-between gap-3">
                    <span
                      className={cn(
                        "truncate text-sm",
                        active ? "text-brand-200" : "text-ink-muted group-hover:text-ink",
                      )}
                    >
                      {r.label}
                    </span>
                    <span className="shrink-0 text-xs tabular-nums text-ink-faint">
                      {r.count}
                      <span className="ml-1 opacity-60">{r.share}%</span>
                    </span>
                  </div>
                  <div className="mt-1 h-1 overflow-hidden rounded-full bg-line">
                    <div
                      className={cn(
                        "h-full rounded-full transition-all duration-500",
                        active ? "bg-brand-400" : "bg-brand-600/60",
                      )}
                      style={{ width: `${Math.max(r.share, 3)}%` }}
                    />
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {activeFilter && (
        <p className="mt-3 text-xs text-brand-200">
          Talent pool filtered to “{activeFilter}”.{" "}
          <button
            type="button"
            onClick={() => onFilter(null)}
            className="underline hover:text-brand-50"
          >
            Clear
          </button>
        </p>
      )}
    </Card>
  );
};
