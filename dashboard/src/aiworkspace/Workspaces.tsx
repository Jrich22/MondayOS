/**
 * Projects as editor workspaces.
 *
 * A workspace switcher, not a project browser. The distinction is the whole
 * design: a browser shows you everything about everything so you can choose; a
 * switcher shows you where you are and lets you go somewhere else.
 *
 * So an inactive project is a dot and a name. That is all — no summary, no task
 * count, no timestamp, no progress. Those are things you would read while
 * *deciding*, and you are not deciding, you are switching. Only the open project
 * earns a second line, and only one: what you are doing in it.
 *
 *     ● MondayOS
 *       Building Repository Intelligence
 *     ○ Cue App
 *     ○ Storm Edge
 *
 * The filled/hollow dot carries the state, the way a tab bar or a file explorer
 * marks the active document. Nothing else needs to.
 */

import type { WorkspaceProject } from "./types";

export function Workspaces({
  projects,
  active,
  /** What is being worked on in the active project — its one subtitle line. */
  subtitle,
  onSelect,
}: {
  projects: WorkspaceProject[];
  active: string;
  subtitle: string;
  onSelect: (project: string) => void;
}) {
  if (projects.length === 0) {
    return <p className="px-3 py-2 text-[11px] text-ink-faint">No projects registered.</p>;
  }

  return (
    <ul className="px-1.5">
      {projects.map((p) => {
        const open = p.name === active;
        return (
          <li key={p.name}>
            <button
              onClick={() => onSelect(p.name)}
              aria-current={open ? "true" : undefined}
              className="group flex w-full items-baseline gap-2 rounded-md px-2 py-[5px] text-left transition hover:bg-canvas-overlay/40"
            >
              <span
                className={`shrink-0 text-[9px] leading-none transition ${
                  open ? "text-brand-400" : "text-ink-faint/40 group-hover:text-ink-faint"
                }`}
                aria-hidden
              >
                {open ? "●" : "○"}
              </span>
              <span
                className={`truncate text-[12px] transition ${
                  open ? "text-ink" : "text-ink-muted group-hover:text-ink"
                }`}
              >
                {p.display_name}
              </span>
            </button>

            {/* One line, only for the project you are in. It says what you are
                doing — not how many tasks exist, which is a browsing fact. */}
            {open && subtitle && (
              <div className="truncate pb-1 pl-[26px] pr-2 text-[10px] text-ink-faint">
                {subtitle}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
