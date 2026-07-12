import {
  STATUS_META,
  audienceLabel,
  openRate,
  pct,
  type Campaign,
} from "@/lib/comms";
import { PlusIcon } from "@/components/icons";
import { cn } from "@/lib/cn";

/**
 * The campaigns in the selected stage, as a horizontal strip of selectable
 * cards, with a "New campaign" affordance. Each card carries the one signal that
 * matters at a glance: status, audience, and — once sent — open rate.
 */
export function CampaignList({
  campaigns,
  selectedId,
  onSelect,
  onNew,
}: {
  campaigns: Campaign[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
}) {
  return (
    <div className="flex gap-2.5 overflow-x-auto pb-1">
      {campaigns.map((c) => {
        const meta = STATUS_META[c.status];
        const active = c.id === selectedId;
        return (
          <button
            key={c.id}
            onClick={() => onSelect(c.id)}
            className={cn(
              "focus-ring group flex w-52 shrink-0 flex-col rounded-xl border p-3 text-left transition-colors",
              active
                ? "border-brand-500/50 bg-brand-500/[0.08]"
                : "border-line bg-canvas-raised hover:border-line-strong",
            )}
          >
            <div className="flex items-center gap-1.5">
              <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", meta.dot)} />
              <span className={cn("text-[10px] font-semibold uppercase tracking-wide", meta.text)}>
                {meta.label}
              </span>
              {c.status === "sent" && (
                <span className="ml-auto text-[10px] font-medium tabular-nums text-ink-faint">
                  {pct(openRate(c.metrics))} open
                </span>
              )}
            </div>
            <p className="mt-1.5 truncate text-sm font-medium text-ink">{c.title}</p>
            <p className="mt-0.5 truncate text-[11px] text-ink-faint">
              {audienceLabel(c.audience, c.audienceTag)}
            </p>
          </button>
        );
      })}

      <button
        onClick={onNew}
        className="focus-ring flex w-40 shrink-0 flex-col items-center justify-center gap-1.5 rounded-xl border border-dashed border-line py-3 text-ink-muted transition-colors hover:border-line-strong hover:text-ink"
      >
        <PlusIcon width={18} height={18} />
        <span className="text-xs font-medium">New campaign</span>
      </button>
    </div>
  );
}
