import { Link } from "react-router-dom";
import type { Person } from "@/lib/people";
import { relativeDay } from "@/lib/format";
import { PersonAvatar } from "./PersonAvatar";
import { MicIcon, CheckCircleIcon, CalendarIcon } from "@/components/icons";

/**
 * A person in the directory — relationship-first, not a table row. The card
 * leads with who they are (photo placeholder, name, role), then the signals an
 * organizer scans for: what they're into, how much history exists, when we last
 * saw them. The whole card links to the full profile.
 */
export function PersonCard({ person }: { person: Person }) {
  const roleLine = [person.title, person.company].filter(Boolean).join(" · ");
  const interests = person.interests.slice(0, 3);

  return (
    <Link
      to={`/people/${person.id}`}
      className="focus-ring card group flex flex-col gap-4 p-5 transition-colors hover:border-line-strong"
    >
      <div className="flex items-start gap-3.5">
        <PersonAvatar person={person} size="md" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate text-[15px] font-semibold text-ink">{person.displayName}</p>
            {person.isSpeaker && (
              <MicIcon width={14} height={14} className="shrink-0 text-brand-400" aria-label="Speaker" />
            )}
          </div>
          <p className="mt-0.5 truncate text-xs text-ink-muted">{roleLine || "—"}</p>
        </div>
      </div>

      {interests.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {interests.map((i) => (
            <span
              key={i}
              className="rounded-full border border-line bg-white/[0.03] px-2 py-0.5 text-[11px] font-medium text-ink-muted"
            >
              {i}
            </span>
          ))}
        </div>
      )}

      <div className="mt-auto flex items-center gap-3 border-t border-line pt-3 text-[11px] text-ink-faint">
        <span className="inline-flex items-center gap-1">
          <CheckCircleIcon width={13} height={13} />
          {person.eventsAttended} attended
        </span>
        <span className="inline-flex items-center gap-1">
          <CalendarIcon width={13} height={13} />
          {person.eventsInvited} invited
        </span>
        {person.lastSeen && (
          <span className="ml-auto truncate">Last seen {relativeDay(person.lastSeen).toLowerCase()}</span>
        )}
      </div>
    </Link>
  );
}
