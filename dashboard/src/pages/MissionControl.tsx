import { useApp } from "@/state/store";
import { BrainStage } from "@/components/brain/BrainStage";
import { WorkspacePanel } from "@/workspace/WorkspacePanel";
import { AIWorkspace } from "@/aiworkspace/AIWorkspace";
import { CommandPalette } from "@/components/command/CommandPalette";
import { DemoBadge } from "@/common/DemoBadge";
import { ErrorBoundary } from "@/common/ErrorBoundary";
import { STATE_LABELS } from "@/components/monday";

/**
 * MondayOS Mission Control — the interactive operating-system dashboard.
 *
 * Monday's Brain is the persistent center + command surface; the orbital nodes
 * navigate; workspaces open beside the Brain without ever hiding it. The header
 * shows real system health and an always-visible provenance badge (LIVE vs DEMO
 * DATA). All behavior flows through the store → command pipeline → adapter, so
 * this file is pure composition.
 */
export function MissionControl() {
  const { state, brainState, connection, openCommand, navigate } = useApp();
  // The AI Workspace is a full surface, not a side panel: conversation needs the
  // width, and the Brain stage would leave it a 440px column.
  const aiWorkspace = state.section === "ai-workspace";
  const workspaceOpen = state.section !== "home" && !aiWorkspace;

  return (
    <div className="flex h-screen flex-col">
      {/* Top bar. */}
      <header className="flex shrink-0 items-center justify-between border-b border-line bg-canvas/70 px-5 py-3 backdrop-blur-md">
        <div className="flex items-center gap-3">
          <div className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-accent-cyan to-accent-violet text-sm font-bold text-canvas">M</div>
          <div>
            <div className="text-sm font-semibold tracking-tight">MondayOS</div>
            <div className="text-[11px] text-ink-faint">Mission Control · Interactive Brain</div>
          </div>
        </div>

        <div className="flex items-center gap-3 text-xs">
          <DemoBadge connection={connection} reason={state.demoReason} />
          {state.system ? (
            <>
              <span className="flex items-center gap-1.5 text-ink-muted">
                <span className={`h-2 w-2 rounded-full ${state.system.healthy ? "bg-status-completed" : "bg-status-blocked"} animate-pulse-soft`} />
                {state.system.healthy ? "Healthy" : "Degraded"}
              </span>
              <span className="hidden text-ink-faint sm:inline">v{state.system.version}</span>
            </>
          ) : (
            <span className="text-ink-faint">connecting…</span>
          )}
          <span className="hidden text-ink-faint md:inline" title="Brain reflects live OS state">
            Brain: {STATE_LABELS[brainState]}
          </span>
          <button
            onClick={() => navigate(aiWorkspace ? "home" : "ai-workspace")}
            className={`focus-ring rounded-lg border px-3 py-1.5 transition ${
              aiWorkspace
                ? "border-brand-400/50 bg-brand-500/10 text-brand-200"
                : "border-line bg-canvas-raised text-ink-muted hover:border-brand-400/50 hover:text-ink"
            }`}
          >
            AI Workspace
          </button>
          <button
            onClick={openCommand}
            className="focus-ring rounded-lg border border-line bg-canvas-raised px-3 py-1.5 text-ink-muted transition hover:border-brand-400/50 hover:text-ink"
          >
            Ask Monday <kbd className="ml-1 text-ink-faint">⌘K</kbd>
          </button>
        </div>
      </header>

      {/* Stage + workspace. */}
      <main className="relative flex min-h-0 flex-1">
        {aiWorkspace ? (
          <div className="min-h-0 min-w-0 flex-1">
            <ErrorBoundary label="AI Workspace">
              <AIWorkspace />
            </ErrorBoundary>
          </div>
        ) : (
          <div className="min-w-0 flex-1">
            <ErrorBoundary label="Brain stage">
              <BrainStage />
            </ErrorBoundary>
          </div>
        )}

        {workspaceOpen && (
          <div className="w-full max-w-[440px] shrink-0 border-l border-line bg-canvas-raised/60 backdrop-blur-md animate-fade-up">
            <ErrorBoundary label="workspace">
              <WorkspacePanel />
            </ErrorBoundary>
          </div>
        )}
      </main>

      <CommandPalette />
    </div>
  );
}
