/**
 * Real adapter — talks to the MondayOS dashboard API (dashboard_api/, a
 * localhost HTTP bridge) over the SAME `MondayAdapter` interface the demo
 * adapter implements. MondayOS stays the system of record and execution engine;
 * this adapter is a thin, typed transport that forwards to it. No task/agent/
 * approval logic lives here.
 *
 * Transport policy:
 *   - every request has a timeout;
 *   - safe READS (GET) retry with backoff on transient failure;
 *   - WRITES (POST) never retry — a write is attempted at most once, and there
 *     is no silent fallback to demo once a write has begun;
 *   - non-2xx bodies are parsed as the API's `{error:{code,message}}` envelope;
 *   - every request reports health via `onHealth` so the client can show a
 *     LIVE / DEGRADED indicator.
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

const MODE = "live" as const;

export interface RealAdapterConfig {
  baseUrl: string;
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
  /** Read-retry attempts (writes never retry). Default 2. */
  readRetries?: number;
  /** Reports transport health so the UI can show LIVE / DEGRADED. */
  onHealth?: (ok: boolean) => void;
}

const errResult = (code: string, message: string): ActionResult<never> => ({
  ok: false,
  mode: MODE,
  error: { code, message },
});

async function fetchWithTimeout(
  f: typeof fetch,
  url: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<Response> {
  const ctrl = typeof AbortController !== "undefined" ? new AbortController() : undefined;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : undefined;
  try {
    return await f(url, { ...init, signal: ctrl?.signal });
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function parseError(res: Response): Promise<{ code: string; message: string }> {
  try {
    const body = await res.json();
    if (body?.error?.code) return { code: body.error.code, message: body.error.message ?? "" };
  } catch {
    /* non-JSON body */
  }
  return { code: "http-error", message: `MondayOS API returned ${res.status}` };
}

/**
 * Is a MondayOS API reachable at `baseUrl`? Used by `selectAdapter` to choose
 * live vs demo. Any failure → unavailable.
 */
export async function probe(cfg: RealAdapterConfig, timeoutMs = 900): Promise<boolean> {
  const f = cfg.fetchImpl ?? (typeof fetch !== "undefined" ? fetch : undefined);
  if (!f) return false;
  try {
    const res = await fetchWithTimeout(f, `${cfg.baseUrl}/health`, {}, timeoutMs);
    return res.ok;
  } catch {
    return false;
  }
}

export function createRealAdapter(cfg: RealAdapterConfig): MondayAdapter {
  const f = cfg.fetchImpl ?? fetch;
  const timeoutMs = cfg.timeoutMs ?? 8000;
  const readRetries = cfg.readRetries ?? 2;
  const health = (ok: boolean) => cfg.onHealth?.(ok);

  async function get<T>(path: string): Promise<ActionResult<T>> {
    let lastErr = { code: "network-error", message: "request failed" };
    for (let attempt = 0; attempt <= readRetries; attempt++) {
      try {
        const res = await fetchWithTimeout(f, `${cfg.baseUrl}${path}`, {}, timeoutMs);
        if (res.ok) {
          health(true);
          return { ok: true, mode: MODE, data: (await res.json()) as T };
        }
        // 4xx are deterministic — don't retry; 5xx are transient — do.
        lastErr = await parseError(res);
        if (res.status < 500) {
          health(true); // server answered; connection is fine
          return { ok: false, mode: MODE, error: lastErr };
        }
      } catch (e) {
        lastErr = { code: "network-error", message: e instanceof Error ? e.message : "request failed" };
      }
      if (attempt < readRetries) await new Promise((r) => setTimeout(r, 150 * (attempt + 1)));
    }
    health(false);
    return { ok: false, mode: MODE, error: lastErr };
  }

  async function post<T>(path: string, body: unknown): Promise<ActionResult<T>> {
    // Writes never retry — attempted at most once, no silent fallback.
    try {
      const res = await fetchWithTimeout(
        f,
        `${cfg.baseUrl}${path}`,
        { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) },
        timeoutMs,
      );
      if (!res.ok) {
        health(res.status < 500);
        return { ok: false, mode: MODE, error: await parseError(res) };
      }
      health(true);
      return { ok: true, mode: MODE, data: (await res.json()) as T };
    } catch (e) {
      health(false);
      return errResult("network-error", e instanceof Error ? e.message : "request failed");
    }
  }

  const q = (params: Record<string, string | undefined>) => {
    const s = Object.entries(params)
      .filter(([, v]) => v != null && v !== "")
      .map(([k, v]) => `${k}=${encodeURIComponent(v as string)}`)
      .join("&");
    return s ? `?${s}` : "";
  };

  return {
    mode: MODE,
    getSystemStatus: () => get<SystemStatus>("/status"),
    listProducts: () => get<Product[]>("/products"),
    getProduct: async (key) => {
      const r = await get<Product[]>("/products");
      if (!r.ok) return r;
      const p = r.data.find((x) => x.key === key);
      return p ? { ok: true as const, mode: MODE, data: p } : errResult("not-found", `No product ${key}.`);
    },
    listTasks: (filter?: TaskFilter) => get<Task[]>(`/tasks${q({ status: filter?.status, product: filter?.product })}`),
    getTask: (id) => get<Task>(`/tasks/${encodeURIComponent(id)}`),
    listAgents: () => get<Agent[]>("/agents").then((r) => (r.ok ? r : { ok: true as const, mode: MODE, data: [] })),
    listTeamRuns: () => get<TeamRun[]>("/team-runs"),
    listAgentRuns: (teamRunId?: string) => get<AgentRun[]>(`/agent-runs${q({ teamRunId })}`),
    listApprovals: () => get<Approval[]>("/approvals"),
    searchKnowledge: (query) => get<KnowledgeItem[]>(`/knowledge/search${q({ query })}`),
    getPublishHistory: () => get<PublishRecord[]>("/publish/history"),
    getRecentActivity: () => get<ActivityEvent[]>("/activity"),
    listPullRequests: () => get<PullRequest[]>("/pull-requests"),

    createTask: (input: CreateTaskInput) => post<Task>("/tasks", input),
    assignTask: (id, assignee) => post<Task>(`/tasks/${encodeURIComponent(id)}/assign`, { assignee }),
    runTeam: (input: RunTeamInput) => post<TeamRun>(`/tasks/${encodeURIComponent(input.taskId)}/team-run`, { mode: input.mode ?? "review" }),
    approveRun: (id) => post<Approval>(`/agent-runs/${encodeURIComponent(id)}/approve`, {}),
    rejectRun: (id, reason) => post<Approval>(`/agent-runs/${encodeURIComponent(id)}/reject`, { reason }),
    publishDocument: () => Promise.resolve(errResult("gated", "Publishing is gated and not available from the dashboard.")),
    updateTaskStatus: (id, status: TaskStatus) => post<Task>(`/tasks/${encodeURIComponent(id)}/status`, { status }),
  };
}
