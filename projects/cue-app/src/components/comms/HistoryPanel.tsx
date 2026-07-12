import { useMemo, useState } from "react";
import type { CueEvent, Guest } from "@/lib/types";
import { guestTimeline, type TimelineEntry } from "@/lib/comms-history";
import { displayName, guestCompany, initials, matchesGuestQuery } from "@/lib/guests-select";
import { SearchIcon } from "@/components/icons";
import { CommsIcon } from "./commsIcons";
import { cn } from "@/lib/cn";

/**
 * Communication History — the per-guest lifecycle timeline (Sprint 2: timeline
 * only, no editing). Pick a guest and see their outbound-comms chain — invitation
 * through survey — derived from the shared roster (lib/comms-history), so it
 * always agrees with Guest Management and Roll Call rather than tracking its own
 * state.
 */
export function HistoryPanel({
  event,
  guests,
  now,
}: {
  event: CueEvent;
  guests: Guest[];
  now: number;
}) {
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(guests[0]?.id ?? null);

  const matches = useMemo(
    () => guests.filter((g) => matchesGuestQuery(g, query)).slice(0, 40),
    [guests, query],
  );

  const selected =
    guests.find((g) => g.id === selectedId) ?? matches[0] ?? guests[0] ?? null;
  const timeline = selected ? guestTimeline(selected, event, now) : [];

  return (
    <div className="space-y-3">
      <div>
        <h3 className="text-sm font-semibold text-ink">Communication History</h3>
        <p className="text-[10px] text-ink-faint">Per-guest lifecycle timeline</p>
      </div>

      {/* Guest search */}
      <div className="relative">
        <SearchIcon
          width={15}
          height={15}
          className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-faint"
        />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Find a guest…"
          className="focus-ring w-full rounded-lg border border-line bg-canvas py-2 pl-8 pr-3 text-sm text-ink placeholder:text-ink-faint hover:border-line-strong"
        />
      </div>

      {/* Guest shortlist (when searching or none picked) */}
      {(query || !selectedId) && matches.length > 0 && (
        <div className="max-h-40 space-y-0.5 overflow-y-auto">
          {matches.map((g) => (
            <button
              key={g.id}
              onClick={() => {
                setSelectedId(g.id);
                setQuery("");
              }}
              className="focus-ring flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-white/[0.04]"
            >
              <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-white/[0.06] text-[9px] font-semibold text-ink">
                {initials(g)}
              </span>
              <span className="min-w-0 flex-1 truncate text-xs text-ink">{displayName(g)}</span>
            </button>
          ))}
        </div>
      )}

      {/* Selected guest header */}
      {selected ? (
        <>
          <div className="flex items-center gap-2.5 rounded-xl border border-line bg-white/[0.02] p-2.5">
            <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-gradient-to-br from-brand-500 to-brand-700 text-xs font-semibold text-white">
              {initials(selected)}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold text-ink">{displayName(selected)}</p>
              <p className="truncate text-[11px] text-ink-faint">
                {guestCompany(selected, event.portfolio) || selected.professional.jobTitle || "—"}
              </p>
            </div>
            {selected.vip && (
              <span className="shrink-0 rounded-md bg-brand-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-brand-200">
                VIP
              </span>
            )}
          </div>

          <Timeline entries={timeline} />
        </>
      ) : (
        <p className="text-[11px] text-ink-faint">No guests on this event yet.</p>
      )}
    </div>
  );
}

function Timeline({ entries }: { entries: TimelineEntry[] }) {
  return (
    <ol className="relative space-y-0 pl-1">
      {entries.map((e, i) => (
        <li key={`${e.kind}-${i}`} className="relative flex gap-3 pb-4 last:pb-0">
          {/* Connector */}
          {i < entries.length - 1 && (
            <span className="absolute left-[13px] top-7 h-[calc(100%-1.25rem)] w-px bg-line" />
          )}
          <span
            className={cn(
              "z-10 grid h-[27px] w-[27px] shrink-0 place-items-center rounded-full border",
              e.done
                ? "border-brand-500/40 bg-brand-500/15 text-brand-300"
                : "border-dashed border-line bg-canvas text-ink-faint",
            )}
          >
            <CommsIcon name={e.icon} width={14} height={14} />
          </span>
          <div className="min-w-0 flex-1 pt-0.5">
            <p className={cn("text-xs font-medium", e.done ? "text-ink" : "text-ink-muted")}>
              {e.label}
            </p>
            <p className="text-[11px] text-ink-faint">{e.when}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}
