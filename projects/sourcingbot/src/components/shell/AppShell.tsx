/**
 * sourcingBOT premium application shell.
 *
 * Sidebar + header + persistent oversight strip. The oversight strip is not
 * decoration: the supervision boundary is a product rule, so it is stated on
 * every surface rather than buried in settings (docs/LINKEDIN_POLICY.md).
 */
import type { FC } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { cn } from "@/components/ui/Primitives";

const NAV = [
  { to: "/", label: "Talent", end: true, hint: "Your command center" },
  { to: "/reqs", label: "Requisitions", end: false, hint: "Searches and pipelines" },
];

const NavItem: FC<{ to: string; label: string; end: boolean; hint: string }> = ({
  to,
  label,
  end,
  hint,
}) => (
  <NavLink
    to={to}
    end={end}
    className={({ isActive }) =>
      cn(
        "block rounded-lg px-3 py-2 text-sm transition-colors",
        isActive
          ? "bg-brand-500/10 text-brand-200 ring-1 ring-brand-500/25"
          : "text-ink-muted hover:bg-white/5 hover:text-ink",
      )
    }
  >
    <span className="font-medium">{label}</span>
    <span className="mt-0.5 block text-xs text-ink-faint">{hint}</span>
  </NavLink>
);

export const AppShell: FC = () => (
  <div className="flex min-h-screen bg-canvas">
    <aside className="hidden w-60 shrink-0 border-r border-line bg-canvas-raised md:block">
      <div className="flex h-16 items-center gap-2 border-b border-line px-5">
        <div
          aria-hidden
          className="h-7 w-7 rounded-lg bg-gradient-to-br from-brand-400 to-brand-700"
        />
        <div>
          <p className="text-sm font-semibold leading-tight text-ink">sourcingBOT</p>
          <p className="text-[11px] leading-tight text-ink-faint">MondayOS product</p>
        </div>
      </div>
      <nav className="space-y-1 p-3">
        {NAV.map((item) => (
          <NavItem key={item.to} {...item} />
        ))}
      </nav>
      <div className="mx-3 mt-2 rounded-lg border border-oversight-line bg-oversight-soft p-3">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-oversight">
          Supervised sourcing
        </p>
        <p className="mt-1 text-[11px] leading-relaxed text-ink-muted">
          Every LinkedIn action is human-initiated. No unattended scraping.
        </p>
      </div>
    </aside>

    <div className="flex min-w-0 flex-1 flex-col">
      <header className="flex h-16 items-center justify-between border-b border-line px-6">
        <div className="md:hidden">
          <p className="text-sm font-semibold text-ink">sourcingBOT</p>
        </div>
        <div className="hidden md:block">
          <p className="text-sm text-ink-muted">Recruiting sourcing workspace</p>
        </div>
        <span className="rounded-full border border-line px-2.5 py-1 text-xs text-ink-faint">
          Foundation increment · local data
        </span>
      </header>

      <main className="min-w-0 flex-1 px-6 py-6">
        <Outlet />
      </main>
    </div>
  </div>
);
