/**
 * The context panel — what Monday knows about the project you are talking about.
 *
 * Not a browser. Every section here is a *fact about the current project*, and
 * the whole panel changes when you switch projects because it is derived from
 * that project's context snapshot. There is nothing to click through to.
 *
 * Every value comes from the snapshot the workspace already assembled — the same
 * one that was sent to the model. That is deliberate: this panel and the prompt
 * are the same data, so what you read here is what Monday actually knew. A panel
 * populated from a second source could disagree with the answer beside it, and
 * you would have no way to tell which was lying.
 *
 * Sections with nothing to report say so. "No uncommitted changes" is a fact;
 * an absent section is an ambiguity.
 */

import { useState } from "react";
import type { ActivityEvent, ContextSnapshot, ContextSource } from "./types";
import { activityFromEvent, labelFor } from "./mondayState";

function sourceOf(context: ContextSnapshot | null, name: string): ContextSource | undefined {
  return context?.sources.find((s) => s.name === name);
}

/** The in-progress task, if the snapshot carries one. Never inferred. */
function currentTask(context: ContextSnapshot | null): string {
  return (
    sourceOf(context, "tasks")?.items.find(
      (i) => i.includes("[in-progress]") || i.includes("[review]"),
    ) ?? ""
  );
}

function branch(context: ContextSnapshot | null): string {
  const line = sourceOf(context, "git")?.items.find((i) => i.startsWith("Current branch:"));
  return line ? line.split(":", 2)[1].trim() : "";
}

/**
 * Files the working tree has changed.
 *
 * Read from git's porcelain lines, which the git adapter already indents. These
 * are genuinely "relevant files" — the ones being edited right now — rather than
 * a guess at which files matter to the conversation.
 */
function changedFiles(context: ContextSnapshot | null): string[] {
  const items = sourceOf(context, "git")?.items ?? [];
  const start = items.findIndex((i) => i.startsWith("Working tree:"));
  if (start < 0) return [];
  const out: string[] = [];
  for (let i = start + 1; i < items.length; i++) {
    const line = items[i];
    if (!line.startsWith("  ") || /^\s+[0-9a-f]{6,12}\s/.test(line)) break;
    out.push(line.trim());
  }
  return out;
}

function commits(context: ContextSnapshot | null): string[] {
  return (sourceOf(context, "git")?.items ?? []).filter((i) => {
    const parts = i.trim().split(/\s+/);
    return parts.length > 1 && /^[0-9a-f]{6,12}$/.test(parts[0]);
  });
}

export function ProjectContext({
  context,
  project,
  activity,
  onRefresh,
}: {
  context: ContextSnapshot | null;
  project: string;
  activity: ActivityEvent[];
  onRefresh: () => void;
}) {
  const task = currentTask(context);
  const files = changedFiles(context);
  const log = commits(context);
  const knowledge = sourceOf(context, "knowledge");
  const docs = sourceOf(context, "docs");

  return (
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 items-baseline justify-between px-4 pb-2 pt-3.5">
        <span className="truncate text-[11px] text-ink-muted">{project || "No project"}</span>
        <button
          onClick={onRefresh}
          title="Rebuild the context snapshot"
          className="shrink-0 text-[10px] text-ink-faint transition hover:text-ink"
        >
          refresh
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
        <Field label="Current task" value={task} empty="Nothing in progress" mono={Boolean(task)} />
        <Field label="Current branch" value={branch(context)} empty="Not a repository" mono />

        <Section label="Relevant files" count={files.length} empty="No uncommitted changes">
          {files.slice(0, 8).map((f) => (
            <Line key={f} text={f} mono />
          ))}
        </Section>

        <Section label="Recent commits" count={log.length} empty="No commits recorded">
          {log.slice(0, 6).map((c) => (
            <Line key={c} text={c.trim()} mono />
          ))}
        </Section>

        <Section
          label="Knowledge used"
          count={knowledge?.item_count ?? 0}
          empty="No entries for this project"
        >
          {(knowledge?.items ?? []).slice(0, 6).map((k, i) => (
            <Line key={k} text={k} reason={knowledge?.reasons[i]} />
          ))}
        </Section>

        <Section
          label="Documentation"
          count={docs?.item_count ?? 0}
          empty="No docs directory"
        >
          {(docs?.items ?? []).slice(0, 6).map((d, i) => (
            <Line key={d} text={d.trim()} reason={docs?.reasons[i]} />
          ))}
        </Section>

        <Section label="Agent activity" count={activity.length} empty="Nothing this session">
          {activity.slice(0, 6).map((e, i) => (
            <Line
              key={`${e.at}-${i}`}
              text={labelFor(activityFromEvent(e.kind, e.ok))}
              reason={e.detail || undefined}
            />
          ))}
        </Section>

        {context && (
          <div className="mt-4 border-t border-line/50 pt-2.5 text-[9px] leading-relaxed text-ink-faint/70">
            <div className="flex justify-between gap-2">
              <span>Snapshot</span>
              <span className="truncate font-mono">{context.id}</span>
            </div>
            <div className="flex justify-between gap-2">
              <span>Size</span>
              <span>~{context.token_estimate} tokens</span>
            </div>
            {context.truncated && (
              <div className="mt-1 text-status-awaiting">
                Truncated — more exists than fit the budget
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  empty,
  mono,
}: {
  label: string;
  value: string;
  empty: string;
  mono?: boolean;
}) {
  return (
    <div className="border-b border-line/40 py-2.5">
      <div className="text-[9px] uppercase tracking-[0.08em] text-ink-faint/70">{label}</div>
      <div
        className={`mt-1 truncate text-[11px] ${value ? "text-ink-muted" : "text-ink-faint/60"} ${
          mono && value ? "font-mono" : ""
        }`}
        title={value || empty}
      >
        {value || empty}
      </div>
    </div>
  );
}

function Section({
  label,
  count,
  empty,
  children,
}: {
  label: string;
  count: number;
  empty: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div className="border-b border-line/40 py-2.5">
      <button
        onClick={() => setOpen((v) => !v)}
        disabled={count === 0}
        className="flex w-full items-baseline justify-between gap-2 text-left disabled:cursor-default"
      >
        <span className="text-[9px] uppercase tracking-[0.08em] text-ink-faint/70">{label}</span>
        <span className="shrink-0 text-[9px] tabular-nums text-ink-faint/70">
          {count === 0 ? "" : open ? count : `${count} ▸`}
        </span>
      </button>
      {count === 0 ? (
        <div className="mt-1 text-[11px] text-ink-faint/60">{empty}</div>
      ) : (
        open && <div className="mt-1 space-y-[3px]">{children}</div>
      )}
    </div>
  );
}

function Line({ text, reason, mono }: { text: string; reason?: string; mono?: boolean }) {
  return (
    <div>
      <div
        className={`truncate text-[10px] text-ink-muted ${mono ? "font-mono" : ""}`}
        title={text}
      >
        {text}
      </div>
      {/* Why this was included. The panel and the prompt are the same data, so
          this answers "why did Monday know that?" about the answer beside it. */}
      {reason && <div className="truncate text-[9px] text-brand-400/50">{reason}</div>}
    </div>
  );
}
