import { MondayBrain, STATE_LABELS } from "@/components/monday";
import { useApp } from "@/state/store";
import { OrbitalNodes } from "./OrbitalNodes";

/**
 * The persistent Brain anchor. Monday's Brain is centered and active, its state
 * driven by real OS activity (via the store's `brainState`). The orbital nodes
 * ring it for navigation; clicking the Brain itself opens the command layer.
 * This region stays mounted across every section so the Brain never pops in or
 * out — it is the operating system's home.
 */
export function BrainStage() {
  const { brainState, openCommand, state } = useApp();

  return (
    <div className="relative h-full w-full">
      <MondayBrain
        state={brainState}
        onActivate={openCommand}
        className="absolute inset-0 h-full w-full"
        ariaLabel="Monday's Brain — open the command interface"
      />

      {/* State readout. */}
      <div className="pointer-events-none absolute left-1/2 top-6 z-10 -translate-x-1/2 text-center">
        <div className="text-[11px] uppercase tracking-[0.2em] text-ink-faint">Monday</div>
        <div className="mt-1 text-xl font-semibold tracking-tight text-ink">
          {STATE_LABELS[brainState]}
        </div>
        <div className="mt-0.5 text-[11px] text-ink-faint">
          {state.brainOverride === "auto" ? "reflecting live OS activity" : "manual preview"}
        </div>
      </div>

      {/* Orbital navigation. */}
      <OrbitalNodes />

      {/* Command affordance. */}
      <button
        onClick={openCommand}
        className="focus-ring absolute bottom-6 left-1/2 z-10 -translate-x-1/2 rounded-full border border-line bg-canvas-raised/70 px-5 py-2 text-sm text-ink-muted backdrop-blur-sm transition hover:border-brand-400/50 hover:text-ink"
      >
        Ask Monday anything… <kbd className="ml-1 text-ink-faint">⌘K</kbd>
      </button>
    </div>
  );
}
