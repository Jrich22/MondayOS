import { useApp } from "@/state/store";
import { useAsync } from "@/common/useAsync";
import type { Section } from "@/command/intents";

/**
 * Interactive orbital navigation nodes ringing Monday's Brain. These are the
 * abstract "neural regions" — Products, Tasks, Agents, Memory, Knowledge,
 * Approvals, Workflows, Integrations — NOT a literal human-brain anatomy. Hover
 * reveals a label + live count/status; click opens that workspace while the
 * Brain stays centered as the persistent anchor.
 *
 * Rendered as DOM buttons (not in-canvas meshes) so navigation is fully
 * keyboard-accessible and screen-reader friendly; they're positioned on a ring
 * in percentage units so the constellation scales with the stage.
 */

interface NodeDef {
  section: Section;
  label: string;
  dot: string; // tailwind bg-* token
  count: (ctx: Counts) => number | undefined;
  status: (ctx: Counts) => string;
}

interface Counts {
  products: number;
  tasksActive: number;
  agentsBusy: number;
  approvalsOpen: number;
  knowledge?: number;
  workflows?: number;
  prsOpen?: number;
}

const NODES: NodeDef[] = [
  { section: "products", label: "Products", dot: "bg-status-completed", count: (c) => c.products, status: () => "portfolio" },
  { section: "tasks", label: "Tasks", dot: "bg-status-executing", count: (c) => c.tasksActive, status: () => "active" },
  { section: "agents", label: "Agents", dot: "bg-accent-violet", count: (c) => c.agentsBusy, status: () => "working" },
  { section: "workflows", label: "Workflows", dot: "bg-accent-cyan", count: (c) => c.workflows, status: () => "runs" },
  { section: "approvals", label: "Approvals", dot: "bg-status-awaiting", count: (c) => c.approvalsOpen, status: () => "awaiting" },
  { section: "memory", label: "Memory", dot: "bg-accent-magenta", count: (c) => c.knowledge, status: () => "recall" },
  { section: "knowledge", label: "Knowledge", dot: "bg-brand-400", count: (c) => c.knowledge, status: () => "docs" },
  { section: "integrations", label: "Integrations", dot: "bg-status-idle", count: (c) => c.prsOpen, status: () => "linked" },
];

export function OrbitalNodes() {
  const { state, navigate, adapter } = useApp();

  // Counts already in the store snapshot.
  const base: Counts = {
    products: state.products.length,
    tasksActive: state.tasks.filter((t) => t.status === "active").length,
    agentsBusy: state.agents.filter((a) => a.activity !== "idle").length,
    approvalsOpen: state.approvals.filter((a) => a.status === "open").length,
  };

  // Extra counts fetched lazily (loading → no badge, never a wrong number).
  const knowledge = useAsync(() => adapter!.searchKnowledge(""), [adapter], !!adapter);
  const runs = useAsync(() => adapter!.listTeamRuns(), [adapter], !!adapter);
  const prs = useAsync(() => adapter!.listPullRequests(), [adapter], !!adapter);

  const counts: Counts = {
    ...base,
    knowledge: knowledge.data?.length,
    workflows: runs.data?.filter((r) => r.status === "running" || r.status === "awaiting").length,
    prsOpen: prs.data?.filter((p) => p.state === "open").length,
  };

  return (
    <div className="pointer-events-none absolute inset-0" aria-label="Brain navigation">
      {NODES.map((node, i) => {
        // Offset by half a step so no node sits at exact top-center (the state
        // readout) or bottom-center (the command affordance).
        const step = 360 / NODES.length;
        const angle = (-90 + step / 2 + i * step) * (Math.PI / 180);
        const left = 50 + 45 * Math.cos(angle);
        const top = 50 + 45 * Math.sin(angle);
        const count = node.count(counts);
        const active = state.section === node.section;
        return (
          <div
            key={node.section}
            className="pointer-events-auto absolute"
            style={{ left: `${left}%`, top: `${top}%`, transform: "translate(-50%, -50%)" }}
          >
            <button
              onClick={() => navigate(node.section)}
              aria-label={`${node.label}${count != null ? ` — ${count} ${node.status(counts)}` : ""}`}
              title={`${node.label} · ${node.status(counts)}`}
              className={`group flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs backdrop-blur-sm transition-all ${
                active
                  ? "border-brand-400 bg-brand-400/20 text-ink shadow-glow"
                  : "border-line bg-canvas-raised/70 text-ink-muted hover:scale-105 hover:border-line-strong hover:text-ink"
              }`}
            >
              <span className={`h-1.5 w-1.5 rounded-full ${node.dot}`} />
              <span className="font-medium">{node.label}</span>
              {count != null && (
                <span className="rounded-full bg-canvas-overlay px-1.5 text-[10px] text-ink-muted group-hover:text-ink">
                  {count}
                </span>
              )}
            </button>
          </div>
        );
      })}
    </div>
  );
}
