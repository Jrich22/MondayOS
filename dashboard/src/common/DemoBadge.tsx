import type { Connection } from "@/state/store";

/**
 * Connection / provenance badge. Always visible so live MondayOS data is never
 * confused with demo data, and a degraded live connection is called out rather
 * than hidden:
 *   LIVE      — connected to the MondayOS API
 *   DEGRADED  — live, but recent requests are failing (data may be stale)
 *   DEMO DATA — running on the offline demo adapter
 */

interface Props {
  connection: Connection;
  reason?: string;
}

export function DemoBadge({ connection, reason }: Props) {
  if (connection === "connecting") {
    return <span className="rounded-full border border-line px-2 py-0.5 text-[10px] text-ink-faint">connecting…</span>;
  }
  if (connection === "live") {
    return (
      <span className="rounded-full border border-status-completed/40 bg-status-completed/10 px-2 py-0.5 text-[10px] font-semibold tracking-wide text-status-completed">
        ● LIVE
      </span>
    );
  }
  if (connection === "degraded") {
    return (
      <span
        title="Live connection, but recent requests are failing — data may be stale."
        className="rounded-full border border-status-awaiting/40 bg-status-awaiting/10 px-2 py-0.5 text-[10px] font-semibold tracking-wide text-status-awaiting"
      >
        ▲ DEGRADED
      </span>
    );
  }
  return (
    <span
      title={reason}
      className="rounded-full border border-status-awaiting/40 bg-status-awaiting/10 px-2 py-0.5 text-[10px] font-semibold tracking-wide text-status-awaiting"
    >
      ▲ DEMO DATA
    </span>
  );
}
