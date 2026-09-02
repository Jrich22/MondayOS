/**
 * AI Workspace client types.
 *
 * These mirror the shapes `dashboard_api` returns for `/workspace/*`, which are
 * produced by `Monday.workspace()`. MondayOS is the system of record; nothing
 * here reimplements conversation, context or isolation logic.
 *
 * Deliberately separate from `adapter/types.ts`: that interface describes the
 * product/task/agent surface Mission Control reads, and the demo adapter has to
 * satisfy all of it. Conversations have no demo shape worth faking, so folding
 * them in would mean nine invented methods returning nothing.
 */

export type MessageRole = "user" | "assistant" | "event";
export type ConversationStatus = "active" | "archived";

export interface WorkspaceProject {
  name: string;
  display_name: string;
  description: string;
  path: string;
  conversation_count: number;
}

export interface Message {
  id: string;
  role: MessageRole;
  content: string;
  created_at: string;
  provider: string;
  model: string;
  snapshot_id: string;
  tokens_used: number;
  /** Set when generation failed. The turn is kept so retry means something. */
  error: string;
}

export interface ConversationSummary {
  id: string;
  project: string;
  title: string;
  status: ConversationStatus;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface Conversation extends ConversationSummary {
  active_snapshot_id: string;
  messages: Message[];
  task_refs: string[];
}

export interface ContextSource {
  name: string;
  label: string;
  items: string[];
  item_count: number;
  char_count: number;
  token_estimate: number;
  truncated: boolean;
  /** Non-empty when the adapter failed. Fail-closed: thin context, never wide. */
  error: string;
  /** Where this came from — the "why did Monday know this?" answer. */
  origin: string;
  ok: boolean;
}

export interface ContextSnapshot {
  id: string;
  project: string;
  created_at: string;
  sources: ContextSource[];
  omitted: string[];
  token_estimate: number;
  char_count: number;
  truncated: boolean;
  summary: string;
}

export interface SendResult {
  conversation: Conversation;
  user_message: Message;
  assistant_message: Message;
  context: ContextSnapshot | null;
}

export interface KnowledgeCapture {
  knowledge_id: string;
  title: string;
  project: string;
  conversation_id: string;
  message_id: string;
}

export interface ApiError {
  code: string;
  message: string;
}

export type Result<T> = { ok: true; data: T } | { ok: false; error: ApiError };
