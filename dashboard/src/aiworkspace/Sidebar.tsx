/**
 * The left rail — lightweight navigation, and nothing else.
 *
 * Everything here is either "where am I" or "take me somewhere". There is no
 * project metadata, no counts, no progress, no timestamps: those are things you
 * read while *deciding* which project to open, and by the time you are looking
 * at this rail you have already decided — you are switching, starting a thread,
 * or finding one you had.
 *
 * The result is deliberately sparse. A rail with four things on it is a rail you
 * stop reading and start using.
 */

import { useState, type ReactNode } from "react";
import { Workspaces } from "./Workspaces";
import type { ConversationSummary, SearchResult, WorkspaceProject } from "./types";

export function Sidebar({
  projects,
  project,
  projectSubtitle,
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
  onOpenDiagnostics,
  statusRail,
}: {
  projects: WorkspaceProject[];
  project: string;
  /** One line describing what is being worked on in the open project. */
  projectSubtitle: string;
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
  onOpenDiagnostics: () => void;
  /** Monday's identity and live state — never drawn over the visualisation. */
  statusRail?: ReactNode;
}) {
  const [query, setQuery] = useState("");
  const [allProjects, setAllProjects] = useState(false);

  if (collapsed) {
    return (
      <nav className="flex w-[44px] shrink-0 flex-col items-center gap-3 border-r border-line/70 py-3">
        <button
          onClick={onToggle}
          aria-label="Expand sidebar"
          className="grid h-6 w-6 place-items-center rounded-md bg-gradient-to-br from-accent-cyan to-accent-violet text-[11px] font-bold text-canvas"
        >
          M
        </button>
        <button
          onClick={onNewConversation}
          aria-label="New conversation"
          className="focus-ring rounded px-2 py-1 text-[13px] text-ink-faint hover:text-brand-400"
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
    <nav className="flex w-[212px] shrink-0 flex-col border-r border-line/70">
      <div className="relative">
        {statusRail}
        <button
          onClick={onToggle}
          aria-label="Collapse sidebar"
          className="absolute right-2 top-2 rounded px-1 text-[11px] text-ink-faint/60 hover:text-ink"
        >
          &lsaquo;
        </button>
      </div>

      <div className="px-3 py-2.5">
        <button
          onClick={onNewConversation}
          disabled={!project}
          className="focus-ring flex w-full items-baseline gap-2 rounded-md px-2 py-1.5 text-left text-[11px] text-ink-muted transition hover:bg-canvas-overlay/40 hover:text-ink disabled:opacity-40"
        >
          <span className="text-ink-faint">+</span>
          New conversation
          <kbd className="ml-auto text-[9px] text-ink-faint/60">&#8984;N</kbd>
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {searchResult ? (
          <SearchResults
            result={searchResult}
            project={project}
            onSelectProject={onSelectProject}
            onSelectConversation={onSelectConversation}
            onClear={() => {
              setQuery("");
              onClearSearch();
            }}
          />
        ) : (
          <>
            <Workspaces
              projects={projects}
              active={project}
              subtitle={projectSubtitle}
              onSelect={onSelectProject}
            />

            {conversations.length > 0 && (
              <section className="mt-3">
                <h2 className="px-3 pb-1 text-[9px] uppercase tracking-[0.08em] text-ink-faint/60">
                  Conversations
                </h2>
                <ul className="px-1.5">
                  {conversations.map((c) => {
                    const open = activeConversation === c.id;
                    return (
                      <li key={c.id} className="group/thread flex items-center">
                        <button
                          onClick={() => onSelectConversation(c.id)}
                          className={`min-w-0 flex-1 truncate rounded-md px-2 py-[5px] text-left text-[11px] transition ${
                            open
                              ? "bg-canvas-overlay/50 text-ink"
                              : "text-ink-faint hover:bg-canvas-overlay/30 hover:text-ink-muted"
                          }`}
                        >
                          {c.title}
                        </button>
                        <button
                          onClick={() => onArchive(c.id)}
                          aria-label={`Archive ${c.title}`}
                          title="Archive"
                          className="shrink-0 rounded px-1.5 text-[10px] text-ink-faint opacity-0 transition hover:text-ink group-hover/thread:opacity-100"
                        >
                          &times;
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </section>
            )}
          </>
        )}
      </div>

      <div className="shrink-0 border-t border-line/50 px-3 py-2">
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
          className="focus-ring w-full rounded-md bg-canvas-overlay/40 px-2 py-1 text-[10px] text-ink placeholder:text-ink-faint/70"
        />
        <div className="mt-1.5 flex items-center justify-between">
          <label className="flex cursor-pointer items-center gap-1.5 text-[9px] text-ink-faint/70">
            <input
              type="checkbox"
              checked={allProjects}
              onChange={(e) => {
                setAllProjects(e.target.checked);
                if (query.trim()) onSearch(query.trim(), e.target.checked ? "all" : "project");
              }}
              className="h-2.5 w-2.5 accent-brand-400"
            />
            {/* Opt-in: a search that silently spanned projects would be the same
                disclosure as a context leak, through a different door. */}
            All projects
          </label>
          <button
            onClick={onOpenDiagnostics}
            className="text-[9px] text-ink-faint/60 transition hover:text-ink-muted"
          >
            Mission Control
          </button>
        </div>
      </div>
    </nav>
  );
}

function SearchResults({
  result,
  project,
  onSelectProject,
  onSelectConversation,
  onClear,
}: {
  result: SearchResult;
  project: string;
  onSelectProject: (p: string) => void;
  onSelectConversation: (id: string) => void;
  onClear: () => void;
}) {
  return (
    <section className="px-1.5">
      <div className="flex items-baseline justify-between px-2 pb-1 pt-1">
        <span className="text-[9px] uppercase tracking-[0.08em] text-ink-faint/60">
          {result.hits.length} result{result.hits.length === 1 ? "" : "s"}
          {result.scope === "all" ? " · all projects" : ""}
        </span>
        <button onClick={onClear} className="text-[9px] text-ink-faint hover:text-ink">
          clear
        </button>
      </div>
      {result.hits.length === 0 && (
        <p className="px-2 text-[11px] text-ink-faint">No conversations match.</p>
      )}
      <ul>
        {result.hits.map((hit) => (
          <li key={`${hit.project}-${hit.conversation_id}`}>
            <button
              onClick={() => {
                if (hit.project !== project) onSelectProject(hit.project);
                onSelectConversation(hit.conversation_id);
              }}
              className="w-full rounded-md px-2 py-1.5 text-left transition hover:bg-canvas-overlay/40"
            >
              <div className="truncate text-[11px] text-ink-muted">{hit.title}</div>
              {/* Every hit names its project, so a cross-project result is never
                  mistaken for the current one. */}
              <div className="mt-0.5 text-[9px] text-brand-400/70">{hit.project}</div>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
