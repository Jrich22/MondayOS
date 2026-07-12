import { workspaceMetrics, compact, pct, type Campaign } from "@/lib/comms";
import { cn } from "@/lib/cn";

/**
 * The seven headline metrics for the workspace, rolled up across an event's
 * campaigns (lib/comms.workspaceMetrics): Sent, Scheduled, Opened, Clicked, RSVP
 * Rate, Delivery, Responses. A slim horizontal board rather than big cards —
 * always-visible context above the builder, in the spirit of a mission-control
 * status strip.
 */
export function MetricsBar({ campaigns, eventId }: { campaigns: Campaign[]; eventId: string }) {
  const m = workspaceMetrics(campaigns, eventId);
  const tiles: { label: string; value: string; accent?: boolean }[] = [
    { label: "Sent", value: compact(m.sent) },
    { label: "Scheduled", value: String(m.scheduled) },
    { label: "Opened", value: compact(m.opened) },
    { label: "Clicked", value: compact(m.clicked) },
    { label: "RSVP rate", value: pct(m.rsvpRate), accent: true },
    { label: "Delivery", value: pct(m.deliveryRate) },
    { label: "Responses", value: compact(m.responded) },
  ];

  return (
    <div className="card grid grid-cols-4 divide-line overflow-hidden sm:grid-cols-7 sm:divide-x">
      {tiles.map((t) => (
        <div key={t.label} className="px-3 py-2.5">
          <p className="text-[10px] font-medium uppercase tracking-wide text-ink-faint">{t.label}</p>
          <p
            className={cn(
              "mt-0.5 text-lg font-semibold tabular-nums",
              t.accent ? "text-brand-300" : "text-ink",
            )}
          >
            {t.value}
          </p>
        </div>
      ))}
    </div>
  );
}
