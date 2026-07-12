import { useEffect } from "react";

/**
 * The Monday command interface — opened by clicking Monday's Brain or the
 * "Ask Monday" affordance. A lightweight command palette over the OS; the
 * suggestions mirror the `monday` surface (ask / task / learn / doctor /
 * project). Wiring to the real MondayOS API is a follow-up — this establishes
 * the entry point and interaction.
 */

const SUGGESTIONS = [
  "What tasks are currently blocked?",
  "Plan the next sprint across the agent fleet",
  "Run Doctor: full system health sweep",
  "Summarize Cue's product health",
  "Learn from the latest research docs",
];

interface Props {
  open: boolean;
  onClose: () => void;
}

export function CommandInterface({ open, onClose }: Props) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-40 flex items-start justify-center bg-black/50 pt-28 backdrop-blur-sm animate-fade-up"
      onClick={onClose}
    >
      <div
        className="card w-full max-w-xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b border-line px-4 py-3">
          <span className="h-2 w-2 rounded-full bg-status-completed shadow-[0_0_10px] shadow-status-completed" />
          <input
            autoFocus
            placeholder="Ask Monday to do something…"
            className="focus-ring w-full bg-transparent text-sm text-ink placeholder:text-ink-faint"
          />
          <kbd className="rounded border border-line px-1.5 py-0.5 text-[10px] text-ink-faint">esc</kbd>
        </div>
        <div className="p-2">
          <div className="px-2 pb-1 pt-2 text-[10px] uppercase tracking-wider text-ink-faint">
            Suggestions
          </div>
          {SUGGESTIONS.map((cmd) => (
            <button
              key={cmd}
              onClick={onClose}
              className="focus-ring block w-full rounded-lg px-3 py-2 text-left text-sm text-ink-muted transition hover:bg-canvas-overlay hover:text-ink"
            >
              {cmd}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
