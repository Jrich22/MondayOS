/**
 * The working queue — the operating surface of the homepage.
 *
 * This is the difference between a dashboard and a workbench. A reporting page
 * lists what needs doing and links you elsewhere to do it; here the two most
 * common actions — moving a strong candidate forward, and ruling one out —
 * mutate real state in place, and the item disappears because the work
 * happened, not because it was hidden.
 *
 * There is deliberately no snooze or dismiss. Hiding an item is the reporting
 * instinct: it makes the queue shorter without making the pipeline better, and
 * a queue you can silence stops being trustworthy. Items leave when the
 * underlying record changes.
 *
 * Every mutation routes through the existing domain functions (`advance`,
 * `startSession`), so the stage graph and the supervision gate apply exactly as
 * they do anywhere else. Nothing here can move a candidate somewhere the domain
 * would refuse.
 */
import { useEffect, useRef, useState, type FC } from "react";
import { Link } from "react-router-dom";
import type { FocusAction, FocusItem, FocusKind } from "@/lib/intel";
import type { ReqCandidate } from "@/lib/types";
import { advance } from "@/lib/req-candidate";
import { ReqCandidateStageError } from "@/lib/req-candidate";
import { updateReqCandidate, useWorkspace } from "@/lib/store";
import { Card, EmptyState, cn } from "@/components/ui/Primitives";

const KIND_LABEL: Record<FocusKind, string> = {
  "strong-candidate": "Strong fit",
  "close-call": "Close call",
  "reuse-opportunity": "Possible duplicate",
  "thin-pipeline": "Thin pipeline",
  "weak-session": "Low capture rate",
  "stale-evaluation": "Needs reassessment",
};

const KIND_TONE: Record<FocusKind, string> = {
  "strong-candidate": "border-stage-advanced/30 bg-stage-advanced/10 text-stage-advanced",
  "close-call": "border-oversight-line bg-oversight-soft text-oversight",
  "reuse-opportunity": "border-brand-500/30 bg-brand-500/10 text-brand-200",
  "thin-pipeline": "border-stage-rejected/30 bg-stage-rejected/10 text-stage-rejected",
  "weak-session": "border-oversight-line bg-oversight-soft text-oversight",
  "stale-evaluation": "border-line-strong bg-white/5 text-ink-muted",
};

export const FocusQueue: FC<{
  items: FocusItem[];
  /** Opens the inline supervision gate for a req. */
  onStartSession: (reqId: string) => void;
}> = ({ items, onStartSession }) => {
  const { reqCandidates } = useWorkspace();
  const [worked, setWorked] = useState(0);
  const [error, setError] = useState("");
  const [cursor, setCursor] = useState(0);
  const listRef = useRef<HTMLUListElement>(null);

  // Keep the cursor inside the list as it drains beneath you.
  useEffect(() => {
    if (cursor > items.length - 1) setCursor(Math.max(0, items.length - 1));
  }, [items.length, cursor]);

  const byId = new Map(reqCandidates.map((rc) => [rc.id, rc]));

  const run = (action: FocusAction) => {
    setError("");
    try {
      if (action.type === "start-session") {
        onStartSession(action.reqId);
        return;
      }
      if (action.type === "navigate") return; // rendered as a link

      const rc: ReqCandidate | undefined = byId.get(action.reqCandidateId);
      if (!rc) {
        setError("That candidate is no longer on this requisition.");
        return;
      }
      const moved =
        action.type === "advance"
          ? advance(rc, action.to, "You", "Moved from the working queue")
          : advance(rc, "rejected", "You", "Ruled out from the working queue");
      updateReqCandidate(moved);
      setWorked((n) => n + 1);
    } catch (e) {
      // The domain refused — surface its reason rather than failing silently.
      setError(e instanceof ReqCandidateStageError ? e.message : "That action could not be applied.");
    }
  };

  // j/k to move, Enter to act — a queue you can work without reaching for a mouse.
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (items.length === 0) return;
    if (e.key === "j" || e.key === "ArrowDown") {
      e.preventDefault();
      setCursor((c) => Math.min(c + 1, items.length - 1));
    } else if (e.key === "k" || e.key === "ArrowUp") {
      e.preventDefault();
      setCursor((c) => Math.max(c - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      run(items[cursor].action);
    }
  };

  if (items.length === 0) {
    return (
      <EmptyState
        title={worked > 0 ? "Queue clear" : "Nothing needs you right now"}
        body={
          worked > 0
            ? `You worked ${worked} ${worked === 1 ? "item" : "items"}. No strong candidates waiting, no thin pipelines, nothing stale.`
            : "No strong candidates waiting, no thin pipelines, no stale evaluations. Start a sourcing session when you're ready."
        }
      />
    );
  }

  return (
    <div>
      {(worked > 0 || error) && (
        <div className="mb-2 flex flex-wrap items-center gap-2">
          {worked > 0 && (
            <p role="status" className="text-xs text-stage-advanced">
              {worked} {worked === 1 ? "item" : "items"} worked
            </p>
          )}
          {error && (
            <p role="alert" className="text-xs text-stage-rejected">
              {error}
            </p>
          )}
        </div>
      )}

      <ul
        ref={listRef}
        tabIndex={0}
        role="list"
        aria-label="Working queue — j and k to move, Enter to act"
        onKeyDown={onKeyDown}
        className="space-y-2 rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-brand-400"
      >
        {items.map((item, i) => (
          <li key={item.id}>
            <Card
              className={cn(
                "transition-colors",
                i === cursor ? "border-brand-500/40 bg-brand-500/[0.03]" : "hover:border-line-strong",
              )}
            >
              <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
                <div className="flex min-w-0 flex-1 items-start gap-3">
                  <span
                    className={cn(
                      "mt-0.5 shrink-0 rounded-md border px-2 py-0.5 text-[11px] font-medium",
                      KIND_TONE[item.kind],
                    )}
                  >
                    {KIND_LABEL[item.kind]}
                  </span>
                  <div className="min-w-0">
                    <Link
                      to={item.href}
                      className="block truncate text-sm font-medium text-ink hover:text-brand-200"
                    >
                      {item.title}
                    </Link>
                    <p className="truncate text-xs text-ink-muted">{item.reason}</p>
                  </div>
                </div>

                <div className="flex shrink-0 items-center gap-1.5">
                  {item.secondary && <ActionButton action={item.secondary} onRun={run} subdued />}
                  <ActionButton action={item.action} onRun={run} />
                </div>
              </div>
            </Card>
          </li>
        ))}
      </ul>

      <p className="mt-2 text-[11px] text-ink-faint">
        Focus the queue and use <kbd className="rounded border border-line px-1">j</kbd>{" "}
        <kbd className="rounded border border-line px-1">k</kbd> to move,{" "}
        <kbd className="rounded border border-line px-1">Enter</kbd> to act.
      </p>
    </div>
  );
};

const ActionButton: FC<{
  action: FocusAction;
  onRun: (a: FocusAction) => void;
  subdued?: boolean;
}> = ({ action, onRun, subdued }) => {
  const className = cn(
    "shrink-0 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors",
    subdued
      ? "border border-line text-ink-faint hover:border-stage-rejected/40 hover:text-stage-rejected"
      : "bg-brand-600 text-white hover:bg-brand-500",
  );

  if (action.type === "navigate") {
    return (
      <Link
        to={action.href}
        className={cn(
          "shrink-0 rounded-lg border border-line px-2.5 py-1.5 text-xs text-ink-muted transition-colors hover:border-brand-500/40 hover:text-brand-200",
        )}
      >
        {action.label}
      </Link>
    );
  }

  return (
    <button type="button" onClick={() => onRun(action)} className={className}>
      {action.label}
    </button>
  );
};
