/**
 * AI Workspace transport.
 *
 * A thin typed client over the `dashboard_api` `/workspace/*` routes, following
 * the same transport policy as `realAdapter`: every request has a timeout, reads
 * may retry, writes never do, and non-2xx bodies are parsed as the API's
 * `{error:{code,message}}` envelope.
 *
 * Writes not retrying matters more here than elsewhere. A retried send-message
 * would post the same user turn twice, and the second copy would be
 * indistinguishable from the operator having said it again.
 *
 * No conversation, context or isolation logic lives here. The project is passed
 * on every call because MondayOS scopes by project, and this client has no
 * opinion about which one — it cannot, by design.
 */

import type {
  Conversation,
  ConversationSummary,
  ContextSnapshot,
  KnowledgeCapture,
  Result,
  SendResult,
  WorkspaceProject,
} from "./types";

const DEFAULT_TIMEOUT_MS = 60_000;
const READ_RETRIES = 2;

export interface WorkspaceClientConfig {
  baseUrl: string;
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
}

function fail(code: string, message: string): { ok: false; error: { code: string; message: string } } {
  return { ok: false, error: { code, message } };
}

async function withTimeout(
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
    const body = (await res.json()) as { error?: { code?: string; message?: string } };
    if (body?.error?.code) {
      return { code: body.error.code, message: body.error.message ?? "" };
    }
  } catch {
    // Body was not the error envelope; fall through to the status-based shape.
  }
  return { code: "http-error", message: `MondayOS API returned ${res.status}` };
}

export interface WorkspaceClient {
  listProjects(): Promise<Result<WorkspaceProject[]>>;
  listConversations(project: string): Promise<Result<ConversationSummary[]>>;
  getConversation(project: string, id: string): Promise<Result<Conversation>>;
  createConversation(project: string, title?: string): Promise<Result<Conversation>>;
  sendMessage(project: string, id: string, content: string): Promise<Result<SendResult>>;
  retry(project: string, id: string): Promise<Result<SendResult>>;
  rename(project: string, id: string, title: string): Promise<Result<Conversation>>;
  archive(project: string, id: string): Promise<Result<Conversation>>;
  unarchive(project: string, id: string): Promise<Result<Conversation>>;
  getContext(project: string): Promise<Result<ContextSnapshot>>;
  saveToKnowledge(
    project: string,
    id: string,
    messageId: string,
  ): Promise<Result<KnowledgeCapture>>;
}

export function createWorkspaceClient(cfg: WorkspaceClientConfig): WorkspaceClient {
  const f = cfg.fetchImpl ?? fetch;
  const timeoutMs = cfg.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const base = cfg.baseUrl.replace(/\/+$/, "");

  async function get<T>(path: string): Promise<Result<T>> {
    let last = { code: "unreachable", message: "MondayOS API is unreachable." };
    for (let attempt = 0; attempt <= READ_RETRIES; attempt++) {
      try {
        const res = await withTimeout(f, `${base}${path}`, { method: "GET" }, timeoutMs);
        if (res.ok) return { ok: true, data: (await res.json()) as T };
        last = await parseError(res);
        // 4xx is a real answer, not a transient failure — retrying cannot help.
        if (res.status < 500) return { ok: false, error: last };
      } catch (err) {
        last = { code: "network", message: err instanceof Error ? err.message : String(err) };
      }
    }
    return { ok: false, error: last };
  }

  async function post<T>(path: string, body: unknown): Promise<Result<T>> {
    try {
      const res = await withTimeout(
        f,
        `${base}${path}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
        timeoutMs,
      );
      if (!res.ok) return { ok: false, error: await parseError(res) };
      return { ok: true, data: (await res.json()) as T };
    } catch (err) {
      return fail("network", err instanceof Error ? err.message : String(err));
    }
  }

  const q = (project: string) => `project=${encodeURIComponent(project)}`;

  return {
    listProjects: () => get<WorkspaceProject[]>("/workspace/projects"),

    listConversations: (project) =>
      get<ConversationSummary[]>(`/workspace/conversations?${q(project)}`),

    getConversation: (project, id) =>
      get<Conversation>(`/workspace/conversations/${encodeURIComponent(id)}?${q(project)}`),

    createConversation: (project, title = "") =>
      post<Conversation>("/workspace/conversations", { project, title }),

    sendMessage: (project, id, content) =>
      post<SendResult>(`/workspace/conversations/${encodeURIComponent(id)}/messages`, {
        project,
        content,
      }),

    retry: (project, id) =>
      post<SendResult>(`/workspace/conversations/${encodeURIComponent(id)}/retry`, { project }),

    rename: (project, id, title) =>
      post<Conversation>(`/workspace/conversations/${encodeURIComponent(id)}/update`, {
        project,
        title,
      }),

    archive: (project, id) =>
      post<Conversation>(`/workspace/conversations/${encodeURIComponent(id)}/update`, {
        project,
        status: "archived",
      }),

    unarchive: (project, id) =>
      post<Conversation>(`/workspace/conversations/${encodeURIComponent(id)}/update`, {
        project,
        status: "active",
      }),

    getContext: (project) =>
      get<ContextSnapshot>(`/workspace/context/${encodeURIComponent(project)}`),

    saveToKnowledge: (project, id, messageId) =>
      post<KnowledgeCapture>(`/workspace/conversations/${encodeURIComponent(id)}/knowledge`, {
        project,
        message_id: messageId,
      }),
  };
}
