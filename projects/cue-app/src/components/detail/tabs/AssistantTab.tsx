import type { CueEvent } from "@/lib/types";
import { AiPanel } from "@/components/detail/AiPanel";
import { Panel } from "@/components/detail/Panel";

/**
 * The AI Assistant tab — the expanded workspace version of the persistent rail.
 * Same actions, more room to review drafts. Cue stays an operations tool; this
 * is a helper the manager drives, not an autopilot.
 */
export function AssistantTab({ event }: { event: CueEvent }) {
  return (
    <div className="max-w-3xl">
      <Panel>
        <AiPanel event={event} variant="expanded" />
      </Panel>
    </div>
  );
}
