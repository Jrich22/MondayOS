/**
 * Authoring field primitives.
 *
 * Deliberately small and label-first: every input is bound to a real <label>,
 * because the authoring surface is the one place a recruiter spends real time
 * and keyboard/screen-reader navigation has to work.
 */
import { useState, type FC, type ReactNode } from "react";
import { cn } from "@/components/ui/Primitives";

const inputBase =
  "w-full rounded-lg border border-line bg-canvas px-3 py-2 text-sm text-ink " +
  "placeholder:text-ink-faint focus:border-brand-500 focus:outline-none";

export const Field: FC<{
  label: string;
  hint?: string;
  htmlFor: string;
  children: ReactNode;
}> = ({ label, hint, htmlFor, children }) => (
  <div className="space-y-1.5">
    <label htmlFor={htmlFor} className="block text-sm font-medium text-ink">
      {label}
    </label>
    {hint && <p className="text-xs text-ink-faint">{hint}</p>}
    {children}
  </div>
);

export const TextField: FC<{
  id: string;
  label: string;
  hint?: string;
  value: string;
  placeholder?: string;
  onChange: (v: string) => void;
}> = ({ id, label, hint, value, placeholder, onChange }) => (
  <Field label={label} hint={hint} htmlFor={id}>
    <input
      id={id}
      type="text"
      className={inputBase}
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
    />
  </Field>
);

export const NumberField: FC<{
  id: string;
  label: string;
  hint?: string;
  value: number | undefined;
  min?: number;
  onChange: (v: number | undefined) => void;
}> = ({ id, label, hint, value, min = 0, onChange }) => (
  <Field label={label} hint={hint} htmlFor={id}>
    <input
      id={id}
      type="number"
      min={min}
      className={inputBase}
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))}
    />
  </Field>
);

export const TextAreaField: FC<{
  id: string;
  label: string;
  hint?: string;
  value: string;
  rows?: number;
  placeholder?: string;
  onChange: (v: string) => void;
}> = ({ id, label, hint, value, rows = 6, placeholder, onChange }) => (
  <Field label={label} hint={hint} htmlFor={id}>
    <textarea
      id={id}
      rows={rows}
      className={cn(inputBase, "resize-y leading-relaxed")}
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
    />
  </Field>
);

export const SelectField: FC<{
  id: string;
  label: string;
  hint?: string;
  value: string;
  options: ReadonlyArray<{ value: string; label: string }>;
  onChange: (v: string) => void;
}> = ({ id, label, hint, value, options, onChange }) => (
  <Field label={label} hint={hint} htmlFor={id}>
    <select id={id} className={inputBase} value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  </Field>
);

/**
 * Tag input for the brief's list fields.
 *
 * Enter commits a value; each existing tag is removable by an explicitly
 * labelled button rather than a bare ×, so the action is announced.
 */
export const TagField: FC<{
  id: string;
  label: string;
  hint?: string;
  values: string[];
  placeholder?: string;
  tone?: "default" | "exclude";
  onAdd: (v: string) => void;
  onRemove: (v: string) => void;
}> = ({ id, label, hint, values, placeholder, tone = "default", onAdd, onRemove }) => {
  const [draft, setDraft] = useState("");

  const commit = () => {
    if (draft.trim()) {
      onAdd(draft);
      setDraft("");
    }
  };

  return (
    <Field label={label} hint={hint} htmlFor={id}>
      <input
        id={id}
        type="text"
        className={inputBase}
        value={draft}
        placeholder={placeholder ?? "Type and press Enter"}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit();
          }
        }}
        onBlur={commit}
      />
      {values.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {values.map((v) => (
            <li key={v}>
              <span
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs",
                  tone === "exclude"
                    ? "border-stage-rejected/30 bg-stage-rejected/10 text-stage-rejected"
                    : "border-line bg-canvas-overlay text-ink-muted",
                )}
              >
                {v}
                <button
                  type="button"
                  aria-label={`Remove ${v}`}
                  onClick={() => onRemove(v)}
                  className="text-ink-faint transition-colors hover:text-ink"
                >
                  ×
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
    </Field>
  );
};
