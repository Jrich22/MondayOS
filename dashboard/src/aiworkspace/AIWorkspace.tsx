/**
 * The AI Workspace — MondayOS's primary surface.
 *
 * Three regions: projects and conversations left, the conversation centre,
 * context and activity right. The conversation gets the space because that is
 * where the work happens; the rails exist to answer "where am I" and "what does
 * Monday know", and then get out of the way.
 *
 * Monday's presence is the Brain, small and in the header. It is a **state
 * indicator**, not decoration: it reflects what the system is actually doing —
 * reading context, thinking, executing, blocked — and is driven by real
 * operations rather than a timer. It never takes space from the conversation.
 *
 * Slash commands are handled here because they compose existing actions rather
 * than adding capability: `/tasks` reads the context panel's task source,
 * `/learn` runs the same knowledge capture the response action does. A command
 * that needed new backend surface would not belong in this increment.
 */

import { useCallback, useEffect, useState } from "react";
import { useApp } from "@/state/store";
import { MondayBrain } from "@/components/monday";
import type { BrainState } from "@/components/monday";
import { ActivityTimeline } from "./ActivityTimeline";
import { Composer, COMMANDS } from "./Composer";
import { ContextPanel } from "./ContextPanel";
import { ConversationView } from "./Conversation";
import { ReturnBrief } from "./ReturnBrief";
import { Sidebar } from "./Sidebar";
import { useWorkspace, type MondayState } from "./useWorkspace";
import type { WorkspaceClient } from "./client";

/** Monday's operational state → the Brain's existing visual vocabulary. */
const BRAIN_STATE: Record<MondayState, BrainState> = {
  idle: "idle",
  thinking: "thinking",
  learning: "learning",
  executing: "executing",
  completed: "completed",
  blocked: "blocked",
};

const STATE_LABEL: Record<MondayState, string> = {
  idle: "Idle",
  thinking: "Thinking",
  learning: "Reading context",
  executing: "Executing",
  completed: "Done",
  blocked: "Error",
};

export function AIWorkspace({ client }: { client?: WorkspaceClient }) {
  const { state } = useApp();
  const [ws, actions] = useWorkspace(state.baseUrl, client);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [panelCollapsed, setPanelCollapsed] = useState(false);
  const [commandNotice, setCommandNotice] = useState("");

  const offline = !state.baseUrl && !client;

  // Slash commands compose existing actions. Anything that would need new
  // backend surface is deliberately absent rather than stubbed.
  const runCommand = useCallback(
    (name: string, args: string) => {
      const ctx = ws.context;
      switch (name) {
        case "continue":
          actions.continueWorking();
          return;
        case "new":
          void actions.newConversation();
          return;
        case "switch": {
          const target = args.trim().toLowerCase();
          const match = ws.projects.find((p) => p.name === target);
          if (match) actions.selectProject(match.name);
          else
            setCommandNotice(
              `No project named ${target || "(none given)"}. Known: ` +
                ws.projects.map((p) => p.name).join(", "),
            );
          return;
        }
        case "status": {
          const branch =
            ctx?.sources
              .find((s) => s.name === "git")
              ?.items.find((i) => i.startsWith("Current branch:")) ?? "branch unknown";
          const tasks = ctx?.sources.find((s) => s.name === "tasks");
          setCommandNotice(
            `${ws.project || "no project"} · ${branch} · ${tasks?.item_count ?? 0} task(s) in context` +
              (ctx ? ` · snapshot ${ctx.id}` : " · no snapshot"),
          );
          return;
        }
        case "context":
          setPanelCollapsed(false);
          setCommandNotice(ctx ? ctx.summary : "No context loaded.");
          return;
        case "tasks": {
          const tasks = ctx?.sources.find((s) => s.name === "tasks");
          setCommandNotice(
            tasks?.items.length
              ? tasks.items.slice(0, 8).join(" · ")
              : "No tasks associated with this project.",
          );
          return;
        }
        case "knowledge": {
          const k = ctx?.sources.find((s) => s.name === "knowledge");
          setCommandNotice(
            k?.items.length
              ? k.items.slice(0, 6).join(" · ")
              : "No knowledge entries loaded for this project.",
          );
          return;
        }
        case "learn": {
          const last = [...(ws.conversation?.messages ?? [])]
            .reverse()
            .find((m) => m.role === "assistant" && !m.error);
          if (last) void actions.saveToKnowledge(last.id);
          else setCommandNotice("No assistant response to save yet.");
          return;
        }
        case "help":
          setCommandNotice(COMMANDS.map((c) => `/${c.name} — ${c.hint}`).join("  ·  "));
          return;
        default:
          setCommandNotice(`Unknown command /${name}. Try /help.`);
      }
    },
    [ws, actions],
  );

  // Keyboard: ⌘N new conversation, Esc stops generation. ⌘K is already the
  // dashboard's command palette and is left alone.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "n") {
        e.preventDefault();
        void actions.newConversation();
      }
      if (e.key === "Escape" && ws.sending) actions.stop();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [actions, ws.sending]);

  const banner = commandNotice || ws.notice;

  return (
    <div className="flex h-full min-h-0">
      <Sidebar
        projects={ws.projects}
        project={ws.project}
        conversations={ws.conversations}
        activeConversation={ws.conversation?.id ?? ""}
        searchResult={ws.searchResult}
        collapsed={sidebarCollapsed}
        onSelectProject={actions.selectProject}
        onSelectConversation={actions.selectConversation}
        onNewConversation={() => void actions.newConversation()}
        onArchive={(id) => void actions.archive(id)}
        onSearch={(q, scope) => void actions.search(q, scope)}
        onClearSearch={actions.clearSearch}
        onToggle={() => setSidebarCollapsed((v) => !v)}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Monday's presence: small, live, and never in the way. */}
        <header className="flex shrink-0 items-center justify-between border-b border-line px-4 py-2">
          <div className="flex min-w-0 items-center gap-2.5">
            <div className="h-7 w-7 shrink-0">
              <MondayBrain state={BRAIN_STATE[ws.mondayState]} />
            </div>
            <div className="min-w-0">
              <div className="truncate text-[12px] text-ink">
                {ws.conversation?.title ?? "AI Workspace"}
              </div>
              <div className="text-[10px] text-ink-faint">
                {STATE_LABEL[ws.mondayState]}
                {ws.project ? ` · ${ws.project}` : ""}
              </div>
            </div>
          </div>
          <button
            onClick={() => setPanelCollapsed((v) => !v)}
            className="shrink-0 rounded px-2 py-1 text-[11px] text-ink-faint transition hover:text-ink"
          >
            {panelCollapsed ? "Show context" : "Hide context"}
          </button>
        </header>

        {offline && (
          <Banner tone="awaiting">
            No MondayOS API configured. The AI Workspace reads and writes real conversations,
            so it has nothing to talk to — start the dashboard API and set VITE_MONDAYOS_API.
          </Banner>
        )}
        {ws.switchingTo && (
          <Banner tone="brand">Loading {ws.switchingTo} context…</Banner>
        )}
        {ws.error && <Banner tone="blocked">{ws.error}</Banner>}
        {banner && (
          <Banner
            tone="completed"
            onDismiss={() => {
              setCommandNotice("");
              actions.dismissNotice();
            }}
          >
            {banner}
          </Banner>
        )}

        <div className="min-h-0 flex-1">
          {ws.conversation ? (
            <ConversationView
              conversation={ws.conversation}
              sending={ws.sending}
              streaming={ws.streaming}
              loading={ws.loadingConversation}
              onRetry={() => void actions.retry()}
              onSave={(id) => void actions.saveToKnowledge(id)}
              onTask={(id) => void actions.createTask(id)}
              onRename={(id, title) => void actions.rename(id, title)}
            />
          ) : (
            <ReturnBrief briefing={ws.briefing} onContinue={actions.continueWorking} />
          )}
        </div>

        <Composer
          onSend={(text) => void actions.send(text)}
          onCommand={runCommand}
          onStop={actions.stop}
          sending={ws.sending}
          project={ws.project}
          contextLoaded={Boolean(ws.context && ws.context.sources.some((s) => s.item_count > 0))}
          provider={
            ws.conversation?.messages.filter((m) => m.provider).slice(-1)[0]?.provider ?? ""
          }
          disabled={!ws.conversation}
        />
      </div>

      {!panelCollapsed && (
        <div className="flex w-[288px] shrink-0 flex-col border-l border-line bg-canvas-raised/40">
          <div className="min-h-0 flex-1 overflow-hidden">
            <ContextPanel
              context={ws.context}
              project={ws.project}
              onRefresh={() => void actions.refreshContext()}
            />
          </div>
          <ActivityTimeline events={ws.activity} />
        </div>
      )}
    </div>
  );
}

function Banner({
  tone,
  children,
  onDismiss,
}: {
  tone: "awaiting" | "blocked" | "completed" | "brand";
  children: React.ReactNode;
  onDismiss?: () => void;
}) {
  const tones = {
    awaiting: "border-status-awaiting/30 bg-status-awaiting/10 text-status-awaiting",
    blocked: "border-status-blocked/30 bg-status-blocked/10 text-status-blocked",
    completed: "border-status-completed/30 bg-status-completed/10 text-status-completed",
    brand: "border-brand-400/30 bg-brand-500/10 text-brand-200",
  } as const;
  return (
    <div
      className={`flex items-center justify-between gap-3 border-b px-6 py-2 text-[11px] ${tones[tone]}`}
    >
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {onDismiss && (
        <button onClick={onDismiss} className="shrink-0 text-ink-faint hover:text-ink">
          ✕
        </button>
      )}
    </div>
  );
}
