/**
 * Monday's identity and live state.
 *
 * Scoped hard: this says who Monday is and what Monday is doing. Everything
 * about the *project* — task, branch, files, commits — lives in the context
 * panel on the right, because that is the panel that changes when you switch
 * projects.
 *
 * That division matters more than it sounds. Both rails used to show "Current
 * task", which meant the same fact rendered twice on one screen and two places
 * to keep correct. One rail answers "what is Monday doing", the other answers
 * "what does Monday know". Neither answers both.
 *
 * Nothing operational is drawn over the visualisation: the Brain conveys mood,
 * text conveys fact, and the two never overlap.
 */

import { MondayBrain } from "@/components/monday";
import { ACTIVITY, brainFor, type MondayActivity } from "./mondayState";

export function StatusRail({
  activity,
  project,
  provider,
  healthy,
  compact,
}: {
  activity: MondayActivity;
  project: string;
  provider: string;
  healthy: boolean;
  compact?: boolean;
}) {
  const presentation = ACTIVITY[activity];

  if (compact) {
    return (
      <div className="flex min-w-0 items-center gap-2.5">
        <div className="h-[18px] w-[18px] shrink-0 opacity-80">
          <MondayBrain state={brainFor(activity)} />
        </div>
        <div className="min-w-0">
          <div className="truncate text-[12px] text-ink">{project || "No project"}</div>
          <div className={`flex items-center gap-1.5 text-[10px] ${presentation.tone}`}>
            <span
              className={`h-1 w-1 rounded-full bg-current ${
                presentation.live ? "animate-pulse-soft" : ""
              }`}
            />
            {presentation.label}
          </div>
        </div>
      </div>
    );
  }

  return (
    <section className="border-b border-line px-4 py-4">
      <div className="flex items-center gap-3">
        {/* Ambient, not a subject. At 22px the brain is a presence you notice
            at the edge of vision when it changes, and stop seeing when it does
            not — which is the whole job. Anything larger becomes an object the
            eye returns to, competing with the conversation it exists to serve. */}
        <div className="h-[22px] w-[22px] shrink-0 opacity-85">
          <MondayBrain state={brainFor(activity)} />
        </div>
        <div className="min-w-0">
          <div className="text-[13px] font-medium tracking-tight text-ink">MONDAY</div>
          <div className="flex items-center gap-1.5 text-[10px] text-ink-faint">
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                healthy ? "bg-status-completed" : "bg-status-awaiting"
              }`}
            />
            {healthy ? "Healthy" : "Degraded"}
          </div>
        </div>
      </div>

      <div className="mt-3 flex items-baseline gap-1.5">
        <span
          className={`h-1 w-1 shrink-0 rounded-full bg-current ${
            presentation.live ? "animate-pulse-soft" : ""
          } ${presentation.tone}`}
        />
        <span className={`truncate text-[10px] ${presentation.tone}`}>{presentation.label}</span>
        {provider && (
          <span className="ml-auto shrink-0 truncate font-mono text-[9px] text-ink-faint/70">
            {provider}
          </span>
        )}
      </div>
    </section>
  );
}
