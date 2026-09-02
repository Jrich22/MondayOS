/**
 * What Monday actually did, most recent first.
 *
 * Every entry comes from a real operation recorded at the point the work
 * happened — assembling context, calling a provider, saving a conversation,
 * creating a task. Nothing is synthesised to make the panel look busy. A feed
 * that invents plausible steps is worse than no feed, because it teaches the
 * operator to trust a display that is not measuring anything.
 *
 * Compact by design: this is a glance, not an audit log. The durable record is
 * the conversation and the context snapshot.
 */

import type { ActivityEvent } from "./types";

const KIND_TONE: Record<ActivityEvent["kind"], string> = {
  context: "bg-accent-violet",
  knowledge: "bg-accent-magenta",
  task: "bg-status-executing",
  provider: "bg-brand-400",
  persist: "bg-ink-faint",
  error: "bg-status-blocked",
};

function clockOf(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function ActivityTimeline({ events }: { events: ActivityEvent[] }) {
  return (
    <section className="max-h-[240px] shrink-0 overflow-y-auto border-t border-line">
      <h2 className="sticky top-0 bg-canvas-raised/90 px-3 py-2 text-[10px] uppercase tracking-wider text-ink-faint backdrop-blur">
        Activity
      </h2>
      {events.length === 0 ? (
        <p className="px-3 pb-3 text-[11px] text-ink-faint">Nothing yet this session.</p>
      ) : (
        <ol className="px-3 pb-3">
          {events.map((e, i) => (
            <li key={`${e.at}-${i}`} className="flex gap-2 py-1">
              <span
                className={`mt-[5px] h-1.5 w-1.5 shrink-0 rounded-full ${
                  e.ok ? KIND_TONE[e.kind] : "bg-status-blocked"
                }`}
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate text-[11px] text-ink-muted">{e.message}</span>
                  <span className="shrink-0 font-mono text-[9px] text-ink-faint/70">
                    {clockOf(e.at)}
                  </span>
                </div>
                {e.detail && (
                  <div className="truncate text-[10px] text-ink-faint" title={e.detail}>
                    {e.detail}
                  </div>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
