/**
 * Left rail — identity, projects, conversations, search.
 *
 * Deliberately quiet. The sidebar's job is to get out of the way: it says where
 * you are, lists what you can return to, and stops. Project status is one dot
 * and one count, not a card.
 */

import { useState } from "react";
import type { ConversationSummary, SearchResult, WorkspaceProject } from "./types";

function relativeDay(iso: string): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const days = Math.floor((Date.now() - then) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 7) return `${days}d`;
  return new Date(then).toLocaleDateString([], { month: "short", day: "numeric" });
}

export function Sidebar({
  projects,
  project,
  conversations,
  activeConversation,
  searchResult,
  collapsed,
  onSelectProject,
  onSelectConversation,
  onNewConversation,
  onArchive,
  onSearch,
  onClearSearch,
  onToggle,
}: {
  projects: WorkspaceProject[];
  project: string;
  conversations: ConversationSummary[];
  activeConversation: string;
  searchResult: SearchResult | null;
  collapsed: boolean;
  onSelectProject: (p: string) => void;
  onSelectConversation: (id: string) => void;
  onNewConversation: () => void;
  onArchive: (id: string) => void;
  onSearch: (q: string, scope: "project" | "all") => void;
  onClearSearch: () => void;
  onToggle: () => void;
}) {
  const [query, setQuery] = useState("");
  const [allProjects, setAllProjects] = useState(false);
  const [menuFor, setMenuFor] = useState("");

  if (collapsed) {
    return (
      <nav className="flex w-[44px] shrink-0 flex-col items-center gap-3 border-r border-line bg-canvas-raised/40 py-3">
        <button
          onClick={onToggle}
          aria-label="Expand sidebar"
          className="grid h-7 w-7 place-items-center rounded-lg bg-gradient-to-br from-accent-cyan to-accent-violet text-[12px] font-bold text-canvas"
        >
          M
        </button>
        <button
          onClick={onNewConversation}
          aria-label="New conversation"
          className="focus-ring rounded-md border border-line px-2 py-1 text-[13px] text-ink-muted hover:text-brand-400"
        >
          +
        </button>
      </nav>
    );
  }

  const runSearch = () => {
    const q = query.trim();
    if (q) onSearch(q, allProjects ? "all" : "project");
    else onClearSearch();
  };

  return (
    <nav className="flex w-[248px] shrink-0 flex-col border-r border-line bg-canvas-raised/40">
      <header className="flex items-center justify-between px-3 py-3">
        <div className="flex items-center gap-2">
          <div className="grid h-6 w-6 place-items-center rounded-md bg-gradient-to-br from-accent-cyan to-accent-violet text-[11px] font-bold text-canvas">
            M
          </div>
          <span className="text-[12px] font-semibold tracking-tight text-ink">MONDAY</span>
        </div>
        <button
          onClick={onToggle}
          aria-label="Collapse sidebar"
          className="rounded px-1 text-[11px] text-ink-faint hover:text-ink"
        >
          ‹
        </button>
      </header>

      <div className="px-3 pb-2">
        <button
          onClick={onNewConversation}
          disabled={!project}
          className="focus-ring w-full rounded-md border border-line bg-canvas px-2.5 py-1.5 text-left text-[12px] text-ink-muted transition hover:border-brand-400/50 hover:text-ink disabled:opacity-40"
        >
          + New conversation
          <kbd className="float-right text-[10px] text-ink-faint">⌘N</kbd>
        </button>
      </div>

      <div className="px-3 pb-2">
        <div className="flex items-center gap-1">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") runSearch();
              if (e.key === "Escape") {
                setQuery("");
                onClearSearch();
              }
            }}
            placeholder="Search conversations"
            className="focus-ring min-w-0 flex-1 rounded-md border border-line bg-canvas px-2 py-1 text-[11px] text-ink placeholder:text-ink-faint"
          />
        </div>
        <label className="mt-1 flex cursor-pointer items-center gap-1.5 text-[10px] text-ink-faint">
          <input
            type="checkbox"
            checked={allProjects}
            onChange={(e) => {
              setAllProjects(e.target.checked);
              if (query.trim()) onSearch(query.trim(), e.target.checked ? "all" : "project");
            }}
            className="h-2.5 w-2.5 accent-brand-400"
          />
          {/* Cross-project search is opt-in: a search that silently spanned
              projects would be the same disclosure as a context leak. */}
          All projects
        </label>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {searchResult ? (
          <section className="px-2 pb-3">
            <div className="flex items-center justify-between px-1 py-1.5">
              <span className="text-[10px] uppercase tracking-wider text-ink-faint">
                {searchResult.hits.length} result
                {searchResult.hits.length === 1 ? "" : "s"}
                {searchResult.scope === "all" ? " · all projects" : ""}
              </span>
              <button
                onClick={() => {
                  setQuery("");
                  onClearSearch();
                }}
                className="text-[10px] text-ink-faint hover:text-ink"
              >
                clear
              </button>
            </div>
            {searchResult.hits.length === 0 && (
              <p className="px-1 text-[11px] text-ink-faint">No conversations match.</p>
            )}
            <ul className="space-y-0.5">
              {searchResult.hits.map((hit) => (
                <li key={`${hit.project}-${hit.conversation_id}`}>
                  <button
                    onClick={() => {
                      if (hit.project !== project) onSelectProject(hit.project);
                      onSelectConversation(hit.conversation_id);
                    }}
                    className="w-full rounded-md px-2 py-1.5 text-left text-ink-muted transition hover:bg-canvas-overlay/60 hover:text-ink"
                  >
                    <div className="truncate text-[12px]">{hit.title}</div>
                    {/* Every hit names its project, so a cross-project result is
                        never mistaken for the current one. */}
                    <div className="mt-0.5 text-[10px] text-brand-400/80">{hit.project}</div>
                    {hit.snippets[0] && (
                      <div className="mt-0.5 truncate text-[10px] text-ink-faint">
                        {hit.snippets[0]}
                      </div>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ) : (
          <>
            <section className="px-2 pb-2">
              <h2 className="px-1 py-1.5 text-[10px] uppercase tracking-wider text-ink-faint">
                Projects
              </h2>
              <ul className="space-y-0.5">
                {projects.map((p) => {
                  const active = p.name === project;
                  return (
                    <li key={p.name}>
                      <button
                        onClick={() => onSelectProject(p.name)}
                        className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition ${
                          active
                            ? "bg-brand-500/10 text-ink"
                            : "text-ink-muted hover:bg-canvas-overlay/60 hover:text-ink"
                        }`}
                      >
                        <span
                          className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                            active ? "bg-brand-400" : "bg-ink-faint/40"
                          }`}
                        />
                        <span className="truncate text-[12px]">{p.display_name}</span>
                        {p.conversation_count > 0 && (
                          <span className="ml-auto text-[10px] text-ink-faint">
                            {p.conversation_count}
                          </span>
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </section>

            <section className="px-2 pb-3">
              <h2 className="px-1 py-1.5 text-[10px] uppercase tracking-wider text-ink-faint">
                Recent
              </h2>
              {conversations.length === 0 ? (
                <p className="px-1 text-[11px] text-ink-faint">
                  {project ? "No conversations yet." : "Select a project."}
                </p>
              ) : (
                <ul className="space-y-0.5">
                  {conversations.map((c) => {
                    const active = activeConversation === c.id;
                    return (
                      <li key={c.id} className="relative">
                        <button
                          onClick={() => onSelectConversation(c.id)}
                          className={`w-full rounded-md py-1.5 pl-2 pr-6 text-left transition ${
                            active
                              ? "bg-brand-500/10 text-ink"
                              : "text-ink-muted hover:bg-canvas-overlay/60 hover:text-ink"
                          }`}
                        >
                          <div className="truncate text-[12px]">{c.title}</div>
                          <div className="mt-0.5 text-[10px] text-ink-faint">
                            {relativeDay(c.updated_at)} · {c.message_count} msg
                          </div>
                        </button>
                        <button
                          onClick={() => setMenuFor(menuFor === c.id ? "" : c.id)}
                          aria-label="Conversation actions"
                          className="absolute right-1 top-1.5 rounded px-1 text-[11px] text-ink-faint hover:text-ink"
                        >
                          ⋯
                        </button>
                        {menuFor === c.id && (
                          <div className="absolute right-1 top-7 z-10 rounded-md border border-line bg-canvas-overlay py-1 shadow-card">
                            <button
                              onClick={() => {
                                setMenuFor("");
                                onArchive(c.id);
                              }}
                              className="block w-full px-3 py-1 text-left text-[11px] text-ink-muted hover:text-ink"
                            >
                              Archive
                            </button>
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>
          </>
        )}
      </div>
    </nav>
  );
}
