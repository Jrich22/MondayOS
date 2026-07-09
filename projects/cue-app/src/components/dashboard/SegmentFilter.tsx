import { cn } from "@/lib/cn";

export interface Segment<T extends string> {
  value: T;
  label: string;
  count: number;
}

/**
 * A segmented control for filtering the event list by status. Fully keyboard
 * accessible (native buttons) with an aria-pressed active state.
 */
export function SegmentFilter<T extends string>({
  segments,
  value,
  onChange,
}: {
  segments: Segment<T>[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="inline-flex items-center gap-1 rounded-xl border border-line bg-canvas-raised p-1">
      {segments.map((s) => {
        const active = s.value === value;
        return (
          <button
            key={s.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(s.value)}
            className={cn(
              "focus-ring rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
              active
                ? "bg-brand-600 text-white shadow-sm"
                : "text-ink-muted hover:text-ink hover:bg-white/5",
            )}
          >
            {s.label}
            <span
              className={cn(
                "ml-1.5 tabular-nums text-xs",
                active ? "text-white/70" : "text-ink-faint",
              )}
            >
              {s.count}
            </span>
          </button>
        );
      })}
    </div>
  );
}
