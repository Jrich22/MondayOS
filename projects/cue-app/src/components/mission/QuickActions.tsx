import { Link } from "react-router-dom";
import type { ReactNode } from "react";
import type { QuickAction, QuickActionId } from "@/lib/mission";
import { cn } from "@/lib/cn";
import {
  PlusIcon,
  CheckCircleIcon,
  MailIcon,
  ListIcon,
} from "@/components/icons";

const ICONS: Record<QuickActionId, ReactNode> = {
  create: <PlusIcon width={18} height={18} />,
  rollcall: <CheckCircleIcon width={18} height={18} />,
  invite: <MailIcon width={18} height={18} />,
  agenda: <ListIcon width={18} height={18} />,
};

const HINTS: Record<QuickActionId, string> = {
  create: "Spin up a new event",
  rollcall: "Run live check-in",
  invite: "Grow the guest list",
  agenda: "Draft a run-of-show",
};

/**
 * The Mission Control quick actions. Routing lives in lib/mission
 * (`quickActions`) so it stays testable; this is presentation only. Live-only
 * actions dim to a disabled state when no event is running rather than
 * dead-linking.
 */
export function QuickActions({ actions }: { actions: QuickAction[] }) {
  return (
    <section className="card p-5">
      <p className="text-sm font-semibold text-ink">Quick actions</p>
      <div className="mt-3 grid grid-cols-2 gap-2">
        {actions.map((a) => {
          const inner = (
            <>
              <span
                className={cn(
                  "grid h-9 w-9 place-items-center rounded-xl border border-line",
                  a.enabled ? "bg-white/[0.03] text-brand-400" : "bg-transparent text-ink-faint",
                )}
              >
                {ICONS[a.id]}
              </span>
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium text-ink">{a.label}</span>
                <span className="block truncate text-[11px] text-ink-faint">{HINTS[a.id]}</span>
              </span>
            </>
          );
          const base =
            "flex items-center gap-3 rounded-xl border border-line px-3 py-2.5 text-left transition-colors";
          return a.enabled ? (
            <Link
              key={a.id}
              to={a.to}
              className={cn(base, "focus-ring hover:border-line-strong hover:bg-white/[0.03]")}
            >
              {inner}
            </Link>
          ) : (
            <div
              key={a.id}
              aria-disabled
              title="Needs a live event"
              className={cn(base, "cursor-not-allowed opacity-50")}
            >
              {inner}
            </div>
          );
        })}
      </div>
    </section>
  );
}
