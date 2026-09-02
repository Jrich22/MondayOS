import { useEffect, useState } from "react";
import { useApp } from "@/state/store";
import { useAsync } from "@/common/useAsync";
import type { Approval, Product, Task } from "@/adapter/types";
import type { Section } from "@/command/intents";
import { canDecide, decisionLabel } from "@/state/approvals";
import { stageTone, workflowProgress, elapsedLabel } from "./workflow";

/**
 * The section workspaces. Clicking an orbital node (or issuing a command that
 * navigates) opens the matching workspace here, while Monday's Brain stays put
 * as the universal anchor. Every workspace reads through the adapter — no
 * product/task/agent/approval logic is reimplemented in the dashboard.
 */

const TITLES: Record<Section, string> = {
  home: "Home",
  // Rendered full-width by MissionControl, not in this side panel.
  "ai-workspace": "AI Workspace",
  products: "Products",
  tasks: "Tasks",
  agents: "Agent fleet",
  memory: "Memory",
  knowledge: "Knowledge",
  approvals: "Approvals",
  workflows: "Workflows",
  integrations: "Integrations",
};

const STATUS_BADGE: Record<Task["status"], string> = {
  active: "text-status-executing border-status-executing/40 bg-status-executing/10",
  blocked: "text-status-blocked border-status-blocked/40 bg-status-blocked/10",
  review: "text-status-awaiting border-status-awaiting/40 bg-status-awaiting/10",
  completed: "text-status-completed border-status-completed/40 bg-status-completed/10",
};

export function WorkspacePanel() {
  const { state, navigate, openCommand } = useApp();
  const { section, activeProduct } = state;
  if (section === "home") return null;

  return (
    <aside className="flex h-full flex-col overflow-hidden">
      <header className="flex items-center justify-between border-b border-line px-4 py-3">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold tracking-tight">{TITLES[section]}</h2>
          {section === "products" && activeProduct && (
            <button onClick={() => navigate("products")} className="text-[11px] text-ink-faint hover:text-ink">
              ← all products
            </button>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button onClick={openCommand} className="text-[11px] text-ink-faint hover:text-ink">Ask ⌘K</button>
          <button
            onClick={() => navigate("home")}
            aria-label="Back to Brain"
            className="focus-ring rounded-md border border-line px-2 py-1 text-[11px] text-ink-muted hover:text-ink"
          >
            ✕
          </button>
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <SectionBody section={section} />
      </div>
    </aside>
  );
}

function SectionBody({ section }: { section: Section }) {
  switch (section) {
    case "products":
      return <Products />;
    case "tasks":
      return <Tasks />;
    case "agents":
      return <Agents />;
    case "memory":
    case "knowledge":
      return <Knowledge section={section} />;
    case "approvals":
      return <Approvals />;
    case "workflows":
      return <Workflows />;
    case "integrations":
      return <Integrations />;
    default:
      return null;
  }
}

function Loading() {
  return <div className="animate-pulse-soft text-sm text-ink-faint">Loading…</div>;
}
function Failed({ message }: { message: string }) {
  return <div className="text-sm text-status-blocked">Couldn't load: {message}</div>;
}

// ---- Products -------------------------------------------------------------

function Products() {
  const { state, navigate, adapter } = useApp();
  const { activeProduct } = state;
  const detail = useAsync(() => adapter!.getProduct(activeProduct!), [adapter, activeProduct], !!adapter && !!activeProduct);

  if (!activeProduct) {
    return (
      <div className="grid gap-3">
        {state.products.map((p) => (
          <button
            key={p.key}
            onClick={() => navigate("products", p.key)}
            className="focus-ring rounded-xl border border-line bg-canvas-overlay/40 p-3 text-left transition hover:border-line-strong"
          >
            <div className="flex items-center justify-between">
              <span className="font-medium text-ink">{p.name}</span>
              <span className="text-[11px] text-ink-faint">{p.openTasks} open</span>
            </div>
            <div className="mt-1 text-[12px] text-ink-faint">{p.summary}</div>
          </button>
        ))}
      </div>
    );
  }
  if (detail.loading) return <Loading />;
  if (detail.error || !detail.data) return <Failed message={detail.error?.message ?? "not found"} />;
  return <ProductDetail product={detail.data} />;
}

function ProductDetail({ product }: { product: Product }) {
  const { adapter, navigate } = useApp();
  const tasks = useAsync(() => adapter!.listTasks({ product: product.key }), [adapter, product.key], !!adapter);
  return (
    <div className="space-y-4">
      <div>
        <div className="text-lg font-semibold text-ink">{product.name}</div>
        <div className="text-[12px] text-ink-muted">{product.summary}</div>
      </div>
      {product.sprint && (
        <div>
          <div className="mb-1 flex justify-between text-[11px] text-ink-faint">
            <span>{product.sprint.name}</span>
            <span>{product.sprint.done}/{product.sprint.total}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-canvas-overlay">
            <div className="h-full rounded-full bg-gradient-to-r from-accent-cyan to-accent-violet" style={{ width: `${(product.sprint.done / product.sprint.total) * 100}%` }} />
          </div>
        </div>
      )}
      {product.metrics && (
        <div className="grid grid-cols-2 gap-2">
          {product.metrics.map((m) => (
            <div key={m.label} className="rounded-lg border border-line bg-canvas-overlay/40 p-2">
              <div className="text-[10px] text-ink-faint">{m.label}</div>
              <div className={`text-sm font-semibold ${m.tone === "good" ? "text-status-completed" : m.tone === "bad" ? "text-status-blocked" : "text-ink"}`}>{m.value}</div>
            </div>
          ))}
        </div>
      )}
      {product.recommendation && (
        <div className="rounded-lg border border-brand-400/30 bg-brand-400/5 p-3 text-[12px] text-ink-muted">
          <span className="font-medium text-brand-200">Next: </span>
          {product.recommendation}
        </div>
      )}
      <div>
        <div className="mb-2 flex items-center justify-between text-[11px] uppercase tracking-wider text-ink-faint">
          <span>Tasks</span>
          <button onClick={() => navigate("tasks", product.key)} className="hover:text-ink">view all</button>
        </div>
        {tasks.loading ? <Loading /> : <TaskList tasks={tasks.data ?? []} />}
      </div>
    </div>
  );
}

// ---- Tasks ----------------------------------------------------------------

function Tasks() {
  const { state, adapter } = useApp();
  const tasks = useAsync(() => adapter!.listTasks({ product: state.activeProduct }), [adapter, state.activeProduct], !!adapter);
  if (tasks.loading) return <Loading />;
  if (tasks.error) return <Failed message={tasks.error.message} />;
  return <TaskList tasks={tasks.data ?? []} />;
}

function TaskList({ tasks }: { tasks: Task[] }) {
  if (!tasks.length) return <div className="text-sm text-ink-faint">No tasks.</div>;
  return (
    <ul className="space-y-2">
      {tasks.map((t) => (
        <li key={t.id} className="flex items-start gap-2 rounded-lg border border-line bg-canvas-overlay/30 p-2.5">
          <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-medium capitalize ${STATUS_BADGE[t.status]}`}>{t.status}</span>
          <div className="min-w-0">
            <div className="text-sm text-ink">{t.id} · {t.title}</div>
            {t.blockedReason && <div className="text-[11px] text-status-blocked">{t.blockedReason}</div>}
            {t.agent && <div className="text-[11px] text-ink-faint">{t.agent}</div>}
          </div>
        </li>
      ))}
    </ul>
  );
}

// ---- Agents ---------------------------------------------------------------

function Agents() {
  const { state } = useApp();
  return (
    <ul className="space-y-2">
      {state.agents.map((a) => (
        <li key={a.id} className="flex items-center gap-3 rounded-lg border border-line bg-canvas-overlay/30 p-2.5">
          <span className="h-2 w-2 rounded-full bg-accent-violet" />
          <div className="min-w-0 flex-1">
            <div className="flex justify-between">
              <span className="text-sm text-ink">{a.name}</span>
              <span className="text-[11px] capitalize text-ink-faint">{a.activity}</span>
            </div>
            <div className="text-[11px] text-ink-faint">{a.task ?? a.role}</div>
          </div>
        </li>
      ))}
    </ul>
  );
}

// ---- Knowledge / Memory ---------------------------------------------------

function Knowledge({ section }: { section: Section }) {
  const { adapter } = useApp();
  const items = useAsync(() => adapter!.searchKnowledge(""), [adapter], !!adapter);
  if (items.loading) return <Loading />;
  if (items.error) return <Failed message={items.error.message} />;
  const data = items.data ?? [];
  const shown = section === "memory" ? data.filter((k) => k.kind === "decision" || k.kind === "sprint" || k.summary) : data;
  return (
    <ul className="space-y-2">
      {shown.map((k) => (
        <li key={k.id} className="rounded-lg border border-line bg-canvas-overlay/30 p-2.5">
          <div className="flex items-center justify-between">
            <span className="text-sm text-ink">{k.title}</span>
            <span className="rounded border border-line px-1.5 py-0.5 text-[10px] capitalize text-ink-faint">{k.kind}</span>
          </div>
          <div className="text-[11px] text-ink-faint">{k.id}{k.summary ? ` · ${k.summary}` : ""}</div>
        </li>
      ))}
    </ul>
  );
}

// ---- Approvals ------------------------------------------------------------

function Approvals() {
  const { adapter, approve, reject, state } = useApp();
  const fetched = useAsync(() => adapter!.listApprovals(), [adapter, state.approvals], !!adapter);
  const [items, setItems] = useState<Approval[] | null>(null);
  const [note, setNote] = useState<string>("");
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    if (fetched.data) setItems(fetched.data);
  }, [fetched.data]);

  if (fetched.loading && !items) return <Loading />;
  if (fetched.error) return <Failed message={fetched.error.message} />;
  const list = items ?? [];

  // Approve/reject route through MondayOS via the store; the server enforces
  // ApprovalGate and returns a friendly idempotent result on duplicates.
  const decide = async (id: string, decision: "approve" | "reject") => {
    setBusyId(id);
    setNote("");
    const r = decision === "approve" ? await approve(id) : await reject(id, "Rejected via dashboard");
    setBusyId(null);
    if (r.ok) {
      const already = (r.data as Approval & { alreadyDecided?: boolean }).alreadyDecided;
      setNote(already ? `${id} was already decided — no change.` : `${id} ${r.data.status}.`);
      setItems((prev) => (prev ?? []).map((a) => (a.id === id ? { ...a, status: r.data.status } : a)));
    } else if (r.error.code === "already-decided") {
      setNote(r.error.message);
    } else {
      setNote(`Couldn't ${decision}: ${r.error.message}`);
    }
  };

  if (!list.length) return <div className="text-sm text-ink-faint">No approvals waiting.</div>;
  return (
    <div className="space-y-3">
      {note && <div className="rounded-lg border border-status-awaiting/30 bg-status-awaiting/10 p-2 text-[11px] text-status-awaiting">{note}</div>}
      {list.map((a) => (
        <div key={a.id} className="rounded-xl border border-line bg-canvas-overlay/40 p-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-ink">{a.taskId} · {a.teamRunId}</span>
            <span className="text-[11px] text-ink-faint">{decisionLabel(a.status)}</span>
          </div>
          <div className="mt-1 text-[12px] text-ink-muted">{a.summary}</div>
          <div className="mt-2 space-y-1">
            {a.verdicts.map((v, i) => (
              <div key={i} className="flex gap-2 text-[11px]">
                <span className={v.verdict === "pass" ? "text-status-completed" : v.verdict === "fail" ? "text-status-blocked" : "text-status-awaiting"}>
                  {v.role}: {v.verdict}
                </span>
                {v.note && <span className="text-ink-faint">— {v.note}</span>}
              </div>
            ))}
          </div>
          {a.affected.length > 0 && (
            <div className="mt-2 text-[11px] text-ink-faint">Affects: {a.affected.join(", ")}</div>
          )}
          <div className="mt-3 flex items-center gap-2">
            <button
              disabled={!canDecide(a) || busyId === a.id}
              onClick={() => decide(a.id, "approve")}
              className="focus-ring rounded-lg border border-status-completed/40 bg-status-completed/10 px-3 py-1 text-[12px] text-status-completed transition hover:bg-status-completed/20 disabled:opacity-40"
            >
              Approve
            </button>
            <button
              disabled={!canDecide(a) || busyId === a.id}
              onClick={() => decide(a.id, "reject")}
              className="focus-ring rounded-lg border border-status-blocked/40 px-3 py-1 text-[12px] text-status-blocked transition hover:bg-status-blocked/10 disabled:opacity-40"
            >
              Reject
            </button>
            {busyId === a.id && <span className="text-[11px] text-ink-faint">working…</span>}
          </div>
        </div>
      ))}
    </div>
  );
}

// ---- Workflows (live team-run visualization) ------------------------------

function Workflows() {
  const { adapter } = useApp();
  const runs = useAsync(() => adapter!.listTeamRuns(), [adapter], !!adapter);
  if (runs.loading) return <Loading />;
  if (runs.error) return <Failed message={runs.error.message} />;
  const list = runs.data ?? [];
  if (!list.length) return <div className="text-sm text-ink-faint">No team runs.</div>;

  const toneClass: Record<string, string> = {
    completed: "border-status-completed/50 bg-status-completed/10 text-status-completed",
    executing: "border-status-executing/50 bg-status-executing/10 text-status-executing",
    awaiting: "border-status-awaiting/50 bg-status-awaiting/10 text-status-awaiting",
    blocked: "border-status-blocked/50 bg-status-blocked/10 text-status-blocked",
    idle: "border-line text-ink-faint",
  };

  return (
    <div className="space-y-4">
      {list.map((run) => {
        const progress = workflowProgress(run);
        return (
          <div key={run.id} className="rounded-xl border border-line bg-canvas-overlay/40 p-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-ink">{run.id} · {run.taskId}</span>
              <span className="text-[11px] text-ink-faint">{run.mode} · {progress.completed}/{progress.total}</span>
            </div>
            <ol className="mt-3 space-y-1.5">
              {run.stages.map((s) => (
                <li key={s.id} className="flex items-center gap-2">
                  <span className={`shrink-0 rounded border px-2 py-0.5 text-[10px] ${toneClass[stageTone(s)]}`}>{s.stage}</span>
                  <span className="min-w-0 flex-1 truncate text-[12px] text-ink-muted">{s.summary ?? "—"}</span>
                  <span className="shrink-0 text-[10px] text-ink-faint">{s.provider ? `${s.model}` : ""} {elapsedLabel(s.elapsedMs)}</span>
                </li>
              ))}
            </ol>
            {progress.status === "awaiting" && (
              <div className="mt-2 text-[11px] text-status-awaiting">Final state: awaiting approval — no commits or pushes performed.</div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---- Integrations ---------------------------------------------------------

function Integrations() {
  const { adapter } = useApp();
  const prs = useAsync(() => adapter!.listPullRequests(), [adapter], !!adapter);
  const pub = useAsync(() => adapter!.getPublishHistory(), [adapter], !!adapter);
  return (
    <div className="space-y-5">
      <div>
        <div className="mb-2 text-[11px] uppercase tracking-wider text-ink-faint">Pull requests</div>
        {prs.loading ? <Loading /> : prs.error ? <Failed message={prs.error.message} /> : (
          <ul className="space-y-2">
            {(prs.data ?? []).map((p) => (
              <li key={p.number} className="flex items-center gap-2 rounded-lg border border-line bg-canvas-overlay/30 p-2.5 text-sm">
                <span className={`rounded border px-1.5 py-0.5 text-[10px] ${p.state === "open" ? "border-status-completed/40 text-status-completed" : "border-line text-ink-faint"}`}>{p.state}</span>
                <span className="min-w-0 truncate text-ink-muted">#{p.number} {p.title}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div>
        <div className="mb-2 text-[11px] uppercase tracking-wider text-ink-faint">Publish history</div>
        {pub.loading ? <Loading /> : pub.error ? <Failed message={pub.error.message} /> : (
          <ul className="space-y-2">
            {(pub.data ?? []).map((r) => (
              <li key={r.id} className="flex items-center justify-between rounded-lg border border-line bg-canvas-overlay/30 p-2.5 text-sm">
                <span className="text-ink-muted">{r.docId} → {r.target}</span>
                <span className="text-[11px] text-ink-faint">{r.status} · {r.at}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
