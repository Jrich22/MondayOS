import type { CueEvent } from "@/lib/types";
import type { ScanResult } from "@/lib/qr";
import { scanMeta, type ScanTone } from "@/lib/checkin";
import { buildBadge } from "@/lib/badge";
import { BadgePreview } from "./BadgePreview";
import {
  CheckCircleIcon,
  AlertTriangleIcon,
  StarIcon,
  SparklesIcon,
  ClockIcon,
} from "@/components/icons";
import { cn } from "@/lib/cn";

/**
 * The scan feedback stage — the single biggest thing on the kiosk when a code is
 * read. It answers "what happened?" in under a second: a large tone-colored
 * status, the attendee's badge on success, a clear reason on failure, and the
 * required motion — a pop for success, a shake for errors, and a VIP arrival
 * celebration (a burst of rings + sparkles) so the host gets cued to greet.
 * High-contrast by design for a bright, busy registration desk.
 */

const TONE: Record<
  ScanTone,
  { ring: string; chip: string; text: string; icon: React.ReactNode; anim: string }
> = {
  success: {
    ring: "border-status-live/40 bg-status-live/[0.07]",
    chip: "bg-status-live/15 text-status-live",
    text: "text-status-live",
    icon: <CheckCircleIcon width={40} height={40} />,
    anim: "animate-pop-in",
  },
  celebrate: {
    ring: "border-status-draft/45 bg-status-draft/[0.08]",
    chip: "bg-status-draft/15 text-status-draft",
    text: "text-status-draft",
    icon: <StarIcon width={40} height={40} />,
    anim: "animate-pop-in",
  },
  warn: {
    ring: "border-status-draft/40 bg-status-draft/[0.06]",
    chip: "bg-status-draft/15 text-status-draft",
    text: "text-status-draft",
    icon: <ClockIcon width={40} height={40} />,
    anim: "animate-pop-in",
  },
  error: {
    ring: "border-red-500/40 bg-red-500/[0.07]",
    chip: "bg-red-500/15 text-red-400",
    text: "text-red-400",
    icon: <AlertTriangleIcon width={40} height={40} />,
    anim: "animate-shake",
  },
};

export function ScanResultCard({
  result,
  event,
  nonce,
}: {
  result: ScanResult;
  event: CueEvent;
  /** Changes on every scan so the entrance animation replays for repeats. */
  nonce: number;
}) {
  const meta = scanMeta(result.status, Boolean(result.guest?.vip));
  const tone = TONE[meta.tone];
  const badge = result.guest ? buildBadge(result.guest, event) : null;
  const celebrate = meta.tone === "celebrate";

  return (
    <div
      // `key` forces a remount per scan so the entrance animation replays.
      key={nonce}
      className={cn(
        "relative flex w-full max-w-md flex-col items-center rounded-3xl border p-6 text-center",
        tone.ring,
        tone.anim,
      )}
    >
      {celebrate && <Celebration />}

      <div
        className={cn(
          "relative grid h-20 w-20 place-items-center rounded-full",
          tone.chip,
          celebrate && "animate-pulse-ring",
        )}
      >
        {tone.icon}
      </div>

      <p className={cn("mt-4 text-2xl font-bold tracking-tight", tone.text)}>{meta.title}</p>

      {result.guest ? (
        <p className="mt-1 text-lg font-semibold text-ink">
          {badge?.name}
          {badge?.company ? <span className="text-ink-muted"> · {badge.company}</span> : null}
        </p>
      ) : null}

      <p className="mt-1 text-sm text-ink-muted">{result.reason}</p>

      {badge && (meta.tone === "success" || meta.tone === "celebrate") && (
        <div className="mt-5 w-full">
          <BadgePreview badge={badge} size="compact" showActions={false} />
        </div>
      )}
    </div>
  );
}

/** The VIP celebration flourish: expanding rings + a few rising sparkles. */
function Celebration() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden rounded-3xl" aria-hidden>
      <span className="absolute left-1/2 top-16 h-24 w-24 -translate-x-1/2 rounded-full border-2 border-status-draft/50 animate-celebrate-ring" />
      <span
        className="absolute left-1/2 top-16 h-24 w-24 -translate-x-1/2 rounded-full border-2 border-status-draft/40 animate-celebrate-ring"
        style={{ animationDelay: "0.25s" }}
      />
      {[18, 38, 62, 82].map((left, i) => (
        <span
          key={left}
          className="absolute top-24 text-status-draft animate-sparkle-float"
          style={{ left: `${left}%`, animationDelay: `${0.1 + i * 0.12}s` }}
        >
          <SparklesIcon width={16} height={16} />
        </span>
      ))}
    </div>
  );
}
