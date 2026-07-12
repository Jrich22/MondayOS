/**
 * Command vocabulary for the Brain interface.
 *
 * This layer is pure NL→intent mapping and risk classification — NOT business
 * logic. It decides *what the user wants* and *how dangerous it is*; MondayOS
 * (via the adapter) decides whether and how to do it. Keeping the catalog here
 * makes the parser reusable and unit-testable in isolation.
 */

/** Where a command wants to take the operator. Also the dashboard's sections. */
export type Section =
  | "home"
  | "products"
  | "tasks"
  | "agents"
  | "memory"
  | "knowledge"
  | "approvals"
  | "workflows"
  | "integrations";

export const SECTIONS: Section[] = [
  "home",
  "products",
  "tasks",
  "agents",
  "memory",
  "knowledge",
  "approvals",
  "workflows",
  "integrations",
];

/** Risk tier drives execution policy: read runs now, write confirms, gated refuses. */
export type RiskLevel = "read" | "write" | "gated";

export type Intent =
  // reads / navigation
  | "system.status"
  | "next.recommendation"
  | "product.progress"
  | "product.switch"
  | "tasks.blocked"
  | "tasks.awaitingApproval"
  | "tasks.list"
  | "task.get"
  | "agents.list"
  | "agentRuns.latest"
  | "pr.list"
  | "activity.today"
  | "memory.query"
  | "knowledge.search"
  | "approvals.list"
  // writes
  | "task.create"
  | "task.assign"
  | "team.run"
  | "task.updateStatus"
  | "run.approve"
  | "run.reject"
  // gated
  | "publish.confluence"
  | "git.action"
  | "deploy"
  | "secrets"
  | "trading"
  // fallback
  | "unknown";

export const RISK: Record<Intent, RiskLevel> = {
  "system.status": "read",
  "next.recommendation": "read",
  "product.progress": "read",
  "product.switch": "read",
  "tasks.blocked": "read",
  "tasks.awaitingApproval": "read",
  "tasks.list": "read",
  "task.get": "read",
  "agents.list": "read",
  "agentRuns.latest": "read",
  "pr.list": "read",
  "activity.today": "read",
  "memory.query": "read",
  "knowledge.search": "read",
  "approvals.list": "read",
  "task.create": "write",
  "task.assign": "write",
  "team.run": "write",
  "task.updateStatus": "write",
  "run.approve": "write",
  "run.reject": "write",
  "publish.confluence": "gated",
  "git.action": "gated",
  deploy: "gated",
  secrets: "gated",
  trading: "gated",
  unknown: "read",
};

/** Which section a given intent navigates to (if any). */
export const INTENT_SECTION: Partial<Record<Intent, Section>> = {
  "system.status": "home",
  "next.recommendation": "products",
  "product.progress": "products",
  "product.switch": "products",
  "tasks.blocked": "tasks",
  "tasks.awaitingApproval": "approvals",
  "tasks.list": "tasks",
  "task.get": "tasks",
  "agents.list": "agents",
  "agentRuns.latest": "workflows",
  "pr.list": "integrations",
  "activity.today": "home",
  "memory.query": "memory",
  "knowledge.search": "knowledge",
  "approvals.list": "approvals",
  "task.create": "tasks",
  "team.run": "workflows",
  "run.approve": "approvals",
  "run.reject": "approvals",
  "publish.confluence": "integrations",
};

/** NL aliases → product key. Presentation-only mapping, not system state. */
export const PRODUCT_ALIASES: { pattern: RegExp; key: string }[] = [
  { pattern: /\bcue(\s*app)?\b/i, key: "cue" },
  { pattern: /\bstorm\s*edge\b/i, key: "storm-edge" },
  { pattern: /\bmonday\s*os\b/i, key: "mondayos" },
];

export interface CommandEntities {
  product?: string;
  taskId?: string;
  query?: string;
  agent?: string;
  docId?: string;
}

export interface ParsedCommand {
  intent: Intent;
  risk: RiskLevel;
  section?: Section;
  entities: CommandEntities;
  /** Human-readable preview of the proposed action. */
  preview: string;
  rawText: string;
}
