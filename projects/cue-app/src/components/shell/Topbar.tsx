import { currentUser } from "@/lib/session";

/**
 * The app top bar: firm context on the left, the mock current-user chip on the
 * right. Sticky so it stays available while the content scrolls.
 */
export function Topbar() {
  return (
    <header className="sticky top-0 z-10 flex h-16 items-center justify-between border-b border-line bg-canvas/80 px-6 backdrop-blur-xl">
      <div className="flex items-center gap-2 text-sm">
        <span className="font-medium text-ink">{currentUser.firm}</span>
        <span className="rounded-md border border-line px-1.5 py-0.5 text-[11px] font-medium text-ink-faint">
          MVP
        </span>
      </div>

      <div className="flex items-center gap-3">
        <div className="hidden text-right sm:block">
          <p className="text-sm font-medium leading-tight text-ink">
            {currentUser.name}
          </p>
          <p className="text-xs leading-tight text-ink-faint">
            {currentUser.role}
          </p>
        </div>
        <span className="grid h-9 w-9 place-items-center rounded-full bg-gradient-to-br from-brand-500 to-brand-700 text-sm font-semibold text-white">
          {currentUser.initials}
        </span>
      </div>
    </header>
  );
}
