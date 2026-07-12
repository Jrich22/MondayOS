/**
 * MondayOS adapter contracts.
 *
 * This is the ONLY seam between the dashboard (visual client) and MondayOS (the
 * system of record + execution engine). The dashboard never talks to MondayOS
 * any other way — no shell calls from components, no duplicated task/agent/
 * approval logic. A real HTTP-backed adapter and the typed demo adapter both
 * implement `MondayAdapter`, so swapping one for the other changes nothing above
 * this layer.
 *
 * Every operation returns a structured `ActionResult` (never throws across the
 * boundary), carries the `mode` it ran in (live vs demo), and write operations
 * are logged. Phase 1 wires the READ surface end-to-end; the WRITE surface is
 * declared here (so callers and tests can depend on it) but is executed in
 * Phase 2 — the demo adapter returns a typed `not-implemented` result for
 * writes rather than mutating anything.
 */

// ---- Domain types (mirror the `monday` Python surface) --------------------

export type DataMode = "live" | "demo";

export interface SystemStatus {
  version: string;
  healthy: boolean;
  sessionId: string;
  uptimeSeconds: number;
  provider: string;
  model: string;
}

export type ProductStatus = "operational" | "building" | "attention";

export interface Product {
  key: string;
  name: string;
  summary: string;
  status: ProductStatus;
  openTasks: number;
  /** Sprint / progress headline shown in the product workspace. */
  sprint?: { name: string; done: number; total: number };
  /** Freeform metric tiles specific to the product. */
  metrics?: { label: string; value: string; tone?: "good" | "warn" | "bad" }[];
  /** Monday's single next recommendation for this product. */
  recommendation?: string;
}

export type TaskStatus = "active" | "blocked" | "review" | "completed";

export interface Task {
  id: string;
  title: string;
  status: TaskStatus;
  product?: string;
  agent?: string;
  objective?: string;
  blockedReason?: string;
  dependencies?: string[];
}

export type AgentActivity =
  | "idle"
  | "thinking"
  | "executing"
  | "awaiting"
  | "blocked"
  | "learning";

export interface Agent {
  id: string;
  name: string;
  role: string;
  activity: AgentActivity;
  task?: string;
}

export type RunStatus = "pending" | "running" | "completed" | "blocked" | "awaiting";

/** One stage in a team workflow (CPO → Lead Eng → QA → Security → Reviewer). */
export interface AgentRun {
  id: string;
  teamRunId: string;
  stage: string;
  agent: string;
  status: RunStatus;
  provider?: string;
  model?: string;
  summary?: string;
  verdict?: "pass" | "fail" | "concerns";
  elapsedMs?: number;
}

export interface TeamRun {
  id: string;
  taskId: string;
  mode: string;
  status: RunStatus;
  startedAt: string;
  stages: AgentRun[];
}

export type ApprovalStatus = "open" | "approved" | "rejected";

export interface Approval {
  id: string;
  taskId: string;
  teamRunId: string;
  summary: string;
  status: ApprovalStatus;
  verdicts: { role: string; verdict: "pass" | "fail" | "concerns"; note?: string }[];
  affected: string[];
}

export type KnowledgeKind = "doc" | "decision" | "sprint" | "research";

export interface KnowledgeItem {
  id: string;
  kind: KnowledgeKind;
  title: string;
  product?: string;
  summary?: string;
}

export interface ActivityEvent {
  id: string;
  at: string;
  agent: string;
  message: string;
  kind: "idle" | "thinking" | "executing" | "awaiting" | "blocked" | "completed" | "learning";
}

export interface PublishRecord {
  id: string;
  docId: string;
  target: string;
  at: string;
  status: "published" | "pending" | "failed";
}

export interface PullRequest {
  number: number;
  title: string;
  state: "open" | "merged" | "closed";
  branch: string;
  product?: string;
}

// ---- Result envelope ------------------------------------------------------

export interface ActionOk<T> {
  ok: true;
  mode: DataMode;
  data: T;
}

export interface ActionErr {
  ok: false;
  mode: DataMode;
  error: { code: string; message: string };
}

export type ActionResult<T> = ActionOk<T> | ActionErr;

// ---- Filters / write inputs ----------------------------------------------

export interface TaskFilter {
  status?: TaskStatus;
  product?: string;
}

export interface CreateTaskInput {
  title: string;
  objective: string;
  product?: string;
}

export interface RunTeamInput {
  taskId: string;
  mode?: string;
}

// ---- The adapter interface ------------------------------------------------

export interface MondayAdapter {
  /** Which data source this adapter represents. */
  readonly mode: DataMode;

  // Read surface (Phase 1: fully wired)
  getSystemStatus(): Promise<ActionResult<SystemStatus>>;
  listProducts(): Promise<ActionResult<Product[]>>;
  getProduct(key: string): Promise<ActionResult<Product>>;
  listTasks(filter?: TaskFilter): Promise<ActionResult<Task[]>>;
  getTask(id: string): Promise<ActionResult<Task>>;
  listAgents(): Promise<ActionResult<Agent[]>>;
  listTeamRuns(): Promise<ActionResult<TeamRun[]>>;
  listAgentRuns(teamRunId?: string): Promise<ActionResult<AgentRun[]>>;
  listApprovals(): Promise<ActionResult<Approval[]>>;
  searchKnowledge(query: string): Promise<ActionResult<KnowledgeItem[]>>;
  getPublishHistory(): Promise<ActionResult<PublishRecord[]>>;
  getRecentActivity(): Promise<ActionResult<ActivityEvent[]>>;
  listPullRequests(): Promise<ActionResult<PullRequest[]>>;

  // Write surface (declared now; executed in Phase 2)
  createTask(input: CreateTaskInput): Promise<ActionResult<Task>>;
  assignTask(id: string, agent: string): Promise<ActionResult<Task>>;
  runTeam(input: RunTeamInput): Promise<ActionResult<TeamRun>>;
  approveRun(id: string): Promise<ActionResult<Approval>>;
  rejectRun(id: string, reason: string): Promise<ActionResult<Approval>>;
  publishDocument(docId: string, target: string): Promise<ActionResult<PublishRecord>>;
  updateTaskStatus(id: string, status: TaskStatus): Promise<ActionResult<Task>>;
}
