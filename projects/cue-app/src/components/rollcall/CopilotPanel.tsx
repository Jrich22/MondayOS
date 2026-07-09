import { useMemo, useState } from "react";
import type { CueEvent, Guest } from "@/lib/types";
import {
  answerCopilot,
  COPILOT_PROMPTS,
  type CopilotPromptId,
  type CopilotAnswer,
} from "@/lib/rollcall-ai";
import { displayName, guestCompany, initials } from "@/lib/guests-select";
import { SparklesIcon } from "@/components/icons";
import { cn } from "@/lib/cn";

/**
 * The Roll Call AI Copilot panel. It "understands the event" by reading the live
 * roster and answering the suggested prompts deterministically — offline
 * templates only (lib/rollcall-ai), matching Cue's operations-first stance. No
 * network, no model; the answer is always correct for the room as it stands.
 */
export function CopilotPanel({
  event,
  guests,
  now,
}: {
  event: CueEvent;
  guests: Guest[];
  now: number;
}) {
  const [active, setActive] = useState<CopilotPromptId | null>(null);

  // Recompute against the live roster so answers stay current as people arrive.
  const answer = useMemo<CopilotAnswer | null>(
    () => (active ? answerCopilot(active, { guests, event, now }) : null),
    [active, guests, event, now],
  );
  const activeLabel = COPILOT_PROMPTS.find((p) => p.id === active)?.label;

  return (
    <section className="card p-4">
      <div className="mb-3 flex items-center gap-2">
        <span className="grid h-6 w-6 place-items-center rounded-md bg-brand-sheen text-brand-400">
          <SparklesIcon width={15} height={15} />
        </span>
        <div>
          <h2 className="text-sm font-semibold text-ink">AI Copilot</h2>
          <p className="text-[10px] text-ink-faint">Reads the live room · offline</p>
        </div>
      </div>

      {/* Suggested prompts */}
      <div className="flex flex-wrap gap-1.5">
        {COPILOT_PROMPTS.map((p) => (
          <button
            key={p.id}
            type="button"
            onClick={() => setActive(p.id)}
            className={cn(
              "focus-ring rounded-lg border px-2.5 py-1.5 text-left text-xs font-medium transition-colors",
              active === p.id
                ? "border-brand-500/50 bg-brand-500/15 text-brand-100"
                : "border-line text-ink-muted hover:border-line-strong hover:text-ink",
            )}
          >
            {p.label}
          </button>
        ))}
      </div>

      {/* Answer */}
      {answer && (
        <div className="mt-3 rounded-xl border border-line bg-canvas p-3">
          <p className="text-[11px] font-medium uppercase tracking-wide text-brand-300">{activeLabel}</p>
          <p className="mt-1.5 text-sm leading-relaxed text-ink">{answer.headline}</p>
          {answer.guests.length > 0 && (
            <ul className="mt-2.5 space-y-1.5 border-t border-line pt-2.5">
              {answer.guests.map((g) => (
                <li key={g.id} className="flex items-center gap-2.5">
                  <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-white/[0.06] text-[10px] font-semibold text-ink">
                    {initials(g)}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-medium text-ink">{displayName(g)}</span>
                    <span className="block truncate text-[11px] text-ink-faint">
                      {guestCompany(g, event.portfolio) || "—"}
                    </span>
                  </span>
                  {g.attendance.checkedIn ? (
                    <span className="shrink-0 text-[10px] font-medium text-status-live">In</span>
                  ) : (
                    <span className="shrink-0 text-[10px] font-medium text-ink-faint">Awaiting</span>
                  )}
                </li>
              ))}
              {answer.overflow > 0 && (
                <li className="pl-9 text-[11px] text-ink-faint">+{answer.overflow} more</li>
              )}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
