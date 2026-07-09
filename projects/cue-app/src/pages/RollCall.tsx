import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useEvent } from "@/lib/store";
import { useGuests, updateGuest } from "@/lib/guests";
import { withCheckIn } from "@/lib/guests-select";
import { liveMetrics } from "@/lib/rollcall";
import type { Guest } from "@/lib/types";
import { CommandBar } from "@/components/rollcall/CommandBar";
import { AttendeeRoster } from "@/components/rollcall/AttendeeRoster";
import { RightRail } from "@/components/rollcall/RightRail";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";

/**
 * Roll Call Command Center (TASK-0040) — the live operational control center for
 * running an event, and Cue's flagship execution surface. A full-viewport mode
 * (no app chrome): its own top bar, a large attendee panel on the left for
 * instant search + one-tap check-in, and a live board on the right (metrics,
 * arrival feeds, alerts, AI copilot). Built so an event manager can check in
 * hundreds of attendees without ever leaving this screen.
 *
 * The clock advances on two cadences: a self-contained per-second clock in the
 * command bar, and a 15-second `now` tick here that refreshes the derived
 * metrics and arrival feeds without re-rendering the roster every second.
 */
export function RollCall() {
  const { id } = useParams();
  const event = useEvent(id);
  const guests = useGuests(id ?? "");
  const [now, setNow] = useState(() => Date.now());
  const [mobileView, setMobileView] = useState<"list" | "board">("list");

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 15000);
    return () => clearInterval(t);
  }, []);

  const onCheckIn = useCallback((g: Guest) => {
    updateGuest(withCheckIn(g, !g.attendance.checkedIn, new Date().toISOString()));
  }, []);

  const metrics = useMemo(
    () => (event ? liveMetrics(guests, event, now) : null),
    [guests, event, now],
  );

  if (!event) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-canvas text-center">
        <h1 className="text-xl font-semibold text-ink">Event not found</h1>
        <p className="text-sm text-ink-muted">This event doesn't exist or was removed.</p>
        <Link to="/">
          <Button variant="outline">Back to events</Button>
        </Link>
      </div>
    );
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-canvas">
      <CommandBar event={event} metrics={metrics!} />

      {/* Mobile panel switch — desktop/tablet-landscape shows both panels. */}
      <div className="flex shrink-0 gap-1 border-b border-line px-4 py-2 lg:hidden">
        <SwitchTab active={mobileView === "list"} onClick={() => setMobileView("list")}>
          Attendees
        </SwitchTab>
        <SwitchTab active={mobileView === "board"} onClick={() => setMobileView("board")}>
          Live board
        </SwitchTab>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* Left — attendee panel */}
        <main
          className={cn(
            "min-w-0 flex-1 flex-col p-4 sm:px-5",
            mobileView === "list" ? "flex" : "hidden",
            "lg:flex",
          )}
        >
          <AttendeeRoster event={event} guests={guests} onCheckIn={onCheckIn} />
        </main>

        {/* Right — live board */}
        <aside
          className={cn(
            "w-full shrink-0 overflow-y-auto border-line bg-canvas-raised/40 p-4 lg:block lg:w-[384px] lg:border-l",
            mobileView === "board" ? "block" : "hidden",
          )}
        >
          <RightRail event={event} guests={guests} now={now} />
        </aside>
      </div>
    </div>
  );
}

function SwitchTab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "focus-ring flex-1 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
        active ? "bg-white/[0.08] text-ink" : "text-ink-muted hover:text-ink",
      )}
    >
      {children}
    </button>
  );
}
