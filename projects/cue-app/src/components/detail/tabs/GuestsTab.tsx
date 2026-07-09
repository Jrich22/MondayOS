import { useMemo, useState } from "react";
import type { CueEvent, Guest, RsvpStatus, GuestRole } from "@/lib/types";
import { useGuests, updateGuest } from "@/lib/guests";
import {
  selectGuests,
  guestSummary,
  uniqueCompanies,
  uniqueTags,
  hasActiveFilters,
  emptyFilters,
  displayName,
  initials,
  guestCompany,
  withCheckIn,
  newGuest,
  RSVP_META,
  RSVP_ORDER,
  ROLE_META,
  ALL_ROLES,
  type GuestFilters,
  type GuestSort,
} from "@/lib/guests-select";
import { Panel } from "@/components/detail/Panel";
import { Select } from "@/components/ui/Field";
import { Button } from "@/components/ui/Button";
import { GuestDrawer } from "@/components/detail/guests/GuestDrawer";
import { SearchIcon, PlusIcon, StarIcon, CheckIcon, UsersIcon } from "@/components/icons";
import { cn } from "@/lib/cn";

const SORTS: { value: GuestSort; label: string }[] = [
  { value: "name", label: "Alphabetical" },
  { value: "company", label: "Company" },
  { value: "checkin", label: "Check-in time" },
  { value: "vip", label: "VIP first" },
  { value: "rsvp", label: "RSVP" },
];

type DrawerState = { guest: Guest; mode: "edit" | "create" } | null;

/**
 * Guests — the attendee CRM for an event (TASK-0035). Not a spreadsheet: instant
 * search, a full filter/sort bar, inline check-in and VIP toggles for the
 * operator at the door, and a record drawer exposing the whole attendee schema.
 * All roster logic is pure (lib/guests-select); this file is presentation + the
 * two immediate write actions (check-in, VIP) that want to feel instant.
 */
export function GuestsTab({ event }: { event: CueEvent }) {
  const guests = useGuests(event.id);
  const [filters, setFilters] = useState<GuestFilters>(emptyFilters());
  const [sort, setSort] = useState<GuestSort>("name");
  const [drawer, setDrawer] = useState<DrawerState>(null);

  const rows = useMemo(() => selectGuests(guests, filters, sort), [guests, filters, sort]);
  const summary = useMemo(() => guestSummary(guests), [guests]);
  const companies = useMemo(() => uniqueCompanies(guests), [guests]);
  const tags = useMemo(() => uniqueTags(guests), [guests]);

  const patch = (p: Partial<GuestFilters>) => setFilters((f) => ({ ...f, ...p }));

  const toggleCheckIn = (g: Guest) =>
    updateGuest(withCheckIn(g, !g.attendance.checkedIn, new Date().toISOString()));
  const toggleVip = (g: Guest) =>
    updateGuest({ ...g, vip: !g.vip, updatedAt: new Date().toISOString() });

  const addAttendee = () =>
    setDrawer({
      guest: newGuest(event.id, `gst-${crypto.randomUUID().slice(0, 8)}`, new Date().toISOString()),
      mode: "create",
    });

  return (
    <div className="space-y-4">
      {/* Roster summary */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <SummaryChip label="Attendees" value={summary.total} />
        <SummaryChip label="Confirmed" value={summary.confirmed} accent="text-status-live" />
        <SummaryChip label="Checked in" value={summary.checkedIn} accent="text-brand-400" />
        <SummaryChip label="VIPs" value={summary.vip} accent="text-status-draft" />
      </div>

      <Panel>
        {/* Toolbar */}
        <div className="mb-4 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-[200px] flex-1">
              <SearchIcon
                width={16}
                height={16}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint"
              />
              <input
                value={filters.query}
                onChange={(e) => patch({ query: e.target.value })}
                placeholder="Search name, company, email, tag…"
                className="focus-ring w-full rounded-xl border border-line bg-canvas py-2 pl-9 pr-3 text-sm text-ink placeholder:text-ink-faint"
              />
            </div>
            <div className="w-44 shrink-0">
              <Select
                value={sort}
                onChange={(e) => setSort(e.target.value as GuestSort)}
                aria-label="Sort attendees"
              >
                {SORTS.map((s) => (
                  <option key={s.value} value={s.value}>
                    Sort: {s.label}
                  </option>
                ))}
              </Select>
            </div>
            <Button onClick={addAttendee}>
              <PlusIcon width={16} height={16} />
              Add attendee
            </Button>
          </div>

          {/* Filters */}
          <div className="flex flex-wrap items-center gap-2">
            <ToggleChip active={filters.vipOnly} onClick={() => patch({ vipOnly: !filters.vipOnly })}>
              <StarIcon width={13} height={13} />
              VIP
            </ToggleChip>
            <ToggleChip
              active={filters.checkedInOnly}
              onClick={() => patch({ checkedInOnly: !filters.checkedInOnly })}
            >
              <CheckIcon width={13} height={13} />
              Checked in
            </ToggleChip>

            <FilterSelect
              value={filters.role ?? ""}
              onChange={(v) => patch({ role: (v || null) as GuestRole | null })}
              placeholder="All roles"
              options={ALL_ROLES.map((r) => ({ value: r, label: ROLE_META[r].label }))}
            />
            <FilterSelect
              value={filters.rsvp ?? ""}
              onChange={(v) => patch({ rsvp: (v || null) as RsvpStatus | null })}
              placeholder="Any RSVP"
              options={RSVP_ORDER.map((r) => ({ value: r, label: RSVP_META[r].label }))}
            />
            {companies.length > 0 && (
              <FilterSelect
                value={filters.company ?? ""}
                onChange={(v) => patch({ company: v || null })}
                placeholder="Any company"
                options={companies.map((c) => ({ value: c, label: c }))}
              />
            )}
            {event.portfolio.length > 0 && (
              <FilterSelect
                value={filters.portfolioCompanyId ?? ""}
                onChange={(v) => patch({ portfolioCompanyId: v || null })}
                placeholder="Any portfolio"
                options={event.portfolio.map((c) => ({ value: c.id, label: c.name }))}
              />
            )}
            {tags.length > 0 && (
              <FilterSelect
                value={filters.tag ?? ""}
                onChange={(v) => patch({ tag: v || null })}
                placeholder="Any tag"
                options={tags.map((t) => ({ value: t, label: t }))}
              />
            )}
            {hasActiveFilters(filters) && (
              <button
                onClick={() => setFilters(emptyFilters())}
                className="focus-ring rounded-lg px-2 py-1.5 text-xs font-medium text-ink-muted hover:text-ink"
              >
                Clear
              </button>
            )}
          </div>
        </div>

        {/* Roster */}
        {guests.length === 0 ? (
          <EmptyState onAdd={addAttendee} />
        ) : rows.length === 0 ? (
          <NoMatches onClear={() => setFilters(emptyFilters())} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-faint">
                  <th className="w-10 py-2.5" aria-label="Check in" />
                  <th className="py-2.5 pr-4 font-medium">Attendee</th>
                  <th className="py-2.5 pr-4 font-medium">Company / Title</th>
                  <th className="py-2.5 pr-4 font-medium">Role</th>
                  <th className="py-2.5 pr-4 font-medium">RSVP</th>
                  <th className="py-2.5 pr-4 font-medium">Tags</th>
                  <th className="w-10 py-2.5" aria-label="VIP" />
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((g) => (
                  <GuestRow
                    key={g.id}
                    guest={g}
                    event={event}
                    onOpen={() => setDrawer({ guest: g, mode: "edit" })}
                    onCheckIn={() => toggleCheckIn(g)}
                    onVip={() => toggleVip(g)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {drawer && (
        <GuestDrawer
          guest={drawer.guest}
          event={event}
          mode={drawer.mode}
          onClose={() => setDrawer(null)}
        />
      )}
    </div>
  );
}

function GuestRow({
  guest,
  event,
  onOpen,
  onCheckIn,
  onVip,
}: {
  guest: Guest;
  event: CueEvent;
  onOpen: () => void;
  onCheckIn: () => void;
  onVip: () => void;
}) {
  const rsvp = RSVP_META[guest.attendance.rsvp];
  const company = guestCompany(guest, event.portfolio);
  const checkedIn = guest.attendance.checkedIn;

  return (
    <tr className="group cursor-pointer text-ink-muted transition-colors hover:bg-white/[0.02]" onClick={onOpen}>
      {/* Check-in toggle */}
      <td className="py-3" onClick={(e) => e.stopPropagation()}>
        <button
          onClick={onCheckIn}
          aria-pressed={checkedIn}
          aria-label={checkedIn ? "Undo check-in" : "Check in"}
          className={cn(
            "focus-ring grid h-6 w-6 place-items-center rounded-full border transition-colors",
            checkedIn
              ? "border-status-live bg-status-live/20 text-status-live"
              : "border-line text-transparent hover:border-line-strong hover:text-ink-faint",
          )}
        >
          <CheckIcon width={13} height={13} />
        </button>
      </td>

      {/* Attendee */}
      <td className="py-3 pr-4">
        <div className="flex items-center gap-2.5">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-white/[0.06] text-[11px] font-semibold text-ink">
            {initials(guest)}
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="truncate font-medium text-ink">{displayName(guest)}</span>
              {guest.attendance.plusOnes > 0 && (
                <span className="rounded bg-white/[0.06] px-1 text-[10px] font-semibold text-ink-muted">
                  +{guest.attendance.plusOnes}
                </span>
              )}
            </div>
            <span className="block truncate text-xs text-ink-faint">
              {guest.identity.email ?? "—"}
            </span>
          </div>
        </div>
      </td>

      {/* Company / Title */}
      <td className="py-3 pr-4">
        <span className="block truncate text-ink">{company || "—"}</span>
        <span className="block truncate text-xs text-ink-faint">
          {guest.professional.jobTitle ?? ""}
        </span>
      </td>

      {/* Roles */}
      <td className="py-3 pr-4">
        {guest.roles.length === 0 ? (
          <span className="text-ink-faint">—</span>
        ) : (
          <div className="flex flex-wrap gap-1">
            {guest.roles.map((r) => (
              <span
                key={r}
                className="rounded-md border border-line px-1.5 py-0.5 text-[11px] font-medium text-ink-muted"
              >
                {ROLE_META[r].label}
              </span>
            ))}
          </div>
        )}
      </td>

      {/* RSVP */}
      <td className="py-3 pr-4">
        <span className={cn("inline-flex items-center gap-1.5 font-medium", rsvp.tone)}>
          <span className={cn("h-1.5 w-1.5 rounded-full", rsvp.dot)} />
          {rsvp.label}
        </span>
        {guest.attendance.noShow && (
          <span className="ml-1.5 text-[11px] font-medium text-status-draft">No show</span>
        )}
      </td>

      {/* Tags */}
      <td className="py-3 pr-4">
        {guest.tags.length === 0 ? (
          <span className="text-ink-faint">—</span>
        ) : (
          <div className="flex flex-wrap items-center gap-1">
            {guest.tags.slice(0, 2).map((t) => (
              <span
                key={t}
                className="rounded-full border border-line bg-white/[0.03] px-2 py-0.5 text-[11px] text-ink-muted"
              >
                {t}
              </span>
            ))}
            {guest.tags.length > 2 && (
              <span className="text-[11px] text-ink-faint">+{guest.tags.length - 2}</span>
            )}
          </div>
        )}
      </td>

      {/* VIP */}
      <td className="py-3" onClick={(e) => e.stopPropagation()}>
        <button
          onClick={onVip}
          aria-pressed={guest.vip}
          aria-label={guest.vip ? "Remove VIP" : "Mark VIP"}
          className={cn(
            "focus-ring rounded-lg p-1 transition-colors",
            guest.vip
              ? "text-status-draft"
              : "text-ink-faint opacity-0 hover:text-ink group-hover:opacity-100",
          )}
        >
          <StarIcon width={16} height={16} />
        </button>
      </td>
    </tr>
  );
}

function SummaryChip({
  label,
  value,
  accent,
}: {
  label: string;
  value: number;
  accent?: string;
}) {
  return (
    <div className="rounded-xl border border-line bg-white/[0.02] px-3.5 py-2.5">
      <p className="text-[11px] font-medium uppercase tracking-wide text-ink-faint">{label}</p>
      <p className={cn("mt-0.5 text-xl font-semibold tabular-nums", accent ?? "text-ink")}>
        {value}
      </p>
    </div>
  );
}

function ToggleChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "focus-ring inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-sm font-medium transition-colors",
        active
          ? "border-brand-500/40 bg-brand-500/15 text-brand-200"
          : "border-line text-ink-muted hover:text-ink",
      )}
    >
      {children}
    </button>
  );
}

function FilterSelect({
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
    <div className="w-36 shrink-0">
      <Select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label={placeholder}
        className={cn("py-1.5 text-sm", value && "border-brand-500/50 text-ink")}
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

function EmptyState({ onAdd }: { onAdd: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-line px-6 py-16 text-center">
      <span className="grid h-12 w-12 place-items-center rounded-2xl border border-line bg-white/[0.03] text-ink-faint">
        <UsersIcon />
      </span>
      <div>
        <p className="text-sm font-medium text-ink">No attendees yet</p>
        <p className="mt-1 max-w-sm text-xs text-ink-muted">
          Build the guest list for this event. Every attendee becomes a record you can
          search, filter, tag, and check in at the door.
        </p>
      </div>
      <Button onClick={onAdd}>
        <PlusIcon width={16} height={16} />
        Add the first attendee
      </Button>
    </div>
  );
}

function NoMatches({ onClear }: { onClear: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-line px-6 py-12 text-center">
      <p className="text-sm font-medium text-ink">No attendees match</p>
      <p className="max-w-sm text-xs text-ink-muted">
        Try a different search or clear the filters to see the whole roster.
      </p>
      <button onClick={onClear} className="focus-ring mt-1 text-sm font-medium text-brand-300 hover:text-brand-200">
        Clear filters
      </button>
    </div>
  );
}
