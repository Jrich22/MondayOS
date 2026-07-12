import { useMemo, useState } from "react";
import { usePeople } from "@/lib/people";
import {
  emptyPeopleFilters,
  selectPeople,
  peopleFacets,
  peopleSummary,
  type PeopleSort,
} from "@/lib/people-select";
import { StatCard } from "@/components/dashboard/StatCard";
import { PersonCard } from "@/components/people/PersonCard";
import { PeopleFilters } from "@/components/people/PeopleFilters";
import { UsersIcon, CheckCircleIcon, StarIcon, HistoryIcon, NetworkIcon } from "@/components/icons";

/**
 * People — Cue's relationship intelligence directory (TASK-0044). Not an address
 * book and not a table: a spacious, searchable workspace over every *persistent*
 * human the firm has hosted, resolved across events by lib/people. Each card is a
 * doorway into a full relationship profile.
 *
 * The page is pure composition over the derived people projection: search and
 * facets are local state, everything shown is computed in lib/people-select.
 */
export function People() {
  const people = usePeople();
  const [filters, setFilters] = useState(emptyPeopleFilters);
  const [sort, setSort] = useState<PeopleSort>("connections");

  const facets = useMemo(() => peopleFacets(people), [people]);
  const summary = useMemo(() => peopleSummary(people), [people]);
  const results = useMemo(
    () => selectPeople(people, filters, sort),
    [people, filters, sort],
  );

  return (
    <div className="animate-fade-up space-y-8">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2.5 text-2xl font-semibold tracking-tight text-ink">
            <span className="grid h-8 w-8 place-items-center rounded-xl bg-brand-500/15 text-brand-300">
              <NetworkIcon width={18} height={18} />
            </span>
            People
          </h1>
          <p className="mt-1.5 max-w-2xl text-sm text-ink-muted">
            Everyone you've hosted, remembered across every event. Each person grows richer with
            every interaction — attendance, connections, and context in one place.
          </p>
        </div>
      </div>

      {/* Directory-wide KPIs */}
      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <StatCard label="People" value={String(summary.total)} hint="in the network" icon={<UsersIcon />} />
        <StatCard label="Attended" value={String(summary.attended)} hint="showed up in person" icon={<CheckCircleIcon />} />
        <StatCard label="Recurring" value={String(summary.recurring)} hint="seen at 2+ events" icon={<HistoryIcon />} />
        <StatCard label="VIPs" value={String(summary.vips)} hint="flagged across events" icon={<StarIcon />} />
      </div>

      {/* Search + facets */}
      <PeopleFilters
        filters={filters}
        sort={sort}
        facets={facets}
        resultCount={results.length}
        onFilters={setFilters}
        onSort={setSort}
        onReset={() => setFilters(emptyPeopleFilters())}
      />

      {/* Results */}
      {results.length === 0 ? (
        <div className="card flex flex-col items-center justify-center px-6 py-16 text-center">
          <span className="grid h-12 w-12 place-items-center rounded-2xl border border-line bg-white/[0.03] text-ink-faint">
            <UsersIcon />
          </span>
          <h3 className="mt-4 text-base font-semibold text-ink">No one matches</h3>
          <p className="mt-1 max-w-sm text-sm text-ink-muted">
            Try a broader search or clear the filters to see the whole network.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {results.map((p) => (
            <PersonCard key={p.id} person={p} />
          ))}
        </div>
      )}

      <p className="text-center text-[11px] text-ink-faint">
        People are resolved from event rosters — local mock data for Sprint 2. Every new event
        enriches these profiles automatically.
      </p>
    </div>
  );
}
