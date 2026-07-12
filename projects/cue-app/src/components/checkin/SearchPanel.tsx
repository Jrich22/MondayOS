import { useEffect, useMemo, useRef, useState } from "react";
import type { CueEvent, Guest } from "@/lib/types";
import { matchesGuestQuery, displayName, guestCompany, initials } from "@/lib/guests-select";
import { qrPayload } from "@/lib/qr";
import { SearchIcon, StarIcon, CheckIcon } from "@/components/icons";
import { cn } from "@/lib/cn";

/**
 * Search Mode — fast lookup when a badge won't scan (forgotten, damaged, or a
 * name at the door). Matches across name, company, and tags via the shared
 * roster search, plus VIP and the badge number (the guest id) so any lookup path
 * lands. Picking a result runs the SAME `onScan` pipeline the scanner uses, so a
 * search-based check-in is indistinguishable downstream from a scanned one.
 */
export function SearchPanel({
  event,
  guests,
  onScan,
  focusSignal,
}: {
  event: CueEvent;
  guests: Guest[];
  onScan: (raw: string) => void;
  focusSignal: number;
}) {
  const [query, setQuery] = useState("");
  const [vipOnly, setVipOnly] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, [focusSignal]);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    return guests
      .filter((g) => {
        if (vipOnly && !g.vip) return false;
        if (!q) return true;
        // Badge number = guest id; otherwise the shared roster search.
        return g.id.toLowerCase().includes(q) || matchesGuestQuery(g, query);
      })
      .sort(
        (a, b) =>
          Number(b.vip) - Number(a.vip) ||
          a.identity.lastName.localeCompare(b.identity.lastName),
      )
      .slice(0, 40);
  }, [guests, query, vipOnly]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="relative">
        <SearchIcon
          width={20}
          height={20}
          className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-ink-faint"
        />
        <input
          ref={inputRef}
          autoFocus
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search name, company, VIP, or badge number"
          aria-label="Search attendees"
          className="focus-ring w-full rounded-2xl border border-line bg-canvas-raised py-4 pl-12 pr-4 text-lg text-ink placeholder:text-ink-faint"
        />
      </div>

      <div className="mt-2.5 flex items-center justify-between">
        <button
          onClick={() => setVipOnly((v) => !v)}
          aria-pressed={vipOnly}
          className={cn(
            "focus-ring inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors",
            vipOnly
              ? "border-status-draft/40 bg-status-draft/15 text-status-draft"
              : "border-line text-ink-muted hover:text-ink",
          )}
        >
          <StarIcon width={12} height={12} />
          VIP only
        </button>
        <span className="text-[11px] text-ink-faint">{results.length} match</span>
      </div>

      <div className="mt-2 min-h-0 flex-1 overflow-y-auto pr-1">
        {results.length === 0 ? (
          <p className="rounded-xl border border-dashed border-line px-4 py-10 text-center text-sm text-ink-muted">
            No attendees match that search.
          </p>
        ) : (
          <ul className="space-y-1.5 pb-3">
            {results.map((g) => {
              const checkedIn = g.attendance.checkedIn;
              return (
                <li key={g.id}>
                  <button
                    onClick={() => onScan(qrPayload(g))}
                    className={cn(
                      "focus-ring group flex w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition-colors",
                      checkedIn
                        ? "border-status-live/25 bg-status-live/[0.06]"
                        : "border-line bg-canvas-raised hover:border-line-strong hover:bg-white/[0.03]",
                    )}
                  >
                    <span
                      className={cn(
                        "grid h-10 w-10 shrink-0 place-items-center rounded-full text-sm font-semibold",
                        checkedIn ? "bg-status-live/15 text-status-live" : "bg-white/[0.06] text-ink",
                      )}
                    >
                      {initials(g)}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5">
                        <span className="truncate font-semibold text-ink">{displayName(g)}</span>
                        {g.vip && <StarIcon width={12} height={12} className="shrink-0 text-status-draft" />}
                      </span>
                      <span className="block truncate text-xs text-ink-muted">
                        {guestCompany(g, event.portfolio) || "—"}
                        <span className="text-ink-faint"> · #{g.id}</span>
                      </span>
                    </span>
                    <span
                      className={cn(
                        "shrink-0 rounded-lg px-2.5 py-1.5 text-[11px] font-medium",
                        checkedIn
                          ? "inline-flex items-center gap-1 text-status-live"
                          : "border border-line-strong text-ink-muted group-hover:border-brand-400 group-hover:text-brand-300",
                      )}
                    >
                      {checkedIn ? (
                        <>
                          <CheckIcon width={12} height={12} /> In
                        </>
                      ) : (
                        "Check in"
                      )}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
