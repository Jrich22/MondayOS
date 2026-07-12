import { Link } from "react-router-dom";
import type { TimelineEntry, TimelineKind } from "@/lib/person-timeline";
import { formatEventDate } from "@/lib/format";
import { cn } from "@/lib/cn";
import {
  MailIcon,
  CheckIcon,
  StarIcon,
  MicIcon,
  LinkIcon,
  ListIcon,
  HistoryIcon,
} from "@/components/icons";
import type { ReactNode } from "react";

/**
 * The relationship timeline — a person's whole history with the firm as one
 * chronological thread, grouped by event, newest first. Every event contributes
 * a run of funnel entries; future interactions simply append. Read-only and pure
 * over the entries from lib/person-timeline.
 */

const KIND_META: Record<TimelineKind, { icon: ReactNode; tone: string }> = {
  invited: { icon: <MailIcon width={13} height={13} />, tone: "text-ink-faint bg-white/[0.04]" },
  rsvp: { icon: <CheckIcon width={13} height={13} />, tone: "text-status-upcoming bg-status-upcoming/10" },
  checkedin: { icon: <CheckIcon width={13} height={13} />, tone: "text-status-live bg-status-live/10" },
  vip: { icon: <StarIcon width={13} height={13} />, tone: "text-status-draft bg-status-draft/10" },
  speaker: { icon: <MicIcon width={13} height={13} />, tone: "text-brand-300 bg-brand-500/10" },
  met: { icon: <LinkIcon width={13} height={13} />, tone: "text-brand-300 bg-brand-500/10" },
  thankyou: { icon: <MailIcon width={13} height={13} />, tone: "text-ink-muted bg-white/[0.04]" },
  survey: { icon: <ListIcon width={13} height={13} />, tone: "text-ink-muted bg-white/[0.04]" },
};

interface EventGroup {
  eventId: string;
  eventTitle: string;
  at: string;
  entries: TimelineEntry[];
}

/** Group consecutive entries by event, preserving the newest-first order. */
function groupByEvent(entries: TimelineEntry[]): EventGroup[] {
  const groups: EventGroup[] = [];
  for (const e of entries) {
    const last = groups[groups.length - 1];
    if (last && last.eventId === e.eventId) last.entries.push(e);
    else groups.push({ eventId: e.eventId, eventTitle: e.eventTitle, at: e.at, entries: [e] });
  }
  return groups;
}

export function TimelineView({ entries }: { entries: TimelineEntry[] }) {
  if (entries.length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-line px-4 py-6 text-center text-sm text-ink-muted">
        No interactions recorded yet.
      </p>
    );
  }

  const groups = groupByEvent(entries);

  return (
    <ol className="relative space-y-6 before:absolute before:left-[13px] before:top-2 before:bottom-2 before:w-px before:bg-line">
      {groups.map((group) => (
        <li key={group.eventId} className="relative pl-10">
          <span className="absolute left-0 top-0.5 grid h-7 w-7 place-items-center rounded-full border border-line bg-canvas-raised text-ink-faint">
            <HistoryIcon width={14} height={14} />
          </span>
          <div className="flex flex-wrap items-baseline justify-between gap-x-3">
            <Link
              to={`/events/${group.eventId}`}
              className="focus-ring text-sm font-semibold text-ink hover:text-brand-300"
            >
              {group.eventTitle}
            </Link>
            <span className="text-[11px] text-ink-faint">{formatEventDate(group.at)}</span>
          </div>
          <ul className="mt-2.5 space-y-1.5">
            {group.entries.map((e) => {
              const meta = KIND_META[e.kind];
              return (
                <li key={e.id} className="flex items-center gap-2.5 text-sm">
                  <span className={cn("grid h-5 w-5 shrink-0 place-items-center rounded-md", meta.tone)}>
                    {meta.icon}
                  </span>
                  <span className="text-ink">{e.label}</span>
                  {e.detail && <span className="text-xs text-ink-faint">· {e.detail}</span>}
                </li>
              );
            })}
          </ul>
        </li>
      ))}
    </ol>
  );
}
