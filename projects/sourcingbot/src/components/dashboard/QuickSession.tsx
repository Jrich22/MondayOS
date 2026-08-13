/**
 * Inline session start — begin real work without leaving the homepage.
 *
 * The supervision gate is reproduced here IN FULL, not abbreviated for
 * convenience. `startSession` still refuses without a named operator, a
 * per-session acknowledgement, and an open requisition, and this surface makes
 * no attempt to route around it: the whole point of the boundary is that no
 * caller gets a shortcut, least of all the one designed for speed.
 *
 * Once started, the recruiter goes to the session surface to capture — that
 * needs the full form. What the homepage removes is the navigation *before* the
 * decision, not the supervision.
 */
import { useState, type FC } from "react";
import { useNavigate } from "react-router-dom";
import type { Req, SourcingBrief } from "@/lib/types";
import { SUPERVISION_POLICY, SupervisionRequiredError, startSession } from "@/lib/linkedin";
import { acceptsSourcing } from "@/lib/req";
import { addSession } from "@/lib/store";
import { Card, cn } from "@/components/ui/Primitives";

export const QuickSession: FC<{
  req: Req;
  brief: SourcingBrief | undefined;
  onCancel: () => void;
}> = ({ req, brief, onCancel }) => {
  const navigate = useNavigate();
  const [operator, setOperator] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState("");

  const open = acceptsSourcing(req);
  const ready = operator.trim() !== "" && acknowledged && open;

  const begin = () => {
    try {
      setError("");
      const session = startSession({
        reqId: req.id,
        operator,
        acknowledgedPolicy: acknowledged,
        reqAcceptsSourcing: open,
        briefVersion: brief?.version,
      });
      addSession(session);
      navigate(`/reqs/${req.id}/session`);
    } catch (e) {
      setError(e instanceof SupervisionRequiredError ? e.message : "Could not start the session.");
    }
  };

  return (
    <Card className="border-brand-500/30 p-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-sm font-medium text-ink">
            Start sourcing · <span className="font-mono text-xs text-ink-faint">{req.code}</span>
          </p>
          <p className="text-xs text-ink-muted">{req.title}</p>
        </div>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg border border-line px-2.5 py-1 text-xs text-ink-faint transition-colors hover:text-ink"
        >
          Cancel
        </button>
      </div>

      {!open && (
        <p className="mb-3 rounded-lg border border-oversight-line bg-oversight-soft px-3 py-2 text-xs text-oversight">
          This requisition is <strong>{req.status}</strong>. Open it for sourcing first.
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
        <div className="space-y-1.5">
          <label htmlFor="quick-operator" className="block text-xs font-medium text-ink">
            Your name
          </label>
          <input
            id="quick-operator"
            type="text"
            value={operator}
            onChange={(e) => setOperator(e.target.value)}
            placeholder="Dana Whitfield"
            className="w-full rounded-lg border border-line bg-canvas px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-brand-500 focus:outline-none"
          />
        </div>
        <button
          type="button"
          onClick={begin}
          disabled={!ready}
          className={cn(
            "rounded-lg px-3 py-2 text-sm font-medium transition-colors",
            ready
              ? "bg-brand-600 text-white hover:bg-brand-500"
              : "cursor-not-allowed border border-line text-ink-faint",
          )}
        >
          Start session
        </button>
      </div>

      <fieldset className="mt-3 rounded-lg border border-line p-3">
        <legend className="px-1 text-[10px] font-semibold uppercase tracking-wide text-ink-muted">
          Supervision policy
        </legend>
        <ul className="space-y-0.5">
          {SUPERVISION_POLICY.map((line) => (
            <li key={line} className="text-[11px] text-ink-muted">
              · {line}
            </li>
          ))}
        </ul>
        <label className="mt-2 flex items-start gap-2 text-xs text-ink">
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(e) => setAcknowledged(e.target.checked)}
            className="mt-0.5 accent-brand-500"
          />
          I acknowledge this policy for this session.
        </label>
      </fieldset>

      {error && (
        <p role="alert" className="mt-2 text-xs text-stage-rejected">
          {error}
        </p>
      )}
    </Card>
  );
};
