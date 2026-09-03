/**
 * Mission Control — diagnostics and operations.
 *
 * This used to be the identity of MondayOS: a full-screen Brain with orbital
 * nodes you navigated by clicking. That identity now lives in the AI Workspace,
 * where Monday is a small ambient presence beside the conversation. What is left
 * here is the part that was always useful and never needed a metaphor — the
 * answers to six operational questions:
 *
 *     What is running?   What is blocked?    What failed?
 *     What needs approval?   What changed?   What is healthy?
 *
 * So it is built like the tools that answer those questions well: a summary
 * strip you read in one glance, a plain section list, and dense tables. Activity
 * Monitor, not a HUD. Every row is scannable, every number is a count of
 * something real, and nothing animates unless it is reporting motion.
 *
 * This is a *secondary* surface. It opens as an overlay over the conversation
 * and closes back to it, and the conversation stays mounted the whole time —
 * see App.tsx. Nothing here can become the home screen.
 */

import { useMemo, useState } from "react";
import { useApp } from "@/state/store";
import { useAsync } from "@/common/useAsync";
import { DemoBadge } from "@/common/DemoBadge";
import { canDecide, decisionLabel } from "@/state/approvals";
import { elapsedLabel, stageTone, workflowProgress } from "@/workspace/workflow";
import type { Agent, Approval, Task } from "@/adapter/types";

type View =
  | "overview"
  | "agents"
  | "tasks"
  | "approvals"
  | "workflows"
  | "knowledge"
  | "integrations"
  | "system";

const VIEWS: { id: View; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "agents", label: "Agents" },
  { id: "tasks", label: "Tasks" },
  { id: "approvals", label: "Approvals" },
  { id: "workflows", label: "Workflows" },
  { id: "knowledge", label: "Knowledge" },
  { id: "integrations", label: "Integrations" },
  { id: "system", label: "System" },
];

export function MissionControl({ onClose }: { onClose: () => void }) {
  const { state, connection } = useApp();
  // Local view state, deliberately. Reading the global `section` would let a
  // command elsewhere decide what this overlay shows, and would reintroduce the
  // coupling that made this page the application shell.
  const [view, setView] = useState<View>("overview");

  const counts = useMemo(() => summarise(state.agents, state.tasks, state.approvals), [
    state.agents,
    state.tasks,
    state.approvals,
  ]);

  return (
    <div className="flex h-screen flex-col bg-canvas text-ink">
      <header className="flex shrink-0 items-center justify-between border-b border-line px-5 py-2.5">
        <div className="flex items-baseline gap-3">
          <h1 className="text-[13px] font-medium tracking-tight">Mission Control</h1>
          <span className="text-[10px] text-ink-faint">Diagnostics &amp; operations</span>
        </div>
        <div className="flex items-center gap-3 text-[10px]">
          <DemoBadge connection={connection} reason={state.demoReason} />
          {state.system && (
            <span className="text-ink-faint">v{state.system.version}</span>
          )}
          <button
            onClick={onClose}
            className="focus-ring rounded-md border border-line px-2.5 py-1 text-[11px] text-ink-muted transition hover:border-brand-400/50 hover:text-ink"
          >
            Back to Monday
          </button>
        </div>
      </header>

      {/* The one-glance answer. Six numbers, no cards. */}
      <div className="flex shrink-0 flex-wrap items-center gap-x-6 gap-y-1 border-b border-line px-5 py-2">
        <Stat label="Running" value={counts.running} tone="text-status-executing" />
        <Stat label="Blocked" value={counts.blocked} tone="text-status-blocked" />
        <Stat label="Failed" value={counts.failed} tone="text-status-blocked" />
        <Stat label="Awaiting approval" value={counts.awaiting} tone="text-status-awaiting" />
        <Stat label="Open tasks" value={counts.openTasks} tone="text-ink-muted" />
        <span className="ml-auto flex items-center gap-1.5 text-[10px] text-ink-faint">
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              state.system?.healthy ? "bg-status-completed" : "bg-status-blocked"
            }`}
          />
          {state.system ? (state.system.healthy ? "Healthy" : "Degraded") : "connecting…"}
        </span>
      </div>

      <div className="flex min-h-0 flex-1">
        <nav className="w-[152px] shrink-0 border-r border-line py-2">
          <ul className="px-1.5">
            {VIEWS.map((v) => (
              <li key={v.id}>
                <button
                  onClick={() => setView(v.id)}
                  aria-current={view === v.id ? "page" : undefined}
                  className={`w-full rounded-md px-2.5 py-[5px] text-left text-[11px] transition ${
                    view === v.id
                      ? "bg-canvas-overlay/60 text-ink"
                      : "text-ink-faint hover:bg-canvas-overlay/30 hover:text-ink-muted"
                  }`}
                >
                  {v.label}
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <main className="min-w-0 flex-1 overflow-y-auto px-5 py-4">
          <Body view={view} />
        </main>
      </div>
    </div>
  );
}

function summarise(agents: Agent[], tasks: Task[], approvals: Approval[]) {
  return {
    running: agents.filter((a) => a.activity === "executing" || a.activity === "thinking").length,
    blocked:
      agents.filter((a) => a.activity === "blocked").length +
      tasks.filter((t) => t.status === "blocked").length,
    failed: approvals.filter((a) => a.status === "rejected").length,
    awaiting:
      approvals.filter((a) => a.status === "open").length +
      agents.filter((a) => a.activity === "awaiting").length,
    openTasks: tasks.filter((t) => t.status !== "completed").length,
  };
}

function Stat({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className={`text-[13px] tabular-nums ${value > 0 ? tone : "text-ink-faint/50"}`}>
        {value}
      </span>
      <span className="text-[10px] text-ink-faint">{label}</span>
    </span>
  );
}

function Body({ view }: { view: View }) {
  switch (view) {
    case "overview":
      return <Overview />;
    case "agents":
      return <Agents />;
    case "tasks":
      return <Tasks />;
    case "approvals":
      return <Approvals />;
    case "workflows":
      return <Workflows />;
    case "knowledge":
      return <Knowledge />;
    case "integrations":
      return <Integrations />;
    case "system":
      return <System />;
  }
}

// --------------------------------------------------------------------------
// Shared primitives — a table vocabulary, used by every section.
// --------------------------------------------------------------------------

function Section({ title, count, children }: { title: string; count?: number; children: React.ReactNode }) {
  return (
    <section className="mb-6">
      <h2 className="mb-1.5 flex items-baseline gap-2 text-[10px] uppercase tracking-[0.08em] text-ink-faint/70">
        {title}
        {count !== undefined && <span className="tabular-nums text-ink-faint/50">{count}</span>}
      </h2>
      {children}
    </section>
  );
}

function Table({ head, children }: { head: string[]; children: React.ReactNode }) {
  return (
    <table className="w-full border-collapse text-left">
      <thead>
        <tr className="border-b border-line">
          {head.map((h) => (
            <th
              key={h}
              className="pb-1 pr-3 text-[9px] font-normal uppercase tracking-[0.08em] text-ink-faint/60"
            >
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody className="divide-y divide-line/40">{children}</tbody>
    </table>
  );
}

function Cell({ children, mono, dim }: { children: React.ReactNode; mono?: boolean; dim?: boolean }) {
  return (
    <td
      className={`max-w-0 truncate py-1.5 pr-3 text-[11px] ${
        dim ? "text-ink-faint" : "text-ink-muted"
      } ${mono ? "font-mono text-[10px]" : ""}`}
    >
      {children}
    </td>
  );
}

function Dot({ tone, title }: { tone: string; title: string }) {
  return <span className={`inline-block h-1.5 w-1.5 rounded-full ${tone}`} title={title} />;
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="text-[11px] text-ink-faint">{children}</p>;
}

function Loading() {
  return <p className="text-[11px] text-ink-faint">Loading…</p>;
}

function Failed({ message }: { message: string }) {
  return <p className="text-[11px] text-status-blocked">{message}</p>;
}

const TASK_TONE: Record<Task["status"], string> = {
  active: "bg-status-executing",
  blocked: "bg-status-blocked",
  review: "bg-status-awaiting",
  completed: "bg-status-completed",
};

const AGENT_TONE: Record<string, string> = {
  executing: "bg-status-executing",
  thinking: "bg-accent-violet",
  awaiting: "bg-status-awaiting",
  blocked: "bg-status-blocked",
  learning: "bg-accent-magenta",
  idle: "bg-ink-faint/40",
};

// --------------------------------------------------------------------------
// Sections
// --------------------------------------------------------------------------

/** Answers all six questions at once, and links nowhere. */
function Overview() {
  const { state } = useApp();
  const attention = state.tasks.filter((t) => t.status === "blocked" || t.status === "review");
  const busy = state.agents.filter((a) => a.activity !== "idle");
  const open = state.approvals.filter((a) => a.status === "open");

  return (
    <>
      <Section title="Needs attention" count={attention.length + open.length}>
        {attention.length + open.length === 0 ? (
          <Empty>Nothing blocked, and nothing waiting on a decision.</Empty>
        ) : (
          <Table head={["", "Item", "State", "Detail"]}>
            {open.map((a) => (
              <tr key={a.id}>
                <td className="w-4 py-1.5">
                  <Dot tone="bg-status-awaiting" title="awaiting approval" />
                </td>
                <Cell mono>{a.taskId}</Cell>
                <Cell dim>awaiting approval</Cell>
                <Cell dim>{a.summary}</Cell>
              </tr>
            ))}
            {attention.map((t) => (
              <tr key={t.id}>
                <td className="w-4 py-1.5">
                  <Dot tone={TASK_TONE[t.status]} title={t.status} />
                </td>
                <Cell mono>{t.id}</Cell>
                <Cell dim>{t.status}</Cell>
                <Cell dim>{t.blockedReason ?? t.title}</Cell>
              </tr>
            ))}
          </Table>
        )}
      </Section>

      <Section title="Running" count={busy.length}>
        {busy.length === 0 ? (
          <Empty>No agent is currently working.</Empty>
        ) : (
          <Table head={["", "Agent", "Activity", "Task"]}>
            {busy.map((a) => (
              <tr key={a.id}>
                <td className="w-4 py-1.5">
                  <Dot tone={AGENT_TONE[a.activity] ?? "bg-ink-faint/40"} title={a.activity} />
                </td>
                <Cell>{a.name}</Cell>
                <Cell dim>{a.activity}</Cell>
                <Cell mono dim>
                  {a.task ?? "—"}
                </Cell>
              </tr>
            ))}
          </Table>
        )}
      </Section>

      <Section title="Recent changes" count={state.activity.length}>
        {state.activity.length === 0 ? (
          <Empty>No activity recorded.</Empty>
        ) : (
          <ol className="divide-y divide-line/40">
            {state.activity.slice(0, 12).map((e) => (
              <li key={e.id} className="flex items-baseline gap-3 py-1.5">
                <span className="shrink-0 font-mono text-[9px] text-ink-faint/60">
                  {new Date(e.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </span>
                <span className="min-w-0 flex-1 truncate text-[11px] text-ink-muted">
                  {e.message}
                </span>
              </li>
            ))}
          </ol>
        )}
      </Section>
    </>
  );
}

function Agents() {
  const { state } = useApp();
  if (!state.agents.length) return <Empty>No agents registered.</Empty>;
  return (
    <Section title="Agent fleet" count={state.agents.length}>
      <Table head={["", "Agent", "Role", "Activity", "Task"]}>
        {state.agents.map((a) => (
          <tr key={a.id}>
            <td className="w-4 py-1.5">
              <Dot tone={AGENT_TONE[a.activity] ?? "bg-ink-faint/40"} title={a.activity} />
            </td>
            <Cell>{a.name}</Cell>
            <Cell dim>{a.role}</Cell>
            <Cell dim>{a.activity}</Cell>
            <Cell mono dim>
              {a.task ?? "—"}
            </Cell>
          </tr>
        ))}
      </Table>
    </Section>
  );
}

function Tasks() {
  const { state, adapter } = useApp();
  const tasks = useAsync(() => adapter!.listTasks(), [adapter], !!adapter);
  if (tasks.loading) return <Loading />;
  if (tasks.error) return <Failed message={tasks.error.message} />;
  const rows = tasks.data ?? state.tasks;
  if (!rows.length) return <Empty>No tasks.</Empty>;
  return (
    <Section title="Tasks" count={rows.length}>
      <Table head={["", "ID", "Title", "State", "Agent"]}>
        {rows.map((t) => (
          <tr key={t.id}>
            <td className="w-4 py-1.5">
              <Dot tone={TASK_TONE[t.status]} title={t.status} />
            </td>
            <Cell mono>{t.id}</Cell>
            <Cell>{t.title}</Cell>
            <Cell dim>{t.status}</Cell>
            <Cell dim>{t.agent ?? "—"}</Cell>
          </tr>
        ))}
      </Table>
    </Section>
  );
}

function Approvals() {
  const { state, approve, reject } = useApp();
  const list = state.approvals;
  if (!list.length) return <Empty>Nothing is waiting on a decision.</Empty>;
  return (
    <Section title="Approvals" count={list.length}>
      <div className="divide-y divide-line/40">
        {list.map((a) => (
          <div key={a.id} className="py-3">
            <div className="flex items-baseline justify-between gap-3">
              <span className="truncate font-mono text-[11px] text-ink">
                {a.taskId} · {a.teamRunId}
              </span>
              <span className="shrink-0 text-[10px] text-ink-faint">{decisionLabel(a.status)}</span>
            </div>
            <p className="mt-1 text-[11px] leading-relaxed text-ink-muted">{a.summary}</p>
            <ul className="mt-1.5 space-y-0.5">
              {a.verdicts.map((v, i) => (
                <li key={i} className="flex items-baseline gap-2 text-[10px]">
                  <span
                    className={
                      v.verdict === "pass"
                        ? "text-status-completed"
                        : v.verdict === "fail"
                          ? "text-status-blocked"
                          : "text-status-awaiting"
                    }
                  >
                    {v.role}: {v.verdict}
                  </span>
                  <span className="min-w-0 truncate text-ink-faint">{v.note}</span>
                </li>
              ))}
            </ul>
            {canDecide(a) && (
              <div className="mt-2 flex gap-2">
                <button
                  onClick={() => void approve(a.id)}
                  className="focus-ring rounded border border-status-completed/40 px-2.5 py-1 text-[10px] text-status-completed transition hover:bg-status-completed/10"
                >
                  Approve
                </button>
                <button
                  onClick={() => void reject(a.id, "Rejected from Mission Control")}
                  className="focus-ring rounded border border-status-blocked/40 px-2.5 py-1 text-[10px] text-status-blocked transition hover:bg-status-blocked/10"
                >
                  Reject
                </button>
              </div>
            )}
          </div>
        ))}
      </div>
    </Section>
  );
}

function Workflows() {
  const { adapter } = useApp();
  const runs = useAsync(() => adapter!.listTeamRuns(), [adapter], !!adapter);
  if (runs.loading) return <Loading />;
  if (runs.error) return <Failed message={runs.error.message} />;
  const list = runs.data ?? [];
  if (!list.length) return <Empty>No workflow runs.</Empty>;

  return (
    <Section title="Workflow runs" count={list.length}>
      <div className="divide-y divide-line/40">
        {list.map((run) => {
          const progress = workflowProgress(run);
          return (
            <div key={run.id} className="py-3">
              <div className="flex items-baseline justify-between gap-3">
                <span className="truncate font-mono text-[11px] text-ink">
                  {run.id} · {run.taskId}
                </span>
                <span className="shrink-0 text-[10px] tabular-nums text-ink-faint">
                  {run.mode} · {progress.completed}/{progress.total}
                </span>
              </div>
              {/* A run is a pipeline; rows read like a build log. */}
              <ol className="mt-1.5 divide-y divide-line/30">
                {run.stages.map((s) => (
                  <li key={s.id} className="flex items-baseline gap-2.5 py-1">
                    <span className={`w-24 shrink-0 text-[10px] ${TONE[stageTone(s)]}`}>
                      {s.stage}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-[10px] text-ink-muted">
                      {s.summary ?? "—"}
                    </span>
                    <span className="shrink-0 font-mono text-[9px] text-ink-faint/70">
                      {s.model ?? ""} {elapsedLabel(s.elapsedMs)}
                    </span>
                  </li>
                ))}
              </ol>
            </div>
          );
        })}
      </div>
    </Section>
  );
}

const TONE: Record<string, string> = {
  done: "text-status-completed",
  running: "text-status-executing",
  failed: "text-status-blocked",
  idle: "text-ink-faint",
  awaiting: "text-status-awaiting",
};

function Knowledge() {
  const { adapter } = useApp();
  const [query, setQuery] = useState("");
  const items = useAsync(() => adapter!.searchKnowledge(query), [adapter, query], !!adapter);

  return (
    <Section title="Knowledge">
      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search knowledge"
        className="focus-ring mb-2 w-full max-w-sm rounded-md bg-canvas-overlay/40 px-2 py-1 text-[11px] text-ink placeholder:text-ink-faint/70"
      />
      {items.loading ? (
        <Loading />
      ) : items.error ? (
        <Failed message={items.error.message} />
      ) : !items.data?.length ? (
        <Empty>No entries.</Empty>
      ) : (
        <Table head={["ID", "Title", "Type", "Summary"]}>
          {items.data.slice(0, 60).map((k) => (
            <tr key={k.id}>
              <Cell mono>{k.id}</Cell>
              <Cell>{k.title}</Cell>
              <Cell dim>{k.kind}</Cell>
              <Cell dim>{k.summary ?? "—"}</Cell>
            </tr>
          ))}
        </Table>
      )}
    </Section>
  );
}

function Integrations() {
  const { adapter } = useApp();
  const prs = useAsync(() => adapter!.listPullRequests(), [adapter], !!adapter);
  const pub = useAsync(() => adapter!.getPublishHistory(), [adapter], !!adapter);

  return (
    <>
      <Section title="Pull requests" count={prs.data?.length}>
        {prs.loading ? (
          <Loading />
        ) : prs.error ? (
          <Failed message={prs.error.message} />
        ) : !prs.data?.length ? (
          <Empty>MondayOS does not manage pull requests yet.</Empty>
        ) : (
          <Table head={["", "PR", "Title"]}>
            {prs.data.map((p) => (
              <tr key={p.number}>
                <td className="w-4 py-1.5">
                  <Dot
                    tone={p.state === "open" ? "bg-status-completed" : "bg-ink-faint/40"}
                    title={p.state}
                  />
                </td>
                <Cell mono>#{p.number}</Cell>
                <Cell>{p.title}</Cell>
              </tr>
            ))}
          </Table>
        )}
      </Section>

      <Section title="Publish history" count={pub.data?.length}>
        {pub.loading ? (
          <Loading />
        ) : pub.error ? (
          <Failed message={pub.error.message} />
        ) : !pub.data?.length ? (
          <Empty>Nothing published.</Empty>
        ) : (
          <Table head={["Document", "Target", "When"]}>
            {pub.data.map((r) => (
              <tr key={r.id}>
                <Cell mono>{r.docId}</Cell>
                <Cell dim>{r.target}</Cell>
                <Cell dim>{r.at}</Cell>
              </tr>
            ))}
          </Table>
        )}
      </Section>
    </>
  );
}

function System() {
  const { state, connection } = useApp();
  const sys = state.system;
  if (!sys) return <Empty>System status unavailable.</Empty>;

  const rows: [string, string][] = [
    ["Health", sys.healthy ? "healthy" : "degraded"],
    ["Version", sys.version],
    ["Session", sys.sessionId],
    ["Uptime", formatUptime(sys.uptimeSeconds)],
    ["Provider", sys.provider || "not configured"],
    ["Model", sys.model || "—"],
    ["Data source", connection === "live" ? "live MondayOS API" : String(connection)],
  ];

  return (
    <Section title="Runtime">
      <Table head={["", "Property", "Value"]}>
        {rows.map(([label, value]) => (
          <tr key={label}>
            <td className="w-4 py-1.5">
              {label === "Health" && (
                <Dot
                  tone={sys.healthy ? "bg-status-completed" : "bg-status-blocked"}
                  title={value}
                />
              )}
            </td>
            <Cell dim>{label}</Cell>
            <Cell mono>{value}</Cell>
          </tr>
        ))}
      </Table>
    </Section>
  );
}

function formatUptime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${Math.floor(seconds % 60)}s`;
  return `${Math.floor(seconds)}s`;
}
