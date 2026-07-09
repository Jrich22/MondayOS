import type { ReactNode } from "react";
import { Panel } from "@/components/detail/Panel";

/**
 * A consistent, tasteful placeholder for detail sections whose full build lands
 * in a later task. Reads as "designed, not yet wired" rather than broken.
 */
export function PlaceholderTab({
  title,
  task,
  blurb,
  icon,
  bullets,
}: {
  title: string;
  task: string;
  blurb: string;
  icon: ReactNode;
  bullets?: string[];
}) {
  return (
    <Panel>
      <div className="flex flex-col items-center justify-center gap-3 px-6 py-12 text-center">
        <span className="grid h-12 w-12 place-items-center rounded-2xl border border-line bg-brand-sheen text-brand-400">
          {icon}
        </span>
        <div>
          <h3 className="text-base font-semibold text-ink">{title}</h3>
          <p className="mt-1 max-w-md text-sm text-ink-muted">{blurb}</p>
        </div>
        {bullets && bullets.length > 0 && (
          <ul className="mt-1 flex flex-wrap justify-center gap-2">
            {bullets.map((b) => (
              <li
                key={b}
                className="rounded-full border border-line px-2.5 py-1 text-xs text-ink-muted"
              >
                {b}
              </li>
            ))}
          </ul>
        )}
        <span className="mt-1 rounded-md bg-white/5 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-ink-faint">
          {task}
        </span>
      </div>
    </Panel>
  );
}
