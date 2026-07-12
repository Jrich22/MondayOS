import { useState } from "react";
import type { CueEvent } from "@/lib/types";
import { audienceLabel, type Campaign } from "@/lib/comms";
import {
  ASSIST_ACTIONS,
  runAssist,
  type AssistAction,
  type AssistResult,
} from "@/lib/comms-ai";
import { SparklesIcon, CheckIcon } from "@/components/icons";
import { cn } from "@/lib/cn";

/**
 * The AI Assistant panel — offline/mock (lib/comms-ai). Generators draft copy
 * from scratch, transforms reshape the current draft, and subject-lines offers
 * options. Results preview before they touch the draft: the user reviews, then
 * Applies — AI augments the campaign, never silently overwrites it.
 */
export function AssistantPanel({
  campaign,
  event,
  onApply,
}: {
  campaign: Campaign;
  event: CueEvent;
  onApply: (patch: Partial<Campaign>) => void;
}) {
  const [busy, setBusy] = useState<AssistAction | null>(null);
  const [result, setResult] = useState<{ action: AssistAction; data: AssistResult } | null>(null);
  const [applied, setApplied] = useState(false);

  async function run(action: AssistAction) {
    setBusy(action);
    setResult(null);
    setApplied(false);
    const data = await runAssist(action, {
      event,
      stage: campaign.stage,
      subject: campaign.subject,
      message: campaign.message,
      audienceLabel: audienceLabel(campaign.audience, campaign.audienceTag),
    });
    setResult({ action, data });
    setBusy(null);
  }

  function apply(patch: Partial<Campaign>) {
    onApply(patch);
    setApplied(true);
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="grid h-6 w-6 place-items-center rounded-md bg-brand-sheen text-brand-400">
          <SparklesIcon width={15} height={15} />
        </span>
        <div>
          <h3 className="text-sm font-semibold text-ink">AI Assistant</h3>
          <p className="text-[10px] text-ink-faint">Drafts & reshapes copy · offline</p>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-1.5">
        {ASSIST_ACTIONS.map((a) => (
          <button
            key={a.action}
            onClick={() => run(a.action)}
            disabled={busy !== null}
            className={cn(
              "focus-ring flex items-center justify-between rounded-lg border px-3 py-2 text-left text-sm transition-colors disabled:opacity-60",
              busy === a.action
                ? "border-brand-500/50 bg-brand-500/10"
                : "border-line hover:border-line-strong",
            )}
          >
            <span className="font-medium text-ink">{a.label}</span>
            <span className="text-[11px] text-ink-faint">
              {busy === a.action ? "Thinking…" : a.hint}
            </span>
          </button>
        ))}
      </div>

      {result && (
        <div className="animate-fade-up rounded-xl border border-line bg-canvas p-3">
          <p className="text-[11px] leading-relaxed text-ink-muted">{result.data.note}</p>

          {/* Subject-line options */}
          {result.data.subjects && (
            <ul className="mt-2.5 space-y-1.5">
              {result.data.subjects.map((s, i) => (
                <li key={i}>
                  <button
                    onClick={() => apply({ subject: s })}
                    className="focus-ring group flex w-full items-center gap-2 rounded-lg border border-line px-2.5 py-1.5 text-left text-xs text-ink transition-colors hover:border-brand-500/40 hover:bg-brand-500/[0.06]"
                  >
                    <span className="min-w-0 flex-1 truncate">{s}</span>
                    <span className="shrink-0 text-[10px] font-medium text-brand-300 opacity-0 transition-opacity group-hover:opacity-100">
                      Use
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}

          {/* Generated / transformed copy */}
          {(result.data.subject || result.data.message) && (
            <div className="mt-2.5 space-y-2">
              {result.data.subject && (
                <div className="rounded-lg border border-line bg-canvas-raised p-2">
                  <p className="text-[10px] uppercase tracking-wide text-ink-faint">Subject</p>
                  <p className="mt-0.5 text-xs text-ink">{result.data.subject}</p>
                </div>
              )}
              {result.data.message && (
                <div className="max-h-56 overflow-y-auto rounded-lg border border-line bg-canvas-raised p-2">
                  <p className="whitespace-pre-wrap break-words text-xs leading-relaxed text-ink-muted">
                    {result.data.message}
                  </p>
                </div>
              )}
              <button
                onClick={() =>
                  apply({
                    ...(result.data.subject ? { subject: result.data.subject } : {}),
                    ...(result.data.message ? { message: result.data.message } : {}),
                  })
                }
                className="focus-ring inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-brand-600 px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-brand-500"
              >
                {applied ? (
                  <>
                    <CheckIcon width={14} height={14} /> Applied
                  </>
                ) : (
                  <>Apply to campaign</>
                )}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
