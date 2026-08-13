/**
 * Talent pool — the table, deliberately last and deliberately small.
 *
 * It exists for when a recruiter already knows who they are looking for. It is
 * NOT the landing experience: it opens showing 8 rows, and everything above it
 * answers a question this table can only be searched for.
 *
 * Saved views are named filters, not stored queries — no new entity, no
 * persistence, nothing to go stale.
 */
import { useMemo, useState, type FC } from "react";
import { Link } from "react-router-dom";
import type { Candidate } from "@/lib/types";
import type { ConcentrationDimension, PoolRow, SavedView } from "@/lib/intel";
import { SAVED_VIEWS, applyView, matchesDimension, toCsv } from "@/lib/intel";
import { searchCandidates } from "@/lib/candidate";
import { Card, EmptyState, cn } from "@/components/ui/Primitives";

const PAGE = 8;

export const TalentPool: FC<{
  rows: PoolRow[];
  dimensionFilter: { dimension: ConcentrationDimension; label: string } | null;
  onClearDimension: () => void;
}> = ({ rows, dimensionFilter, onClearDimension }) => {
  const [query, setQuery] = useState("");
  const [view, setView] = useState<SavedView>("all");
  const [expanded, setExpanded] = useState(false);

  const visible = useMemo(() => {
    let out = applyView(rows, view);
    if (dimensionFilter) {
      out = out.filter((r) =>
        matchesDimension(r.candidate, dimensionFilter.dimension, dimensionFilter.label),
      );
    }
    if (query.trim()) {
      const matched = new Set(
        searchCandidates(
          out.map((r) => r.candidate),
          query,
        ).map((c: Candidate) => c.id),
      );
      out = out.filter((r) => matched.has(r.candidate.id));
    }
    return out;
  }, [rows, view, query, dimensionFilter]);

  const shown = expanded ? visible : visible.slice(0, PAGE);

  const exportCsv = () => {
    const blob = new Blob([toCsv(visible)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `talent-pool-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label className="sr-only" htmlFor="pool-search">
          Search the talent pool
        </label>
        <input
          id="pool-search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search name, skill, company…"
          className="min-w-[14rem] flex-1 rounded-lg border border-line bg-canvas-raised px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-brand-500 focus:outline-none"
        />
        <div role="group" aria-label="Saved views" className="flex flex-wrap gap-1">
          {SAVED_VIEWS.map((v) => (
            <button
              key={v.id}
              type="button"
              title={v.hint}
              aria-pressed={view === v.id}
              onClick={() => setView(v.id)}
              className={cn(
                "rounded-lg px-2.5 py-1.5 text-xs transition-colors",
                view === v.id
                  ? "bg-brand-500/15 text-brand-200 ring-1 ring-brand-500/30"
                  : "text-ink-faint hover:bg-white/5 hover:text-ink-muted",
              )}
            >
              {v.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={exportCsv}
          disabled={visible.length === 0}
          className="rounded-lg border border-line px-2.5 py-1.5 text-xs text-ink-muted transition-colors hover:border-line-strong hover:text-ink disabled:opacity-40"
        >
          Export CSV
        </button>
      </div>

      {dimensionFilter && (
        <p className="mb-2 text-xs text-brand-200">
          Filtered by {dimensionFilter.dimension}: “{dimensionFilter.label}”.{" "}
          <button type="button" onClick={onClearDimension} className="underline hover:text-brand-50">
            Clear
          </button>
        </p>
      )}

      {visible.length === 0 ? (
        <EmptyState
          title={rows.length === 0 ? "No one in the pool yet" : "No matches"}
          body={
            rows.length === 0
              ? "People arrive here from supervised sourcing sessions, referrals, or manual entry."
              : "Try a different search, view, or clear the concentration filter."
          }
        />
      ) : (
        <>
          <Card className="overflow-x-auto">
            <table className="w-full min-w-[40rem] text-left text-sm">
              <thead>
                <tr className="border-b border-line text-xs uppercase tracking-wide text-ink-faint">
                  <th className="px-4 py-2.5 font-medium">Person</th>
                  <th className="px-4 py-2.5 font-medium">Company</th>
                  <th className="px-4 py-2.5 font-medium">Reqs</th>
                  <th className="px-4 py-2.5 font-medium">Best fit</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.candidate.id} className="border-b border-line/60 last:border-0">
                    <td className="px-4 py-2.5">
                      <Link
                        to={`/candidates/${r.candidate.id}`}
                        className="font-medium text-ink hover:text-brand-200"
                      >
                        {r.candidate.fullName}
                      </Link>
                      <p className="truncate text-xs text-ink-faint">
                        {r.title || r.candidate.headline}
                      </p>
                    </td>
                    <td className="px-4 py-2.5 text-ink-muted">{r.company || "—"}</td>
                    <td className="px-4 py-2.5">
                      {r.reqCount > 1 ? (
                        <span className="rounded-md border border-brand-500/30 bg-brand-500/10 px-1.5 py-0.5 text-xs text-brand-200">
                          {r.reqCount} reqs
                        </span>
                      ) : (
                        <span className="text-xs text-ink-faint">{r.reqCount || "—"}</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      {r.bestFit === null ? (
                        <span className="text-xs text-ink-faint">—</span>
                      ) : (
                        <span
                          className={cn(
                            "text-sm font-semibold tabular-nums",
                            r.bestFit >= 75
                              ? "text-stage-advanced"
                              : r.bestFit >= 40
                                ? "text-brand-400"
                                : "text-stage-rejected",
                          )}
                        >
                          {r.bestFit}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

          {visible.length > PAGE && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="mt-2 w-full rounded-lg border border-line py-2 text-xs text-ink-muted transition-colors hover:border-line-strong hover:text-ink"
            >
              {expanded
                ? `Show fewer — ${visible.length} in view`
                : `Show all ${visible.length}`}
            </button>
          )}
        </>
      )}
    </div>
  );
};
