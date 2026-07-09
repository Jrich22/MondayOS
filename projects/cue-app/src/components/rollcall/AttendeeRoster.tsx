import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { CueEvent, Guest, GuestRole, RsvpStatus } from "@/lib/types";
import {
  selectGuests,
  uniqueCompanies,
  uniqueTags,
  emptyFilters,
  hasActiveFilters,
  RSVP_META,
  RSVP_ORDER,
  ROLE_META,
  ALL_ROLES,
  type GuestFilters,
  type GuestSort,
} from "@/lib/guests-select";
import { Select } from "@/components/ui/Field";
import { AttendeeCard } from "./AttendeeCard";
import { SearchIcon, StarIcon, CheckIcon, UsersIcon } from "@/components/icons";
import { cn } from "@/lib/cn";

const PAGE = 60;

const SORTS: { value: GuestSort; label: string }[] = [
  { value: "name", label: "Name" },
  { value: "checkin", label: "Arrival" },
  { value: "vip", label: "VIP first" },
  { value: "company", label: "Company" },
  { value: "rsvp", label: "RSVP" },
];

/**
 * The command center's left panel (TASK-0040): instant, always-focused search,
 * the full filter set, and an infinite-scrolling attendee list with one-tap
 * check-in. Optimized for speed — search stays hot (it refocuses after every
 * check-in) and Enter checks in the top unchecked match, so the door flow is
 * type-name → Enter → next, without touching the mouse.
 *
 * All roster logic reuses the pure, tested selectors in lib/guests-select; this
 * file is presentation plus the windowing that keeps hundreds of rows smooth.
 */
export function AttendeeRoster({
  event,
  guests,
  onCheckIn,
}: {
  event: CueEvent;
  guests: Guest[];
  onCheckIn: (g: Guest) => void;
}) {
  const [filters, setFilters] = useState<GuestFilters>(emptyFilters());
  const [sort, setSort] = useState<GuestSort>("name");
  const [limit, setLimit] = useState(PAGE);

  const inputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);

  const rows = useMemo(() => selectGuests(guests, filters, sort), [guests, filters, sort]);
  const companies = useMemo(() => uniqueCompanies(guests), [guests]);
  const tags = useMemo(() => uniqueTags(guests), [guests]);

  const patch = (p: Partial<GuestFilters>) => setFilters((f) => ({ ...f, ...p }));

  // Reset the window whenever the visible set changes shape, and scroll to top.
  useEffect(() => {
    setLimit(PAGE);
    scrollRef.current?.scrollTo({ top: 0 });
  }, [filters, sort]);

  // Infinite scroll: grow the window as the sentinel nears the viewport.
  useEffect(() => {
    const sentinel = sentinelRef.current;
    const root = scrollRef.current;
    if (!sentinel || !root) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) setLimit((l) => Math.min(l + PAGE, rows.length));
      },
      { root, rootMargin: "400px" },
    );
    io.observe(sentinel);
    return () => io.disconnect();
  }, [rows.length]);

  const handleCheckIn = (g: Guest) => {
    onCheckIn(g);
    inputRef.current?.focus();
  };

  // Enter checks in the top match that isn't already in the room, then clears
  // the query so the operator can immediately type the next name.
  const onSearchKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== "Enter" || !filters.query.trim()) return;
    e.preventDefault();
    const target = rows.find((g) => !g.attendance.checkedIn) ?? rows[0];
    if (target) {
      onCheckIn(target);
      patch({ query: "" });
    }
  };

  const visible = rows.slice(0, limit);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Search — always focused */}
      <div className="relative">
        <SearchIcon
          width={18}
          height={18}
          className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-ink-faint"
        />
        <input
          ref={inputRef}
          autoFocus
          value={filters.query}
          onChange={(e) => patch({ query: e.target.value })}
          onKeyDown={onSearchKeyDown}
          placeholder="Search name, company, email, tag — Enter to check in"
          aria-label="Search attendees"
          className="focus-ring w-full rounded-xl border border-line bg-canvas-raised py-3 pl-11 pr-3 text-[15px] text-ink placeholder:text-ink-faint"
        />
      </div>

      {/* Filters */}
      <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
        <Chip active={filters.vipOnly} onClick={() => patch({ vipOnly: !filters.vipOnly })}>
          <StarIcon width={12} height={12} />
          VIP
        </Chip>
        <Chip
          active={filters.checkedInOnly}
          onClick={() => patch({ checkedInOnly: !filters.checkedInOnly })}
        >
          <CheckIcon width={12} height={12} />
          Checked in
        </Chip>
        <MiniSelect
          value={filters.rsvp ?? ""}
          onChange={(v) => patch({ rsvp: (v || null) as RsvpStatus | null })}
          placeholder="RSVP"
          options={RSVP_ORDER.map((r) => ({ value: r, label: RSVP_META[r].label }))}
        />
        <MiniSelect
          value={filters.role ?? ""}
          onChange={(v) => patch({ role: (v || null) as GuestRole | null })}
          placeholder="Role"
          options={ALL_ROLES.map((r) => ({ value: r, label: ROLE_META[r].label }))}
        />
        {event.portfolio.length > 0 && (
          <MiniSelect
            value={filters.portfolioCompanyId ?? ""}
            onChange={(v) => patch({ portfolioCompanyId: v || null })}
            placeholder="Portfolio"
            options={event.portfolio.map((c) => ({ value: c.id, label: c.name }))}
          />
        )}
        {companies.length > 0 && (
          <MiniSelect
            value={filters.company ?? ""}
            onChange={(v) => patch({ company: v || null })}
            placeholder="Company"
            options={companies.map((c) => ({ value: c, label: c }))}
          />
        )}
        {tags.length > 0 && (
          <MiniSelect
            value={filters.tag ?? ""}
            onChange={(v) => patch({ tag: v || null })}
            placeholder="Tag"
            options={tags.map((t) => ({ value: t, label: t }))}
          />
        )}
        <div className="ml-auto flex items-center gap-1.5">
          {hasActiveFilters(filters) && (
            <button
              onClick={() => setFilters(emptyFilters())}
              className="focus-ring rounded-lg px-2 py-1 text-xs font-medium text-ink-muted hover:text-ink"
            >
              Clear
            </button>
          )}
          <div className="w-32">
            <Select
              value={sort}
              onChange={(e) => setSort(e.target.value as GuestSort)}
              aria-label="Sort attendees"
              className="py-1.5 text-xs"
            >
              {SORTS.map((s) => (
                <option key={s.value} value={s.value}>
                  Sort: {s.label}
                </option>
              ))}
            </Select>
          </div>
        </div>
      </div>

      {/* Count line */}
      <p className="mt-2 px-0.5 text-xs text-ink-faint">
        {rows.length === guests.length
          ? `${guests.length} attendees`
          : `${rows.length} of ${guests.length} attendees`}
      </p>

      {/* List */}
      <div ref={scrollRef} className="mt-1.5 min-h-0 flex-1 overflow-y-auto pr-1">
        {rows.length === 0 ? (
          <Empty onClear={() => setFilters(emptyFilters())} hasFilters={hasActiveFilters(filters)} />
        ) : (
          <div className="space-y-1.5 pb-4">
            {visible.map((g) => (
              <AttendeeCard key={g.id} guest={g} event={event} onCheckIn={handleCheckIn} />
            ))}
            {limit < rows.length && <div ref={sentinelRef} className="h-1" />}
          </div>
        )}
      </div>
    </div>
  );
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "focus-ring inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors",
        active
          ? "border-brand-500/40 bg-brand-500/15 text-brand-200"
          : "border-line text-ink-muted hover:text-ink",
      )}
    >
      {children}
    </button>
  );
}

function MiniSelect({
  value,
  onChange,
  placeholder,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  options: { value: string; label: string }[];
}) {
  return (
    <div className="w-28">
      <Select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label={placeholder}
        className={cn("py-1.5 text-xs", value && "border-brand-500/50 text-ink")}
      >
        <option value="">{placeholder}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </Select>
    </div>
  );
}

function Empty({ onClear, hasFilters }: { onClear: () => void; hasFilters: boolean }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-line px-6 py-16 text-center">
      <span className="grid h-11 w-11 place-items-center rounded-2xl border border-line bg-white/[0.03] text-ink-faint">
        <UsersIcon />
      </span>
      <p className="text-sm font-medium text-ink">No attendees match</p>
      <p className="max-w-xs text-xs text-ink-muted">
        Try a different search{hasFilters ? " or clear the filters" : ""} to see the whole room.
      </p>
      {hasFilters && (
        <button onClick={onClear} className="focus-ring mt-1 text-sm font-medium text-brand-300 hover:text-brand-200">
          Clear filters
        </button>
      )}
    </div>
  );
}
