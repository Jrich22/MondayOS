/**
 * Candidate profile — the person, plus every requisition they have been
 * evaluated for.
 *
 * This is the payoff surface for the persistent-person model: the cross-req
 * history below is assembled from ReqCandidate rows, each holding its own
 * stage and fit score for a different requisition. The same person can be
 * advanced on one req and rejected on another, and both remain true.
 */
import type { FC } from "react";
import { Link, useParams } from "react-router-dom";
import { useWorkspace } from "@/lib/store";
import { reqHistoryFor } from "@/lib/req-candidate";
import { currentCompany } from "@/lib/candidate";
import { Card, EmptyState, FitScore, SectionTitle, StageBadge } from "@/components/ui/Primitives";

const CandidateProfile: FC = () => {
  const { candidateId = "" } = useParams();
  const { candidates, reqs, reqCandidates } = useWorkspace();

  const candidate = candidates.find((c) => c.id === candidateId);
  if (!candidate) {
    return (
      <div className="mx-auto max-w-3xl">
        <EmptyState title="Person not found" body="This record may have been removed." />
        <div className="mt-4 text-center">
          <Link to="/candidates" className="text-sm text-brand-400 hover:underline">
            Back to Talent
          </Link>
        </div>
      </div>
    );
  }

  const history = reqHistoryFor(candidate.id, reqCandidates);
  const reqById = new Map(reqs.map((r) => [r.id, r]));

  return (
    <div className="mx-auto max-w-4xl">
      <Link to="/candidates" className="text-sm text-ink-muted hover:text-ink">
        ← Talent
      </Link>

      <header className="mb-6 mt-3">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">{candidate.fullName}</h1>
        <p className="mt-1 text-sm text-ink-muted">
          {candidate.headline}
          {currentCompany(candidate) && ` · ${currentCompany(candidate)}`}
          {candidate.location && ` · ${candidate.location}`}
        </p>
        <p className="mt-1 text-xs text-ink-faint">
          Source: {candidate.origin.replace(/-/g, " ")}
          {!candidate.linkedInUrl && " · no profile URL on record"}
        </p>
      </header>

      <div className="space-y-6">
        <section>
          <SectionTitle hint={`${history.length} total`}>Requisition history</SectionTitle>
          {history.length === 0 ? (
            <EmptyState
              title="Not on any requisition"
              body="This person is in the talent pool but has not been evaluated for a req yet."
            />
          ) : (
            <ul className="space-y-3">
              {history.map((rc) => {
                const req = reqById.get(rc.reqId);
                return (
                  <li key={rc.id}>
                    <Card className="px-5 py-4">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-xs text-ink-faint">
                              {req?.code ?? "—"}
                            </span>
                            <StageBadge stage={rc.stage} />
                          </div>
                          {req && (
                            <Link
                              to={`/reqs/${req.id}`}
                              className="mt-1 block font-medium text-ink hover:text-brand-200"
                            >
                              {req.title}
                            </Link>
                          )}
                          {rc.rationale && (
                            <p className="mt-1 text-sm text-ink-muted">{rc.rationale}</p>
                          )}
                        </div>
                        <FitScore score={rc.fitScore} />
                      </div>
                    </Card>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        {candidate.roles.length > 0 && (
          <section>
            <SectionTitle>Career</SectionTitle>
            <Card className="divide-y divide-line/60">
              {candidate.roles.map((r, i) => (
                <div key={`${r.company}-${i}`} className="px-5 py-3">
                  <p className="text-sm font-medium text-ink">{r.title}</p>
                  <p className="text-sm text-ink-muted">
                    {r.company} · {r.startedAt} – {r.endedAt ?? "present"}
                  </p>
                </div>
              ))}
            </Card>
          </section>
        )}

        {candidate.notes && (
          <section>
            <SectionTitle hint="about the person, not one req">Notes</SectionTitle>
            <Card className="px-5 py-4">
              <p className="text-sm text-ink-muted">{candidate.notes}</p>
            </Card>
          </section>
        )}
      </div>
    </div>
  );
};

export default CandidateProfile;
