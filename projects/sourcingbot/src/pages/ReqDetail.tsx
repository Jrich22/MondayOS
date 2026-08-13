/**
 * Req detail — the sourcing brief and this requisition's pipeline.
 *
 * The pipeline table joins ReqCandidate rows to Candidate records at RENDER
 * time via joinPipeline. Nothing about the person is stored on the pipeline
 * row, so the join is the only place the two ever meet.
 */
import type { FC } from "react";
import { Link, useParams } from "react-router-dom";
import { useWorkspace } from "@/lib/store";
import { briefReadinessIssues, isSourcingReady } from "@/lib/brief";
import { joinPipeline, needsReassessment, pipelineFor } from "@/lib/req-candidate";
import { currentCompany } from "@/lib/candidate";
import {
  Card,
  EmptyState,
  FitScore,
  ReqStatusBadge,
  SectionTitle,
  StageBadge,
} from "@/components/ui/Primitives";

const ReqDetail: FC = () => {
  const { reqId = "" } = useParams();
  const { reqs, briefs, candidates, reqCandidates } = useWorkspace();

  const req = reqs.find((r) => r.id === reqId);
  const brief = briefs.find((b) => b.reqId === reqId);

  if (!req) {
    return (
      <div className="mx-auto max-w-3xl">
        <EmptyState title="Requisition not found" body="This requisition may have been removed." />
        <div className="mt-4 text-center">
          <Link to="/" className="text-sm text-brand-400 hover:underline">
            Back to workspace
          </Link>
        </div>
      </div>
    );
  }

  const rows = joinPipeline(pipelineFor(req.id, reqCandidates), candidates);
  const readinessIssues = brief ? briefReadinessIssues(brief) : [];

  return (
    <div className="mx-auto max-w-6xl">
      <Link to="/" className="text-sm text-ink-muted hover:text-ink">
        ← Workspace
      </Link>

      <header className="mb-6 mt-3 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs text-ink-faint">{req.code}</span>
            <ReqStatusBadge status={req.status} />
          </div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-ink">{req.title}</h1>
          <p className="mt-1 text-sm text-ink-muted">
            {req.team} · {req.location} · {req.workModel} · Hiring manager: {req.hiringManager || "—"}
          </p>
        </div>
      </header>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <section>
          <SectionTitle hint={`${rows.length} on this req`}>Pipeline</SectionTitle>
          {rows.length === 0 ? (
            <EmptyState
              title="No one on this requisition yet"
              body="Add an existing person from Talent, or record a supervised sourcing session."
            />
          ) : (
            <Card className="overflow-x-auto">
              <table className="w-full min-w-[36rem] text-left text-sm">
                <thead>
                  <tr className="border-b border-line text-xs uppercase tracking-wide text-ink-faint">
                    <th className="px-4 py-3 font-medium">Person</th>
                    <th className="px-4 py-3 font-medium">Stage</th>
                    <th className="px-4 py-3 font-medium">Fit</th>
                    <th className="px-4 py-3 font-medium">Rationale</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(({ reqCandidate: rc, candidate }) => (
                    <tr key={rc.id} className="border-b border-line/60 last:border-0">
                      <td className="px-4 py-3">
                        <Link
                          to={`/candidates/${candidate.id}`}
                          className="font-medium text-ink hover:text-brand-200"
                        >
                          {candidate.fullName}
                        </Link>
                        <p className="text-xs text-ink-faint">
                          {candidate.headline}
                          {currentCompany(candidate) && ` · ${currentCompany(candidate)}`}
                        </p>
                      </td>
                      <td className="px-4 py-3">
                        <StageBadge stage={rc.stage} />
                      </td>
                      <td className="px-4 py-3">
                        <FitScore score={rc.fitScore} />
                        {brief && needsReassessment(rc, brief) && (
                          <p className="mt-0.5 text-[11px] text-oversight">
                            Brief v{brief.version} — reassess
                          </p>
                        )}
                      </td>
                      <td className="px-4 py-3 text-ink-muted">{rc.rationale || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          )}
        </section>

        <aside>
          <SectionTitle hint={brief ? `v${brief.version}` : undefined}>Sourcing brief</SectionTitle>
          {!brief ? (
            <EmptyState title="No brief yet" body="A brief defines what this search is looking for." />
          ) : (
            <Card className="space-y-4 p-4">
              <p className="text-sm text-ink">{brief.headline}</p>

              {!isSourcingReady(brief) && (
                <div className="rounded-lg border border-oversight-line bg-oversight-soft p-3">
                  <p className="text-xs font-medium text-oversight">Not ready to source</p>
                  <ul className="mt-1 space-y-0.5 text-xs text-ink-muted">
                    {readinessIssues.map((issue) => (
                      <li key={issue}>· {issue}</li>
                    ))}
                  </ul>
                </div>
              )}

              <div>
                <p className="mb-1.5 text-xs uppercase tracking-wide text-ink-faint">Requirements</p>
                <ul className="space-y-1.5">
                  {brief.requirements.map((r) => (
                    <li key={r.id} className="flex items-start gap-2 text-sm">
                      <span
                        className={
                          r.kind === "required"
                            ? "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-brand-400"
                            : "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-ink-faint"
                        }
                        aria-hidden
                      />
                      <span className="text-ink-muted">
                        {r.label}
                        <span className="ml-1 text-xs text-ink-faint">
                          {r.kind === "required" ? "required" : `preferred ·${r.weight}`}
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>
              </div>

              {brief.keywords.length > 0 && (
                <div>
                  <p className="mb-1.5 text-xs uppercase tracking-wide text-ink-faint">Keywords</p>
                  <div className="flex flex-wrap gap-1.5">
                    {brief.keywords.map((k) => (
                      <span
                        key={k}
                        className="rounded-md border border-line px-1.5 py-0.5 text-xs text-ink-muted"
                      >
                        {k}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {brief.outreachAngle && (
                <div>
                  <p className="mb-1 text-xs uppercase tracking-wide text-ink-faint">Outreach angle</p>
                  <p className="text-sm text-ink-muted">{brief.outreachAngle}</p>
                </div>
              )}
            </Card>
          )}
        </aside>
      </div>
    </div>
  );
};

export default ReqDetail;
