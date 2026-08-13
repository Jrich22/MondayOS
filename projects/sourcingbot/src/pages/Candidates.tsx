/**
 * Talent — the persistent-person directory.
 *
 * This surface exists to make the model rule visible: people are listed once,
 * with a count of how many requisitions they have been evaluated for. A
 * per-req candidate model could not render this page at all.
 */
import { useState, type FC } from "react";
import { Link } from "react-router-dom";
import { useWorkspace } from "@/lib/store";
import { currentCompany, searchCandidates, talentConcentration } from "@/lib/candidate";
import { reqHistoryFor } from "@/lib/req-candidate";
import { Card, EmptyState, SectionTitle } from "@/components/ui/Primitives";

const Candidates: FC = () => {
  const { candidates, reqCandidates } = useWorkspace();
  const [query, setQuery] = useState("");

  const visible = searchCandidates(candidates, query);
  const concentration = talentConcentration(candidates).slice(0, 5);

  return (
    <div className="mx-auto max-w-6xl">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">Talent</h1>
        <p className="mt-1 text-sm text-ink-muted">
          People persist across requisitions. Each person appears once here, however many reqs they
          have been considered for.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
        <section>
          <label className="sr-only" htmlFor="talent-search">
            Search people
          </label>
          <input
            id="talent-search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by name, skill, company…"
            className="mb-4 w-full rounded-lg border border-line bg-canvas-raised px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-brand-500"
          />

          {visible.length === 0 ? (
            <EmptyState
              title={candidates.length === 0 ? "No people yet" : "No matches"}
              body={
                candidates.length === 0
                  ? "People are added manually or recorded during a supervised sourcing session."
                  : "Try a different name, skill, or company."
              }
            />
          ) : (
            <ul className="space-y-3">
              {visible.map((c) => {
                const history = reqHistoryFor(c.id, reqCandidates);
                return (
                  <li key={c.id}>
                    <Card className="transition-colors hover:border-line-strong">
                      <Link to={`/candidates/${c.id}`} className="block px-5 py-4">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="truncate font-medium text-ink">{c.fullName}</p>
                            <p className="mt-0.5 truncate text-sm text-ink-muted">
                              {c.headline}
                              {currentCompany(c) && ` · ${currentCompany(c)}`}
                            </p>
                            {c.skills.length > 0 && (
                              <div className="mt-2 flex flex-wrap gap-1.5">
                                {c.skills.slice(0, 4).map((s) => (
                                  <span
                                    key={s}
                                    className="rounded-md border border-line px-1.5 py-0.5 text-xs text-ink-muted"
                                  >
                                    {s}
                                  </span>
                                ))}
                              </div>
                            )}
                          </div>
                          <p className="shrink-0 text-xs text-ink-faint">
                            {history.length} {history.length === 1 ? "requisition" : "requisitions"}
                          </p>
                        </div>
                      </Link>
                    </Card>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <aside>
          <SectionTitle hint="by current employer">Talent concentration</SectionTitle>
          {concentration.length === 0 ? (
            <EmptyState title="No data" body="Add people with a current role to see concentration." />
          ) : (
            <Card className="p-4">
              <ul className="space-y-2">
                {concentration.map(({ company, count }) => (
                  <li key={company} className="flex items-center justify-between gap-3 text-sm">
                    <span className="truncate text-ink-muted">{company}</span>
                    <span className="tabular-nums text-ink">{count}</span>
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-xs text-ink-faint">
                Only meaningful because each person is stored once.
              </p>
            </Card>
          )}
        </aside>
      </div>
    </div>
  );
};

export default Candidates;
