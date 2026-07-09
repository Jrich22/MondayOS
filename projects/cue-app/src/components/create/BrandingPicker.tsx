import { BRAND_THEMES, themeByKey } from "@/lib/branding";
import { cn } from "@/lib/cn";
import { Field } from "@/components/ui/Field";

/**
 * Branding controls: a live banner preview, a color-theme swatch row, and
 * image/logo *placeholders*. Uploading is deferred (MVP scope) — the dropzones
 * show what will exist without pretending to store files.
 */
export function BrandingPicker({
  theme,
  title,
  onThemeChange,
}: {
  theme: string;
  title: string;
  onThemeChange: (key: string) => void;
}) {
  const active = themeByKey(theme);

  return (
    <div className="space-y-5">
      {/* Live banner preview */}
      <div
        className="relative flex h-32 items-end overflow-hidden rounded-xl border border-line"
        style={{ backgroundImage: active.gradient }}
      >
        <div className="absolute inset-0 bg-black/10" />
        <div className="relative flex items-center gap-3 p-4">
          <span className="grid h-9 w-9 place-items-center rounded-lg bg-white/20 text-xs font-semibold text-white backdrop-blur">
            Logo
          </span>
          <span className="text-sm font-semibold text-white drop-shadow">
            {title.trim() || "Your event"}
          </span>
        </div>
        <span className="absolute right-3 top-3 rounded-md bg-black/25 px-2 py-0.5 text-[11px] font-medium text-white/80 backdrop-blur">
          Banner preview
        </span>
      </div>

      <Field label="Color theme">
        <div className="flex flex-wrap gap-2.5">
          {BRAND_THEMES.map((t) => {
            const on = t.key === theme;
            return (
              <button
                key={t.key}
                type="button"
                aria-pressed={on}
                aria-label={t.label}
                title={t.label}
                onClick={() => onThemeChange(t.key)}
                className={cn(
                  "focus-ring h-9 w-9 rounded-full ring-2 ring-offset-2 ring-offset-canvas-raised transition-transform hover:scale-105",
                  on ? "ring-white/80" : "ring-transparent",
                )}
                style={{ backgroundImage: t.gradient }}
              />
            );
          })}
        </div>
      </Field>

      <div className="grid gap-3 sm:grid-cols-2">
        <Placeholder label="Event image / banner" hint="Upload arrives with the event workspace" />
        <Placeholder label="Logo" hint="Square PNG or SVG" />
      </div>
    </div>
  );
}

function Placeholder({ label, hint }: { label: string; hint: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-line-strong bg-white/[0.02] px-4 py-6 text-center">
      <span className="text-sm font-medium text-ink-muted">{label}</span>
      <span className="text-xs text-ink-faint">{hint}</span>
      <span className="mt-1 rounded-md bg-white/5 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-ink-faint">
        Soon
      </span>
    </div>
  );
}
