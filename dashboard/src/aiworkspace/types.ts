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
  /** Stopped before the model finished. Partial text, never shown as complete. */
  incomplete: boolean;
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
  /** Why each item survived ranking, parallel to `items`. */
  reasons: string[];
  /** Aggregated reason counts, for the panel's summary line. */
  reason_counts: Record<string, number>;
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
  /** Digest of what this was built from. Reuse compares fingerprints. */
  fingerprint: string;
  /** The request this was ranked for, when it was ranked for one. */
  query: string;
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

/** A frame from the streaming endpoint. Mirrors the server's event vocabulary. */
export type StreamEvent =
  | { type: "user"; message: Message }
  | { type: "context"; context: ContextSnapshot }
  | { type: "delta"; text: string }
  | { type: "done"; message: Message; conversation: Conversation }
  | { type: "error"; code: string; message: string };

export interface SearchHit {
  conversation_id: string;
  project: string;
  title: string;
  updated_at: string;
  matched_title: boolean;
  snippets: string[];
  message_count: number;
  score: number;
}

export interface SearchResult {
  query: string;
  scope: "project" | "all";
  project: string;
  projects_searched: string[];
  hits: SearchHit[];
}

export interface NextStep {
  task_id: string;
  title: string;
  status: string;
  priority: string;
  reason: string;
}

export interface TaskRef {
  id: string;
  title: string;
  status: string;
  priority: string;
}

export interface Briefing {
  greeting: string;
  project: string;
  conversation_id: string;
  conversation_title: string;
  last_active: string;
  away_hours: number;
  is_return: boolean;
  active_task: TaskRef | null;
  last_completed: TaskRef | null;
  recent_commits: string[];
  branch: string;
  open_task_count: number;
  next_step: NextStep | null;
  /** Stated when a section had nothing to report, so empty reads as "nothing recorded". */
  notes: string[];
  can_continue: boolean;
}

export interface ActivityEvent {
  kind: "context" | "knowledge" | "task" | "provider" | "persist" | "error";
  message: string;
  at: string;
  project: string;
  detail: string;
  ok: boolean;
}

export interface CreatedTask {
  task: TaskRef & { project?: string };
  conversation_id: string;
  message_id: string;
  project: string;
}
