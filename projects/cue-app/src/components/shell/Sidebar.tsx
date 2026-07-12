import { NavLink } from "react-router-dom";
import { cn } from "@/lib/cn";
import {
  LayoutIcon,
  CalendarIcon,
  UsersIcon,
  NetworkIcon,
  BuildingIcon,
  SparklesIcon,
  SettingsIcon,
} from "@/components/icons";
import type { ReactNode } from "react";

interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  soon?: boolean;
}

/**
 * Primary navigation. Mission Control and Events are live now; later surfaces
 * are shown with a "soon" marker so the shell reads as a real product while
 * those tasks are still in flight. The nav is data-driven so adding a surface is
 * one entry.
 */
const NAV: NavItem[] = [
  { to: "/", label: "Mission Control", icon: <LayoutIcon /> },
  { to: "/events", label: "Events", icon: <CalendarIcon /> },
  { to: "/people", label: "People", icon: <NetworkIcon /> },
  { to: "/guests", label: "Guests", icon: <UsersIcon />, soon: true },
  { to: "/portfolio", label: "Portfolio", icon: <BuildingIcon />, soon: true },
  { to: "/assistant", label: "AI Assistant", icon: <SparklesIcon />, soon: true },
];

export function Sidebar() {
  return (
    <aside className="hidden w-64 shrink-0 flex-col border-r border-line bg-canvas-raised/60 px-4 py-5 lg:flex">
      <div className="flex items-center gap-2.5 px-2">
        <span className="grid h-9 w-9 place-items-center rounded-xl bg-brand-600 text-white shadow-glow">
          <SparklesIcon width={20} height={20} />
        </span>
        <div className="leading-tight">
          <p className="text-sm font-semibold text-ink">Cue</p>
          <p className="text-[11px] text-ink-faint">Event Operations</p>
        </div>
      </div>

      <nav className="mt-8 flex-1 space-y-1">
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            className={({ isActive }) =>
              cn(
                "focus-ring group flex items-center gap-3 rounded-xl px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-white/[0.06] text-ink"
                  : "text-ink-muted hover:bg-white/[0.03] hover:text-ink",
              )
            }
          >
            {({ isActive }) => (
              <>
                <span className={cn(isActive ? "text-brand-400" : "text-ink-faint")}>
                  {item.icon}
                </span>
                <span className="flex-1">{item.label}</span>
                {item.soon && (
                  <span className="rounded-md bg-white/5 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-ink-faint">
                    Soon
                  </span>
                )}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      <div className="space-y-1 border-t border-line pt-3">
        <button className="focus-ring flex w-full items-center gap-3 rounded-xl px-3 py-2 text-sm font-medium text-ink-muted transition-colors hover:bg-white/[0.03] hover:text-ink">
          <span className="text-ink-faint">
            <SettingsIcon />
          </span>
          Settings
        </button>
      </div>
    </aside>
  );
}
