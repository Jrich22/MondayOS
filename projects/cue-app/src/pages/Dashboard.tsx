import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useEvents } from "@/lib/store";
import type { EventStatus } from "@/lib/types";
import { selectEvents, type EventFilter } from "@/lib/select";
import { fillRatio, isUncapped } from "@/lib/format";
import { EventCard } from "@/components/dashboard/EventCard";
import { StatCard } from "@/components/dashboard/StatCard";
import { SegmentFilter, type Segment } from "@/components/dashboard/SegmentFilter";
import { Button } from "@/components/ui/Button";
import {
  CalendarIcon,
  UsersIcon,
  ChartIcon,
  ClockIcon,
  SearchIcon,
  PlusIcon,
} from "@/components/icons";

/**
 * The Event Dashboard — the landing surface of Cue (TASK-0032).
 *
 * Shows portfolio events with status at a glance, KPI tiles, status filtering,
 * and full-text search. All data comes from the mock layer for the MVP; the
 * component is pure presentation over `events` so a real data source can drop in
 * unchanged later.
 */
export function Dashboard() {
  const events = useEvents();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const createdId = params.get("created");
  const [filter, setFilter] = useState<EventFilter>("all");
  const [query, setQuery] = useState("");

  const stats = useMemo(() => {
    const upcoming = events.filter((e) => e.status === "upcoming").length;
    const live = events.filter((e) => e.status === "live").length;
    const confirmed = events.reduce((n, e) => n + e.confirmedGuests, 0);
    const active = events.filter((e) => e.status !== "done" && !isUncapped(e));
    const avgFill =
      active.length === 0
        ? 0
        : Math.round(
            (active.reduce((n, e) => n + fillRatio(e), 0) / active.length) * 100,
          );
    return { upcoming, live, confirmed, avgFill };
  }, [events]);

  const segments: Segment<EventFilter>[] = useMemo(() => {
    const by = (s: EventStatus) => events.filter((e) => e.status === s).length;
    return [
      { value: "all", label: "All", count: events.length },
      { value: "upcoming", label: "Upcoming", count: by("upcoming") },
      { value: "live", label: "Live", count: by("live") },
      { value: "draft", label: "Drafts", count: by("draft") },
      { value: "done", label: "Past", count: by("done") },
    ];
  }, [events]);

  const visible = useMemo(
    () => selectEvents(events, filter, query),
    [events, filter, query],
  );

  const justCreated = createdId
    ? events.find((e) => e.id === createdId)
    : undefined;

  return (
    <div className="animate-fade-up space-y-8">
      {justCreated && (
        <div className="flex items-center gap-3 rounded-xl border border-status-live/30 bg-status-live/10 px-4 py-3 text-sm text-ink">
          <span className="h-1.5 w-1.5 rounded-full bg-status-live" />
          <span>
            <span className="font-medium">{justCreated.title}</span> was created.
          </span>
          <button
            onClick={() => navigate("/", { replace: true })}
            className="focus-ring ml-auto rounded-md px-2 py-0.5 text-xs text-ink-muted hover:text-ink"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Page heading + primary action */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-ink">
            Events
          </h1>
          <p className="mt-1 text-sm text-ink-muted">
            Every gathering across the portfolio, with status at a glance.
          </p>
        </div>
        <Button onClick={() => navigate("/events/new")}>
          <PlusIcon width={18} height={18} />
          New event
        </Button>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Upcoming"
          value={String(stats.upcoming)}
          hint="scheduled ahead"
          icon={<CalendarIcon />}
        />
        <StatCard
          label="Live now"
          value={String(stats.live)}
          hint="happening today"
          icon={<ClockIcon />}
        />
        <StatCard
          label="Confirmed guests"
          value={stats.confirmed.toLocaleString()}
          hint="across all events"
          icon={<UsersIcon />}
        />
        <StatCard
          label="Avg. fill"
          value={`${stats.avgFill}%`}
          hint="active events"
          icon={<ChartIcon />}
        />
      </div>

      {/* Controls */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <SegmentFilter segments={segments} value={filter} onChange={setFilter} />
        <div className="relative w-full sm:w-72">
          <SearchIcon
            width={18}
            height={18}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint"
          />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search events, venues, companies…"
            className="focus-ring w-full rounded-xl border border-line bg-canvas-raised py-2 pl-10 pr-3 text-sm text-ink placeholder:text-ink-faint"
          />
        </div>
      </div>

      {/* Event grid or empty state */}
      {visible.length > 0 ? (
        <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
          {visible.map((event) => (
            <EventCard key={event.id} event={event} />
          ))}
        </div>
      ) : (
        <EmptyState query={query} />
      )}
    </div>
  );
}

function EmptyState({ query }: { query: string }) {
  return (
    <div className="card flex flex-col items-center justify-center px-6 py-16 text-center">
      <span className="grid h-12 w-12 place-items-center rounded-2xl border border-line bg-white/[0.03] text-ink-faint">
        <CalendarIcon />
      </span>
      <h3 className="mt-4 text-base font-semibold text-ink">No events found</h3>
      <p className="mt-1 max-w-sm text-sm text-ink-muted">
        {query
          ? `Nothing matches “${query}”. Try a different search or clear the filter.`
          : "There are no events in this view yet."}
      </p>
    </div>
  );
}
