/**
 * The AI Workspace — MondayOS's conversational surface.
 *
 * Three columns: projects and conversations on the left, the conversation in the
 * middle, loaded context on the right. The middle column gets the space, because
 * that is where the work happens.
 *
 * The currently loaded project is stated in two places — the sidebar selection
 * and the context panel header — on purpose. Every answer is scoped to one
 * project (ADR-017), so which project is loaded is never something the operator
 * should have to infer.
 */

import { useState } from "react";
import { useApp } from "@/state/store";
import { ConversationView } from "./Conversation";
import { ContextPanel } from "./ContextPanel";
import { useWorkspace } from "./useWorkspace";
import type { WorkspaceClient } from "./client";

function relativeDay(iso: string): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const days = Math.floor((Date.now() - then) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 7) return `${days}d`;
  return new Date(then).toLocaleDateString([], { month: "short", day: "numeric" });
}

export function AIWorkspace({ client }: { client?: WorkspaceClient }) {
  const { state } = useApp();
  const [ws, actions] = useWorkspace(state.baseUrl, client);
  const [menuFor, setMenuFor] = useState("");

  const offline = !state.baseUrl && !client;

  return (
    <div className="flex h-full min-h-0">
      {/* Left: projects + conversations. */}
      <nav className="flex w-[240px] shrink-0 flex-col border-r border-line bg-canvas-raised/40">
        <div className="border-b border-line px-3 py-2.5">
          <label
            htmlFor="ws-project"
            className="text-[10px] uppercase tracking-wider text-ink-faint"
          >
            Project
          </label>
          <select
            id="ws-project"
            value={ws.project}
            onChange={(e) => actions.selectProject(e.target.value)}
            disabled={ws.loadingProjects || ws.projects.length === 0}
            className="focus-ring mt-1 w-full rounded-md border border-line bg-canvas px-2 py-1.5 text-[12px] text-ink disabled:opacity-50"
          >
            {ws.projects.length === 0 && <option value="">No projects</option>}
            {ws.projects.map((p) => (
              <option key={p.name} value={p.name}>
                {p.display_name}
              </option>
            ))}
          </select>
        </div>

        <div className="flex items-center justify-between px-3 py-2">
          <span className="text-[10px] uppercase tracking-wider text-ink-faint">
            Conversations
          </span>
          <button
            onClick={() => void actions.newConversation()}
            disabled={!ws.project}
            className="focus-ring rounded px-1.5 py-0.5 text-[11px] text-ink-muted transition hover:text-brand-400 disabled:opacity-40"
            title="New conversation"
          >
            + New
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
          {ws.conversations.length === 0 ? (
            <div className="px-1 py-2 text-[11px] text-ink-faint">
              {ws.project ? "No conversations yet." : "Select a project."}
            </div>
          ) : (
            <ul className="space-y-0.5">
              {ws.conversations.map((c) => {
                const active = ws.conversation?.id === c.id;
                return (
                  <li key={c.id} className="relative">
                    <button
                      onClick={() => actions.selectConversation(c.id)}
                      className={`w-full rounded-md px-2 py-1.5 text-left transition ${
                        active
                          ? "bg-brand-500/10 text-ink"
                          : "text-ink-muted hover:bg-canvas-overlay/60 hover:text-ink"
                      }`}
                    >
                      <div className="truncate text-[12px]">{c.title}</div>
                      <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-ink-faint">
                        <span>{relativeDay(c.updated_at)}</span>
                        <span>·</span>
                        <span>{c.message_count} msg</span>
                      </div>
                    </button>
                    <button
                      onClick={() => setMenuFor(menuFor === c.id ? "" : c.id)}
                      className="absolute right-1 top-1.5 rounded px-1 text-[11px] text-ink-faint hover:text-ink"
                      aria-label="Conversation actions"
                    >
                      ⋯
                    </button>
                    {menuFor === c.id && (
                      <div className="absolute right-1 top-7 z-10 rounded-md border border-line bg-canvas-overlay py-1 shadow-card">
                        <button
                          onClick={() => {
                            setMenuFor("");
                            void actions.archive(c.id);
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
        </div>
      </nav>

      {/* Center: the conversation. */}
      <div className="flex min-w-0 flex-1 flex-col">
        {offline && (
          <div className="border-b border-status-awaiting/30 bg-status-awaiting/10 px-6 py-2 text-[11px] text-status-awaiting">
            No MondayOS API configured. The AI Workspace reads and writes real
            conversations, so it has nothing to talk to — start the dashboard API and set
            VITE_MONDAYOS_API.
          </div>
        )}
        {ws.error && (
          <div className="border-b border-status-blocked/30 bg-status-blocked/10 px-6 py-2 text-[11px] text-status-blocked">
            {ws.error}
          </div>
        )}
        {ws.notice && (
          <div className="flex items-center justify-between border-b border-status-completed/30 bg-status-completed/10 px-6 py-2 text-[11px] text-status-completed">
            <span>{ws.notice}</span>
            <button onClick={actions.dismissNotice} className="text-ink-faint hover:text-ink">
              ✕
            </button>
          </div>
        )}
        <ConversationView
          conversation={ws.conversation}
          sending={ws.sending}
          loading={ws.loadingConversation}
          onSend={(text) => void actions.send(text)}
          onRetry={() => void actions.retry()}
          onSave={(id) => void actions.saveToKnowledge(id)}
          onRename={(id, title) => void actions.rename(id, title)}
        />
      </div>

      {/* Right: what was loaded. */}
      <ContextPanel
        context={ws.context}
        project={ws.project}
        onRefresh={() => void actions.refreshContext()}
      />
    </div>
  );
}
