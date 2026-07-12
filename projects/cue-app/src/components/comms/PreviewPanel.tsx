import { useState } from "react";
import type { CueEvent, Guest } from "@/lib/types";
import { resolveAudience, type Campaign } from "@/lib/comms";
import { formatEventDate } from "@/lib/format";
import { displayName } from "@/lib/guests-select";
import { currentUser } from "@/lib/session";
import { LayoutIcon, DeviceMobileIcon, DocumentIcon } from "@/components/icons";
import { cn } from "@/lib/cn";

type PreviewMode = "desktop" | "mobile" | "plain";

const MODES: { id: PreviewMode; label: string; icon: JSX.Element }[] = [
  { id: "desktop", label: "Desktop", icon: <LayoutIcon width={15} height={15} /> },
  { id: "mobile", label: "Mobile", icon: <DeviceMobileIcon width={15} height={15} /> },
  { id: "plain", label: "Plain text", icon: <DocumentIcon width={15} height={15} /> },
];

/** Substitute the merge tokens with a real sample recipient + event context. */
function merge(text: string, event: CueEvent, sampleFirstName: string): string {
  return text
    .replaceAll("{{first_name}}", sampleFirstName)
    .replaceAll("{{event_name}}", event.title)
    .replaceAll("{{event_date}}", event.startsAt ? formatEventDate(event.startsAt) : "the date")
    .replaceAll("{{venue}}", event.venue || "the venue")
    .replaceAll("{{city}}", event.city || "")
    .replaceAll("{{host}}", event.host);
}

/**
 * Live preview of the campaign as recipients will see it, in three modes:
 * Desktop (a full email chrome), Mobile (a phone frame), and Plain Text (the raw
 * merged copy). Merge tokens are resolved against a real sample recipient from
 * the campaign's audience, so the preview reflects an actual send.
 */
export function PreviewPanel({
  campaign,
  event,
  guests,
}: {
  campaign: Campaign;
  event: CueEvent;
  guests: Guest[];
}) {
  const [mode, setMode] = useState<PreviewMode>("desktop");
  const sample = resolveAudience(guests, campaign.audience, campaign.audienceTag)[0];
  const sampleName = sample ? displayName(sample).split(" ")[0] : "Alex";
  const subject = merge(campaign.subject, event, sampleName) || "(no subject)";
  const body = merge(campaign.message, event, sampleName);

  return (
    <div className="space-y-3">
      <div className="flex gap-1">
        {MODES.map((m) => (
          <button
            key={m.id}
            onClick={() => setMode(m.id)}
            className={cn(
              "focus-ring flex flex-1 items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-medium transition-colors",
              mode === m.id ? "bg-white/[0.08] text-ink" : "text-ink-muted hover:text-ink",
            )}
          >
            {m.icon}
            {m.label}
          </button>
        ))}
      </div>

      {mode === "plain" ? (
        <div className="rounded-xl border border-line bg-canvas p-3">
          <p className="text-[11px] text-ink-faint">Subject</p>
          <p className="mb-2 whitespace-pre-wrap break-words font-mono text-xs text-ink">{subject}</p>
          <p className="text-[11px] text-ink-faint">Body</p>
          <p className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-ink-muted">
            {body || "(empty)"}
          </p>
        </div>
      ) : (
        <div className={cn(mode === "mobile" && "mx-auto max-w-[17rem]")}>
          <div
            className={cn(
              "overflow-hidden border border-line bg-white text-slate-800 shadow-card",
              mode === "mobile" ? "rounded-[1.75rem] p-1.5" : "rounded-xl",
            )}
          >
            <div className={cn("overflow-hidden bg-white", mode === "mobile" && "rounded-[1.4rem]")}>
              {/* Email header */}
              <div className="border-b border-slate-200 px-4 py-3">
                <div className="flex items-center gap-2">
                  <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-gradient-to-br from-brand-500 to-brand-700 text-xs font-semibold text-white">
                    {currentUser.initials}
                  </span>
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-slate-900">{event.host}</p>
                    <p className="truncate text-[11px] text-slate-500">
                      {currentUser.firm} · to {sample ? displayName(sample) : "you"}
                    </p>
                  </div>
                </div>
                <p className="mt-2.5 text-[15px] font-semibold leading-snug text-slate-900">{subject}</p>
              </div>
              {/* Body */}
              <div className="px-4 py-4">
                {body ? (
                  <p className="whitespace-pre-wrap break-words text-[13px] leading-relaxed text-slate-700">
                    {body}
                  </p>
                ) : (
                  <p className="text-[13px] italic text-slate-400">
                    Your message will appear here as recipients see it.
                  </p>
                )}
                <div className="mt-5 border-t border-slate-100 pt-3 text-[11px] text-slate-400">
                  Sent with Cue · {event.title}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      <p className="text-center text-[11px] text-ink-faint">
        Preview merges <code className="text-ink-muted">{"{{first_name}}"}</code> as{" "}
        <span className="text-ink-muted">{sampleName}</span>
        {sample ? " — a real recipient in this audience." : " — no recipient in this audience yet."}
      </p>
    </div>
  );
}
