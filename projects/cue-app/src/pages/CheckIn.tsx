import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useEvent } from "@/lib/store";
import { useGuests, updateGuest, addGuest } from "@/lib/guests";
import { withCheckIn } from "@/lib/guests-select";
import { liveMetrics, recentArrivals, arrivalTime } from "@/lib/rollcall";
import { validateScan, type ScanResult } from "@/lib/qr";
import { walkInGuest, shouldCheckIn, type WalkInInput } from "@/lib/checkin";
import { initials, displayName } from "@/lib/guests-select";
import { ScannerPanel } from "@/components/checkin/ScannerPanel";
import { SearchPanel } from "@/components/checkin/SearchPanel";
import { WalkInPanel } from "@/components/checkin/WalkInPanel";
import { ScanResultCard } from "@/components/checkin/ScanResultCard";
import { Button } from "@/components/ui/Button";
import {
  ArrowLeftIcon,
  ScanIcon,
  SearchIcon,
  UserPlusIcon,
  ClockIcon,
  ListIcon,
  HomeIcon,
  ArrowRightIcon,
  CheckCircleIcon,
  StarIcon,
  UsersIcon,
} from "@/components/icons";
import { cn } from "@/lib/cn";

/**
 * QR & Badge Check-In kiosk (TASK-0045) — the on-site registration desk. A
 * full-viewport mode routed outside the app shell (like Roll Call), landscape-
 * tablet first. It opens on a branded Welcome landing with three large choices
 * — Scan, Search, Walk-In — then drops into that mode; a Welcome home button
 * returns to the landing. Every path resolves through the same validation layer
 * (lib/qr) and commits through the same `withCheckIn` seam Roll Call uses, so a
 * scan here updates Roll Call, Mission Control, and the relationship timeline
 * instantly with no duplicate state.
 *
 * The result stage on the right is the focal point: it shows the scan outcome
 * with success / error / VIP-celebration motion, and otherwise a live "ready"
 * board with the latest arrivals so the desk always sees the room filling.
 */

type Mode = "scanner" | "search" | "walkin";
/** The landing screen plus the three working modes. */
type Screen = "welcome" | Mode;

const MODES: { id: Mode; label: string; icon: React.ReactNode }[] = [
  { id: "scanner", label: "Scanner", icon: <ScanIcon width={17} height={17} /> },
  { id: "search", label: "Search", icon: <SearchIcon width={17} height={17} /> },
  { id: "walkin", label: "Walk-in", icon: <UserPlusIcon width={17} height={17} /> },
];

/** How long a result stays on the stage before the kiosk resets to "ready". */
const RESULT_TTL_MS = 4200;
const VIP_RESULT_TTL_MS = 5200;

export function CheckIn() {
  const { id } = useParams();
  const event = useEvent(id);
  const guests = useGuests(id ?? "");

  const [mode, setMode] = useState<Screen>("welcome");
  const [result, setResult] = useState<ScanResult | null>(null);
  const [focusSignal, setFocusSignal] = useState(0);
  const [now, setNow] = useState(() => Date.now());

  // Last accepted scan, for double-fire suppression. A ref so it never triggers
  // a re-render and is always current when the next scan resolves.
  const lastScan = useRef<{ payload: string; at: number } | null>(null);
  const clearTimer = useRef<number | null>(null);

  // 15s tick refreshes the live board without repainting on every second.
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 15000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => () => {
    if (clearTimer.current) window.clearTimeout(clearTimer.current);
  }, []);

  const scheduleClear = useCallback((vip: boolean) => {
    if (clearTimer.current) window.clearTimeout(clearTimer.current);
    clearTimer.current = window.setTimeout(
      () => setResult(null),
      vip ? VIP_RESULT_TTL_MS : RESULT_TTL_MS,
    );
  }, []);

  const present = useCallback(
    (res: ScanResult) => {
      setResult(res);
      setNow(Date.now());
      setFocusSignal((n) => n + 1);
      scheduleClear(Boolean(res.guest?.vip));
    },
    [scheduleClear],
  );

  // The single scan pipeline every mode feeds into.
  const handleScan = useCallback(
    (raw: string) => {
      if (!event) return;
      const at = Date.now();
      const res = validateScan(raw, { event, guests, now: at, last: lastScan.current });
      if (shouldCheckIn(res.status) && res.guest) {
        updateGuest(withCheckIn(res.guest, true, new Date(at).toISOString()));
      }
      lastScan.current = { payload: raw, at };
      present(res);
    },
    [event, guests, present],
  );

  const handleWalkIn = useCallback(
    (input: WalkInInput) => {
      if (!event) return;
      const at = new Date().toISOString();
      const created = walkInGuest(event, input, `gst-${crypto.randomUUID().slice(0, 8)}`, at);
      addGuest(created);
      setMode("scanner");
      present({
        status: "ready",
        guest: created,
        reason: created.vip ? "VIP walk-in checked in." : "Walk-in created and checked in.",
      });
    },
    [event, present],
  );

  // Enter a working mode from the landing (or the tabs), refocusing its input.
  const enterMode = useCallback((m: Mode) => {
    setMode(m);
    setFocusSignal((n) => n + 1);
  }, []);

  // Return to the Welcome landing, clearing any lingering result.
  const goHome = useCallback(() => {
    if (clearTimer.current) window.clearTimeout(clearTimer.current);
    setResult(null);
    setMode("welcome");
  }, []);

  const metrics = useMemo(
    () => (event ? liveMetrics(guests, event, now) : null),
    [guests, event, now],
  );
  const recent = useMemo(() => recentArrivals(guests, 6), [guests]);

  if (!event) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-canvas text-center">
        <h1 className="text-xl font-semibold text-ink">Event not found</h1>
        <p className="text-sm text-ink-muted">This event doesn't exist or was removed.</p>
        <Link to="/events">
          <Button variant="outline">Back to events</Button>
        </Link>
      </div>
    );
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-canvas">
      <KioskBar event={event} metrics={metrics!} />

      {mode === "welcome" ? (
        <WelcomeLanding metrics={metrics!} onPick={enterMode} />
      ) : (
        <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
          {/* Left — active mode */}
          <main className="flex min-h-0 min-w-0 flex-1 flex-col p-4 sm:p-6">
            <ModeSwitch mode={mode} onChange={enterMode} onHome={goHome} />
            <div className="mt-5 min-h-0 flex-1 overflow-y-auto pr-0.5">
              {mode === "scanner" && (
                <ScannerPanel event={event} guests={guests} onScan={handleScan} focusSignal={focusSignal} />
              )}
              {mode === "search" && (
                <SearchPanel event={event} guests={guests} onScan={handleScan} focusSignal={focusSignal} />
              )}
              {mode === "walkin" && <WalkInPanel onCreate={handleWalkIn} />}
            </div>
          </main>

          {/* Right — result stage / live board */}
          <aside className="flex w-full shrink-0 flex-col items-center justify-center gap-5 border-line bg-canvas-raised/40 p-4 sm:p-6 lg:w-[26rem] lg:border-l">
            {result ? (
              <ScanResultCard result={result} event={event} nonce={focusSignal} />
            ) : (
              <IdleStage metrics={metrics!} recent={recent} />
            )}
          </aside>
        </div>
      )}
    </div>
  );
}

// --- Kiosk header -----------------------------------------------------------

function KioskBar({
  event,
  metrics,
}: {
  event: CueEventLike;
  metrics: ReturnType<typeof liveMetrics>;
}) {
  const isLive = event.status === "live";
  return (
    <header className="flex flex-wrap items-center gap-x-6 gap-y-3 border-b border-line bg-canvas-raised/80 px-4 py-3 backdrop-blur-xl sm:px-6">
      <div className="flex min-w-0 items-center gap-3">
        <Link
          to={`/events/${event.id}`}
          aria-label="Exit check-in kiosk"
          className="focus-ring grid h-9 w-9 shrink-0 place-items-center rounded-xl border border-line text-ink-muted transition-colors hover:text-ink"
        >
          <ArrowLeftIcon width={18} height={18} />
        </Link>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="truncate text-base font-semibold tracking-tight text-ink">{event.title}</h1>
            {isLive && (
              <span className="relative inline-flex shrink-0 items-center gap-1.5 rounded-full bg-status-live/12 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-status-live">
                <span className="relative flex h-1.5 w-1.5">
                  <span className="absolute inline-flex h-full w-full animate-pulse-ring rounded-full bg-status-live/60" />
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-status-live" />
                </span>
                Live
              </span>
            )}
          </div>
          <p className="truncate text-xs text-ink-faint">{event.venue} · Check-In Kiosk</p>
        </div>
      </div>

      <div className="ml-auto flex items-center gap-x-5 gap-y-2 overflow-x-auto sm:gap-x-7">
        <BarStat label="Checked in" value={metrics.checkedIn} accent="text-status-live" primary />
        <BarStat label="VIPs in" value={`${metrics.vipCheckedIn}/${metrics.vipTotal}`} accent="text-status-draft" />
        <BarStat label="Remaining" value={metrics.remaining} />
        <BarStat label="Attendance" value={`${metrics.attendancePct}%`} accent="text-brand-400" />
        <Link
          to={`/events/${event.id}/rollcall`}
          className="focus-ring hidden shrink-0 items-center gap-1.5 rounded-xl border border-line-strong px-3 py-2 text-xs font-medium text-ink-muted hover:text-ink sm:inline-flex"
        >
          <ListIcon width={15} height={15} />
          Roll Call
        </Link>
      </div>
    </header>
  );
}

/** Structural subset of CueEvent the kiosk header reads. */
type CueEventLike = { id: string; title: string; venue: string; status: string };

function BarStat({
  label,
  value,
  accent,
  primary,
}: {
  label: string;
  value: string | number;
  accent?: string;
  primary?: boolean;
}) {
  return (
    <div className="shrink-0 text-right sm:text-left">
      <p className="text-[10px] font-medium uppercase tracking-wide text-ink-faint">{label}</p>
      <p className={cn("tabular-nums font-semibold leading-tight", primary ? "text-xl" : "text-lg", accent ?? "text-ink")}>
        {value}
      </p>
    </div>
  );
}

// --- Mode switch ------------------------------------------------------------

function ModeSwitch({
  mode,
  onChange,
  onHome,
}: {
  mode: Mode;
  onChange: (m: Mode) => void;
  onHome: () => void;
}) {
  return (
    <div className="flex gap-1.5 rounded-2xl border border-line bg-canvas-raised/60 p-1.5">
      <button
        onClick={onHome}
        aria-label="Back to welcome"
        className="focus-ring flex shrink-0 items-center justify-center gap-2 rounded-xl px-3 py-3 text-sm font-semibold text-ink-muted transition-colors hover:bg-white/[0.04] hover:text-ink"
      >
        <HomeIcon width={17} height={17} />
        <span className="hidden sm:inline">Welcome</span>
      </button>
      <span className="my-1 w-px shrink-0 bg-line" />
      {MODES.map((m) => (
        <button
          key={m.id}
          onClick={() => onChange(m.id)}
          aria-pressed={mode === m.id}
          className={cn(
            "focus-ring flex flex-1 items-center justify-center gap-2 rounded-xl px-4 py-3 text-sm font-semibold transition-colors",
            mode === m.id
              ? "bg-brand-600 text-white shadow-glow"
              : "text-ink-muted hover:bg-white/[0.04] hover:text-ink",
          )}
        >
          {m.icon}
          {m.label}
        </button>
      ))}
    </div>
  );
}

// --- Welcome landing --------------------------------------------------------

const WELCOME_CHOICES: {
  id: Mode;
  title: string;
  detail: string;
  icon: React.ReactNode;
}[] = [
  {
    id: "scanner",
    title: "Scan QR Code",
    detail: "Fastest option",
    icon: <ScanIcon width={24} height={24} />,
  },
  {
    id: "search",
    title: "Search Guest",
    detail: "Find by name or company",
    icon: <SearchIcon width={24} height={24} />,
  },
  {
    id: "walkin",
    title: "Register Walk-In",
    detail: "Create a new attendee",
    icon: <UserPlusIcon width={24} height={24} />,
  },
];

function WelcomeLanding({
  metrics,
  onPick,
}: {
  metrics: ReturnType<typeof liveMetrics>;
  onPick: (m: Mode) => void;
}) {
  return (
    <main className="flex min-h-0 flex-1 flex-col items-center justify-center overflow-y-auto p-6 sm:p-8">
      <div className="animate-fade-up w-full max-w-xl">
        {/* Hero */}
        <div className="text-center">
          <h1 className="text-3xl font-bold tracking-tight text-ink sm:text-4xl">Welcome</h1>
          <p className="mt-2 text-sm text-ink-muted">How would you like to check in?</p>
        </div>

        {/* Choices — full-width, large tap targets */}
        <div className="mt-8 space-y-3">
          {WELCOME_CHOICES.map((c, i) => (
            <button
              key={c.id}
              onClick={() => onPick(c.id)}
              className="focus-ring group flex w-full items-center gap-4 rounded-2xl border border-line bg-canvas-raised px-5 py-5 text-left transition-all hover:-translate-y-0.5 hover:border-brand-500/50 hover:bg-white/[0.03] hover:shadow-glow"
            >
              <span className="grid h-14 w-14 shrink-0 place-items-center rounded-2xl bg-brand-500/12 text-brand-300 transition-colors group-hover:bg-brand-500/20 group-hover:text-brand-200">
                {c.icon}
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2">
                  <span className="text-lg font-semibold text-ink">{c.title}</span>
                  {i === 0 && (
                    <span className="rounded-full bg-status-live/12 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-status-live">
                      Fastest
                    </span>
                  )}
                </span>
                <span className="mt-0.5 block text-sm text-ink-faint">{c.detail}</span>
              </span>
              <ArrowRightIcon
                width={20}
                height={20}
                className="shrink-0 text-ink-faint transition-transform group-hover:translate-x-0.5 group-hover:text-brand-300"
              />
            </button>
          ))}
        </div>

        {/* Live Today */}
        <div className="mt-9">
          <div className="mb-3 flex items-center gap-2">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-pulse-ring rounded-full bg-status-live/60" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-status-live" />
            </span>
            <h2 className="text-xs font-semibold uppercase tracking-[0.15em] text-ink-faint">Live Today</h2>
          </div>
          <div className="grid grid-cols-3 gap-2.5">
            <LiveStat
              icon={<CheckCircleIcon width={18} height={18} />}
              value={metrics.checkedIn}
              label="Checked In"
              accent="text-status-live"
              tint="bg-status-live/12"
            />
            <LiveStat
              icon={<StarIcon width={18} height={18} />}
              value={metrics.vipCheckedIn}
              label="VIPs Arrived"
              accent="text-status-draft"
              tint="bg-status-draft/12"
            />
            <LiveStat
              icon={<UsersIcon width={18} height={18} />}
              value={metrics.remaining}
              label="Remaining"
              accent="text-ink"
              tint="bg-white/[0.05]"
            />
          </div>
        </div>
      </div>
    </main>
  );
}

function LiveStat({
  icon,
  value,
  label,
  accent,
  tint,
}: {
  icon: React.ReactNode;
  value: number;
  label: string;
  accent: string;
  tint: string;
}) {
  return (
    <div className="rounded-2xl border border-line bg-canvas-raised px-3 py-3.5 text-center">
      <span className={cn("mx-auto grid h-8 w-8 place-items-center rounded-lg", tint, accent)}>{icon}</span>
      <p className={cn("mt-2 text-2xl font-bold tabular-nums leading-none", accent)}>{value}</p>
      <p className="mt-1 text-[11px] font-medium text-ink-muted">{label}</p>
    </div>
  );
}

// --- Idle stage -------------------------------------------------------------

function IdleStage({
  metrics,
  recent,
}: {
  metrics: ReturnType<typeof liveMetrics>;
  recent: ReturnType<typeof recentArrivals>;
}) {
  return (
    <div className="flex w-full max-w-sm flex-col items-center gap-5 text-center">
      <div className="grid h-20 w-20 place-items-center rounded-2xl border border-line bg-white/[0.03] text-brand-300">
        <ScanIcon width={34} height={34} />
      </div>
      <div>
        <p className="text-lg font-semibold text-ink">Ready to scan</p>
        <p className="mt-1 text-sm text-ink-muted">
          Scan a badge, search a name, or add a walk-in.
        </p>
      </div>

      <div className="grid w-full grid-cols-2 gap-2">
        <MiniStat label="Checked in" value={metrics.checkedIn} accent="text-status-live" />
        <MiniStat label="Remaining" value={metrics.remaining} />
      </div>

      <div className="w-full text-left">
        <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-ink-faint">
          <ClockIcon width={14} height={14} />
          Latest arrivals
        </div>
        {recent.length === 0 ? (
          <p className="rounded-xl border border-dashed border-line px-4 py-6 text-center text-sm text-ink-muted">
            No arrivals yet — you're first at the door.
          </p>
        ) : (
          <ul className="space-y-1.5">
            {recent.map((g) => (
              <li key={g.id} className="flex items-center gap-2.5 rounded-xl border border-line bg-white/[0.02] px-3 py-2">
                <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-white/[0.06] text-[11px] font-semibold text-ink">
                  {initials(g)}
                </span>
                <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink">{displayName(g)}</span>
                <span className="shrink-0 text-[11px] tabular-nums text-ink-faint">{arrivalTime(g)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function MiniStat({ label, value, accent }: { label: string; value: number; accent?: string }) {
  return (
    <div className="rounded-xl border border-line bg-white/[0.02] px-3 py-2.5 text-center">
      <p className={cn("text-2xl font-bold tabular-nums leading-none", accent ?? "text-ink")}>{value}</p>
      <p className="mt-1 text-[10px] font-medium uppercase tracking-wide text-ink-faint">{label}</p>
    </div>
  );
}
