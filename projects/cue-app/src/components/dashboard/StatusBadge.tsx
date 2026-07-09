import type { EventStatus } from "@/lib/types";
import { STATUS_META } from "@/lib/format";
import { cn } from "@/lib/cn";

/**
 * The at-a-glance status indicator — the core of the dashboard's "status at a
 * glance" requirement. Live events get an animated pulse so they read instantly.
 */
export function StatusBadge({ status }: { status: EventStatus }) {
  const meta = STATUS_META[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ring-1",
        meta.bg,
        meta.text,
        meta.ring,
      )}
    >
      <span
        className={cn(
          "h-1.5 w-1.5 rounded-full",
          meta.dot,
          status === "live" && "animate-pulse-ring",
        )}
      />
      {meta.label}
    </span>
  );
}
