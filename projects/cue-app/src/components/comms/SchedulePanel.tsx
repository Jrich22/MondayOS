import { useState } from "react";
import { STAGE_META, type Campaign } from "@/lib/comms";
import { formatEventDate } from "@/lib/format";
import { Button } from "@/components/ui/Button";
import { CalendarClockIcon, SendIcon, CloseIcon } from "@/components/icons";
import { cn } from "@/lib/cn";

/**
 * The Schedule panel — choose when a campaign goes out, or send it immediately.
 * Offers quick presets alongside an exact date/time, and surfaces the stage's
 * recommended timing relative to the event so the send lands at the right point
 * in the lifecycle. Scheduling and sending are the only writes; both flow back up
 * so the store stays the single source of truth.
 */
export function SchedulePanel({
  campaign,
  onSchedule,
  onSend,
  onUnschedule,
}: {
  campaign: Campaign;
  onSchedule: (iso: string) => void;
  onSend: () => void;
  onUnschedule: () => void;
}) {
  const [value, setValue] = useState<string>(() =>
    toLocalInput(campaign.scheduledAt ?? defaultWhen()),
  );

  const presets = buildPresets();

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="grid h-6 w-6 place-items-center rounded-md bg-white/[0.05] text-brand-400">
          <CalendarClockIcon width={15} height={15} />
        </span>
        <div>
          <h3 className="text-sm font-semibold text-ink">Schedule</h3>
          <p className="text-[10px] text-ink-faint">
            Recommended: {STAGE_META[campaign.stage].timing}
          </p>
        </div>
      </div>

      {/* Current state */}
      {campaign.status === "scheduled" && campaign.scheduledAt && (
        <div className="flex items-center gap-2 rounded-lg border border-status-upcoming/30 bg-status-upcoming/10 px-3 py-2">
          <span className="h-1.5 w-1.5 rounded-full bg-status-upcoming" />
          <p className="flex-1 text-xs text-ink">
            Scheduled for <span className="font-medium">{formatEventDate(campaign.scheduledAt)}</span>
          </p>
          <button
            onClick={onUnschedule}
            title="Cancel schedule"
            className="focus-ring grid h-6 w-6 place-items-center rounded text-ink-faint hover:text-ink"
          >
            <CloseIcon width={14} height={14} />
          </button>
        </div>
      )}
      {campaign.status === "sent" && campaign.sentAt && (
        <div className="flex items-center gap-2 rounded-lg border border-status-live/30 bg-status-live/10 px-3 py-2">
          <span className="h-1.5 w-1.5 rounded-full bg-status-live" />
          <p className="text-xs text-ink">
            Sent <span className="font-medium">{formatEventDate(campaign.sentAt)}</span>
          </p>
        </div>
      )}

      {/* Presets */}
      <div>
        <p className="mb-1.5 text-[11px] font-medium text-ink-muted">Quick presets</p>
        <div className="grid grid-cols-2 gap-1.5">
          {presets.map((p) => (
            <button
              key={p.label}
              onClick={() => setValue(toLocalInput(p.iso))}
              className={cn(
                "focus-ring rounded-lg border px-2.5 py-1.5 text-left text-xs transition-colors",
                toLocalInput(p.iso) === value
                  ? "border-brand-500/50 bg-brand-500/10 text-ink"
                  : "border-line text-ink-muted hover:border-line-strong hover:text-ink",
              )}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Exact time */}
      <div>
        <p className="mb-1.5 text-[11px] font-medium text-ink-muted">Exact date & time</p>
        <input
          type="datetime-local"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="focus-ring w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm text-ink [color-scheme:dark] hover:border-line-strong"
        />
      </div>

      <div className="flex flex-col gap-2 border-t border-line pt-3">
        <Button
          variant="outline"
          onClick={() => value && onSchedule(fromLocalInput(value))}
          disabled={!value}
        >
          <CalendarClockIcon width={16} height={16} />
          {campaign.status === "scheduled" ? "Update schedule" : "Schedule campaign"}
        </Button>
        <Button onClick={onSend}>
          <SendIcon width={16} height={16} />
          Send now instead
        </Button>
      </div>
    </div>
  );
}

interface Preset {
  label: string;
  iso: string;
}

/** A sensible default schedule: tomorrow at 9am local. */
function defaultWhen(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  d.setHours(9, 0, 0, 0);
  return d.toISOString();
}

function buildPresets(): Preset[] {
  const inHours = (h: number) => new Date(Date.now() + h * 3600 * 1000).toISOString();
  const tomorrowAt = (hour: number) => {
    const d = new Date();
    d.setDate(d.getDate() + 1);
    d.setHours(hour, 0, 0, 0);
    return d.toISOString();
  };
  const nextWeek = () => {
    const d = new Date();
    d.setDate(d.getDate() + 7);
    d.setHours(9, 0, 0, 0);
    return d.toISOString();
  };
  return [
    { label: "In 1 hour", iso: inHours(1) },
    { label: "Today/tomorrow 6pm", iso: todayOrTomorrowAt(18) },
    { label: "Tomorrow 9am", iso: tomorrowAt(9) },
    { label: "Next week", iso: nextWeek() },
  ];
}

/** 6pm today if still ahead, else 6pm tomorrow. */
function todayOrTomorrowAt(hour: number): string {
  const d = new Date();
  if (d.getHours() >= hour) d.setDate(d.getDate() + 1);
  d.setHours(hour, 0, 0, 0);
  return d.toISOString();
}

/** ISO → "YYYY-MM-DDTHH:mm" in local time, for the datetime-local input. */
function toLocalInput(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(
    d.getMinutes(),
  )}`;
}

/** Local datetime-local value → ISO. */
function fromLocalInput(value: string): string {
  return new Date(value).toISOString();
}
