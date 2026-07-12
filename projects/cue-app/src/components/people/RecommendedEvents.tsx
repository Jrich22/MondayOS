import { Link } from "react-router-dom";
import type { EventRecommendation } from "@/lib/person-ai";
import { formatEventDate, STATUS_META } from "@/lib/format";
import { SparklesIcon, ArrowRightIcon } from "@/components/icons";

/**
 * Where to invite this person next — upcoming events scored against their
 * history, each with a plain-language "why" (see lib/person-ai). The forward-
 * looking half of relationship intelligence: not who they are, but what to do.
 */
export function RecommendedEvents({ recommendations }: { recommendations: EventRecommendation[] }) {
  if (recommendations.length === 0) return null;

  return (
    <section className="card p-5">
      <div className="flex items-center gap-2">
        <SparklesIcon width={16} height={16} className="text-brand-400" />
        <h3 className="text-sm font-semibold text-ink">Recommended events</h3>
      </div>
      <ul className="mt-3 space-y-2">
        {recommendations.map(({ event, reason }) => {
          const meta = STATUS_META[event.status];
          return (
            <li key={event.id}>
              <Link
                to={`/events/${event.id}`}
                className="focus-ring group flex items-start gap-3 rounded-xl border border-line px-3 py-2.5 transition-colors hover:border-line-strong"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p className="truncate text-sm font-medium text-ink">{event.title}</p>
                    <span className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${meta.bg} ${meta.text}`}>
                      {meta.label}
                    </span>
                  </div>
                  <p className="mt-0.5 text-[11px] text-ink-faint">{formatEventDate(event.startsAt)}</p>
                  <p className="mt-1 text-xs text-ink-muted">{reason}.</p>
                </div>
                <ArrowRightIcon
                  width={14}
                  height={14}
                  className="mt-0.5 shrink-0 text-ink-faint opacity-0 transition-opacity group-hover:opacity-100"
                />
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
