import type { EventClassification } from "@/lib/types";
import { CLASSIFICATION_META, CLASSIFICATION_ORDER } from "@/lib/classification";
import { cn } from "@/lib/cn";
import { Field, TextInput } from "@/components/ui/Field";

/**
 * Classification as a grid of selectable chips — fast to scan and one tap to
 * set, rather than a dropdown. Selecting "Custom" reveals a free-text field.
 */
export function ClassificationPicker({
  value,
  custom,
  onChange,
  onCustomChange,
  customError,
}: {
  value: EventClassification;
  custom: string;
  onChange: (v: EventClassification) => void;
  onCustomChange: (v: string) => void;
  customError?: string;
}) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        {CLASSIFICATION_ORDER.map((key) => {
          const active = key === value;
          const meta = CLASSIFICATION_META[key];
          return (
            <button
              key={key}
              type="button"
              aria-pressed={active}
              onClick={() => onChange(key)}
              className={cn(
                "focus-ring flex flex-col items-start rounded-xl border px-3 py-2.5 text-left transition-all",
                active
                  ? "border-brand-500/60 bg-brand-500/10 shadow-[0_0_0_1px_rgba(99,102,241,0.35)]"
                  : "border-line bg-canvas hover:border-line-strong hover:bg-white/[0.03]",
              )}
            >
              <span className={cn("text-sm font-medium", active ? "text-ink" : "text-ink-muted")}>
                {meta.label}
              </span>
              <span className="mt-0.5 text-[11px] leading-tight text-ink-faint">
                {meta.hint}
              </span>
            </button>
          );
        })}
      </div>

      {value === "custom" && (
        <Field label="Custom classification" htmlFor="custom-classification" error={customError}>
          <TextInput
            id="custom-classification"
            value={custom}
            invalid={!!customError}
            onChange={(e) => onCustomChange(e.target.value)}
            placeholder="e.g. Portfolio × Enterprise"
          />
        </Field>
      )}
    </div>
  );
}
