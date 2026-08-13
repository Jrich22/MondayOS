/**
 * Candidate Workspace — the recruiter's home.
 *
 * Ordered conclusions-first, records-last, which inverts a normal ATS on
 * purpose. A recruiter opening this page does not want to browse a database;
 * they want to know what to do next. So:
 *
 *   pulse        → the reflexive check, one dense strip
 *   focus        → THE page: a ranked, reasoned worklist with one action each
 *   intelligence → concentration that FILTERS the pool rather than decorating
 *   activity     → what moved since last time
 *   pool         → the table, last, 8 rows, for when you know who you want
 *
 * All five read derived views from lib/intel.ts, which composes the existing
 * domain functions and stores nothing. No competing model, no duplicated logic.
 */
import { useMemo, useState, type FC } from "react";
import { Link } from "react-router-dom";
import { useWorkspace } from "@/lib/store";
import {
  concentration,
  poolRows,
  pulse,
  recentActivity,
  recommendedFocus,
  sourcingTotals,
  type ConcentrationDimension,
  type IntelInput,
} from "@/lib/intel";
import { PulseStrip } from "@/components/workspace/Pulse";
import { FocusList } from "@/components/workspace/FocusList";
import { TalentIntel } from "@/components/workspace/TalentIntel";
import { TalentPool } from "@/components/workspace/TalentPool";
import { Card, EmptyState, SectionTitle, cn } from "@/components/ui/Primitives";

const ACTIVITY_TONE = {
  capture: "bg-stage-advanced",
  evaluation: "bg-brand-400",
  skip: "bg-ink-faint",
} as const;

const CandidateWorkspace: FC = () => {
  const { reqs, briefs, candidates, reqCandidates, sessions } = useWorkspace();
  const [dimension, setDimension] = useState<ConcentrationDimension>("company");
  const [filter, setFilter] = useState<string | null>(null);

  const input: IntelInput = useMemo(
    () => ({ reqs, briefs, candidates, reqCandidates, sessions }),
    [reqs, briefs, candidates, reqCandidates, sessions],
  );

  const today = useMemo(() => pulse(input), [input]);
  const focus = useMemo(() => recommendedFocus(input), [input]);
  const rows = useMemo(() => poolRows(input), [input]);
  const activity = useMemo(() => recentActivity(input), [input]);
  const conc = useMemo(() => concentration(candidates, dimension), [candidates, dimension]);
  const totals = useMemo(() => sourcingTotals(sessions), [sessions]);

  const emptyProduct = candidates.length === 0 && reqs.length === 0;

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-ink">Talent</h1>
          <p className="mt-1 text-sm text-ink-muted">
            {candidates.length} {candidates.length === 1 ? "person" : "people"} ·{" "}
            {totals.captured} sourced
            {totals.captureRate !== null && ` · ${totals.captureRate}% capture rate`}
          </p>
        </div>
        <Link
          to="/reqs"
          className="rounded-lg border border-line px-3 py-2 text-sm text-ink-muted transition-colors hover:border-line-strong hover:text-ink"
        >
          Req workspace
        </Link>
      </header>

      {emptyProduct ? (
        <EmptyState
          title="Nothing here yet"
          body="Create a requisition, open it for sourcing, then run a supervised session. People you capture will appear here with the intelligence built from them."
        />
      ) : (
        <>
          <PulseStrip pulse={today} />

          <section id="focus" className="scroll-mt-6">
            <SectionTitle hint={focus.length > 0 ? `${focus.length} items` : undefined}>
              Recommended focus
            </SectionTitle>
            <FocusList items={focus} />
          </section>

          <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
            <section id="activity" className="scroll-mt-6 lg:order-2">
              <SectionTitle>Recent activity</SectionTitle>
              {activity.length === 0 ? (
                <EmptyState title="Nothing yet" body="Captures and stage changes appear here." />
              ) : (
                <Card className="p-4">
                  <ul className="space-y-2.5">
                    {activity.map((a) => (
                      <li key={a.id} className="flex gap-2.5">
                        <span
                          aria-hidden
                          className={cn(
                            "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full",
                            ACTIVITY_TONE[a.kind],
                          )}
                        />
                        <div className="min-w-0">
                          <Link
                            to={a.href}
                            className="block truncate text-sm text-ink hover:text-brand-200"
                          >
                            {a.title}
                          </Link>
                          <p className="truncate text-xs text-ink-faint">{a.detail}</p>
                        </div>
                      </li>
                    ))}
                  </ul>
                </Card>
              )}
            </section>

            <section className="lg:order-1">
              <SectionTitle hint="click to filter the pool">Talent intelligence</SectionTitle>
              <TalentIntel
                dimension={dimension}
                rows={conc}
                activeFilter={filter}
                onDimension={setDimension}
                onFilter={setFilter}
              />
            </section>
          </div>

          <section id="pool" className="scroll-mt-6">
            <SectionTitle hint={`${rows.length} total`}>Talent pool</SectionTitle>
            <TalentPool
              rows={rows}
              dimensionFilter={filter ? { dimension, label: filter } : null}
              onClearDimension={() => setFilter(null)}
            />
          </section>
        </>
      )}
    </div>
  );
};

export default CandidateWorkspace;
