/**
 * The context panel — what MondayOS loaded, and where each piece came from.
 *
 * This panel is the visible half of ADR-016. Every source shows its origin, its
 * size, and whether it was truncated, so "why did Monday know this?" is
 * answerable by looking rather than by reading logs. A failed adapter shows its
 * error instead of quietly contributing nothing, because a thin context that
 * looks identical to a complete one is the failure mode worth designing against.
 */

import { useState } from "react";
import type { ContextSnapshot, ContextSource } from "./types";

function relativeTime(iso: string): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function SourceBlock({ source }: { source: ContextSource }) {
  const [open, setOpen] = useState(false);
  const empty = source.items.length === 0;

  return (
    <div className="border-b border-line/60 last:border-0">
      <button
        onClick={() => setOpen((v) => !v)}
        disabled={empty}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left transition hover:bg-canvas-overlay/60 disabled:cursor-default disabled:hover:bg-transparent"
      >
        <span className="flex min-w-0 items-center gap-2">
          <span
            className={`h-1.5 w-1.5 shrink-0 rounded-full ${
              source.error
                ? "bg-status-blocked"
                : empty
                  ? "bg-ink-faint/50"
                  : "bg-status-completed"
            }`}
          />
          <span className="truncate text-[12px] text-ink">{source.label}</span>
        </span>
        <span className="flex shrink-0 items-center gap-1.5 text-[10px] text-ink-faint">
          {source.truncated && <span className="text-status-awaiting">truncated</span>}
          <span>{empty ? "—" : source.item_count}</span>
          {!empty && <span className="text-ink-faint/60">{open ? "▾" : "▸"}</span>}
        </span>
      </button>

      {source.error && (
        <div className="px-3 pb-2 text-[10px] leading-relaxed text-status-blocked/90">
          {source.error}
        </div>
      )}

      {open && !empty && (
        <div className="px-3 pb-2">
          <ul className="space-y-0.5">
            {source.items.map((item, i) => (
              <li key={i} className="truncate font-mono text-[10px] text-ink-muted" title={item}>
                {item}
              </li>
            ))}
          </ul>
          <div className="mt-1.5 text-[10px] text-ink-faint/70">
            from {source.origin} · ~{source.token_estimate} tok
          </div>
        </div>
      )}
    </div>
  );
}

export function ContextPanel({
  context,
  project,
  onRefresh,
}: {
  context: ContextSnapshot | null;
  project: string;
  onRefresh: () => void;
}) {
  return (
    <aside className="flex h-full w-[280px] shrink-0 flex-col border-l border-line bg-canvas-raised/40">
      <header className="flex items-center justify-between border-b border-line px-3 py-2.5">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
          Context
        </h2>
        <button
          onClick={onRefresh}
          className="focus-ring rounded px-1.5 py-0.5 text-[10px] text-ink-faint transition hover:text-ink"
          title="Rebuild the context snapshot"
        >
          refresh
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="border-b border-line/60 px-3 py-2.5">
          <div className="text-[10px] uppercase tracking-wider text-ink-faint">Project</div>
          <div className="mt-0.5 truncate text-[13px] font-medium text-ink">
            {project || "—"}
          </div>
        </div>

        {!context ? (
          <div className="px-3 py-4 text-[11px] text-ink-faint">
            No snapshot loaded.
          </div>
        ) : (
          <>
            {context.sources.map((source) => (
              <SourceBlock key={source.name} source={source} />
            ))}

            {context.omitted.length > 0 && (
              <div className="border-b border-line/60 px-3 py-2 text-[10px] text-status-awaiting">
                Omitted for budget: {context.omitted.join(", ")}
              </div>
            )}

            <div className="px-3 py-2.5 text-[10px] leading-relaxed text-ink-faint">
              <div className="flex justify-between">
                <span>Snapshot</span>
                <span className="font-mono">{context.id}</span>
              </div>
              <div className="flex justify-between">
                <span>Assembled</span>
                <span>{relativeTime(context.created_at)}</span>
              </div>
              <div className="flex justify-between">
                <span>Size</span>
                <span>~{context.token_estimate} tokens</span>
              </div>
            </div>
          </>
        )}
      </div>
    </aside>
  );
}
