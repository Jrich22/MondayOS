/**
 * sourcingBOT Dashboard — the recruiter command center.
 *
 * The product's home. Ordered so that a recruiter opening it in the morning
 * reads down and knows what to do, rather than reading across and knowing what
 * exists:
 *
 *   pulse        eight numbers, one strip — the reflexive check
 *   focus        what to do next, ranked, each with a reason
 *   requisitions the searches themselves, with one action apiece
 *   performance  is sourcing working?
 *   intelligence where the talent is, click-to-filter
 *   activity     what moved
 *   pool preview a compact table; the full workspace is one click away
 *
 * Every metric is derived in lib/intel.ts from the existing Req, Candidate,
 * ReqCandidate, SourcingSession and readiness models. No dashboard-only entity
 * exists, and nothing here is stored.
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
  reqDashboard,
  reusableTalent,
  sourcingPerformance,
  type ConcentrationDimension,
  type IntelInput,
} from "@/lib/intel";
import { isDemoData } from "@/lib/seed";
import { relativeTime } from "@/lib/format";
import { DemoBanner } from "@/components/dashboard/DemoBanner";
import { ReqBoard } from "@/components/dashboard/ReqBoard";
import { SourcingPerformancePanel } from "@/components/dashboard/SourcingPerformance";
import { FocusQueue } from "@/components/dashboard/FocusQueue";
import { QuickSession } from "@/components/dashboard/QuickSession";
import { TalentIntel } from "@/components/workspace/TalentIntel";
import { Card, EmptyState, SectionTitle, StageBadge, cn } from "@/components/ui/Primitives";

const ACTIVITY_TONE = {
  capture: "bg-stage-advanced",
  evaluation: "bg-brand-400",
  skip: "bg-ink-faint",
} as const;

const Stat: FC<{
  label: string;
  value: string | number;
  href: string;
  tone?: "brand" | "oversight" | "advanced";
  quiet?: string;
}> = ({ label, value, href, tone, quiet }) => (
  <li className="bg-canvas-raised">
    <Link to={href} className="block px-4 py-3 transition-colors hover:bg-canvas-overlay">
      <p className="text-[11px] uppercase tracking-wide text-ink-faint">{label}</p>
      {value === 0 && quiet ? (
        <p className="mt-1 text-sm text-ink-faint">{quiet}</p>
      ) : (
        <p
          className={cn(
            "mt-0.5 text-2xl font-semibold tabular-nums",
            tone === "brand" ? "text-brand-400"
              : tone === "oversight" ? "text-oversight"
                : tone === "advanced" ? "text-stage-advanced"
                  : "text-ink",
          )}
        >
          {value}
        </p>
      )}
    </Link>
  </li>
);

const Dashboard: FC = () => {
  const { reqs, briefs, candidates, reqCandidates, sessions } = useWorkspace();
  const [dimension, setDimension] = useState<ConcentrationDimension>("company");
  const [filter, setFilter] = useState<string | null>(null);
  /** Req whose supervision gate is open inline. */
  const [startingReq, setStartingReq] = useState<string | null>(null);

  const input: IntelInput = useMemo(
    () => ({ reqs, briefs, candidates, reqCandidates, sessions }),
    [reqs, briefs, candidates, reqCandidates, sessions],
  );

  const today = useMemo(() => pulse(input), [input]);
  const focus = useMemo(() => recommendedFocus(input, 6), [input]);
  const board = useMemo(() => reqDashboard(input), [input]);
  const performance = useMemo(() => sourcingPerformance(input), [input]);
  const conc = useMemo(() => concentration(candidates, dimension), [candidates, dimension]);
  const reusable = useMemo(() => reusableTalent(input), [input]);
  const activity = useMemo(() => recentActivity(input, 10), [input]);
  const pool = useMemo(() => poolRows(input).slice(0, 6), [input]);
  const demo = useMemo(() => isDemoData({ sessions, candidates }), [sessions, candidates]);

  const activeBoard = board.filter((r) => r.req.status !== "closed");
  const empty = reqs.length === 0 && candidates.length === 0;

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-ink">Dashboard</h1>
          <p className="mt-1 text-sm text-ink-muted">
            {today.activeReqs} open {today.activeReqs === 1 ? "requisition" : "requisitions"} ·{" "}
            {candidates.length} people ·{" "}
            {today.captureRate === null ? "no sourcing yet" : `${today.captureRate}% capture rate`}
          </p>
        </div>
        <nav className="flex gap-2">
          <Link
            to="/talent"
            className="rounded-lg border border-line px-3 py-2 text-sm text-ink-muted transition-colors hover:border-line-strong hover:text-ink"
          >
            Talent workspace
          </Link>
          <Link
            to="/reqs"
            className="rounded-lg border border-line px-3 py-2 text-sm text-ink-muted transition-colors hover:border-line-strong hover:text-ink"
          >
            Requisitions
          </Link>
        </nav>
      </header>

      {demo && <DemoBanner />}

      {empty ? (
        <EmptyState
          title="Nothing here yet"
          body="Create a requisition, open it for sourcing, then run a supervised session. Everything on this dashboard is built from that work."
        />
      ) : (
        <>
          {/* ── 1. Pulse ─────────────────────────────────────────────── */}
          <ul className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-4">
            <Stat label="Open reqs" value={today.activeReqs} href="/reqs" />
            <Stat label="Live sessions" value={today.activeSessions} href="/reqs" tone="brand" quiet="none running" />
            <Stat label="Candidates captured" value={today.capturedTotal} href="/talent" tone="advanced" />
            <Stat label="Needs review" value={today.needsReview} href="#focus" tone="oversight" quiet="all current" />
            <Stat label="Close calls" value={today.closeCalls} href="#focus" tone="oversight" quiet="none" />
            <Stat label="Reusable people" value={today.reusedCandidates} href="#intelligence" quiet="none yet" />
            <Stat
              label="Capture rate"
              value={today.captureRate === null ? "—" : `${today.captureRate}%`}
              href="#performance"
            />
            <Stat
              label="Avg fit score"
              value={today.averageFit === null ? "—" : today.averageFit}
              href="/talent"
            />
          </ul>

          {/* ── 2. Recommended focus ─────────────────────────────────── */}
          <section id="focus" className="scroll-mt-6">
            <SectionTitle hint={focus.length > 0 ? `${focus.length} to work` : undefined}>
              Working queue
            </SectionTitle>
            {startingReq && (
              <div className="mb-3">
                <QuickSession
                  req={reqs.find((r) => r.id === startingReq)!}
                  brief={briefs.find((b) => b.reqId === startingReq)}
                  onCancel={() => setStartingReq(null)}
                />
              </div>
            )}
            <FocusQueue items={focus} onStartSession={setStartingReq} />
          </section>

          {/* ── 3. Active requisitions ───────────────────────────────── */}
          <section id="requisitions" className="scroll-mt-6">
            <SectionTitle hint={`${activeBoard.length} active`}>Requisitions</SectionTitle>
            <ReqBoard rows={activeBoard} />
          </section>

          {/* ── 5. Sourcing performance ──────────────────────────────── */}
          <section id="performance" className="scroll-mt-6">
            <SectionTitle hint="supervised sessions only">Sourcing performance</SectionTitle>
            <SourcingPerformancePanel performance={performance} />
          </section>

          {/* ── 4. Talent intelligence + 6. Activity ─────────────────── */}
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
            <section id="intelligence" className="scroll-mt-6">
              <SectionTitle hint="click to filter the talent workspace">
                Talent intelligence
              </SectionTitle>
              <div className="space-y-3">
                <TalentIntel
                  dimension={dimension}
                  rows={conc}
                  activeFilter={filter}
                  onDimension={setDimension}
                  onFilter={setFilter}
                />
                <Card className="p-4">
                  <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">
                    Reusable across requisitions
                  </p>
                  {reusable.length === 0 ? (
                    <p className="py-2 text-xs text-ink-faint">
                      Nobody has been evaluated for more than one req yet.
                    </p>
                  ) : (
                    <ul className="space-y-1.5">
                      {reusable.map((r) => (
                        <li key={r.candidate.id} className="flex items-center justify-between gap-3">
                          <Link
                            to={`/candidates/${r.candidate.id}`}
                            className="min-w-0 truncate text-sm text-ink hover:text-brand-200"
                          >
                            {r.candidate.fullName}
                            <span className="ml-2 text-xs text-ink-faint">
                              {r.reqCodes.join(" · ")}
                            </span>
                          </Link>
                          <span className="shrink-0 rounded-md border border-brand-500/30 bg-brand-500/10 px-1.5 py-0.5 text-[11px] text-brand-200">
                            {r.reqCount} reqs
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </Card>
              </div>
            </section>

            <section id="activity" className="scroll-mt-6">
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
                          className={cn("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", ACTIVITY_TONE[a.kind])}
                        />
                        <div className="min-w-0 flex-1">
                          <Link to={a.href} className="block truncate text-sm text-ink hover:text-brand-200">
                            {a.title}
                          </Link>
                          <p className="truncate text-xs text-ink-faint">{a.detail}</p>
                        </div>
                        <span className="shrink-0 text-[10px] text-ink-faint">
                          {relativeTime(a.at)}
                        </span>
                      </li>
                    ))}
                  </ul>
                </Card>
              )}
            </section>
          </div>

          {/* ── 7. Talent pool preview ───────────────────────────────── */}
          <section id="pool" className="scroll-mt-6">
            <SectionTitle hint={`${candidates.length} total`}>Talent pool</SectionTitle>
            <Card className="overflow-x-auto">
              <table className="w-full min-w-[44rem] text-left text-sm">
                <thead>
                  <tr className="border-b border-line text-xs uppercase tracking-wide text-ink-faint">
                    <th className="px-4 py-2.5 font-medium">Person</th>
                    <th className="px-4 py-2.5 font-medium">Company</th>
                    <th className="px-4 py-2.5 font-medium">Location</th>
                    <th className="px-4 py-2.5 font-medium">Reqs</th>
                    <th className="px-4 py-2.5 font-medium">Fit</th>
                    <th className="px-4 py-2.5 font-medium">Status</th>
                    <th className="px-4 py-2.5 font-medium">Last activity</th>
                  </tr>
                </thead>
                <tbody>
                  {pool.map((r) => (
                    <tr key={r.candidate.id} className="border-b border-line/60 last:border-0">
                      <td className="px-4 py-2.5">
                        <Link
                          to={`/candidates/${r.candidate.id}`}
                          className="font-medium text-ink hover:text-brand-200"
                        >
                          {r.candidate.fullName}
                        </Link>
                        <p className="truncate text-xs text-ink-faint">{r.title}</p>
                      </td>
                      <td className="px-4 py-2.5 text-ink-muted">{r.company || "—"}</td>
                      <td className="px-4 py-2.5 text-ink-muted">{r.candidate.location || "—"}</td>
                      <td className="px-4 py-2.5 text-ink-muted tabular-nums">{r.reqCount || "—"}</td>
                      <td className="px-4 py-2.5">
                        {r.bestFit === null ? (
                          <span className="text-xs text-ink-faint">—</span>
                        ) : (
                          <span
                            className={cn(
                              "text-sm font-semibold tabular-nums",
                              r.bestFit >= 75 ? "text-stage-advanced"
                                : r.bestFit >= 40 ? "text-brand-400"
                                  : "text-stage-rejected",
                            )}
                          >
                            {r.bestFit}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-2.5">
                        {r.latestStage ? <StageBadge stage={r.latestStage} /> : <span className="text-xs text-ink-faint">—</span>}
                      </td>
                      <td className="px-4 py-2.5 text-xs text-ink-faint">
                        {relativeTime(r.lastActivity)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
            <Link
              to="/talent"
              className="mt-2 block w-full rounded-lg border border-line py-2 text-center text-xs text-ink-muted transition-colors hover:border-line-strong hover:text-ink"
            >
              Open the full talent workspace — search, saved views, CSV export
            </Link>
          </section>
        </>
      )}
    </div>
  );
};

export default Dashboard;
