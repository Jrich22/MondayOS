import type { CueEvent, Guest } from "@/lib/types";
import {
  AUDIENCE_ORDER,
  AUDIENCE_META,
  audienceCount,
  resolveAudience,
  type AudienceId,
  type Campaign,
} from "@/lib/comms";
import { displayName, guestCompany, initials, uniqueTags } from "@/lib/guests-select";
import { UsersIcon } from "@/components/icons";
import { cn } from "@/lib/cn";

/**
 * The Audience panel — pick who a campaign reaches. Every segment resolves live
 * against the shared Guest roster (Guest Management), so counts are always real
 * and a sample of actual recipients is shown. "Custom Tags" reveals the tags
 * present on this event's roster.
 */
export function AudiencePanel({
  campaign,
  event,
  guests,
  onPatch,
}: {
  campaign: Campaign;
  event: CueEvent;
  guests: Guest[];
  onPatch: (patch: Partial<Campaign>) => void;
}) {
  const tags = uniqueTags(guests);
  const recipients = resolveAudience(guests, campaign.audience, campaign.audienceTag);

  function select(audience: AudienceId) {
    if (audience === "custom") {
      onPatch({ audience, audienceTag: campaign.audienceTag ?? tags[0] });
    } else {
      onPatch({ audience, audienceTag: undefined });
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="grid h-6 w-6 place-items-center rounded-md bg-white/[0.05] text-brand-400">
          <UsersIcon width={15} height={15} />
        </span>
        <div>
          <h3 className="text-sm font-semibold text-ink">Audience</h3>
          <p className="text-[10px] text-ink-faint">Resolved live from the guest list</p>
        </div>
      </div>

      <div className="space-y-1">
        {AUDIENCE_ORDER.map((id) => {
          const count = id === "custom" ? guests.length : audienceCount(guests, id);
          const active = campaign.audience === id;
          return (
            <button
              key={id}
              onClick={() => select(id)}
              className={cn(
                "focus-ring flex w-full items-center gap-2 rounded-lg border px-2.5 py-2 text-left transition-colors",
                active
                  ? "border-brand-500/50 bg-brand-500/[0.08]"
                  : "border-line hover:border-line-strong",
              )}
            >
              <span
                className={cn(
                  "h-1.5 w-1.5 shrink-0 rounded-full",
                  active ? "bg-brand-400" : "bg-ink-faint",
                )}
              />
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium text-ink">{AUDIENCE_META[id].label}</span>
                <span className="block truncate text-[11px] text-ink-faint">
                  {AUDIENCE_META[id].description}
                </span>
              </span>
              {id !== "custom" && (
                <span className="shrink-0 text-xs font-semibold tabular-nums text-ink-muted">
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* Custom tag picker */}
      {campaign.audience === "custom" && (
        <div className="rounded-lg border border-line bg-canvas p-2.5">
          <p className="mb-1.5 text-[11px] font-medium text-ink-muted">Choose a tag</p>
          {tags.length === 0 ? (
            <p className="text-[11px] text-ink-faint">No tags on this roster yet.</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {tags.map((t) => (
                <button
                  key={t}
                  onClick={() => onPatch({ audience: "custom", audienceTag: t })}
                  className={cn(
                    "focus-ring rounded-md border px-2 py-1 text-[11px] font-medium transition-colors",
                    campaign.audienceTag === t
                      ? "border-brand-500/50 bg-brand-500/15 text-brand-100"
                      : "border-line text-ink-muted hover:text-ink",
                  )}
                >
                  #{t}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Recipient sample */}
      <div className="rounded-xl border border-line bg-white/[0.02] p-3">
        <div className="mb-2 flex items-center justify-between">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">
            Recipients
          </p>
          <p className="text-xs font-semibold tabular-nums text-ink">{recipients.length}</p>
        </div>
        {recipients.length === 0 ? (
          <p className="text-[11px] text-ink-faint">No guests match this segment.</p>
        ) : (
          <ul className="space-y-1.5">
            {recipients.slice(0, 5).map((g) => (
              <RecipientRow key={g.id} guest={g} event={event} />
            ))}
            {recipients.length > 5 && (
              <li className="pl-9 text-[11px] text-ink-faint">
                +{recipients.length - 5} more recipient{recipients.length - 5 === 1 ? "" : "s"}
              </li>
            )}
          </ul>
        )}
      </div>
    </div>
  );
}

function RecipientRow({ guest, event }: { guest: Guest; event: CueEvent }) {
  return (
    <li className="flex items-center gap-2.5">
      <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-white/[0.06] text-[10px] font-semibold text-ink">
        {initials(guest)}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-xs font-medium text-ink">{displayName(guest)}</span>
        <span className="block truncate text-[11px] text-ink-faint">
          {guestCompany(guest, event.portfolio) || guest.professional.jobTitle || "—"}
        </span>
      </span>
      {guest.vip && <span className="shrink-0 text-[10px] font-semibold text-brand-300">VIP</span>}
    </li>
  );
}
