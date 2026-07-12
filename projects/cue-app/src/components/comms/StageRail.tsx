import { STAGE_ORDER, STAGE_META, type CampaignStage } from "@/lib/comms";
import { CommsIcon } from "./commsIcons";
import { cn } from "@/lib/cn";

export type CommsView =
  | { type: "stage"; stage: CampaignStage }
  | { type: "templates" };

/**
 * The Communications workspace left navigation: the nine lifecycle stages in
 * timeline order, then Templates. Each stage shows a live campaign count. Renders
 * vertically for the desktop three-pane layout, and as a horizontal scroller on
 * narrow screens (`horizontal`), driven by the same selection callbacks.
 */
export function StageRail({
  view,
  counts,
  onSelectStage,
  onSelectTemplates,
  horizontal = false,
}: {
  view: CommsView;
  counts: Record<CampaignStage, number>;
  onSelectStage: (stage: CampaignStage) => void;
  onSelectTemplates: () => void;
  horizontal?: boolean;
}) {
  const isStage = (s: CampaignStage) => view.type === "stage" && view.stage === s;

  if (horizontal) {
    return (
      <div className="flex gap-1.5 overflow-x-auto pb-1">
        {STAGE_ORDER.map((s) => (
          <button
            key={s}
            onClick={() => onSelectStage(s)}
            className={cn(
              "focus-ring flex shrink-0 items-center gap-2 rounded-xl border px-3 py-2 text-sm font-medium transition-colors",
              isStage(s)
                ? "border-brand-500/50 bg-brand-500/15 text-ink"
                : "border-line text-ink-muted hover:border-line-strong hover:text-ink",
            )}
          >
            <CommsIcon name={STAGE_META[s].icon} width={16} height={16} />
            {STAGE_META[s].label}
            {counts[s] > 0 && <span className="text-xs text-ink-faint">{counts[s]}</span>}
          </button>
        ))}
        <button
          onClick={onSelectTemplates}
          className={cn(
            "focus-ring flex shrink-0 items-center gap-2 rounded-xl border px-3 py-2 text-sm font-medium transition-colors",
            view.type === "templates"
              ? "border-brand-500/50 bg-brand-500/15 text-ink"
              : "border-line text-ink-muted hover:border-line-strong hover:text-ink",
          )}
        >
          <CommsIcon name="document" width={16} height={16} />
          Templates
        </button>
      </div>
    );
  }

  return (
    <nav className="flex h-full flex-col">
      <p className="px-2 pb-2 text-[11px] font-semibold uppercase tracking-wide text-ink-faint">
        Lifecycle
      </p>
      <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto pr-1">
        {STAGE_ORDER.map((s) => (
          <button
            key={s}
            onClick={() => onSelectStage(s)}
            className={cn(
              "focus-ring group flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-sm font-medium transition-colors",
              isStage(s)
                ? "bg-white/[0.06] text-ink"
                : "text-ink-muted hover:bg-white/[0.03] hover:text-ink",
            )}
          >
            <span className={cn("shrink-0", isStage(s) ? "text-brand-400" : "text-ink-faint")}>
              <CommsIcon name={STAGE_META[s].icon} width={17} height={17} />
            </span>
            <span className="min-w-0 flex-1 truncate">{STAGE_META[s].label}</span>
            {counts[s] > 0 && (
              <span
                className={cn(
                  "shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-semibold tabular-nums",
                  isStage(s) ? "bg-white/10 text-ink" : "bg-white/[0.04] text-ink-faint",
                )}
              >
                {counts[s]}
              </span>
            )}
          </button>
        ))}
      </div>

      <div className="mt-2 border-t border-line pt-2">
        <button
          onClick={onSelectTemplates}
          className={cn(
            "focus-ring flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-sm font-medium transition-colors",
            view.type === "templates"
              ? "bg-white/[0.06] text-ink"
              : "text-ink-muted hover:bg-white/[0.03] hover:text-ink",
          )}
        >
          <span className={cn("shrink-0", view.type === "templates" ? "text-brand-400" : "text-ink-faint")}>
            <CommsIcon name="document" width={17} height={17} />
          </span>
          Templates
        </button>
      </div>
    </nav>
  );
}
