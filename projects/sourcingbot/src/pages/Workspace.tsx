/**
 * Req Workspace — the product's home surface. Lists requisitions with live
 * pipeline counts drawn from ReqCandidate rows.
 */
import type { FC } from "react";
import { Link } from "react-router-dom";
import { useWorkspace } from "@/lib/store";
import { sortForWorkspace } from "@/lib/req";
import { stageCounts } from "@/lib/req-candidate";
import { Card, EmptyState, ReqStatusBadge, SectionTitle } from "@/components/ui/Primitives";

const Workspace: FC = () => {
  const { reqs, reqCandidates, candidates } = useWorkspace();
  const ordered = sortForWorkspace(reqs);
  const openCount = reqs.filter((r) => r.status === "open").length;

  return (
    <div className="mx-auto max-w-6xl">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">Req Workspace</h1>
        <p className="mt-1 text-sm text-ink-muted">
          {openCount} open {openCount === 1 ? "requisition" : "requisitions"} ·{" "}
          {candidates.length} people tracked across all reqs
        </p>
      </div>

      <SectionTitle hint={`${reqs.length} total`}>Requisitions</SectionTitle>

      {ordered.length === 0 ? (
        <EmptyState
          title="No requisitions yet"
          body="Requisitions are the unit of work in sourcingBOT — briefs, sessions, and evaluations all hang off one req."
        />
      ) : (
        <ul className="space-y-3">
          {ordered.map((req) => {
            const counts = stageCounts(req.id, reqCandidates);
            const active =
              counts.identified + counts.reviewing + counts.contacted + counts.responded;
            return (
              <li key={req.id}>
                <Card className="transition-colors hover:border-line-strong">
                  <Link to={`/reqs/${req.id}`} className="block px-5 py-4">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-xs text-ink-faint">{req.code}</span>
                          <ReqStatusBadge status={req.status} />
                        </div>
                        <p className="mt-1 truncate text-base font-medium text-ink">{req.title}</p>
                        <p className="mt-0.5 text-sm text-ink-muted">
                          {req.team} · {req.location} · {req.openings}{" "}
                          {req.openings === 1 ? "opening" : "openings"}
                        </p>
                      </div>
                      <div className="text-right">
                        <p className="text-sm text-ink">
                          <span className="font-semibold tabular-nums">{active}</span>{" "}
                          <span className="text-ink-muted">in pipeline</span>
                        </p>
                        <p className="mt-0.5 text-xs text-ink-faint">
                          {counts.advanced} advanced · {counts.rejected} rejected
                        </p>
                      </div>
                    </div>
                  </Link>
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
