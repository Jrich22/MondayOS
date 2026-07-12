import { Link } from "react-router-dom";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { ArrowRightIcon } from "@/components/icons";

export type AlertTone = "critical" | "warn" | "info" | "clear";

const TONE: Record<AlertTone, { ring: string; icon: string; chip: string }> = {
  critical: {
    ring: "hover:border-red-500/40",
    icon: "text-red-400 bg-red-500/10",
    chip: "text-red-300",
  },
  warn: {
    ring: "hover:border-status-draft/40",
    icon: "text-status-draft bg-status-draft/10",
    chip: "text-status-draft",
  },
  info: {
    ring: "hover:border-brand-500/40",
    icon: "text-brand-400 bg-brand-500/10",
    chip: "text-brand-200",
  },
  clear: {
    ring: "hover:border-status-live/30",
    icon: "text-status-live bg-status-live/10",
    chip: "text-status-live",
  },
};

/**
 * A single operational alert tile: an icon, a headline count, and a one-line
 * detail. When nothing is wrong it renders in the calm "clear" tone rather than
 * disappearing, so the four operational signals (VIPs, speaker, capacity,
 * follow-ups) are always legible at a glance — present, and either green or hot.
 */
export function AlertCard({
  icon,
  label,
  value,
  detail,
  tone,
  to,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  detail: string;
  tone: AlertTone;
  to?: string;
}) {
  const t = TONE[tone];
  const body = (
    <div
      className={cn(
        "card group flex h-full flex-col p-4 transition-all duration-200",
        to && "hover:-translate-y-0.5",
        t.ring,
      )}
    >
      <div className="flex items-start justify-between">
        <span className={cn("grid h-9 w-9 place-items-center rounded-xl", t.icon)}>
          {icon}
        </span>
        {to && (
          <ArrowRightIcon
            width={16}
            height={16}
            className="text-ink-faint opacity-0 transition-opacity group-hover:opacity-100"
          />
        )}
      </div>
      <p className="mt-3 text-xs font-medium uppercase tracking-wide text-ink-faint">
        {label}
      </p>
      <p className={cn("mt-1 text-2xl font-semibold tabular-nums", tone === "clear" ? "text-ink" : t.chip)}>
        {value}
      </p>
      <p className="mt-1 line-clamp-2 text-xs text-ink-muted">{detail}</p>
    </div>
  );

  return to ? (
    <Link to={to} className="focus-ring rounded-2xl">
      {body}
    </Link>
  ) : (
    body
  );
}
