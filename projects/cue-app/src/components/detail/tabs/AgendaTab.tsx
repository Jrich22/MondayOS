import type { CueEvent } from "@/lib/types";
import { suggestAgenda, assistInputFromEvent } from "@/lib/ai";
import { Panel } from "@/components/detail/Panel";
import { Button } from "@/components/ui/Button";
import { SparklesIcon } from "@/components/icons";

/** A simple clock label for the placeholder timeline. */
function timeAt(startIso: string, offsetMin: number): string {
  const d = new Date(new Date(startIso).getTime() + offsetMin * 60 * 1000);
  return new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" }).format(d);
}

/**
 * Agenda — a vertical timeline. Sprint-1 shows a placeholder run-of-show
 * (seeded from the AI agenda suggestion) so the timeline component is real; the
 * editable, persisted agenda lands with the Run-of-Show builder (TASK-0025).
 */
export function AgendaTab({ event }: { event: CueEvent }) {
  const items = suggestAgenda(assistInputFromEvent(event));
  const slotMinutes = 30;

  return (
    <Panel
      title="Run of show"
      action={
        <Button variant="outline" disabled>
          <SparklesIcon width={16} height={16} />
          Regenerate
        </Button>
      }
    >
      <p className="mb-5 text-xs text-ink-faint">
        Placeholder timeline — editing and persistence arrive with the Run-of-Show builder.
      </p>
      <ol className="relative ml-2 border-l border-line">
        {items.map((item, i) => (
          <li key={i} className="mb-6 ml-6 last:mb-0">
            <span className="absolute -left-[7px] mt-1 h-3.5 w-3.5 rounded-full border-2 border-canvas bg-brand-500" />
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="text-sm font-medium text-ink">{item}</span>
              <span className="tabular-nums text-xs text-ink-faint">
                {timeAt(event.startsAt, i * slotMinutes)}
              </span>
            </div>
          </li>
        ))}
      </ol>
    </Panel>
  );
}
