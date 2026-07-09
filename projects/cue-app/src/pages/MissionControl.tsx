import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useEvents } from "@/lib/store";
import { useAllGuests } from "@/lib/guests";
import { currentUser } from "@/lib/session";
import { displayName, guestCompany } from "@/lib/guests-select";
import {
  liveEvents,
  flagshipEvent,
  upcomingEvents,
  orgMetrics,
  attendanceHealth,
  vipsAwaiting,
  speakersAwaiting,
  capacitySignal,
  followUps,
  quickActions,
  aiBriefing,
  upcomingMilestones,
  rollCallPath,
  CAPACITY_RISK_PCT,
} from "@/lib/mission";
import { StatCard } from "@/components/dashboard/StatCard";
import { Button } from "@/components/ui/Button";
import { LiveEventHero } from "@/components/mission/LiveEventHero";
import { AttendanceHealthCard } from "@/components/mission/AttendanceHealthCard";
import { AlertCard, type AlertTone } from "@/components/mission/AlertCard";
import { BriefingPanel } from "@/components/mission/BriefingPanel";
import { QuickActions } from "@/components/mission/QuickActions";
import { MilestoneList } from "@/components/mission/MilestoneList";
import { UpcomingList } from "@/components/mission/UpcomingList";
import {
  ClockIcon,
  CalendarIcon,
  UsersIcon,
  ChartIcon,
  StarIcon,
  MicIcon,
  AlertTriangleIcon,
  MailIcon,
  PlusIcon,
  ArrowRightIcon,
} from "@/components/icons";

/**
 * Mission Control (TASK-0042) — the organizer's home screen and Cue's landing
 * surface. Where the Events page is a catalog and Roll Call runs a single event,
 * Mission Control answers one question across the whole portfolio: *what needs
 * me right now?* Live events, attendance health, operational alerts, an AI
 * briefing, quick actions, and what's coming up — all derived from the mock
 * event + guest stores through lib/mission, so this file is pure composition.
 *
 * A 15-second `now` tick refreshes the live-derived numbers (attendance, pace,
 * capacity) without churning the whole tree every second — the same cadence as
 * the Roll Call Command Center.
 */
export function MissionControl() {
  const events = useEvents();
  const allGuests = useAllGuests();
  const navigate = useNavigate();
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 15000);
    return () => clearInterval(t);
  }, []);

  const live = useMemo(() => liveEvents(events), [events]);
  const flagship = useMemo(() => flagshipEvent(events), [events]);
  const upcoming = useMemo(() => upcomingEvents(events), [events]);
  const org = useMemo(() => orgMetrics(events), [events]);
  const health = useMemo(
    () => attendanceHealth(events, allGuests, now),
    [events, allGuests, now],
  );
  const briefing = useMemo(
    () => aiBriefing(events, allGuests, now),
    [events, allGuests, now],
  );
  const milestones = useMemo(() => upcomingMilestones(events, now), [events, now]);
  const actions = useMemo(() => quickActions(flagship), [flagship]);

  const alerts = useMemo(() => {
    const vips = vipsAwaiting(events, allGuests);
    const speakers = speakersAwaiting(events, allGuests);
    const cap = capacitySignal(events, allGuests, now);
    const tasks = followUps(events, allGuests);
    const fallbackRoll = flagship ? rollCallPath(flagship) : undefined;

    const vipTop = vips[0];
    const speakerTop = speakers[0];

    const capTone: AlertTone =
      cap.pct === null ? "clear" : cap.atRisk ? "critical" : cap.pct >= 70 ? "warn" : "clear";

    return [
      {
        key: "vip",
        icon: <StarIcon width={18} height={18} />,
        label: "VIPs not checked in",
        value: String(vips.length),
        detail: vipTop
          ? `${displayName(vipTop)}${flagship ? ` · ${guestCompany(vipTop, flagship.portfolio)}` : ""} and others still expected.`
          : "Every expected VIP is in the room.",
        tone: (vips.length > 0 ? "critical" : "clear") as AlertTone,
        to: vipTop ? `/events/${vipTop.eventId}/rollcall` : fallbackRoll,
      },
      {
        key: "speaker",
        icon: <MicIcon width={18} height={18} />,
        label: "Speakers not arrived",
        value: String(speakers.length),
        detail: speakerTop
          ? `${displayName(speakerTop)} is confirmed but hasn't checked in yet.`
          : "All confirmed speakers have arrived.",
        tone: (speakers.length > 0 ? "warn" : "clear") as AlertTone,
        to: speakerTop ? `/events/${speakerTop.eventId}/rollcall` : fallbackRoll,
      },
      {
        key: "capacity",
        icon: <AlertTriangleIcon width={18} height={18} />,
        label: "Capacity risk",
        value: cap.pct === null ? "—" : `${cap.pct}%`,
        detail:
          cap.pct === null
            ? "No live capped room under pressure."
            : cap.atRisk
              ? `${cap.eventTitle} is near full — hold walk-ins (${cap.checkedIn}/${cap.capacity}).`
              : `${cap.eventTitle}: ${cap.checkedIn} of ${cap.capacity} checked in.`,
        tone: capTone,
        to: cap.eventId ? `/events/${cap.eventId}/rollcall` : undefined,
      },
      {
        key: "followup",
        icon: <MailIcon width={18} height={18} />,
        label: "Follow-up tasks",
        value: String(tasks.total),
        detail:
          tasks.total === 0
            ? "Nothing owed — you're all caught up."
            : `${tasks.waitlist} waitlist decision${tasks.waitlist === 1 ? "" : "s"} · ${tasks.thankYous} thank-you${tasks.thankYous === 1 ? "" : "s"} to send.`,
        tone: (tasks.total > 0 ? "info" : "clear") as AlertTone,
        to: "/events",
      },
    ];
  }, [events, allGuests, now, flagship]);

  const greeting = getGreeting(now);
  const secondaryLive = flagship ? live.filter((e) => e.id !== flagship.id) : live;

  return (
    <div className="animate-fade-up space-y-8">
      {/* Greeting */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-ink">
            {greeting}, {currentUser.name.split(" ")[0]}
          </h1>
          <p className="mt-1 text-sm text-ink-muted">
            {live.length > 0
              ? `${live.length} event${live.length === 1 ? "" : "s"} live right now across the portfolio.`
              : "No events are live right now — here's what's ahead."}
          </p>
        </div>
        <Button onClick={() => navigate("/events/new")}>
          <PlusIcon width={18} height={18} />
          New event
        </Button>
      </div>

      {/* Org-level KPI row */}
      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <StatCard label="Live now" value={String(org.liveCount)} hint="happening today" icon={<ClockIcon />} />
        <StatCard label="Upcoming" value={String(org.upcomingCount)} hint="scheduled ahead" icon={<CalendarIcon />} />
        <StatCard
          label="Confirmed guests"
          value={org.confirmedGuests.toLocaleString()}
          hint="across all events"
          icon={<UsersIcon />}
        />
        <StatCard label="Avg. fill" value={`${org.avgFill}%`} hint="active events" icon={<ChartIcon />} />
      </div>

      {/* Main grid */}
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        {/* Primary column */}
        <div className="space-y-6 xl:col-span-2">
          {flagship ? (
            <LiveEventHero event={flagship} guests={allGuests.filter((g) => g.eventId === flagship.id)} now={now} />
          ) : (
            <NoLiveEvent />
          )}

          {secondaryLive.length > 0 && (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {secondaryLive.map((e) => (
                <SecondaryLiveCard key={e.id} eventId={e.id} title={e.title} venue={e.venue} />
              ))}
            </div>
          )}

          {/* Operational alerts */}
          <div>
            <p className="mb-3 text-sm font-semibold text-ink">Needs attention</p>
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              {alerts.map((a) => (
                <AlertCard
                  key={a.key}
                  icon={a.icon}
                  label={a.label}
                  value={a.value}
                  detail={a.detail}
                  tone={a.tone}
                  to={a.to}
                />
              ))}
            </div>
          </div>

          <UpcomingList events={upcoming} />
        </div>

        {/* Rail */}
        <div className="space-y-6">
          <QuickActions actions={actions} />
          {health.liveEventCount > 0 && <AttendanceHealthCard health={health} />}
          <BriefingPanel recommendations={briefing} />
          <MilestoneList milestones={milestones} />
        </div>
      </div>

      <p className="text-center text-[11px] text-ink-faint">
        Live data is mock/seed for Sprint 2 · Capacity risk trips at {CAPACITY_RISK_PCT}% of room.
      </p>
    </div>
  );
}

function getGreeting(now: number): string {
  const h = new Date(now).getHours();
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

function SecondaryLiveCard({
  eventId,
  title,
  venue,
}: {
  eventId: string;
  title: string;
  venue: string;
}) {
  return (
    <Link
      to={`/events/${eventId}/rollcall`}
      className="focus-ring card group flex items-center gap-3 p-4 transition-colors hover:border-line-strong"
    >
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-status-live/10 text-status-live">
        <span className="h-1.5 w-1.5 animate-pulse-ring rounded-full bg-status-live" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-ink">{title}</span>
        <span className="block truncate text-[11px] text-ink-faint">Live · {venue}</span>
      </span>
      <ArrowRightIcon
        width={16}
        height={16}
        className="shrink-0 text-ink-faint opacity-0 transition-opacity group-hover:opacity-100"
      />
    </Link>
  );
}

function NoLiveEvent() {
  return (
    <div className="card flex flex-col items-center justify-center px-6 py-14 text-center">
      <span className="grid h-12 w-12 place-items-center rounded-2xl border border-line bg-white/[0.03] text-ink-faint">
        <ClockIcon />
      </span>
      <h3 className="mt-4 text-base font-semibold text-ink">Nothing live right now</h3>
      <p className="mt-1 max-w-sm text-sm text-ink-muted">
        When an event goes live, it takes over this space with real-time attendance and one-tap
        access to Roll Call.
      </p>
    </div>
  );
}
