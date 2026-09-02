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
import { Composer, COMMANDS } from "./Composer";
import { ProjectContext } from "./ProjectContext";
import { ConversationView } from "./Conversation";
import { ReturnBrief } from "./ReturnBrief";
import { Sidebar } from "./Sidebar";
import { StatusRail } from "./StatusRail";
import { useWorkspace } from "./useWorkspace";
import type { MondayActivity } from "./mondayState";
import type { WorkspaceClient } from "./client";
import type { Conversation, ContextSnapshot } from "./types";


/**
 * What is being worked on in this project, as one readable line.
 *
 * The in-progress task's title with its id and status markers stripped — the
 * rail wants "Building Repository Intelligence", not
 * "TASK-0074 [in-progress] P1 Building Repository Intelligence". Empty when
 * nothing is in progress; the rail then shows no subtitle rather than a
 * placeholder.
 */
function activeTaskTitle(context: ContextSnapshot | null): string {
  const item = context?.sources
    .find((s) => s.name === "tasks")
    ?.items.find((i) => i.includes("[in-progress]") || i.includes("[review]"));
  if (!item) return "";
  return item.replace(/^\S+\s*/, "").replace(/^\[[^\]]+\]\s*/, "").replace(/^P\d\s*/, "").trim();
}


function lastProvider(conversation: Conversation | null): string {
  const withProvider = conversation?.messages.filter((m) => m.provider) ?? [];
  const last = withProvider[withProvider.length - 1];
  return last ? last.model || last.provider : "";
}

/**
 * The ambient wash, per activity. Deliberately near-invisible: these are 4-6%
 * tints, chosen so a state change is felt peripherally rather than seen.
 */
const AMBIENT: Record<MondayActivity, string> = {
  idle: "from-brand-500/[0.04]",
  "reading-project": "from-accent-magenta/[0.05]",
  "loading-context": "from-accent-violet/[0.05]",
  "searching-knowledge": "from-accent-magenta/[0.05]",
  "analyzing-repository": "from-accent-violet/[0.05]",
  thinking: "from-accent-violet/[0.06]",
  streaming: "from-brand-500/[0.06]",
  "writing-knowledge": "from-accent-magenta/[0.05]",
  "creating-task": "from-status-executing/[0.05]",
  "waiting-approval": "from-status-awaiting/[0.05]",
  error: "from-status-blocked/[0.05]",
};

export function AIWorkspace({
  client,
  onOpenDiagnostics = () => {},
}: {
  client?: WorkspaceClient;
  onOpenDiagnostics?: () => void;
}) {
  const { state } = useApp();
  const [ws, actions] = useWorkspace(state.baseUrl, client, state.activeProduct);
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
        onOpenDiagnostics={onOpenDiagnostics}
        projectSubtitle={activeTaskTitle(ws.context)}
        statusRail={
          <StatusRail
            activity={ws.activityState}
            project={ws.project}
            provider={lastProvider(ws.conversation)}
            healthy={state.healthOk}
          />
        }
      />

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Monday's presence: small, live, and never in the way. */}
        <header className="flex shrink-0 items-center justify-between border-b border-line px-4 py-2">
          <div className="min-w-0 truncate text-[12px] text-ink">
            {ws.conversation?.title ?? "AI Workspace"}
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

        {/* The atmosphere. A single very faint wash behind the conversation,
            tinted by what Monday is doing. This is the ambient presence the orb
            used to provide by being large and bright — moved behind the text
            where it can be felt without being looked at. At 5% it is below the
            threshold of "an element"; you notice it change, not that it exists. */}
        <div className="relative min-h-0 flex-1">
          <div
            aria-hidden
            className={`pointer-events-none absolute inset-x-0 top-0 h-64 bg-gradient-to-b ${AMBIENT[ws.activityState]} to-transparent transition-colors duration-[1500ms]`}
          />
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
            <ReturnBrief
              briefing={ws.briefing}
              onContinue={actions.continueWorking}
              onSuggest={(text) => {
                // A suggestion needs somewhere to land. With no conversation
                // open, start one and let the composer carry the text.
                if (ws.conversation) void actions.send(text);
                else void actions.newConversation().then(() => actions.send(text));
              }}
            />
          )}
        </div>

        <Composer
          onSend={(text) => void actions.send(text)}
          onCommand={runCommand}
          onStop={actions.stop}
          sending={ws.sending}
          project={ws.project}
          contextLoaded={Boolean(ws.context && ws.context.sources.some((s) => s.item_count > 0))}
          provider={lastProvider(ws.conversation)}
          disabled={!ws.conversation}
        />
      </div>

      {!panelCollapsed && (
        <div className="w-[248px] shrink-0 border-l border-line/70">
          <ProjectContext
            context={ws.context}
            project={ws.project}
            activity={ws.activity}
            onRefresh={() => void actions.refreshContext()}
          />
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
