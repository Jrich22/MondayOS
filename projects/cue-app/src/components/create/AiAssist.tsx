import { useState } from "react";
import { runAssist, type AssistKind, type AssistInput } from "@/lib/ai";
import { SparklesIcon } from "@/components/icons";
import { cn } from "@/lib/cn";

const ACTIONS: { kind: AssistKind; label: string }[] = [
  { kind: "description", label: "Draft a description" },
  { kind: "agenda", label: "Suggest an agenda" },
  { kind: "invite", label: "Suggest invite copy" },
];

/**
 * Optional AI assist for the create flow. Cue is an operations tool first — AI
 * enhances, never blocks. This is a secondary affordance: the user opts in,
 * reviews the draft, and chooses to use it. Descriptions insert straight into
 * the form; agenda/invite drafts are shown to copy (they attach to the event
 * workspace in a later surface).
 */
export function AiAssist({
  input,
  onUseDescription,
}: {
  input: AssistInput;
  onUseDescription: (text: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<AssistKind | null>(null);
  const [result, setResult] = useState<{ kind: AssistKind; text: string } | null>(null);
  const [copied, setCopied] = useState(false);

  async function run(kind: AssistKind) {
    setBusy(kind);
    setResult(null);
    const text = await runAssist(kind, input);
    setBusy(null);
    setResult({ kind, text });
  }

  async function copy() {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(result.text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard may be unavailable; ignore */
    }
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="focus-ring inline-flex items-center gap-1.5 rounded-lg border border-brand-500/40 bg-brand-500/10 px-2.5 py-1.5 text-xs font-medium text-brand-200 transition-colors hover:bg-brand-500/20"
      >
        <SparklesIcon width={15} height={15} />
        Generate with AI
      </button>

      {open && (
        <div
          className="absolute right-0 z-20 mt-2 w-72 overflow-hidden rounded-xl border border-line bg-canvas-overlay shadow-card"
          role="menu"
        >
          <div className="border-b border-line px-3 py-2">
            <p className="text-xs font-medium text-ink">AI assist</p>
            <p className="text-[11px] text-ink-faint">Optional — review before using.</p>
          </div>
          <div className="p-1.5">
            {ACTIONS.map((a) => (
              <button
                key={a.kind}
                type="button"
                disabled={busy !== null}
                onClick={() => run(a.kind)}
                className={cn(
                  "focus-ring flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-left text-sm text-ink-muted transition-colors hover:bg-white/5 hover:text-ink",
                  busy === a.kind && "text-ink",
                )}
              >
                {a.label}
                {busy === a.kind && (
                  <span className="text-xs text-ink-faint">Generating…</span>
                )}
              </button>
            ))}
          </div>

          {result && (
            <div className="border-t border-line p-3">
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap font-sans text-xs leading-relaxed text-ink-muted">
                {result.text}
              </pre>
              <div className="mt-2.5 flex items-center gap-2">
                {result.kind === "description" && (
                  <button
                    type="button"
                    onClick={() => {
                      onUseDescription(result.text);
                      setOpen(false);
                    }}
                    className="focus-ring rounded-lg bg-brand-600 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-brand-500"
                  >
                    Use this
                  </button>
                )}
                <button
                  type="button"
                  onClick={copy}
                  className="focus-ring rounded-lg border border-line px-2.5 py-1.5 text-xs font-medium text-ink-muted hover:text-ink"
                >
                  {copied ? "Copied ✓" : "Copy"}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
