/**
 * MondayOS runtime model for Mission Control.
 *
 * This is the *operating system's* view of itself — agents, tasks, managed
 * products, knowledge, and overall health. It is deliberately decoupled from
 * any single product (Cue is just one row in `PRODUCTS`). Today it is mock data
 * shaped to mirror the `monday` Python surface (status / agents / tasks /
 * projects / knowledge); when a MondayOS web API lands, only this module is
 * swapped — the components read exclusively from here.
 */

import type { BrainState } from "@/components/monday";

export interface SystemStatus {
  version: string;
  healthy: boolean;
  sessionId: string;
  uptimeSeconds: number;
}

export type AgentActivity =
  | "idle"
  | "thinking"
  | "executing"
  | "awaiting"
  | "blocked"
  | "learning";

export interface AgentInfo {
  id: string;
  name: string;
  role: string;
  activity: AgentActivity;
  task?: string;
}

export type TaskStatus = "active" | "blocked" | "review" | "completed";

export interface TaskInfo {
  id: string;
  title: string;
  status: TaskStatus;
  agent?: string;
}

export type ProductStatus = "operational" | "building" | "attention";

export interface ProductInfo {
  key: string;
  name: string;
  summary: string;
  status: ProductStatus;
  openTasks: number;
}

export interface KnowledgeStats {
  documents: number;
  decisions: number;
  sprints: number;
  indexedToday: number;
}

export interface ActivityEvent {
  id: string;
  at: string;
  agent: string;
  message: string;
  kind: BrainState;
}

export interface OSState {
  system: SystemStatus;
  agents: AgentInfo[];
  tasks: TaskInfo[];
  products: ProductInfo[];
  knowledge: KnowledgeStats;
  activity: ActivityEvent[];
}

/** A snapshot of MondayOS. Deterministic — no clocks/random at module load. */
export const OS_STATE: OSState = {
  system: {
    version: "0.1.0",
    healthy: true,
    sessionId: "a3f91c",
    uptimeSeconds: 42_930,
  },
  agents: [
    { id: "orchestrator", name: "Orchestrator", role: "Plans & routes work", activity: "thinking", task: "TASK-0031" },
    { id: "advisor", name: "Advisor", role: "Strategy & review", activity: "idle" },
    { id: "doctor", name: "Doctor", role: "Health & diagnostics", activity: "executing", task: "System sweep" },
    { id: "migrate", name: "Migrate", role: "Schema & data moves", activity: "idle" },
    { id: "brain", name: "Brain", role: "Knowledge & recall", activity: "learning", task: "Indexing DOC-0014" },
    { id: "search", name: "Search", role: "Retrieval", activity: "idle" },
    { id: "events", name: "Events", role: "Event bus", activity: "executing", task: "Dispatching" },
    { id: "publish", name: "Publish", role: "Confluence sync", activity: "awaiting", task: "DEC-0007 approval" },
  ],
  tasks: [
    { id: "TASK-0031", title: "Wire agent telemetry into Mission Control", status: "active", agent: "Orchestrator" },
    { id: "TASK-0030", title: "MondayOS dashboard shell", status: "review", agent: "Advisor" },
    { id: "TASK-0028", title: "Knowledge auto-indexing pipeline", status: "active", agent: "Brain" },
    { id: "TASK-0025", title: "Confluence publish mapping", status: "blocked", agent: "Publish" },
    { id: "TASK-0022", title: "Doctor: dependency graph audit", status: "active", agent: "Doctor" },
    { id: "TASK-0041", title: "Cue product onboarding report", status: "completed" },
    { id: "TASK-0037", title: "Agent team runner", status: "completed" },
  ],
  products: [
    {
      key: "cue",
      name: "Cue",
      summary: "AI-native event operations for VC firms",
      status: "operational",
      openTasks: 6,
    },
    {
      key: "weatherbot",
      name: "Weatherbot",
      summary: "Forecast digest & alerting agent",
      status: "building",
      openTasks: 2,
    },
  ],
  knowledge: {
    documents: 14,
    decisions: 7,
    sprints: 3,
    indexedToday: 5,
  },
  activity: [
    { id: "a1", at: "just now", agent: "Doctor", message: "Ran system sweep — 0 critical findings", kind: "executing" },
    { id: "a2", at: "1m", agent: "Brain", message: "Learning from DOC-0014 (research)", kind: "learning" },
    { id: "a3", at: "3m", agent: "Publish", message: "Awaiting approval to publish DEC-0007", kind: "awaiting" },
    { id: "a4", at: "6m", agent: "Orchestrator", message: "Planned TASK-0031 across 3 agents", kind: "thinking" },
    { id: "a5", at: "9m", agent: "Publish", message: "Blocked: missing Confluence mapping for TASK-0025", kind: "blocked" },
    { id: "a6", at: "12m", agent: "Events", message: "Completed onboarding report for Cue", kind: "completed" },
  ],
};

/** Count tasks by status — used across the header + tasks panel. */
export function taskCounts(state: OSState) {
  const c = { active: 0, blocked: 0, review: 0, completed: 0 } as Record<TaskStatus, number>;
  for (const t of state.tasks) c[t.status]++;
  return c;
}

/**
 * Derive the single dominant brain state from live OS activity. Priority order
 * reflects what most demands the operator's attention: a block outranks a
 * pending approval, which outranks active execution, and so on. This is what
 * makes Monday's Brain *reflect the OS* rather than a manual toggle.
 */
export function deriveBrainState(state: OSState): BrainState {
  const acts = state.agents.map((a) => a.activity);
  if (acts.includes("blocked")) return "blocked";
  if (acts.includes("awaiting")) return "awaiting";
  if (acts.includes("executing")) return "executing";
  if (acts.includes("learning")) return "learning";
  if (acts.includes("thinking")) return "thinking";
  return "idle";
}
