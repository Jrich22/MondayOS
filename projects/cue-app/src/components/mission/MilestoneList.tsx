import { Link } from "react-router-dom";
import type { Milestone, MilestoneKind } from "@/lib/mission";
import { relativeDay, formatEventDate } from "@/lib/format";
import { cn } from "@/lib/cn";

const DOT: Record<MilestoneKind, string> = {
  wrap: "bg-status-live",
  doors: "bg-status-upcoming",
  draft: "bg-status-draft",
};

/**
 * Upcoming milestones — the next things on the clock across the portfolio
 * (events wrapping up, doors opening, drafts to finalize), rendered as a compact
 * timeline. Derived and ordered in lib/mission.
 */
export function MilestoneList({ milestones }: { milestones: Milestone[] }) {
  return (
    <section className="card p-5">
      <p className="text-sm font-semibold text-ink">Upcoming milestones</p>
      {milestones.length === 0 ? (
        <p className="mt-3 text-xs text-ink-muted">Nothing scheduled ahead.</p>
      ) : (
        <ul className="mt-3 space-y-1">
          {milestones.map((m) => (
            <li key={m.id}>
              <Link
                to={`/events/${m.eventId}`}
                className="focus-ring group flex items-center gap-3 rounded-lg px-1.5 py-2 transition-colors hover:bg-white/[0.03]"
              >
                <span className="relative flex h-2 w-2 shrink-0">
                  <span className={cn("h-2 w-2 rounded-full", DOT[m.kind])} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-ink">
                    {m.eventTitle}
                  </span>
                  <span className="block text-[11px] text-ink-faint">
                    {m.label} · {formatEventDate(m.at)}
                  </span>
                </span>
                <span className="shrink-0 text-xs font-medium text-ink-muted">
                  {relativeDay(m.at)}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
