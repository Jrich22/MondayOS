import { useState } from "react";
import type { CueEvent } from "@/lib/types";
import { runAssist, assistInputFromEvent, type AssistKind } from "@/lib/ai";
import { SparklesIcon } from "@/components/icons";
import { cn } from "@/lib/cn";

export const ASSISTANT_ACTIONS: { kind: AssistKind; label: string; desc: string }[] = [
  { kind: "agenda", label: "Generate agenda", desc: "A starting run-of-show" },
  { kind: "invite", label: "Draft invitations", desc: "On-brand invite copy" },
  { kind: "followup", label: "Draft follow-up email", desc: "Post-event thank-you" },
  { kind: "summary", label: "Event summary", desc: "Where things stand" },
  { kind: "attendees", label: "Suggested attendees", desc: "Portfolio-aware picks" },
];

/**
 * The event's AI assistant. Cue is an operations tool first, so this is an
 * always-available *helper*, not the workflow: the manager picks an action,
 * reviews the draft, and copies what's useful. Rendered both as the persistent
 * right rail and (expanded) inside the AI Assistant tab.
 */
export function AiPanel({
  event,
  variant = "rail",
}: {
  event: CueEvent;
  variant?: "rail" | "expanded";
}) {
  const [busy, setBusy] = useState<AssistKind | null>(null);
  const [result, setResult] = useState<{ kind: AssistKind; text: string } | null>(null);
  const [copied, setCopied] = useState(false);

  async function run(kind: AssistKind) {
    setBusy(kind);
    setResult(null);
    const text = await runAssist(kind, assistInputFromEvent(event));
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
      /* ignore */
    }
  }

  const activeLabel = result
    ? ASSISTANT_ACTIONS.find((a) => a.kind === result.kind)?.label
    : undefined;

  return (
    <div className={cn("flex flex-col", variant === "rail" && "h-full")}>
      <div className="flex items-center gap-2.5">
        <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand-sheen text-brand-400">
          <SparklesIcon width={18} height={18} />
        </span>
        <div>
          <p className="text-sm font-semibold text-ink">AI Assistant</p>
          <p className="text-[11px] text-ink-faint">Optional — you're in control.</p>
        </div>
      </div>

      <div className={cn("mt-4 grid gap-2", variant === "expanded" && "sm:grid-cols-2")}>
        {ASSISTANT_ACTIONS.map((a) => (
          <button
            key={a.kind}
            type="button"
            disabled={busy !== null}
            onClick={() => run(a.kind)}
            className={cn(
              "focus-ring group flex items-center justify-between rounded-xl border border-line bg-canvas px-3 py-2.5 text-left transition-colors hover:border-line-strong hover:bg-white/[0.03]",
              busy === a.kind && "border-brand-500/50",
            )}
          >
            <span>
              <span className="block text-sm font-medium text-ink">{a.label}</span>
              <span className="block text-[11px] text-ink-faint">{a.desc}</span>
            </span>
            <span className="text-xs text-brand-400 opacity-0 transition-opacity group-hover:opacity-100">
              {busy === a.kind ? "…" : "→"}
            </span>
          </button>
        ))}
      </div>

      <div className={cn("mt-4 flex-1", variant === "rail" && "min-h-0")}>
        {busy && (
          <div className="flex items-center gap-2 rounded-xl border border-line bg-canvas px-3 py-3 text-sm text-ink-muted">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand-400" />
            Generating…
          </div>
        )}
        {result && !busy && (
          <div className="flex h-full flex-col rounded-xl border border-line bg-canvas">
            <div className="flex items-center justify-between border-b border-line px-3 py-2">
              <span className="text-xs font-medium text-ink">{activeLabel}</span>
              <button
                type="button"
                onClick={copy}
                className="focus-ring rounded-md px-2 py-0.5 text-xs text-ink-muted hover:text-ink"
              >
                {copied ? "Copied ✓" : "Copy"}
              </button>
            </div>
            <pre className="max-h-80 flex-1 overflow-auto whitespace-pre-wrap px-3 py-3 font-sans text-xs leading-relaxed text-ink-muted">
              {result.text}
            </pre>
          </div>
        )}
        {!result && !busy && (
          <p className="rounded-xl border border-dashed border-line px-3 py-4 text-center text-xs text-ink-faint">
            Pick an action to draft something. Nothing is sent until you use it.
          </p>
        )}
      </div>
    </div>
  );
}
