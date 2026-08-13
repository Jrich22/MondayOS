/**
 * Req Workspace — the product's home surface. Lists requisitions with live
 * pipeline counts drawn from ReqCandidate rows.
 */
import type { FC } from "react";
import { Link, useNavigate } from "react-router-dom";
import { createDraftReq, useWorkspace } from "@/lib/store";
import { sortForWorkspace } from "@/lib/req";
import { stageCounts } from "@/lib/req-candidate";
import { evaluateReadiness } from "@/lib/readiness";
import { Card, EmptyState, ReqStatusBadge, SectionTitle } from "@/components/ui/Primitives";

const Workspace: FC = () => {
  const { reqs, briefs, reqCandidates, candidates } = useWorkspace();
  const navigate = useNavigate();
  const ordered = sortForWorkspace(reqs);
  const openCount = reqs.filter((r) => r.status === "open").length;
  const draftCount = reqs.filter((r) => r.status === "draft").length;

  const startNewReq = () => {
    const { req } = createDraftReq();
    navigate(`/reqs/${req.id}/edit`);
  };

  return (
    <div className="mx-auto max-w-6xl">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-ink">Req Workspace</h1>
          <p className="mt-1 text-sm text-ink-muted">
            {openCount} open {openCount === 1 ? "requisition" : "requisitions"}
            {draftCount > 0 && ` · ${draftCount} draft${draftCount === 1 ? "" : "s"}`} ·{" "}
            {candidates.length} people tracked across all reqs
          </p>
        </div>
        <button
          type="button"
          onClick={startNewReq}
          className="rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-500"
        >
          New requisition
        </button>
      </div>

      <SectionTitle hint={`${reqs.length} total`}>Requisitions</SectionTitle>

      {ordered.length === 0 ? (
        <EmptyState
          title="No requisitions yet"
          body="Requisitions are the unit of work in sourcingBOT — briefs, sessions, and evaluations all hang off one req. Start one to begin."
        />
      ) : (
        <ul className="space-y-3">
          {ordered.map((req) => {
            const counts = stageCounts(req.id, reqCandidates);
            const active =
              counts.identified + counts.reviewing + counts.contacted + counts.responded;
            const isDraft = req.status === "draft";
            const readiness = isDraft
              ? evaluateReadiness(req, briefs.find((b) => b.reqId === req.id))
              : null;
            return (
              <li key={req.id}>
                <Card className="transition-colors hover:border-line-strong">
                  <div className="flex flex-wrap items-start justify-between gap-3 px-5 py-4">
                    <Link to={isDraft ? `/reqs/${req.id}/edit` : `/reqs/${req.id}`} className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs text-ink-faint">
                          {req.code || "unassigned"}
                        </span>
                        <ReqStatusBadge status={req.status} />
                      </div>
                      <p className="mt-1 truncate text-base font-medium text-ink">
                        {req.title || "Untitled requisition"}
                      </p>
                      <p className="mt-0.5 truncate text-sm text-ink-muted">
                        {[req.team, req.location].filter(Boolean).join(" · ") || "Not yet described"}
                        {req.team && ` · ${req.openings} ${req.openings === 1 ? "opening" : "openings"}`}
                      </p>
                    </Link>

                    <div className="flex items-center gap-4 text-right">
                      {readiness ? (
                        <div>
                          <p className="text-sm font-semibold tabular-nums text-ink">
                            {readiness.completeness}%
                          </p>
                          <p className="mt-0.5 text-xs text-ink-faint">
                            {readiness.sourcingReady ? "ready" : "in progress"}
                          </p>
                        </div>
                      ) : (
                        <div>
                          <p className="text-sm text-ink">
                            <span className="font-semibold tabular-nums">{active}</span>{" "}
                            <span className="text-ink-muted">in pipeline</span>
                          </p>
                          <p className="mt-0.5 text-xs text-ink-faint">
                            {counts.advanced} advanced · {counts.rejected} rejected
                          </p>
                        </div>
                      )}
                      <Link
                        to={`/reqs/${req.id}/edit`}
                        className="shrink-0 rounded-lg border border-line px-2.5 py-1.5 text-xs text-ink-muted transition-colors hover:border-line-strong hover:text-ink"
                      >
                        Edit
                      </Link>
                    </div>
                  </div>
                </Card>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
};

export default Workspace;
