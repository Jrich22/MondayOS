import type { Person } from "@/lib/people";
import { ROLE_META } from "@/lib/guests-select";
import { relativeDay } from "@/lib/format";
import { PersonAvatar } from "./PersonAvatar";
import { MailIcon, PhoneIcon, MicIcon, StarIcon } from "@/components/icons";

/**
 * The profile hero — who this person is, at a glance and relationship-first: the
 * photo placeholder, name, current role, the badges an organizer reads (VIP,
 * speaker), and the affiliations + interests that place them. Contact details
 * sit quietly beneath, never the headline.
 */
export function ProfileHeader({ person }: { person: Person }) {
  const roleLine = [person.title, person.company].filter(Boolean).join(" · ");

  return (
    <div className="card overflow-hidden">
      <div className="h-20 bg-brand-sheen" aria-hidden />
      <div className="px-6 pb-6">
        <div className="-mt-10 flex flex-wrap items-end gap-4">
          <div className="rounded-full ring-4 ring-canvas-raised">
            <PersonAvatar person={person} size="lg" />
          </div>
          <div className="min-w-0 flex-1 pb-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-2xl font-semibold tracking-tight text-ink">{person.displayName}</h1>
              {person.vip && (
                <span className="inline-flex items-center gap-1 rounded-full bg-status-draft/15 px-2 py-0.5 text-[11px] font-semibold text-status-draft">
                  <StarIcon width={12} height={12} /> VIP
                </span>
              )}
              {person.isSpeaker && (
                <span className="inline-flex items-center gap-1 rounded-full bg-brand-500/15 px-2 py-0.5 text-[11px] font-semibold text-brand-200">
                  <MicIcon width={12} height={12} /> Speaker
                </span>
              )}
            </div>
            <p className="mt-1 text-sm text-ink-muted">{roleLine || "—"}</p>
          </div>
        </div>

        {/* Roles + organizations */}
        {(person.roles.length > 0 || person.organizations.length > 0) && (
          <div className="mt-5 flex flex-wrap gap-1.5">
            {person.roles.map((r) => (
              <span
                key={r}
                className="rounded-full border border-line px-2.5 py-0.5 text-xs font-medium text-ink-muted"
              >
                {ROLE_META[r].label}
              </span>
            ))}
            {person.organizations.map((o) => (
              <span
                key={o}
                className="rounded-full border border-brand-500/25 bg-brand-500/10 px-2.5 py-0.5 text-xs font-medium text-brand-200"
              >
                {o}
              </span>
            ))}
          </div>
        )}

        {/* Interests */}
        {person.interests.length > 0 && (
          <div className="mt-3">
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-ink-faint">
              Interests
            </p>
            <div className="flex flex-wrap gap-1.5">
              {person.interests.map((i) => (
                <span
                  key={i}
                  className="rounded-full bg-white/[0.04] px-2.5 py-0.5 text-xs font-medium text-ink-muted"
                >
                  {i}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Contact + first seen */}
        <div className="mt-5 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-line pt-4 text-xs text-ink-muted">
          {person.email && (
            <a href={`mailto:${person.email}`} className="focus-ring inline-flex items-center gap-1.5 hover:text-ink">
              <MailIcon width={14} height={14} className="text-ink-faint" />
              {person.email}
            </a>
          )}
          {person.phone && (
            <span className="inline-flex items-center gap-1.5">
              <PhoneIcon width={14} height={14} className="text-ink-faint" />
              {person.phone}
            </span>
          )}
          {person.firstSeen && (
            <span className="text-ink-faint">
              First seen {relativeDay(person.firstSeen).toLowerCase()}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
