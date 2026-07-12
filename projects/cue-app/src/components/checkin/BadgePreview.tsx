import { useState } from "react";
import type { BadgeData } from "@/lib/badge";
import { QrGlyph } from "./QrGlyph";
import { StarIcon, PrinterIcon, DownloadIcon } from "@/components/icons";
import { cn } from "@/lib/cn";

/**
 * The printable attendee badge — a light "credential" card that stands apart from
 * the dark kiosk chrome so it reads like a physical name tag. Two sizes: `large`
 * for the preview/lanyard and `compact` for an inline confirmation. The
 * organization color (from the event brand theme) accents the header and VIP mark.
 *
 * Print and Export are real buttons but deliberately placeholders in the MVP (no
 * printer, no file system) — they surface a preview-only note rather than no-op
 * silently, so the affordance is honest.
 */
export function BadgePreview({
  badge,
  size = "large",
  showActions = true,
}: {
  badge: BadgeData;
  size?: "large" | "compact";
  showActions?: boolean;
}) {
  return (
    <div className={cn("flex flex-col gap-3", size === "large" ? "w-full max-w-[19rem]" : "w-full")}>
      {size === "large" ? <LargeBadge badge={badge} /> : <CompactBadge badge={badge} />}
      {showActions && <BadgeActions />}
    </div>
  );
}

function LargeBadge({ badge }: { badge: BadgeData }) {
  return (
    <article
      className="overflow-hidden rounded-2xl bg-white text-slate-900 shadow-2xl ring-1 ring-black/10"
      style={{ borderTop: `6px solid ${badge.color}` }}
    >
      {/* Org color header band */}
      <div
        className="flex items-center justify-between px-5 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-white"
        style={{ backgroundColor: badge.color }}
      >
        <span className="truncate">{badge.eventTitle}</span>
        {badge.vip && (
          <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-black/20 px-2 py-0.5">
            <StarIcon width={11} height={11} />
            VIP
          </span>
        )}
      </div>

      {/* Identity */}
      <div className="px-5 pt-4">
        <h3 className="text-2xl font-bold leading-tight tracking-tight">{badge.name}</h3>
        {badge.title && <p className="mt-0.5 text-sm font-medium text-slate-600">{badge.title}</p>}
        {badge.company && <p className="text-sm font-semibold text-slate-800">{badge.company}</p>}
        {badge.role && (
          <span
            className="mt-2.5 inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-bold uppercase tracking-wide"
            style={{ backgroundColor: `${badge.color}1f`, color: badge.color }}
          >
            {badge.role}
          </span>
        )}
      </div>

      {/* QR credential */}
      <div className="mt-4 flex items-end justify-between gap-3 px-5 pb-5">
        <div className="text-[10px] font-medium leading-tight text-slate-400">
          <p className="font-semibold uppercase tracking-wider text-slate-500">Check-in</p>
          <p className="mt-0.5">Scan at the door</p>
        </div>
        <div className="h-24 w-24 shrink-0 rounded-lg ring-1 ring-black/5">
          <QrGlyph payload={badge.qr} />
        </div>
      </div>
    </article>
  );
}

function CompactBadge({ badge }: { badge: BadgeData }) {
  return (
    <article className="flex items-center gap-3 overflow-hidden rounded-xl bg-white p-3 text-slate-900 shadow-lg ring-1 ring-black/10">
      <span
        className="grid h-11 w-11 shrink-0 place-items-center rounded-full text-sm font-bold text-white"
        style={{ backgroundColor: badge.color }}
      >
        {badge.initials}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <p className="truncate text-sm font-bold">{badge.name}</p>
          {badge.vip && <StarIcon width={12} height={12} style={{ color: badge.color }} className="shrink-0" />}
        </div>
        <p className="truncate text-xs text-slate-600">
          {badge.company || "—"}
          {badge.title ? ` · ${badge.title}` : ""}
        </p>
      </div>
      <div className="h-11 w-11 shrink-0 rounded ring-1 ring-black/5">
        <QrGlyph payload={badge.qr} size={21} />
      </div>
    </article>
  );
}

function BadgeActions() {
  const [note, setNote] = useState<string | null>(null);

  const placeholder = (what: string) => {
    setNote(`${what} is a preview-only placeholder in this build.`);
    window.setTimeout(() => setNote(null), 2600);
  };

  return (
    <div>
      <div className="flex gap-2">
        <button
          onClick={() => placeholder("Printing")}
          className="focus-ring inline-flex flex-1 items-center justify-center gap-2 rounded-xl border border-line-strong px-3 py-2 text-sm font-medium text-ink hover:bg-white/5"
        >
          <PrinterIcon width={16} height={16} />
          Print
        </button>
        <button
          onClick={() => placeholder("Export")}
          className="focus-ring inline-flex flex-1 items-center justify-center gap-2 rounded-xl border border-line-strong px-3 py-2 text-sm font-medium text-ink hover:bg-white/5"
        >
          <DownloadIcon width={16} height={16} />
          Export
        </button>
      </div>
      {note && <p className="mt-2 text-center text-[11px] text-ink-faint">{note}</p>}
    </div>
  );
}
