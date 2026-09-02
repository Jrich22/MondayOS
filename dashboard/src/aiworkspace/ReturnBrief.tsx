/**
 * The return brief — where work stood when you left.
 *
 * Shown in place of an empty conversation, so opening MondayOS lands on
 * something useful rather than a blank pane.
 *
 * Everything here is read from stored MondayOS state. Where a section has
 * nothing, it says so: the `notes` from the briefing are rendered verbatim
 * rather than hidden, because "no completed tasks recorded" and "no completed
 * tasks" are different claims and only the first one is true.
 *
 * Continue Working appears only when the briefing actually found a project and
 * conversation to return to. With nothing recorded there is nothing to
 * continue, and offering the button anyway would be a lie the first click
 * exposes.
 */

import type { Briefing } from "./types";

function awayLabel(hours: number): string {
  if (hours < 1) return "less than an hour ago";
  if (hours < 24) return `${Math.round(hours)} hour${Math.round(hours) === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

export function ReturnBrief({
  briefing,
  onContinue,
}: {
  briefing: Briefing | null;
  onContinue: () => void;
}) {
  if (!briefing) {
    return (
      <div className="flex h-full items-center justify-center">
        <span className="text-[12px] text-ink-faint">Reading MondayOS state…</span>
      </div>
    );
  }

  return (
    <div className="flex h-full items-center justify-center px-6">
      <div className="w-full max-w-[520px]">
        <h1 className="text-[19px] font-medium tracking-tight text-ink">{briefing.greeting}</h1>

        {briefing.project ? (
          <p className="mt-1 text-[13px] text-ink-muted">
            You were working on <span className="text-ink">{briefing.project}</span>
            {briefing.last_active && (
              <span className="text-ink-faint"> · {awayLabel(briefing.away_hours)}</span>
            )}
          </p>
        ) : (
          <p className="mt-1 text-[13px] text-ink-muted">
            No previous session recorded. Pick a project to start.
          </p>
        )}

        <dl className="mt-5 space-y-2.5 border-l border-line pl-4">
          {briefing.conversation_title && (
            <Row label="Last conversation" value={briefing.conversation_title} />
          )}
          {briefing.active_task && (
            <Row
              label="In progress"
              value={`${briefing.active_task.id} · ${briefing.active_task.title}`}
            />
          )}
          {briefing.last_completed && (
            <Row
              label="Last completed"
              value={`${briefing.last_completed.id} · ${briefing.last_completed.title}`}
            />
          )}
          {briefing.branch && <Row label="Branch" value={briefing.branch} mono />}
          {briefing.open_task_count > 0 && (
            <Row label="Open work" value={`${briefing.open_task_count} task(s)`} />
          )}
          {briefing.recent_commits.length > 0 && (
            <div className="flex gap-3">
              <dt className="w-[112px] shrink-0 text-[11px] text-ink-faint">Recent commits</dt>
              <dd className="min-w-0 flex-1 space-y-0.5">
                {briefing.recent_commits.slice(0, 3).map((c) => (
                  <div key={c} className="truncate font-mono text-[11px] text-ink-muted">
                    {c}
                  </div>
                ))}
              </dd>
            </div>
          )}
        </dl>

        {briefing.next_step ? (
          <div className="mt-5 rounded-lg border border-line bg-canvas-raised/60 px-4 py-3">
            <div className="text-[10px] uppercase tracking-wider text-ink-faint">Next</div>
            <div className="mt-1 text-[13px] text-ink">
              {briefing.next_step.task_id} · {briefing.next_step.title}
            </div>
            {/* The recommendation states its basis, so it reads as derived from
                task state rather than as an opinion. */}
            <div className="mt-0.5 text-[11px] text-ink-faint">
              {briefing.next_step.priority} · {briefing.next_step.reason}
            </div>
          </div>
        ) : null}

        {briefing.notes.length > 0 && (
          <ul className="mt-4 space-y-0.5">
            {briefing.notes.map((note) => (
              <li key={note} className="text-[11px] text-ink-faint">
                {note}
              </li>
            ))}
          </ul>
        )}

        {briefing.can_continue && (
          <button
            onClick={onContinue}
            className="focus-ring mt-5 rounded-lg border border-brand-400/40 bg-brand-500/10 px-4 py-2 text-[12px] font-medium text-brand-200 transition hover:bg-brand-500/20"
          >
            Continue working
          </button>
        )}
      </div>
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex gap-3">
      <dt className="w-[112px] shrink-0 text-[11px] text-ink-faint">{label}</dt>
      <dd className={`min-w-0 flex-1 truncate text-[12px] text-ink ${mono ? "font-mono" : ""}`}>
        {value}
      </dd>
    </div>
  );
}
