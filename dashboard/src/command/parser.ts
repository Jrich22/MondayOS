/**
 * The command parser: natural language → `ParsedCommand`.
 *
 * Pure and deterministic (no adapter, no I/O), so it is exhaustively unit
 * tested. Rules are ordered by specificity — the first match wins — so gated
 * and write phrasings are recognised before the looser read patterns. It does
 * NOT execute anything; it only classifies intent, extracts entities, assigns a
 * risk tier, and writes a human preview.
 */

import {
  INTENT_SECTION,
  PRODUCT_ALIASES,
  RISK,
  type CommandEntities,
  type Intent,
  type ParsedCommand,
} from "./intents";

function resolveProduct(text: string): string | undefined {
  for (const { pattern, key } of PRODUCT_ALIASES) if (pattern.test(text)) return key;
  return undefined;
}

function resolveTaskId(text: string): string | undefined {
  const m = text.match(/\b([A-Z]{2,}-\d{1,5})\b/i);
  return m ? m[1].toUpperCase() : undefined;
}

interface Rule {
  intent: Intent;
  test: (t: string) => boolean;
  entities?: (t: string) => CommandEntities;
  preview: (e: CommandEntities, t: string) => string;
}

const any = (t: string, ...words: string[]) => words.some((w) => t.includes(w));

// Ordered most-specific → least. Gated + write phrasings come first.
const RULES: Rule[] = [
  // ---- Gated (recognised so they can be safely refused) ----
  {
    intent: "publish.confluence",
    test: (t) => t.includes("publish") && (t.includes("confluence") || t.includes("roadmap") || t.includes("doc")),
    entities: (t) => ({ docId: resolveTaskId(t), product: resolveProduct(t) }),
    preview: () => "Publish a document to Confluence (gated).",
  },
  { intent: "git.action", test: (t) => any(t, "commit", "push", "merge", "pull request merge", "force push"), preview: () => "Perform a Git write (commit/push/merge) — gated." },
  { intent: "deploy", test: (t) => any(t, "deploy", "release to prod", "ship to production"), preview: () => "Deploy / release (gated)." },
  { intent: "secrets", test: (t) => any(t, "secret", "api key", "credential", "token"), preview: () => "Access or change secrets (gated)." },
  { intent: "trading", test: (t) => any(t, "trade", "buy shares", "sell shares", "live trading"), preview: () => "Execute a live trade (gated)." },

  // ---- Writes ----
  {
    intent: "team.run",
    test: (t) => (t.includes("run the team") || t.includes("run team") || (t.includes("run") && t.includes("workflow")) || (t.includes("run") && /\b[a-z]{2,}-\d+/i.test(t))) && !t.includes("agent run"),
    entities: (t) => ({ taskId: resolveTaskId(t) }),
    preview: (e) => `Run the team workflow${e.taskId ? ` on ${e.taskId}` : ""} in review-required mode (no commits or pushes).`,
  },
  {
    intent: "task.create",
    test: (t) => any(t, "create a task", "create task", "new task", "add a task", "make a task"),
    entities: (t) => ({ product: resolveProduct(t) }),
    preview: (_e, t) => `Create a new task${/for (.+)/i.test(t) ? ` for ${t.replace(/.*for /i, "").trim()}` : ""}.`,
  },
  {
    intent: "task.assign",
    test: (t) => t.includes("assign") && /\b[a-z]{2,}-\d+/i.test(t),
    entities: (t) => ({ taskId: resolveTaskId(t) }),
    preview: (e) => `Reassign ${e.taskId ?? "a task"}.`,
  },
  { intent: "run.approve", test: (t) => any(t, "approve"), entities: (t) => ({ taskId: resolveTaskId(t) }), preview: () => "Approve an agent/team run." },
  { intent: "run.reject", test: (t) => any(t, "reject", "decline"), entities: (t) => ({ taskId: resolveTaskId(t) }), preview: () => "Reject an agent/team run." },

  // ---- Reads / navigation ----
  { intent: "next.recommendation", test: (t) => any(t, "what should we", "what next", "what's next", "what should i", "work on next", "do next"), preview: () => "Recommend what to work on next." },
  { intent: "tasks.awaitingApproval", test: (t) => (t.includes("await") && t.includes("approval")) || t.includes("awaiting approval") || (t.includes("pending") && t.includes("approval")), preview: () => "Show tasks awaiting approval." },
  { intent: "approvals.list", test: (t) => t.includes("approval") || t.includes("approvals"), preview: () => "Open the approvals queue." },
  { intent: "tasks.blocked", test: (t) => t.includes("block"), preview: () => "Show blocked tasks." },
  { intent: "agentRuns.latest", test: (t) => (t.includes("agent run") || (t.includes("latest") && t.includes("run")) || (t.includes("open") && t.includes("run"))), preview: () => "Open the latest agent run." },
  { intent: "product.progress", test: (t) => (t.includes("progress") || t.includes("status") || t.includes("show")) && resolveProduct(t) != null, entities: (t) => ({ product: resolveProduct(t) }), preview: (e) => `Show ${e.product} progress.` },
  { intent: "product.switch", test: (t) => (t.includes("switch") || t.includes("go to") || t.includes("open")) && resolveProduct(t) != null, entities: (t) => ({ product: resolveProduct(t) }), preview: (e) => `Switch to ${e.product}.` },
  { intent: "pr.list", test: (t) => (t.includes("pull request") || t.includes("pull requests") || t.includes(" pr") || t.startsWith("pr")) , preview: () => "Show open pull requests." },
  { intent: "activity.today", test: (t) => (t.includes("changed today") || t.includes("what changed") || (t.includes("today") && t.includes("what")) || t.includes("recent activity")), preview: () => "Summarize what changed today." },
  { intent: "memory.query", test: (t) => (t.includes("remember") || t.includes("memory") || t.includes("what does monday know")), entities: (t) => ({ product: resolveProduct(t), query: t }), preview: (e) => `Recall what Monday remembers${e.product ? ` about ${e.product}` : ""}.` },
  { intent: "knowledge.search", test: (t) => any(t, "search knowledge", "find doc", "knowledge", "docs about", "decision"), entities: (t) => ({ query: t }), preview: () => "Search knowledge." },
  { intent: "task.get", test: (t) => /\b[a-z]{2,}-\d+/i.test(t) && any(t, "show", "open", "get", "details", "task"), entities: (t) => ({ taskId: resolveTaskId(t) }), preview: (e) => `Open ${e.taskId}.` },
  { intent: "tasks.list", test: (t) => t.includes("task"), entities: (t) => ({ product: resolveProduct(t) }), preview: () => "List tasks." },
  { intent: "agents.list", test: (t) => t.includes("agent"), preview: () => "Show the agent fleet." },
  { intent: "system.status", test: (t) => any(t, "status", "health", "system", "how are you", "uptime"), preview: () => "Report MondayOS system status." },
];

/** Classify a raw command string. Always returns a `ParsedCommand`. */
export function classifyCommand(rawText: string): ParsedCommand {
  const t = rawText.trim().toLowerCase();
  if (!t) {
    return { intent: "unknown", risk: "read", entities: {}, preview: "", rawText };
  }
  for (const rule of RULES) {
    if (rule.test(t)) {
      const entities = rule.entities ? rule.entities(t) : {};
      return {
        intent: rule.intent,
        risk: RISK[rule.intent],
        section: INTENT_SECTION[rule.intent],
        entities,
        preview: rule.preview(entities, t),
        rawText,
      };
    }
  }
  return {
    intent: "unknown",
    risk: "read",
    entities: {},
    preview: "",
    rawText,
  };
}
