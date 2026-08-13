/**
 * Shared UI primitives. Kept in one file while the surface area is small —
 * split when a primitive earns its own module, matching Cue's components/ui.
 */
import type { FC, ReactNode } from "react";
import type { PipelineStage, ReqStatus } from "@/lib/types";

export const cn = (...parts: Array<string | false | null | undefined>): string =>
  parts.filter(Boolean).join(" ");

export const Card: FC<{ children: ReactNode; className?: string }> = ({ children, className }) => (
  <div
    className={cn(
      "rounded-xl border border-line bg-canvas-raised shadow-[0_1px_0_0_rgba(255,255,255,0.03)_inset]",
      className,
    )}
  >
    {children}
  </div>
);

export const SectionTitle: FC<{ children: ReactNode; hint?: string }> = ({ children, hint }) => (
  <div className="mb-3 flex items-baseline justify-between gap-3">
    <h2 className="text-sm font-semibold uppercase tracking-wider text-ink-muted">{children}</h2>
    {hint && <span className="text-xs text-ink-faint">{hint}</span>}
  </div>
);

const REQ_STATUS_STYLES: Record<ReqStatus, string> = {
  open: "bg-emerald-500/10 text-emerald-300 border-emerald-500/25",
  draft: "bg-slate-500/10 text-slate-300 border-slate-500/25",
  "on-hold": "bg-amber-500/10 text-amber-300 border-amber-500/25",
  closed: "bg-white/5 text-ink-faint border-line",
};

export const ReqStatusBadge: FC<{ status: ReqStatus }> = ({ status }) => (
  <span
    className={cn(
      "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium capitalize",
      REQ_STATUS_STYLES[status],
    )}
  >
    {status.replace("-", " ")}
  </span>
);

const STAGE_STYLES: Record<PipelineStage, string> = {
  identified: "bg-stage-identified/15 text-stage-identified border-stage-identified/30",
  reviewing: "bg-stage-reviewing/15 text-stage-reviewing border-stage-reviewing/30",
  contacted: "bg-stage-contacted/15 text-stage-contacted border-stage-contacted/30",
  responded: "bg-stage-responded/15 text-stage-responded border-stage-responded/30",
  advanced: "bg-stage-advanced/15 text-stage-advanced border-stage-advanced/30",
  rejected: "bg-stage-rejected/15 text-stage-rejected border-stage-rejected/30",
};

export const StageBadge: FC<{ stage: PipelineStage }> = ({ stage }) => (
  <span
    className={cn(
      "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium capitalize",
      STAGE_STYLES[stage],
    )}
  >
    {stage}
  </span>
);

export const FitScore: FC<{ score: number | null }> = ({ score }) => {
  if (score === null) {
    return <span className="text-xs text-ink-faint">Not scored</span>;
  }
  const tone =
    score >= 75 ? "text-stage-advanced" : score >= 40 ? "text-brand-400" : "text-stage-rejected";
  return (
    <span className={cn("text-sm font-semibold tabular-nums", tone)} title="Fit for this requisition">
      {score}
      <span className="text-xs font-normal text-ink-faint">/100</span>
    </span>
  );
};

export const EmptyState: FC<{ title: string; body: string }> = ({ title, body }) => (
  <div className="rounded-xl border border-dashed border-line px-6 py-10 text-center">
    <p className="text-sm font-medium text-ink">{title}</p>
    <p className="mx-auto mt-1 max-w-md text-sm text-ink-muted">{body}</p>
  </div>
);
