import { useEffect, useMemo, useRef, useState } from "react";
import type { CueEvent, Guest } from "@/lib/types";
import { qrPayload, qrPayloadFor } from "@/lib/qr";
import { displayName, guestCompany, initials } from "@/lib/guests-select";
import { ScanIcon, CameraIcon, KeyboardIcon, StarIcon } from "@/components/icons";

/**
 * Scanner Mode — the default door flow. A keyboard-wedge scanner (or a paste)
 * types a payload into the always-focused field and submits on Enter; the same
 * pipeline handles a tap on any simulated badge, since the MVP has no camera. A
 * camera viewfinder placeholder marks where a real reader lands later.
 *
 * The "simulate" list and the validation-layer chips feed *real* payloads through
 * the exact same `onScan` the hardware path uses, so the demo exercises the whole
 * validation layer (ready / already-in / wrong-event / invalid) truthfully.
 */
export function ScannerPanel({
  event,
  guests,
  onScan,
  focusSignal,
}: {
  event: CueEvent;
  guests: Guest[];
  onScan: (raw: string) => void;
  /** Bumped by the parent after each processed scan to refocus the field. */
  focusSignal: number;
}) {
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  // Keep the scanner hot: focus on mount and whenever the parent signals.
  useEffect(() => {
    inputRef.current?.focus();
  }, [focusSignal]);

  const expected = useMemo(
    () => guests.filter((g) => !g.attendance.checkedIn).slice(0, 8),
    [guests],
  );
  const anyGuestId = guests[0]?.id ?? "gst-unknown";

  const submit = () => {
    const raw = value.trim();
    if (!raw) return;
    onScan(raw);
    setValue("");
  };

  return (
    <div className="space-y-5">
      {/* Camera viewfinder placeholder + keyboard scanner input */}
      <div className="grid gap-4 lg:grid-cols-[1fr_1.1fr]">
        <div className="relative grid aspect-[4/3] place-items-center overflow-hidden rounded-2xl border border-dashed border-line-strong bg-white/[0.02]">
          {/* Corner reticle */}
          <span className="pointer-events-none absolute inset-6 rounded-xl border-2 border-brand-500/30" />
          <span className="pointer-events-none absolute left-1/2 top-6 h-[calc(100%-3rem)] w-px -translate-x-1/2 animate-pulse bg-brand-400/40" />
          <div className="relative flex flex-col items-center gap-2 text-center">
            <CameraIcon width={30} height={30} className="text-ink-faint" />
            <p className="text-sm font-medium text-ink-muted">Camera scanning</p>
            <p className="text-xs text-ink-faint">Reader lands here — keyboard scanner works now</p>
          </div>
        </div>

        <div className="flex flex-col justify-center gap-3">
          <label htmlFor="scan-input" className="flex items-center gap-2 text-sm font-semibold text-ink">
            <span className="grid h-7 w-7 place-items-center rounded-lg bg-brand-500/15 text-brand-300">
              <KeyboardIcon width={16} height={16} />
            </span>
            Scan or key a badge
          </label>
          <div className="relative">
            <ScanIcon
              width={20}
              height={20}
              className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-ink-faint"
            />
            <input
              id="scan-input"
              ref={inputRef}
              autoFocus
              value={value}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  submit();
                }
              }}
              placeholder="Waiting for scan…"
              aria-label="Scan a badge"
              className="focus-ring w-full rounded-2xl border border-line bg-canvas-raised py-4 pl-12 pr-4 text-lg text-ink placeholder:text-ink-faint"
            />
          </div>
          <p className="text-xs text-ink-faint">
            A hardware scanner types the code and presses Enter automatically. The field stays focused between scans.
          </p>
        </div>
      </div>

      {/* Simulated badges — real payloads through the real pipeline */}
      <div>
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-faint">
            Simulate a scan
          </h3>
          <span className="text-[11px] text-ink-faint">{expected.length} awaiting</span>
        </div>
        {expected.length === 0 ? (
          <p className="rounded-xl border border-dashed border-line px-4 py-6 text-center text-sm text-ink-muted">
            Everyone expected is already checked in.
          </p>
        ) : (
          <ul className="grid gap-1.5 sm:grid-cols-2">
            {expected.map((g) => (
              <li key={g.id}>
                <button
                  onClick={() => onScan(qrPayload(g))}
                  className="focus-ring group flex w-full items-center gap-2.5 rounded-xl border border-line bg-canvas-raised px-3 py-2 text-left transition-colors hover:border-line-strong hover:bg-white/[0.03]"
                >
                  <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-white/[0.06] text-xs font-semibold text-ink">
                    {initials(g)}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-1.5">
                      <span className="truncate text-sm font-medium text-ink">{displayName(g)}</span>
                      {g.vip && <StarIcon width={11} height={11} className="shrink-0 text-status-draft" />}
                    </span>
                    <span className="block truncate text-[11px] text-ink-faint">
                      {guestCompany(g, event.portfolio) || "—"}
                    </span>
                  </span>
                  <span className="shrink-0 rounded-lg border border-line-strong px-2 py-1 text-[11px] font-medium text-ink-muted group-hover:border-brand-400 group-hover:text-brand-300">
                    Scan
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Validation-layer demo chips */}
      <div>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-faint">
          Test the validation layer
        </h3>
        <div className="flex flex-wrap gap-1.5">
          <DemoChip onClick={() => onScan(qrPayloadFor("evt-other", anyGuestId))}>
            Wrong event
          </DemoChip>
          <DemoChip onClick={() => onScan("NOT-A-CUE-CODE")}>Invalid code</DemoChip>
          <DemoChip onClick={() => onScan(qrPayloadFor(event.id, "gst-ghost"))}>
            Unknown attendee
          </DemoChip>
        </div>
      </div>
    </div>
  );
}

function DemoChip({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className="focus-ring rounded-lg border border-line px-2.5 py-1.5 text-xs font-medium text-ink-muted transition-colors hover:border-red-500/40 hover:text-red-300"
    >
      {children}
    </button>
  );
}
