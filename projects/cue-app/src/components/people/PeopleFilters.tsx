import type { GuestRole } from "@/lib/types";
import { ALL_ROLES, ROLE_META } from "@/lib/guests-select";
import {
  type PeopleFilters as Filters,
  type PeopleSort,
  type PeopleFacets,
  hasActivePeopleFilters,
} from "@/lib/people-select";
import { Select } from "@/components/ui/Field";
import { cn } from "@/lib/cn";
import { SearchIcon, StarIcon, MicIcon, CheckCircleIcon, CloseIcon } from "@/components/icons";

/**
 * The search + facet toolbar for the People directory. Global search leads; the
 * facet selects and toggle chips narrow across the dimensions the workspace is
 * organized by (role, company, portfolio, organization, interest, VIP, speaker,
 * attendance). Pure controlled component — all state lives in the page.
 */
export function PeopleFilters({
  filters,
  sort,
  facets,
  resultCount,
  onFilters,
  onSort,
  onReset,
}: {
  filters: Filters;
  sort: PeopleSort;
  facets: PeopleFacets;
  resultCount: number;
  onFilters: (next: Filters) => void;
  onSort: (s: PeopleSort) => void;
  onReset: () => void;
}) {
  const set = (patch: Partial<Filters>) => onFilters({ ...filters, ...patch });
  const active = hasActivePeopleFilters(filters);

  return (
    <div className="space-y-3">
      {/* Search + sort */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <SearchIcon
            width={18}
            height={18}
            className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-ink-faint"
          />
          <input
            value={filters.query}
            onChange={(e) => set({ query: e.target.value })}
            placeholder="Search people, companies, interests…"
            className="focus-ring w-full rounded-xl border border-line bg-canvas py-2.5 pl-11 pr-3.5 text-sm text-ink placeholder:text-ink-faint hover:border-line-strong"
            aria-label="Search people"
          />
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor="people-sort" className="text-xs text-ink-faint">
            Sort
          </label>
          <Select
            id="people-sort"
            value={sort}
            onChange={(e) => onSort(e.target.value as PeopleSort)}
            className="w-auto py-2"
          >
            <option value="connections">Most connected</option>
            <option value="recent">Recently seen</option>
            <option value="events">Most attended</option>
            <option value="name">Name (A–Z)</option>
          </Select>
        </div>
      </div>

      {/* Facet selects */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        <FacetSelect
          label="Role"
          value={filters.role ?? ""}
          onChange={(v) => set({ role: (v || null) as GuestRole | null })}
          options={ALL_ROLES.map((r) => ({ value: r, label: ROLE_META[r].label }))}
        />
        <FacetSelect
          label="Company"
          value={filters.company ?? ""}
          onChange={(v) => set({ company: v || null })}
          options={facets.companies.map((c) => ({ value: c, label: c }))}
        />
        <FacetSelect
          label="Portfolio"
          value={filters.portfolioCompanyId ?? ""}
          onChange={(v) => set({ portfolioCompanyId: v || null })}
          options={facets.portfolio.map((p) => ({ value: p.id, label: p.name }))}
        />
        <FacetSelect
          label="Organization"
          value={filters.organization ?? ""}
          onChange={(v) => set({ organization: v || null })}
          options={facets.organizations.map((o) => ({ value: o, label: o }))}
        />
        <FacetSelect
          label="Interest"
          value={filters.interest ?? ""}
          onChange={(v) => set({ interest: v || null })}
          options={facets.interests.map((i) => ({ value: i, label: i }))}
        />
      </div>

      {/* Toggle chips + result count */}
      <div className="flex flex-wrap items-center gap-2">
        <ToggleChip
          active={filters.vipOnly}
          onClick={() => set({ vipOnly: !filters.vipOnly })}
          icon={<StarIcon width={13} height={13} />}
        >
          VIPs
        </ToggleChip>
        <ToggleChip
          active={filters.speakerOnly}
          onClick={() => set({ speakerOnly: !filters.speakerOnly })}
          icon={<MicIcon width={13} height={13} />}
        >
          Speakers
        </ToggleChip>
        <ToggleChip
          active={filters.attendedOnly}
          onClick={() => set({ attendedOnly: !filters.attendedOnly })}
          icon={<CheckCircleIcon width={13} height={13} />}
        >
          Attended
        </ToggleChip>

        <span className="ml-auto text-xs text-ink-faint">
          {resultCount} {resultCount === 1 ? "person" : "people"}
        </span>
        {active && (
          <button
            onClick={onReset}
            className="focus-ring inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs font-medium text-ink-muted hover:text-ink"
          >
            <CloseIcon width={12} height={12} />
            Clear
          </button>
        )}
      </div>
    </div>
  );
}

function FacetSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
}) {
  return (
    <Select
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={cn("py-2 text-xs", value ? "border-line-strong text-ink" : "text-ink-muted")}
    >
      <option value="">{label}: All</option>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </Select>
  );
}

function ToggleChip({
  active,
  onClick,
  icon,
  children,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "focus-ring inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors",
        active
          ? "border-brand-500/40 bg-brand-500/15 text-brand-200"
          : "border-line text-ink-muted hover:text-ink",
      )}
    >
      {icon}
      {children}
    </button>
  );
}
