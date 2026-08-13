/**
 * Readiness panel — the recruiter's answer to "is this req good enough yet?"
 *
 * Shows completeness as a ring and readiness as a separate, explicit verdict.
 * They are not the same question and are not merged into one number: a req can
 * be 90% complete and unable to source (no must-haves), which a single
 * percentage would hide at exactly the moment it matters.
 */
import type { FC } from "react";
import type { ReqReadiness, SectionId } from "@/lib/readiness";
import { completenessTone } from "@/lib/readiness";
import { cn } from "@/components/ui/Primitives";

const RING_TONE = {
  high: "text-stage-advanced",
  medium: "text-brand-400",
  low: "text-oversight",
} as const;

const CompletenessRing: FC<{ pct: number }> = ({ pct }) => {
  const tone = completenessTone(pct);
  const r = 34;
  const circumference = 2 * Math.PI * r;
  return (
    <div className="relative h-24 w-24 shrink-0">
      <svg viewBox="0 0 80 80" className="h-24 w-24 -rotate-90" aria-hidden>
        <circle cx="40" cy="40" r={r} fill="none" strokeWidth="7" className="stroke-line" />
        <circle
          cx="40"
          cy="40"
          r={r}
          fill="none"
          strokeWidth="7"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - pct / 100)}
          className={cn("transition-all duration-500", RING_TONE[tone])}
          stroke="currentColor"
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-xl font-semibold tabular-nums text-ink">{pct}%</span>
        <span className="text-[10px] uppercase tracking-wide text-ink-faint">complete</span>
      </div>
    </div>
  );
};

export const ReadinessPanel: FC<{
  readiness: ReqReadiness;
  activeSection: SectionId;
  onJump: (id: SectionId) => void;
}> = ({ readiness, activeSection, onJump }) => (
  <div className="space-y-4">
    <div
      className="flex items-center gap-4 rounded-xl border border-line bg-canvas-raised p-4"
      role="status"
      aria-label={`Requisition ${readiness.completeness} percent complete`}
    >
      <CompletenessRing pct={readiness.completeness} />
      <div className="min-w-0">
        {readiness.sourcingReady ? (
          <>
            <p className="text-sm font-medium text-stage-advanced">Ready to source</p>
            <p className="mt-0.5 text-xs text-ink-muted">
              This req can discriminate between candidates.
            </p>
          </>
        ) : (
          <>
            <p className="text-sm font-medium text-oversight">Not ready to source</p>
            <p className="mt-0.5 text-xs text-ink-muted">
              {readiness.blockers.length}{" "}
              {readiness.blockers.length === 1 ? "essential item" : "essential items"} outstanding.
            </p>
          </>
        )}
      </div>
    </div>

    <nav aria-label="Requisition sections">
      <ul className="space-y-1">
        {readiness.sections.map((s) => {
          const pct = Math.round(s.progress * 100);
          const done = s.progress === 1;
          return (
            <li key={s.id}>
              <button
                type="button"
                onClick={() => onJump(s.id)}
                aria-current={activeSection === s.id ? "true" : undefined}
                className={cn(
                  "w-full rounded-lg px-3 py-2 text-left transition-colors",
                  activeSection === s.id
                    ? "bg-brand-500/10 ring-1 ring-brand-500/25"
                    : "hover:bg-white/5",
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span
                    className={cn(
                      "truncate text-sm",
                      activeSection === s.id ? "text-brand-200" : "text-ink-muted",
                    )}
                  >
                    {s.label}
                    {s.essential && (
                      <span className="ml-1 text-[10px] uppercase text-ink-faint">required</span>
                    )}
                  </span>
                  <span
                    className={cn(
                      "shrink-0 text-xs tabular-nums",
                      done ? "text-stage-advanced" : "text-ink-faint",
                    )}
                  >
                    {done ? "✓" : `${pct}%`}
                  </span>
                </div>
                <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-line">
                  <div
                    className={cn(
                      "h-full rounded-full transition-all duration-500",
                      done ? "bg-stage-advanced" : s.essential ? "bg-oversight" : "bg-brand-500",
                    )}
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </button>
            </li>
          );
        })}
      </ul>
    </nav>

    {readiness.blockers.length > 0 && (
      <div className="rounded-xl border border-oversight-line bg-oversight-soft p-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-oversight">
          Before sourcing
        </p>
        <ul className="mt-1.5 space-y-1">
          {readiness.blockers.map((b) => (
            <li key={b} className="text-xs text-ink-muted">
              · {b}
            </li>
          ))}
        </ul>
      </div>
    )}

    {readiness.suggestions.length > 0 && (
      <details className="rounded-xl border border-line bg-canvas-raised p-3">
        <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Strengthen this req ({readiness.suggestions.length})
        </summary>
        <ul className="mt-2 space-y-1">
          {readiness.suggestions.map((s) => (
            <li key={s} className="text-xs text-ink-faint">
              · {s}
            </li>
          ))}
        </ul>
      </details>
    )}
  </div>
);
