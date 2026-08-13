/**
 * Recommended focus — the ranked worklist. The reason this page exists.
 *
 * Every row carries three things a table cannot: what it is, WHY it surfaced,
 * and one action. Ranked by priority rather than grouped by type, because the
 * next thing to do is whatever matters most — not whatever category the
 * recruiter happens to scroll to first.
 */
import type { FC } from "react";
import { Link } from "react-router-dom";
import type { FocusItem, FocusKind } from "@/lib/intel";
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

export const FocusList: FC<{ items: FocusItem[] }> = ({ items }) => {
  if (items.length === 0) {
    return (
      <EmptyState
        title="Nothing needs you right now"
        body="No strong candidates waiting, no thin pipelines, no stale evaluations. Start a sourcing session when you're ready."
      />
    );
  }

  return (
    <ul className="space-y-2">
      {items.map((item) => (
        <li key={item.id}>
          <Card className="transition-colors hover:border-line-strong">
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
                  <p className="truncate text-sm font-medium text-ink">{item.title}</p>
                  <p className="truncate text-xs text-ink-muted">{item.reason}</p>
                </div>
              </div>
              <Link
                to={item.action.type === "navigate" ? item.action.href : item.href}
                className="shrink-0 rounded-lg border border-line px-2.5 py-1.5 text-xs text-ink-muted transition-colors hover:border-brand-500/40 hover:text-brand-200"
              >
                {item.action.label}
              </Link>
            </div>
          </Card>
        </li>
      ))}
    </ul>
  );
};
