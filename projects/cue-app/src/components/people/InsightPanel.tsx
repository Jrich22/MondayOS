import type { PersonInsight } from "@/lib/person-ai";
import { SparklesIcon } from "@/components/icons";
import { cn } from "@/lib/cn";

/**
 * The AI relationship read — an offline, derived summary plus the specific
 * signals worth knowing before you say hello (see lib/person-ai). This is the
 * payoff of remembering people across events; it deliberately reads like a
 * briefing, not a data dump.
 */

const KIND_DOT: Record<PersonInsight["kind"], string> = {
  history: "bg-ink-faint",
  affinity: "bg-brand-400",
  relationship: "bg-status-live",
  reliability: "bg-status-upcoming",
  recommendation: "bg-status-draft",
};

export function InsightPanel({
  summary,
  insights,
}: {
  summary: string;
  insights: PersonInsight[];
}) {
  return (
    <section className="card overflow-hidden">
      <div className="flex items-center gap-2 border-b border-line bg-brand-sheen/40 px-5 py-3">
        <span className="grid h-7 w-7 place-items-center rounded-lg bg-brand-500/20 text-brand-300">
          <SparklesIcon width={16} height={16} />
        </span>
        <div>
          <p className="text-sm font-semibold text-ink">AI relationship summary</p>
          <p className="text-[11px] text-ink-faint">Derived offline from event history</p>
        </div>
      </div>
      <div className="space-y-4 p-5">
        <p className="text-sm leading-relaxed text-ink-muted">{summary}</p>
        {insights.length > 0 && (
          <ul className="space-y-2">
            {insights.map((i) => (
              <li key={i.id} className="flex items-start gap-2.5 text-sm text-ink">
                <span className={cn("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", KIND_DOT[i.kind])} />
                <span>{i.text}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
