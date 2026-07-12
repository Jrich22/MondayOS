import { useEffect, useRef, useState } from "react";
import { useApp } from "@/state/store";
import { ResultCard } from "./ResultCard";

/**
 * The Brain command layer — "Ask Monday anything…". Typed natural-language
 * commands are classified and executed through the command pipeline (parser →
 * execute → adapter); results stream back as structured conversation turns with
 * action buttons. This component owns only presentation + local input state;
 * all behavior is delegated to the store.
 */

const SUGGESTIONS = [
  "What should we work on next?",
  "Show Cue App progress",
  "Show blocked tasks",
  "Show tasks awaiting approval",
  "Open the latest agent run",
  "What changed today?",
  "Switch to Storm Edge",
];

export function CommandPalette() {
  const { state, submitCommand, confirmAction, runAction, closeCommand } = useApp();
  const [input, setInput] = useState("");
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!state.commandOpen) return;
    inputRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeCommand();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [state.commandOpen, closeCommand]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [state.transcript.length, state.busy]);

  if (!state.commandOpen) return null;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const text = input.trim();
    if (!text) return;
    setInput("");
    void submitCommand(text);
  };

  const dismiss = (id: string) => setDismissed((s) => new Set(s).add(id));

  return (
    <div
      className="fixed inset-0 z-40 flex items-start justify-center bg-black/50 pt-20 backdrop-blur-sm animate-fade-up"
      onClick={closeCommand}
    >
      <div
        className="card flex max-h-[75vh] w-full max-w-2xl flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Ask Monday"
      >
        <form onSubmit={submit} className="flex items-center gap-3 border-b border-line px-4 py-3">
          <span className={`h-2 w-2 rounded-full ${state.busy ? "bg-accent-violet animate-pulse-soft" : "bg-status-completed"}`} />
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask Monday anything…"
            aria-label="Ask Monday anything"
            className="focus-ring w-full bg-transparent text-sm text-ink placeholder:text-ink-faint"
          />
          <kbd className="rounded border border-line px-1.5 py-0.5 text-[10px] text-ink-faint">esc</kbd>
        </form>

        <div ref={scrollRef} className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
          {state.transcript.length === 0 ? (
            <div>
              <div className="px-1 pb-2 text-[10px] uppercase tracking-wider text-ink-faint">Try</div>
              <div className="flex flex-col gap-1">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => void submitCommand(s)}
                    className="focus-ring rounded-lg px-3 py-2 text-left text-sm text-ink-muted transition hover:bg-canvas-overlay hover:text-ink"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            state.transcript.map((turn) => (
              <ResultCard
                key={turn.id}
                turn={turn}
                dismissed={dismissed.has(turn.id)}
                onAction={runAction}
                onConfirm={(parsed) => {
                  void confirmAction(parsed);
                  dismiss(turn.id);
                }}
                onCancel={dismiss}
              />
            ))
          )}
          {state.busy && <div className="text-[12px] text-ink-faint">Monday is thinking…</div>}
        </div>
      </div>
    </div>
  );
}
