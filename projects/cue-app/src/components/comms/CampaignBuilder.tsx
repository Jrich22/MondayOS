import type { CueEvent, Guest } from "@/lib/types";
import {
  audienceCount,
  audienceLabel,
  STAGE_META,
  STATUS_META,
  openRate,
  clickRate,
  responseRate,
  deliveryRate,
  pct,
  compact,
  type Campaign,
} from "@/lib/comms";
import { Button } from "@/components/ui/Button";
import { TextInput, TextArea } from "@/components/ui/Field";
import {
  UsersIcon,
  SparklesIcon,
  SendIcon,
  CalendarClockIcon,
  CopyIcon,
  TrashIcon,
  EyeIcon,
} from "@/components/icons";
import { CommsIcon } from "./commsIcons";
import { cn } from "@/lib/cn";

/**
 * The Campaign Builder — the center of the workspace. A focused authoring surface
 * for one campaign's title, audience, subject, and message, with lifecycle
 * actions (schedule / send) and, once sent, an inline performance readout. Not a
 * form dump: audience and AI live one tap away in the right panel, so the center
 * stays about the words.
 */
export function CampaignBuilder({
  campaign,
  event,
  guests,
  onPatch,
  onSend,
  onOpenAudience,
  onOpenSchedule,
  onOpenAssistant,
  onOpenPreview,
  onDuplicate,
  onDelete,
}: {
  campaign: Campaign;
  event: CueEvent;
  guests: Guest[];
  onPatch: (patch: Partial<Campaign>) => void;
  onSend: () => void;
  onOpenAudience: () => void;
  onOpenSchedule: () => void;
  onOpenAssistant: () => void;
  onOpenPreview: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
}) {
  const recipients = audienceCount(guests, campaign.audience, campaign.audienceTag);
  const meta = STATUS_META[campaign.status];
  const canSend =
    campaign.subject.trim().length > 0 && campaign.message.trim().length > 0 && recipients > 0;

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-start gap-3">
        <span className="mt-1 grid h-9 w-9 shrink-0 place-items-center rounded-xl border border-line bg-white/[0.03] text-brand-400">
          <CommsIcon name={STAGE_META[campaign.stage].icon} width={18} height={18} />
        </span>
        <div className="min-w-0 flex-1">
          <input
            value={campaign.title}
            onChange={(e) => onPatch({ title: e.target.value })}
            placeholder="Untitled campaign"
            className="focus-ring w-full truncate bg-transparent text-lg font-semibold text-ink placeholder:text-ink-faint"
          />
          <div className="mt-0.5 flex items-center gap-2 text-xs">
            <span className="inline-flex items-center gap-1.5">
              <span className={cn("h-1.5 w-1.5 rounded-full", meta.dot)} />
              <span className={cn("font-medium", meta.text)}>{meta.label}</span>
            </span>
            <span className="text-ink-faint">·</span>
            <span className="text-ink-muted">{STAGE_META[campaign.stage].label}</span>
            <span className="text-ink-faint">·</span>
            <span className="text-ink-faint">{STAGE_META[campaign.stage].timing}</span>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <IconButton title="Duplicate" onClick={onDuplicate}>
            <CopyIcon width={16} height={16} />
          </IconButton>
          <IconButton title="Delete" onClick={onDelete}>
            <TrashIcon width={16} height={16} />
          </IconButton>
        </div>
      </div>

      {/* Quick actions: audience + assist + preview */}
      <div className="mt-4 flex flex-wrap gap-2">
        <button
          onClick={onOpenAudience}
          className="focus-ring inline-flex items-center gap-2 rounded-xl border border-line bg-canvas-raised px-3 py-2 text-sm transition-colors hover:border-line-strong"
        >
          <UsersIcon width={16} height={16} className="text-brand-400" />
          <span className="font-medium text-ink">{audienceLabel(campaign.audience, campaign.audienceTag)}</span>
          <span className="rounded-md bg-white/[0.06] px-1.5 py-0.5 text-[11px] font-semibold tabular-nums text-ink-muted">
            {recipients}
          </span>
        </button>
        <button
          onClick={onOpenAssistant}
          className="focus-ring inline-flex items-center gap-2 rounded-xl border border-brand-500/30 bg-brand-500/[0.08] px-3 py-2 text-sm font-medium text-brand-200 transition-colors hover:bg-brand-500/[0.14]"
        >
          <SparklesIcon width={16} height={16} />
          Assist with AI
        </button>
        <button
          onClick={onOpenPreview}
          className="focus-ring inline-flex items-center gap-2 rounded-xl border border-line px-3 py-2 text-sm font-medium text-ink-muted transition-colors hover:border-line-strong hover:text-ink"
        >
          <EyeIcon width={16} height={16} />
          Preview
        </button>
      </div>

      {/* Fields */}
      <div className="mt-4 min-h-0 flex-1 space-y-3 overflow-y-auto pr-0.5">
        <div>
          <label className="mb-1.5 block text-xs font-medium text-ink-muted">Subject</label>
          <TextInput
            value={campaign.subject}
            onChange={(e) => onPatch({ subject: e.target.value })}
            placeholder={`e.g. You're invited: ${event.title}`}
          />
        </div>
        <div className="flex min-h-[16rem] flex-col">
          <label className="mb-1.5 block text-xs font-medium text-ink-muted">Message</label>
          <TextArea
            value={campaign.message}
            onChange={(e) => onPatch({ message: e.target.value })}
            placeholder="Write your message, or generate a draft with the AI assistant…"
            className="min-h-[14rem] flex-1 leading-relaxed"
          />
          <p className="mt-1.5 text-[11px] text-ink-faint">
            Personalize with{" "}
            <code className="rounded bg-white/[0.06] px-1 py-0.5 text-ink-muted">{"{{first_name}}"}</code>. Merged per recipient on send.
          </p>
        </div>

        {campaign.status === "sent" && <SentReadout campaign={campaign} />}
      </div>

      {/* Footer actions */}
      <div className="mt-3 flex items-center gap-2 border-t border-line pt-3">
        <p className="mr-auto text-[11px] text-ink-faint">
          {campaign.status === "sent"
            ? "Sent · edits create a new send"
            : "Saved as draft automatically"}
        </p>
        <Button variant="outline" onClick={onOpenSchedule}>
          <CalendarClockIcon width={16} height={16} />
          {campaign.status === "scheduled" ? "Reschedule" : "Schedule"}
        </Button>
        <Button onClick={onSend} disabled={!canSend} className={cn(!canSend && "cursor-not-allowed opacity-50")}>
          <SendIcon width={16} height={16} />
          {campaign.status === "sent" ? "Resend" : "Send now"}
        </Button>
      </div>
    </div>
  );
}

function IconButton({
  title,
  onClick,
  children,
}: {
  title: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      title={title}
      onClick={onClick}
      className="focus-ring grid h-8 w-8 place-items-center rounded-lg text-ink-faint transition-colors hover:bg-white/5 hover:text-ink"
    >
      {children}
    </button>
  );
}

/** Inline performance readout for a sent campaign — the per-campaign funnel. */
function SentReadout({ campaign }: { campaign: Campaign }) {
  const m = campaign.metrics;
  const tiles = [
    { label: "Recipients", value: compact(m.recipients) },
    { label: "Delivered", value: pct(deliveryRate(m)) },
    { label: "Opened", value: pct(openRate(m)) },
    { label: "Clicked", value: pct(clickRate(m)) },
    { label: "Responded", value: pct(responseRate(m)) },
  ];
  return (
    <div className="rounded-xl border border-line bg-white/[0.02] p-3">
      <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-ink-faint">
        Performance
      </p>
      <div className="grid grid-cols-5 gap-2">
        {tiles.map((t) => (
          <div key={t.label}>
            <p className="text-base font-semibold tabular-nums text-ink">{t.value}</p>
            <p className="text-[10px] text-ink-faint">{t.label}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
