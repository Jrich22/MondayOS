/**
 * Demo adapter — a full, typed implementation of `MondayAdapter` backed by the
 * static demo dataset. It exists so the entire dashboard runs and is testable
 * before a MondayOS web API exists. It shares the real adapter's interface
 * exactly, so nothing above this boundary knows or cares which is active.
 *
 * Reads resolve against `demo-data`. Writes are NOT executed in Phase 1: they
 * return a structured `not-implemented` error (and are logged) rather than
 * pretending to mutate the system of record. Phase 2 replaces those bodies.
 */

import type {
  ActionResult,
  Agent,
  Approval,
  ActivityEvent,
  CreateTaskInput,
  KnowledgeItem,
  MondayAdapter,
  Product,
  PublishRecord,
  PullRequest,
  RunTeamInput,
  SystemStatus,
  Task,
  TaskFilter,
  TaskStatus,
  TeamRun,
  AgentRun,
} from "./types";
import * as demo from "./demo-data";
import { logAction } from "./log";

const MODE = "demo" as const;

const ok = <T>(data: T): ActionResult<T> => ({ ok: true, mode: MODE, data });
const err = (code: string, message: string): ActionResult<never> => ({
  ok: false,
  mode: MODE,
  error: { code, message },
});

/** Phase-2 stub: log the attempt and return a typed not-implemented result. */
function pending<T>(op: string, args: unknown): ActionResult<T> {
  logAction({ mode: MODE, op, args, ok: false, message: "not-implemented (Phase 2)" });
  return err(
    "not-implemented",
    "Write actions are wired in Phase 2. In demo mode nothing is mutated.",
  ) as ActionResult<T>;
}

export function createDemoAdapter(): MondayAdapter {
  return {
    mode: MODE,

    async getSystemStatus(): Promise<ActionResult<SystemStatus>> {
      return ok(demo.SYSTEM);
    },
    async listProducts(): Promise<ActionResult<Product[]>> {
      return ok(demo.PRODUCTS);
    },
    async getProduct(key: string): Promise<ActionResult<Product>> {
      const p = demo.PRODUCTS.find((x) => x.key === key);
      return p ? ok(p) : err("not-found", `No product "${key}".`);
    },
    async listTasks(filter?: TaskFilter): Promise<ActionResult<Task[]>> {
      let tasks = demo.TASKS;
      if (filter?.status) tasks = tasks.filter((t) => t.status === filter.status);
      if (filter?.product) tasks = tasks.filter((t) => t.product === filter.product);
      return ok(tasks);
    },
    async getTask(id: string): Promise<ActionResult<Task>> {
      const t = demo.TASKS.find((x) => x.id.toLowerCase() === id.toLowerCase());
      return t ? ok(t) : err("not-found", `No task "${id}".`);
    },
    async listAgents(): Promise<ActionResult<Agent[]>> {
      return ok(demo.AGENTS);
    },
    async listTeamRuns(): Promise<ActionResult<TeamRun[]>> {
      return ok(demo.TEAM_RUNS);
    },
    async listAgentRuns(teamRunId?: string): Promise<ActionResult<AgentRun[]>> {
      const runs = demo.TEAM_RUNS.flatMap((tr) => tr.stages);
      return ok(teamRunId ? runs.filter((r) => r.teamRunId === teamRunId) : runs);
    },
    async listApprovals(): Promise<ActionResult<Approval[]>> {
      return ok(demo.APPROVALS);
    },
    async searchKnowledge(query: string): Promise<ActionResult<KnowledgeItem[]>> {
      const q = query.trim().toLowerCase();
      if (!q) return ok(demo.KNOWLEDGE);
      const hits = demo.KNOWLEDGE.filter(
        (k) =>
          k.title.toLowerCase().includes(q) ||
          k.summary?.toLowerCase().includes(q) ||
          k.product?.toLowerCase().includes(q) ||
          k.id.toLowerCase().includes(q),
      );
      return ok(hits);
    },
    async getPublishHistory(): Promise<ActionResult<PublishRecord[]>> {
      return ok(demo.PUBLISH_HISTORY);
    },
    async getRecentActivity(): Promise<ActionResult<ActivityEvent[]>> {
      return ok(demo.ACTIVITY);
    },
    async listPullRequests(): Promise<ActionResult<PullRequest[]>> {
      return ok(demo.PULL_REQUESTS);
    },

    // ---- Write surface (Phase 2) ----
    async createTask(input: CreateTaskInput) {
      return pending<Task>("createTask", input);
    },
    async assignTask(id: string, agent: string) {
      return pending<Task>("assignTask", { id, agent });
    },
    async runTeam(input: RunTeamInput) {
      return pending<TeamRun>("runTeam", input);
    },
    async approveRun(id: string) {
      return pending<Approval>("approveRun", { id });
    },
    async rejectRun(id: string, reason: string) {
      return pending<Approval>("rejectRun", { id, reason });
    },
    async publishDocument(docId: string, target: string) {
      return pending<PublishRecord>("publishDocument", { docId, target });
    },
    async updateTaskStatus(id: string, status: TaskStatus) {
      return pending<Task>("updateTaskStatus", { id, status });
    },
  };
}
