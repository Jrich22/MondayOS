import { Link } from "react-router-dom";
import type { Recommendation } from "@/lib/mission";
import { SparklesIcon, ArrowRightIcon } from "@/components/icons";

/**
 * The AI Briefing — a contextual, always-offline read of what needs the
 * organizer next, computed deterministically from the live state (see
 * lib/mission `aiBriefing`). It advises; it never acts on its own, matching
 * Cue's "operations tool first, AI as helper" stance.
 */
export function BriefingPanel({ recommendations }: { recommendations: Recommendation[] }) {
  return (
    <section className="card p-5">
      <div className="flex items-center gap-2.5">
        <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand-sheen text-brand-400">
          <SparklesIcon width={18} height={18} />
        </span>
        <div>
          <p className="text-sm font-semibold text-ink">AI Briefing</p>
          <p className="text-[11px] text-ink-faint">Read from the live room · nothing sent</p>
        </div>
      </div>

      <ol className="mt-4 space-y-2.5">
        {recommendations.length === 0 && (
          <li className="rounded-xl border border-dashed border-line px-3 py-4 text-center text-xs text-ink-faint">
            No recommendations right now — the portfolio is quiet.
          </li>
        )}
        {recommendations.map((r, i) => (
          <li
            key={r.id}
            className="rounded-xl border border-line bg-canvas px-3.5 py-3 transition-colors hover:border-line-strong"
          >
            <div className="flex items-start gap-2.5">
              <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-md bg-brand-500/10 text-[11px] font-semibold text-brand-300">
                {i + 1}
              </span>
              <div className="min-w-0">
                <p className="text-sm font-medium leading-snug text-ink">{r.headline}</p>
                <p className="mt-0.5 text-xs leading-relaxed text-ink-muted">{r.detail}</p>
                {r.action && (
                  <Link
                    to={r.action.to}
                    className="focus-ring mt-2 inline-flex items-center gap-1 rounded-md text-xs font-medium text-brand-300 hover:text-brand-200"
                  >
                    {r.action.label}
                    <ArrowRightIcon width={13} height={13} />
                  </Link>
                )}
              </div>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
