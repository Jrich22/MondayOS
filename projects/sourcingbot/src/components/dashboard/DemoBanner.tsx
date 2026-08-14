/**
 * Demo-data banner.
 *
 * Shown whenever the workspace is still seeded content. It is not decoration:
 * a dashboard full of names, capture rates and session history is exactly the
 * kind of surface a viewer assumes is real. Saying so once, plainly, at the top
 * is the honest cost of shipping a populated demo.
 */
import type { FC } from "react";
import { cn } from "@/components/ui/Primitives";

export const DemoBanner: FC<{ className?: string }> = ({ className }) => (
  <div
    role="status"
    className={cn(
      "flex flex-wrap items-center gap-x-2 gap-y-1 rounded-xl border border-oversight-line bg-oversight-soft px-4 py-2.5",
      className,
    )}
  >
    <span className="rounded-md border border-oversight-line bg-oversight/15 px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-oversight">
      Demo data
    </span>
    <p className="text-xs text-ink-muted">
      Every requisition, person and sourcing session below is{" "}
      <strong className="text-ink">synthetic</strong> — invented for review. No
      real recruiter activity is shown. Your first saved change replaces all of it.
    </p>
  </div>
);
