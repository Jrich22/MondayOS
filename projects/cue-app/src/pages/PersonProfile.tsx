import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { usePeople, usePerson, type Person } from "@/lib/people";
import { useEvents } from "@/lib/store";
import { currentUser } from "@/lib/session";
import { personNetwork } from "@/lib/person-graph";
import { personTimeline } from "@/lib/person-timeline";
import { personInsights, personSummary, recommendedEvents } from "@/lib/person-ai";
import { Button } from "@/components/ui/Button";
import { ProfileHeader } from "@/components/people/ProfileHeader";
import { InsightPanel } from "@/components/people/InsightPanel";
import { TimelineView } from "@/components/people/TimelineView";
import { RelationshipNetwork } from "@/components/people/RelationshipNetwork";
import { RecommendedEvents } from "@/components/people/RecommendedEvents";
import { AttendanceHistory } from "@/components/people/AttendanceHistory";
import { CheckCircleIcon, CalendarIcon, NetworkIcon, HistoryIcon } from "@/components/icons";
import type { ReactNode } from "react";

/**
 * Person Profile (TASK-0044) — the full relationship record for one persistent
 * human: who they are, an offline AI read, their whole timeline across events,
 * their relationship network, where to invite them next, and the notes captured
 * along the way. Pure composition over the lib/people* projections; the current
 * organizer anchors the relationship insight.
 */
export function PersonProfile() {
  const { id } = useParams();
  const person = usePerson(id);
  const people = usePeople();
  const events = useEvents();

  const me = useMemo(() => findCurrentUserPerson(people), [people]);

  const network = useMemo(() => (person ? personNetwork(person, people) : null), [person, people]);
  const timeline = useMemo(() => (person ? personTimeline(person, people) : []), [person, people]);
  const insights = useMemo(
    () => (person ? personInsights(person, people, me) : []),
    [person, people, me],
  );
  const summary = useMemo(() => (person ? personSummary(person, people) : ""), [person, people]);
  const recs = useMemo(() => (person ? recommendedEvents(person, events) : []), [person, events]);

  if (!person) {
    return (
      <div className="animate-fade-up flex min-h-[60vh] flex-col items-center justify-center text-center">
        <h1 className="text-xl font-semibold text-ink">Person not found</h1>
        <p className="mt-2 text-sm text-ink-muted">
          This profile doesn't exist — they may not have appeared at any event yet.
        </p>
        <Link to="/people" className="mt-4">
          <Button variant="outline">Back to people</Button>
        </Link>
      </div>
    );
  }

  return (
    <div className="animate-fade-up space-y-6">
      <Link to="/people" className="focus-ring text-sm text-ink-muted hover:text-ink">
        ← People
      </Link>

      <ProfileHeader person={person} />

      {/* Cross-event stats */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MiniStat label="Events attended" value={person.eventsAttended} icon={<CheckCircleIcon width={16} height={16} />} />
        <MiniStat label="Total invited" value={person.eventsInvited} icon={<CalendarIcon width={16} height={16} />} />
        <MiniStat label="Connections" value={network?.coAttendees.length ?? 0} icon={<NetworkIcon width={16} height={16} />} />
        <MiniStat label="Companies met" value={network?.companies.length ?? 0} icon={<HistoryIcon width={16} height={16} />} />
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        {/* Main column */}
        <div className="space-y-6 xl:col-span-2">
          <InsightPanel summary={summary} insights={insights} />

          <TitledCard title="Timeline" hint="Every interaction, newest first">
            <TimelineView entries={timeline} />
          </TitledCard>

          {person.notes.length > 0 && (
            <TitledCard title="Notes" hint="Captured at events">
              <ul className="space-y-3">
                {person.notes.map((n, i) => (
                  <li key={i} className="rounded-xl border border-line bg-white/[0.02] p-3">
                    <p className="text-sm text-ink">{n.text}</p>
                    <p className="mt-1.5 flex items-center gap-2 text-[11px] text-ink-faint">
                      <span
                        className={
                          n.kind === "internal"
                            ? "rounded bg-white/5 px-1.5 py-0.5 font-medium uppercase tracking-wide"
                            : "rounded bg-brand-500/10 px-1.5 py-0.5 font-medium uppercase tracking-wide text-brand-200"
                        }
                      >
                        {n.kind}
                      </span>
                      {n.eventTitle}
                    </p>
                  </li>
                ))}
              </ul>
            </TitledCard>
          )}
        </div>

        {/* Rail */}
        <div className="space-y-6">
          {network && <RelationshipNetwork network={network} />}
          <RecommendedEvents recommendations={recs} />
          <TitledCard title="Attendance history">
            <AttendanceHistory appearances={person.appearances} />
          </TitledCard>
        </div>
      </div>

      <p className="text-center text-[11px] text-ink-faint">
        This profile is assembled from event rosters — local mock data for Sprint 2.
      </p>
    </div>
  );
}

/** Match the mock current user to their Person record by full name. */
function findCurrentUserPerson(people: Person[]): Person | undefined {
  const target = currentUser.name.trim().toLowerCase();
  return people.find(
    (p) => `${p.firstName} ${p.lastName}`.trim().toLowerCase() === target,
  );
}

function MiniStat({ label, value, icon }: { label: string; value: number; icon: ReactNode }) {
  return (
    <div className="card flex items-center gap-3 p-4">
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl border border-line bg-white/[0.03] text-brand-400">
        {icon}
      </span>
      <div>
        <p className="text-xl font-semibold tabular-nums text-ink">{value}</p>
        <p className="text-[11px] text-ink-faint">{label}</p>
      </div>
    </div>
  );
}

function TitledCard({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <section className="card p-5">
      <div className="mb-4 flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold text-ink">{title}</h3>
        {hint && <span className="text-[11px] text-ink-faint">{hint}</span>}
      </div>
      {children}
    </section>
  );
}
