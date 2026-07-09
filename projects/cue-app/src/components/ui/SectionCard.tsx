import type { ReactNode } from "react";

/**
 * A titled section card. The create flow is a stack of these rather than one
 * long form — progressive, breathable, and closer to Linear/Stripe than to an
 * enterprise form. `step` shows a subtle numeric marker; `aside` hosts an
 * optional action (e.g. AI assist) aligned to the header.
 */
export function SectionCard({
  step,
  title,
  description,
  aside,
  children,
}: {
  step: number;
  title: string;
  description?: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="card p-6 sm:p-7">
      <header className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full border border-line text-xs font-semibold text-ink-muted">
            {step}
          </span>
          <div>
            <h2 className="text-base font-semibold text-ink">{title}</h2>
            {description && (
              <p className="mt-0.5 text-sm text-ink-muted">{description}</p>
            )}
          </div>
        </div>
        {aside}
      </header>
      <div className="mt-5 pl-0 sm:pl-9">{children}</div>
    </section>
  );
}
