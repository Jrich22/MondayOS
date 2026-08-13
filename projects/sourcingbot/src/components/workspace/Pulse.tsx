/**
 * Pulse strip — six numbers a recruiter checks reflexively.
 *
 * Deliberately one dense row rather than six hero cards. This is orientation,
 * not the main event: the focus list below is what the page is for. Every tile
 * links to the work it counts — a number you cannot act on is a vanity metric.
 */
import type { FC } from "react";
import { Link } from "react-router-dom";
import type { Pulse as PulseData } from "@/lib/intel";
import { cn } from "@/components/ui/Primitives";

interface Tile {
  label: string;
  value: number;
  href: string;
  tone?: "brand" | "oversight" | "advanced";
  /** Shown when the value is 0 — silence is a valid, calm state. */
  quiet?: string;
}

export const PulseStrip: FC<{ pulse: PulseData }> = ({ pulse }) => {
  const tiles: Tile[] = [
    { label: "Open reqs", value: pulse.activeReqs, href: "/reqs" },
    { label: "Live sessions", value: pulse.activeSessions, href: "/reqs", tone: "brand", quiet: "none running" },
    { label: "Added today", value: pulse.capturedToday, href: "#activity", tone: "advanced", quiet: "nothing yet" },
    { label: "Close calls", value: pulse.closeCalls, href: "#focus", tone: "oversight", quiet: "none" },
    { label: "Reusable people", value: pulse.reusedCandidates, href: "#pool", quiet: "none yet" },
    { label: "Needs review", value: pulse.needsReview, href: "#focus", tone: "oversight", quiet: "all current" },
  ];

  return (
    <ul className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-3 lg:grid-cols-6">
      {tiles.map((t) => (
        <li key={t.label} className="bg-canvas-raised">
          <Link
            to={t.href}
            className="block px-4 py-3 transition-colors hover:bg-canvas-overlay"
          >
            <p className="text-[11px] uppercase tracking-wide text-ink-faint">{t.label}</p>
            {t.value === 0 && t.quiet ? (
              <p className="mt-1 text-sm text-ink-faint">{t.quiet}</p>
            ) : (
              <p
                className={cn(
                  "mt-0.5 text-2xl font-semibold tabular-nums",
                  t.tone === "brand"
                    ? "text-brand-400"
                    : t.tone === "oversight"
                      ? "text-oversight"
                      : t.tone === "advanced"
                        ? "text-stage-advanced"
                        : "text-ink",
                )}
              >
                {t.value}
              </p>
            )}
          </Link>
        </li>
      ))}
    </ul>
  );
};
