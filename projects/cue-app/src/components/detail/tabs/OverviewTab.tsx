import type { CueEvent } from "@/lib/types";
import { formatEventDate, formatTimeRange, isUncapped } from "@/lib/format";
import { classificationLabel } from "@/lib/classification";
import { rsvpSummary, eventHealth, type HealthCard } from "@/lib/detail";
import { TIMEZONES } from "@/lib/create";
import { Panel, InfoRow } from "@/components/detail/Panel";
import { cn } from "@/lib/cn";

const toneClass: Record<HealthCard["tone"], string> = {
  good: "text-status-live",
  warn: "text-status-draft",
  neutral: "text-ink",
};

function tzLabel(tz: string): string {
  return TIMEZONES.find((t) => t.value === tz)?.label ?? tz;
}

export function OverviewTab({ event }: { event: CueEvent }) {
  const rsvp = rsvpSummary(event);
  const health = eventHealth(event, Date.now());
  const uncapped = isUncapped(event);

  return (
    <div className="space-y-5">
      {/* Health cards */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {health.map((h) => (
          <div key={h.label} className="card p-4">
            <p className="text-xs font-medium uppercase tracking-wide text-ink-faint">
              {h.label}
            </p>
            <p className={cn("mt-1.5 text-xl font-semibold", toneClass[h.tone])}>{h.value}</p>
            <p className="mt-0.5 text-xs text-ink-muted">{h.hint}</p>
          </div>
        ))}
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        {/* Event information */}
        <Panel title="Event information">
          <dl className="divide-y divide-line">
            <InfoRow label="Host">{event.host}</InfoRow>
            <InfoRow label="Type">
              {classificationLabel(event.classification, event.customClassification)}
            </InfoRow>
            <InfoRow label="Date">{formatEventDate(event.startsAt)}</InfoRow>
            <InfoRow label="Time">{formatTimeRange(event.startsAt, event.endsAt)}</InfoRow>
            <InfoRow label="Time zone">{tzLabel(event.timezone)}</InfoRow>
          </dl>
        </Panel>

        {/* Venue */}
        <Panel title="Venue">
          <dl className="divide-y divide-line">
            <InfoRow label="Venue">{event.venue || "TBD"}</InfoRow>
            <InfoRow label="Address">{event.address || "—"}</InfoRow>
            <InfoRow label="City">{event.city || "—"}</InfoRow>
          </dl>
          <div className="mt-4 grid h-28 place-items-center rounded-xl border border-dashed border-line bg-white/[0.02] text-xs text-ink-faint">
            Map preview
          </div>
        </Panel>

        {/* Description */}
        <Panel title="Description" className="lg:col-span-2">
          {event.summary ? (
            <p className="text-sm leading-relaxed text-ink-muted">{event.summary}</p>
          ) : (
            <p className="text-sm text-ink-faint">
              No description yet. Add one from Edit, or draft it with the AI assistant.
            </p>
          )}
        </Panel>

        {/* Capacity & RSVP */}
        <Panel title="Capacity & RSVP">
          <dl className="divide-y divide-line">
            <InfoRow label="Capacity">
              {uncapped ? "No cap" : `${event.capacity.maxAttendees} attendees`}
            </InfoRow>
            <InfoRow label="RSVP">{event.capacity.rsvpEnabled ? "Enabled" : "Off"}</InfoRow>
            <InfoRow label="Waitlist">
              {event.capacity.waitlistEnabled ? "Enabled" : "Off"}
            </InfoRow>
          </dl>

          <div className="mt-4">
            <div className="flex items-center justify-between text-xs text-ink-muted">
              <span>
                <span className="font-medium text-ink">{rsvp.confirmed}</span> confirmed ·{" "}
                {rsvp.pending} pending
              </span>
              <span className="tabular-nums font-medium text-ink">{rsvp.ratePct}%</span>
            </div>
            <div className="mt-2 flex h-2 overflow-hidden rounded-full bg-white/[0.06]">
              <div
                className="bg-status-live"
                style={{ width: `${rsvp.invited ? (rsvp.confirmed / rsvp.invited) * 100 : 0}%` }}
              />
              <div
                className="bg-brand-500/40"
                style={{ width: `${rsvp.invited ? (rsvp.pending / rsvp.invited) * 100 : 0}%` }}
              />
            </div>
            <p className="mt-1.5 text-[11px] text-ink-faint">
              {rsvp.invited} invited · confirmed vs. pending
            </p>
          </div>
        </Panel>

        {/* Live attendance (placeholder) */}
        <Panel
          title="Live attendance"
          action={
            <span className="rounded-md bg-white/5 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-ink-faint">
              Live soon
            </span>
          }
        >
          <div className="flex h-full min-h-32 flex-col items-center justify-center gap-1 text-center">
            <p className="text-3xl font-semibold tabular-nums text-ink-faint">—</p>
            <p className="text-sm text-ink-muted">Check-ins appear here during the event.</p>
            <p className="text-xs text-ink-faint">Powered by Roll Call.</p>
          </div>
        </Panel>
      </div>
    </div>
  );
}
