/**
 * Supervised sourcing session — the recruiter's live capture surface.
 *
 * Three states, one page:
 *   no session   → the policy acknowledgement gate
 *   in-progress  → capture form, live counts, session log
 *   paused       → everything visible, capture suspended
 *
 * The gate is not a formality. `startSession` in lib/linkedin.ts throws without
 * a named operator, a per-session acknowledgement, and an open req — this
 * surface cannot bypass it, and does not try to. Nothing here browses,
 * fetches, or parses anything: the operator reviews profiles themselves and
 * records what they found.
 */
import { useState, type FC } from "react";
import { Link, useParams } from "react-router-dom";
import {
  activeSession,
  commitCapture,
  sessionHistory,
  addSession,
  updateSession,
  useWorkspace,
} from "@/lib/store";
import {
  SUPERVISION_POLICY,
  SupervisionRequiredError,
  completeSession,
  pauseSession,
  recordSkip,
  resumeSession,
  sessionCounts,
  startSession,
} from "@/lib/linkedin";
import { acceptsSourcing } from "@/lib/req";
import { captureCandidate, CaptureError, type CaptureInput, type DuplicateResolution } from "@/lib/capture";
import { CaptureForm } from "@/components/session/CaptureForm";
import { Card, EmptyState, SectionTitle, cn } from "@/components/ui/Primitives";

const SourcingSessionPage: FC = () => {
  const { reqId = "" } = useParams();
  const { reqs, briefs, candidates, reqCandidates } = useWorkspace();

  const [operator, setOperator] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const req = reqs.find((r) => r.id === reqId);
  const brief = briefs.find((b) => b.reqId === reqId);
  const session = activeSession(reqId);
  const history = sessionHistory(reqId);

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

  const guarded = (fn: () => void) => {
    try {
      setError("");
      fn();
    } catch (e) {
      if (e instanceof SupervisionRequiredError || e instanceof CaptureError) {
        setError(e.message);
      } else {
        throw e;
      }
    }
  };

  const begin = () =>
    guarded(() => {
      addSession(
        startSession({
          reqId,
          operator,
          acknowledgedPolicy: acknowledged,
          reqAcceptsSourcing: acceptsSourcing(req),
          briefVersion: brief?.version,
        }),
      );
      setNotice("");
    });

  const capture = (input: CaptureInput, resolution: DuplicateResolution) =>
    guarded(() => {
      if (!session) return;
      const result = captureCandidate(
        { session, reqId, brief, candidates, reqCandidates, operator: session.operator },
        input,
        resolution,
      );
      commitCapture(result);
      setNotice(
        result.reusedExistingCandidate
          ? `${result.candidate.fullName} added to this req — existing person reused, history preserved.`
          : `${result.candidate.fullName} captured.`,
      );
    });

  const skip = (name: string, reason: string, closeCall: boolean) =>
    guarded(() => {
      if (!session) return;
      updateSession(recordSkip(session, { name, reason, closeCall }));
      setNotice(`${name} skipped${closeCall ? " — flagged as a close call." : "."}`);
    });

  const counts = session ? sessionCounts(session) : null;

  return (
    <div className="mx-auto max-w-6xl">
      <Link to={`/reqs/${req.id}`} className="text-sm text-ink-muted hover:text-ink">
        ← {req.code || "Requisition"}
      </Link>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight text-ink">Sourcing session</h1>
      <p className="mt-1 text-sm text-ink-muted">
        {req.title} · {req.team}
      </p>

      <div className="mt-3 rounded-xl border border-oversight-line bg-oversight-soft px-4 py-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-oversight">
          Human-supervised sourcing
        </p>
        <p className="mt-1 text-xs text-ink-muted">
          You browse LinkedIn yourself and record what you find. sourcingBOT does not open,
          fetch, or parse any profile.
        </p>
      </div>

      {error && (
        <p role="alert" className="mt-3 rounded-lg border border-stage-rejected/30 bg-stage-rejected/10 px-3 py-2 text-sm text-stage-rejected">
          {error}
        </p>
      )}
      {notice && !error && (
        <p role="status" className="mt-3 rounded-lg border border-line bg-canvas-raised px-3 py-2 text-sm text-ink-muted">
          {notice}
        </p>
      )}

      <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_19rem]">
        <section>
          {!session ? (
            <Card className="p-5">
              <SectionTitle>Start a supervised session</SectionTitle>
              {!acceptsSourcing(req) && (
                <p className="mb-4 rounded-lg border border-oversight-line bg-oversight-soft px-3 py-2 text-sm text-oversight">
                  This requisition is <strong>{req.status}</strong>. Open it for sourcing before
                  starting a session.
                </p>
              )}
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <label htmlFor="sess-operator" className="block text-sm font-medium text-ink">
                    Your name
                  </label>
                  <p className="text-xs text-ink-faint">
                    A session cannot exist without a named human operator.
                  </p>
                  <input
                    id="sess-operator"
                    type="text"
                    value={operator}
                    onChange={(e) => setOperator(e.target.value)}
                    placeholder="Dana Whitfield"
                    className="w-full rounded-lg border border-line bg-canvas px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-brand-500 focus:outline-none"
                  />
                </div>

                <fieldset className="rounded-lg border border-line p-3">
                  <legend className="px-1 text-xs font-semibold uppercase tracking-wide text-ink-muted">
                    Supervision policy
                  </legend>
                  <ul className="space-y-1">
                    {SUPERVISION_POLICY.map((line) => (
                      <li key={line} className="text-xs text-ink-muted">
                        · {line}
                      </li>
                    ))}
                  </ul>
                  <label className="mt-3 flex items-start gap-2 text-sm text-ink">
                    <input
                      type="checkbox"
                      checked={acknowledged}
                      onChange={(e) => setAcknowledged(e.target.checked)}
                      className="mt-0.5 accent-brand-500"
                    />
                    I acknowledge this policy for this session.
                  </label>
                </fieldset>

                <button
                  type="button"
                  onClick={begin}
                  disabled={!operator.trim() || !acknowledged || !acceptsSourcing(req)}
                  className={cn(
                    "rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                    operator.trim() && acknowledged && acceptsSourcing(req)
                      ? "bg-brand-600 text-white hover:bg-brand-500"
                      : "cursor-not-allowed border border-line text-ink-faint",
                  )}
                >
                  Start session
                </button>
              </div>
            </Card>
          ) : (
            <Card className="p-5">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
                <SectionTitle
                  hint={session.status === "paused" ? "paused" : `operator: ${session.operator}`}
                >
                  {session.status === "paused" ? "Session paused" : "Capture candidate"}
                </SectionTitle>
                <div className="flex gap-2">
                  {session.status === "in-progress" ? (
                    <button
                      type="button"
                      onClick={() => guarded(() => updateSession(pauseSession(session)))}
                      className="rounded-lg border border-line px-2.5 py-1.5 text-xs text-ink-muted transition-colors hover:text-ink"
                    >
                      Pause
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => guarded(() => updateSession(resumeSession(session)))}
                      className="rounded-lg bg-brand-600 px-2.5 py-1.5 text-xs font-medium text-white transition-colors hover:bg-brand-500"
                    >
                      Resume
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => guarded(() => updateSession(completeSession(session)))}
                    className="rounded-lg border border-line px-2.5 py-1.5 text-xs text-ink-muted transition-colors hover:text-ink"
                  >
                    Complete
                  </button>
                </div>
              </div>

              {session.status === "paused" ? (
                <p className="text-sm text-ink-muted">
                  Capture is suspended. Resume to continue recording candidates — the session and
                  its counts are preserved.
                </p>
              ) : (
                <CaptureForm
                  brief={brief}
                  pool={candidates}
                  disabled={false}
                  onCapture={capture}
                  onSkip={skip}
                />
              )}
            </Card>
          )}
        </section>

        <aside className="space-y-4">
          {counts && (
            <Card className="p-4">
              <p className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
                This session
              </p>
              <dl className="mt-3 grid grid-cols-2 gap-3">
                <Stat label="Captured" value={counts.captured} tone="advanced" />
                <Stat label="Skipped" value={counts.skipped} />
                <Stat label="Close calls" value={counts.closeCalls} tone="oversight" />
                <Stat
                  label="Capture rate"
                  value={counts.captureRate === null ? "—" : `${counts.captureRate}%`}
                />
              </dl>
              {counts.pauseCount > 0 && (
                <p className="mt-3 text-xs text-ink-faint">
                  Paused {counts.pauseCount} {counts.pauseCount === 1 ? "time" : "times"}
                </p>
              )}
            </Card>
          )}

          {session && (session.skipped?.length ?? 0) > 0 && (
            <Card className="p-4">
              <p className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
                Skipped this session
              </p>
              <ul className="mt-2 space-y-1.5">
                {(session.skipped ?? []).slice().reverse().map((s) => (
                  <li key={s.id} className="text-xs">
                    <span className="text-ink">{s.name}</span>
                    {s.closeCall && (
                      <span className="ml-1 rounded border border-oversight-line bg-oversight-soft px-1 text-[10px] text-oversight">
                        close call
                      </span>
                    )}
                    {s.reason && <p className="text-ink-faint">{s.reason}</p>}
                  </li>
                ))}
              </ul>
            </Card>
          )}

          <div>
            <SectionTitle hint={`${history.length}`}>Session history</SectionTitle>
            {history.length === 0 ? (
              <EmptyState title="No sessions yet" body="Start one to begin sourcing this req." />
            ) : (
              <ul className="space-y-2">
                {history.map((s) => {
                  const c = sessionCounts(s);
                  return (
                    <li key={s.id}>
                      <Card className="px-4 py-3">
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate text-sm text-ink">{s.operator}</span>
                          <span
                            className={cn(
                              "shrink-0 rounded-full border px-2 py-0.5 text-[10px] capitalize",
                              s.status === "ended"
                                ? "border-line text-ink-faint"
                                : "border-brand-500/30 bg-brand-500/10 text-brand-200",
                            )}
                          >
                            {s.status}
                          </span>
                        </div>
                        <p className="mt-1 text-xs text-ink-faint">
                          {c.captured} captured · {c.skipped} skipped
                          {s.briefVersion !== undefined && ` · brief v${s.briefVersion}`}
                        </p>
                      </Card>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
};

const Stat: FC<{ label: string; value: number | string; tone?: "advanced" | "oversight" }> = ({
  label,
  value,
  tone,
}) => (
  <div>
    <dt className="text-[10px] uppercase tracking-wide text-ink-faint">{label}</dt>
    <dd
      className={cn(
        "text-lg font-semibold tabular-nums",
        tone === "advanced" ? "text-stage-advanced" : tone === "oversight" ? "text-oversight" : "text-ink",
      )}
    >
      {value}
    </dd>
  </div>
);

export default SourcingSessionPage;
