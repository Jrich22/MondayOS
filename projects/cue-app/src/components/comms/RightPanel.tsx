import type { CueEvent, Guest } from "@/lib/types";
import type { Campaign } from "@/lib/comms";
import { PreviewPanel } from "./PreviewPanel";
import { AssistantPanel } from "./AssistantPanel";
import { AudiencePanel } from "./AudiencePanel";
import { SchedulePanel } from "./SchedulePanel";
import { HistoryPanel } from "./HistoryPanel";
import {
  EyeIcon,
  SparklesIcon,
  UsersIcon,
  CalendarClockIcon,
  ClockIcon,
} from "@/components/icons";
import { cn } from "@/lib/cn";

export type RightTab = "preview" | "assistant" | "audience" | "schedule" | "history";

const TABS: { id: RightTab; label: string; icon: JSX.Element }[] = [
  { id: "preview", label: "Preview", icon: <EyeIcon width={15} height={15} /> },
  { id: "assistant", label: "Assistant", icon: <SparklesIcon width={15} height={15} /> },
  { id: "audience", label: "Audience", icon: <UsersIcon width={15} height={15} /> },
  { id: "schedule", label: "Schedule", icon: <CalendarClockIcon width={15} height={15} /> },
  { id: "history", label: "History", icon: <ClockIcon width={15} height={15} /> },
];

/**
 * The workspace right panel: a tabbed context surface beside the builder —
 * Preview, AI Assistant, Audience, Schedule, and Communication History. Each tab
 * reads and writes the same selected campaign through the callbacks the page
 * provides, so the panel is pure composition.
 */
export function RightPanel({
  tab,
  onTab,
  campaign,
  event,
  guests,
  now,
  onPatch,
  onSend,
  onSchedule,
  onUnschedule,
}: {
  tab: RightTab;
  onTab: (tab: RightTab) => void;
  campaign: Campaign;
  event: CueEvent;
  guests: Guest[];
  now: number;
  onPatch: (patch: Partial<Campaign>) => void;
  onSend: () => void;
  onSchedule: (iso: string) => void;
  onUnschedule: () => void;
}) {
  return (
    <div className="flex h-full flex-col">
      {/* Tab strip */}
      <div className="flex shrink-0 gap-0.5 rounded-xl border border-line bg-canvas p-1">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => onTab(t.id)}
            className={cn(
              "focus-ring flex flex-1 flex-col items-center gap-1 rounded-lg py-1.5 text-[10px] font-medium transition-colors",
              tab === t.id ? "bg-white/[0.08] text-ink" : "text-ink-faint hover:text-ink",
            )}
            title={t.label}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </div>

      {/* Active panel */}
      <div className="mt-3 min-h-0 flex-1 overflow-y-auto pr-0.5">
        {tab === "preview" && <PreviewPanel campaign={campaign} event={event} guests={guests} />}
        {tab === "assistant" && (
          <AssistantPanel campaign={campaign} event={event} onApply={onPatch} />
        )}
        {tab === "audience" && (
          <AudiencePanel campaign={campaign} event={event} guests={guests} onPatch={onPatch} />
        )}
        {tab === "schedule" && (
          <SchedulePanel
            campaign={campaign}
            onSchedule={onSchedule}
            onSend={onSend}
            onUnschedule={onUnschedule}
          />
        )}
        {tab === "history" && <HistoryPanel event={event} guests={guests} now={now} />}
      </div>
    </div>
  );
}
