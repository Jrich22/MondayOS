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

/**
 * Merged pull requests named in the recent commit log.
 *
 * Derived from data the briefing already returns — git's own merge commits —
 * rather than from a new API. "PR #39 merged" is a fact git recorded; inferring
 * it here keeps the claim true without widening the surface. A repository whose
 * merges are squashed simply yields none, and the section does not appear.
 */
function mergedPullRequests(commits: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const line of commits) {
    const match = /Merge pull request #(\d+) from \S+\/(\S+)/.exec(line);
    if (match && !seen.has(match[1])) {
      seen.add(match[1]);
      out.push(`PR #${match[1]} merged — ${match[2]}`);
    }
  }
  return out;
}

function awayLabel(hours: number): string {
  if (hours < 1) return "less than an hour ago";
  if (hours < 24) return `${Math.round(hours)} hour${Math.round(hours) === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

function suggestions(briefing: Briefing): string[] {
  const out: string[] = [];
  if (briefing.next_step) out.push("What should we do next?");
  if (briefing.recent_commits.length) out.push("What did we build last?");
  if (briefing.open_task_count > 0) out.push("What is still open?");
  return out.slice(0, 3);
}

export function ReturnBrief({
  briefing,
  onContinue,
  onSuggest,
}: {
  briefing: Briefing | null;
  onContinue: () => void;
  onSuggest?: (text: string) => void;
}) {
  if (!briefing) {
    return (
      <div className="flex h-full items-center justify-center">
        <span className="text-[12px] text-ink-faint">Reading MondayOS state…</span>
      </div>
    );
  }

  const away = mergedPullRequests(briefing.recent_commits);

  return (
    <div className="flex h-full items-center justify-center px-6">
      <div className="w-full max-w-[560px] animate-fade-up">
        <h1 className="text-[20px] font-medium tracking-tight text-ink">{briefing.greeting}</h1>

        {briefing.project ? (
          <p className="mt-1 text-[13px] text-ink-muted">
            Welcome back. You were working on{" "}
            <span className="text-ink">{briefing.project}</span>
            {briefing.last_active && (
              <span className="text-ink-faint"> · {awayLabel(briefing.away_hours)}</span>
            )}
          </p>
        ) : (
          <p className="mt-1 text-[13px] text-ink-muted">
            No previous session recorded. Pick a project to start.
          </p>
        )}

        {/* "While you were away" appears only when there is something real to
            report. An empty section headed with a promise is worse than none. */}
        {(away.length > 0 || briefing.last_completed) && (
          <div className="mt-4">
            <div className="text-[9px] uppercase tracking-[0.08em] text-ink-faint/70">
              While you were away
            </div>
            <ul className="mt-1.5 space-y-1">
              {briefing.last_completed && (
                <li className="flex items-baseline gap-2 text-[12px] text-ink-muted">
                  <span className="text-status-completed">✓</span>
                  <span className="truncate">
                    {briefing.last_completed.id} · {briefing.last_completed.title}
                  </span>
                </li>
              )}
              {away.map((item) => (
                <li key={item} className="flex items-baseline gap-2 text-[12px] text-ink-muted">
                  <span className="text-status-completed">✓</span>
                  <span className="truncate">{item}</span>
                </li>
              ))}
            </ul>
          </div>
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
          {/* "Last completed" is not repeated here: it already appears under
              "While you were away", and the same fact stated twice on one screen
              reads as two facts. */}
          {/* Branch and open-task count live in the status rail, which is
              always on screen. Repeating them here would state the same fact
              twice on first open. */}
          {/* Recent commits, branch and open work all live in the context
              panel, which is on screen permanently. The brief is orientation —
              what changed and what to do next — not a second copy of the
              standing facts. */}
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

        <div className="mt-5 flex flex-wrap items-center gap-2">
          {/* The resume button names the thread rather than repeating the
              rail's "Continue working" heading. Two identically-labelled
              affordances on one screen read as two different actions; saying
              what will open removes the ambiguity and is more useful besides. */}
          {briefing.can_continue && (
            <button
              onClick={onContinue}
              className="focus-ring max-w-[280px] truncate rounded-lg border border-brand-400/40 bg-brand-500/10 px-4 py-2 text-[12px] font-medium text-brand-200 transition hover:bg-brand-500/20"
            >
              Resume &ldquo;{briefing.conversation_title || briefing.project}&rdquo;
            </button>
          )}
          {/* Suggested openers, phrased as the operator would ask them. Offered
              only when there is state behind them, so a suggestion never leads
              to an answer of "I don't have that". */}
          {onSuggest &&
            suggestions(briefing).map((text) => (
              <button
                key={text}
                onClick={() => onSuggest(text)}
                className="focus-ring rounded-lg border border-line px-3 py-2 text-[11px] text-ink-muted transition hover:border-brand-400/40 hover:text-ink"
              >
                {text}
              </button>
            ))}
        </div>
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
