/**
 * Command → action mapping. Given a `ParsedCommand` and an adapter, produce a
 * structured `CommandOutcome` the Brain can speak and the UI can render.
 *
 * Execution policy is enforced HERE, once, for every command:
 *   - read  → execute immediately against the adapter, return an answer
 *   - write → return a `confirm` outcome (never auto-executed); the operator
 *             must confirm, and even then MondayOS enforces its own gates
 *   - gated → refuse with a `blocked` outcome; the Brain never bypasses
 *             ApprovalGate
 *
 * No business logic lives here — every fact comes from the adapter (MondayOS is
 * the system of record). This module only shapes conversation + navigation.
 */

import type {
  Agent,
  Approval,
  ActivityEvent,
  KnowledgeItem,
  MondayAdapter,
  Product,
  PullRequest,
  SystemStatus,
  Task,
  TeamRun,
  DataMode,
} from "@/adapter/types";
import type { ParsedCommand, Section } from "./intents";

export interface ResultAction {
  label: string;
  /** A follow-up command to run when clicked. */
  command?: string;
  /** Or a direct navigation. */
  section?: Section;
  product?: string;
  variant?: "primary" | "ghost" | "danger";
  /** Marks the confirm button of a write outcome. */
  confirm?: boolean;
}

/** Typed payload telling the UI which structured view to render. */
export type DataView =
  | { type: "none" }
  | { type: "status"; status: SystemStatus }
  | { type: "products"; products: Product[] }
  | { type: "product"; product: Product }
  | { type: "tasks"; tasks: Task[] }
  | { type: "agents"; agents: Agent[] }
  | { type: "approvals"; approvals: Approval[] }
  | { type: "activity"; events: ActivityEvent[] }
  | { type: "prs"; prs: PullRequest[] }
  | { type: "knowledge"; items: KnowledgeItem[] }
  | { type: "runs"; runs: TeamRun[] };

export type CommandOutcome =
  | {
      kind: "answer";
      speech: string;
      section?: Section;
      product?: string;
      data: DataView;
      actions: ResultAction[];
      mode: DataMode;
    }
  | {
      kind: "confirm";
      speech: string;
      parsed: ParsedCommand;
      actions: ResultAction[];
      mode: DataMode;
    }
  | { kind: "blocked"; speech: string; reason: string; mode: DataMode }
  | { kind: "error"; speech: string; error: { code: string; message: string }; mode: DataMode };

const GATED_MESSAGE: Record<string, string> = {
  "publish.confluence": "Publishing to Confluence is a gated action.",
  "git.action": "Git writes (commit / push / merge) are gated actions.",
  deploy: "Deployments are gated actions.",
  secrets: "Reading or changing secrets is a gated action.",
  trading: "Live trading is a gated action.",
};

const HELP = [
  "What should we work on next?",
  "Show Cue App progress",
  "Show blocked tasks",
  "Show tasks awaiting approval",
  "Open the latest agent run",
  "What changed today?",
  "What does Monday remember about Cue App?",
];

export async function runCommand(
  parsed: ParsedCommand,
  adapter: MondayAdapter,
): Promise<CommandOutcome> {
  const mode = adapter.mode;

  // --- Gated: refuse. The Brain never bypasses ApprovalGate. ---
  if (parsed.risk === "gated") {
    const base = GATED_MESSAGE[parsed.intent] ?? "That is a gated action.";
    return {
      kind: "blocked",
      reason: base,
      speech: `${base} MondayOS keeps these behind its approval policy — I won't run it without explicit approval. (Approval-backed execution arrives in a later phase.)`,
      mode,
    };
  }

  // --- Write: preview + require confirmation. Never auto-execute. ---
  if (parsed.risk === "write") {
    return {
      kind: "confirm",
      speech: `${parsed.preview} This is a write action, so I need your confirmation before MondayOS runs it.`,
      parsed,
      actions: [
        { label: confirmLabel(parsed), confirm: true, variant: "primary" },
        { label: "Cancel", variant: "ghost" },
      ],
      mode,
    };
  }

  // --- Read: execute immediately. ---
  try {
    return await runRead(parsed, adapter, mode);
  } catch (e) {
    return {
      kind: "error",
      speech: "Something went wrong reaching MondayOS. Nothing was changed.",
      error: { code: "adapter-throw", message: e instanceof Error ? e.message : String(e) },
      mode,
    };
  }
}

function confirmLabel(p: ParsedCommand): string {
  switch (p.intent) {
    case "team.run":
      return `Run workflow${p.entities.taskId ? ` on ${p.entities.taskId}` : ""}`;
    case "task.create":
      return "Create task";
    case "task.assign":
      return "Assign task";
    case "run.approve":
      return "Approve";
    case "run.reject":
      return "Reject";
    default:
      return "Confirm";
  }
}

async function runRead(
  parsed: ParsedCommand,
  adapter: MondayAdapter,
  mode: DataMode,
): Promise<CommandOutcome> {
  const { intent, entities } = parsed;
  const nav = (extra: Partial<CommandOutcome & { section: Section }>) => extra;
  void nav;

  const fail = (speech: string, error: { code: string; message: string }): CommandOutcome => ({
    kind: "error",
    speech,
    error,
    mode,
  });

  switch (intent) {
    case "system.status": {
      const r = await adapter.getSystemStatus();
      if (!r.ok) return fail("Couldn't read system status.", r.error);
      const s = r.data;
      return {
        kind: "answer",
        speech: `MondayOS ${s.version} is ${s.healthy ? "healthy" : "degraded"} — running on ${s.provider}/${s.model}. Session ${s.sessionId}.`,
        section: "home",
        data: { type: "status", status: s },
        actions: [
          { label: "View agents", section: "agents" },
          { label: "What changed today?", command: "what changed today" },
        ],
        mode,
      };
    }

    case "next.recommendation": {
      const r = await adapter.listProducts();
      if (!r.ok) return fail("Couldn't read the product portfolio.", r.error);
      const withRec = r.data.find((p) => p.recommendation && p.status !== "attention") ?? r.data[0];
      return {
        kind: "answer",
        speech: withRec?.recommendation
          ? `${withRec.name}: ${withRec.recommendation}`
          : "No standout recommendation right now — the portfolio looks balanced.",
        section: "products",
        product: withRec?.key,
        data: { type: "products", products: r.data },
        actions: [
          { label: "Create task", command: "create a task for vendor management", variant: "primary" },
          { label: "View backlog", section: "tasks", product: withRec?.key },
          { label: "Open TASK-0046", command: "open TASK-0046" },
        ],
        mode,
      };
    }

    case "product.progress":
    case "product.switch": {
      const key = entities.product;
      if (!key) return fail("Which product?", { code: "missing-entity", message: "no product resolved" });
      const r = await adapter.getProduct(key);
      if (!r.ok) return fail(`Couldn't open ${key}.`, r.error);
      const p = r.data;
      const sprint = p.sprint ? ` ${p.sprint.name}: ${p.sprint.done}/${p.sprint.total} done.` : "";
      return {
        kind: "answer",
        speech:
          intent === "product.switch"
            ? `Switched to ${p.name}.${sprint}`
            : `${p.name} — ${p.summary}.${sprint}${p.recommendation ? ` Next: ${p.recommendation}` : ""}`,
        section: "products",
        product: p.key,
        data: { type: "product", product: p },
        actions: [
          { label: "View tasks", section: "tasks", product: p.key },
          { label: "Show approvals", section: "approvals" },
        ],
        mode,
      };
    }

    case "tasks.blocked": {
      const r = await adapter.listTasks({ status: "blocked", product: entities.product });
      if (!r.ok) return fail("Couldn't read tasks.", r.error);
      const list = r.data;
      return {
        kind: "answer",
        speech: list.length
          ? `${list.length} blocked task${list.length > 1 ? "s" : ""}: ${list.map((t) => `${t.id} (${t.blockedReason ?? "no reason given"})`).join("; ")}`
          : "Nothing is blocked right now.",
        section: "tasks",
        data: { type: "tasks", tasks: list },
        actions: [{ label: "Show approvals", section: "approvals" }],
        mode,
      };
    }

    case "tasks.awaitingApproval":
    case "approvals.list": {
      const r = await adapter.listApprovals();
      if (!r.ok) return fail("Couldn't read approvals.", r.error);
      const open = r.data.filter((a) => a.status === "open");
      return {
        kind: "answer",
        speech: open.length
          ? `${open.length} item${open.length > 1 ? "s" : ""} awaiting approval. ${open.map((a) => a.summary).join(" ")}`
          : "No approvals are waiting.",
        section: "approvals",
        data: { type: "approvals", approvals: r.data },
        actions: [{ label: "Open approvals", section: "approvals" }],
        mode,
      };
    }

    case "task.get": {
      const id = entities.taskId;
      if (!id) return fail("Which task?", { code: "missing-entity", message: "no task id" });
      const r = await adapter.getTask(id);
      if (!r.ok) return fail(`Couldn't find ${id}.`, r.error);
      const t = r.data;
      return {
        kind: "answer",
        speech: `${t.id} — ${t.title} (${t.status})${t.objective ? `. ${t.objective}` : ""}${t.blockedReason ? ` Blocked: ${t.blockedReason}` : ""}`,
        section: "tasks",
        product: t.product,
        data: { type: "tasks", tasks: [t] },
        actions: [{ label: "View all tasks", section: "tasks", product: t.product }],
        mode,
      };
    }

    case "tasks.list": {
      const r = await adapter.listTasks({ product: entities.product });
      if (!r.ok) return fail("Couldn't read tasks.", r.error);
      return {
        kind: "answer",
        speech: `${r.data.length} task${r.data.length === 1 ? "" : "s"}${entities.product ? ` for ${entities.product}` : ""}.`,
        section: "tasks",
        product: entities.product,
        data: { type: "tasks", tasks: r.data },
        actions: [{ label: "Show blocked", command: "show blocked tasks" }],
        mode,
      };
    }

    case "agents.list": {
      const r = await adapter.listAgents();
      if (!r.ok) return fail("Couldn't read the agent fleet.", r.error);
      const busy = r.data.filter((a) => a.activity !== "idle").length;
      return {
        kind: "answer",
        speech: `${r.data.length} agents, ${busy} active. ${r.data.filter((a) => a.task).map((a) => `${a.name}→${a.task}`).join(", ")}.`,
        section: "agents",
        data: { type: "agents", agents: r.data },
        actions: [{ label: "Open latest run", command: "open the latest agent run" }],
        mode,
      };
    }

    case "agentRuns.latest": {
      const r = await adapter.listTeamRuns();
      if (!r.ok) return fail("Couldn't read runs.", r.error);
      const latest = r.data[0];
      return {
        kind: "answer",
        speech: latest
          ? `${latest.id} on ${latest.taskId} — ${latest.status}. Stages: ${latest.stages.map((s) => `${s.stage}(${s.status})`).join(" → ")}.`
          : "No team runs yet.",
        section: "workflows",
        data: { type: "runs", runs: r.data },
        actions: latest ? [{ label: "Show approvals", section: "approvals" }] : [],
        mode,
      };
    }

    case "pr.list": {
      const r = await adapter.listPullRequests();
      if (!r.ok) return fail("Couldn't read pull requests.", r.error);
      const open = r.data.filter((p) => p.state === "open");
      return {
        kind: "answer",
        speech: `${open.length} open PR${open.length === 1 ? "" : "s"}: ${open.map((p) => `#${p.number} ${p.title}`).join("; ") || "none"}.`,
        section: "integrations",
        data: { type: "prs", prs: r.data },
        actions: [],
        mode,
      };
    }

    case "activity.today": {
      const r = await adapter.getRecentActivity();
      if (!r.ok) return fail("Couldn't read activity.", r.error);
      return {
        kind: "answer",
        speech: `Recent activity: ${r.data.slice(0, 3).map((e) => `${e.agent} ${e.message.toLowerCase()}`).join("; ")}.`,
        section: "home",
        data: { type: "activity", events: r.data },
        actions: [{ label: "System status", command: "system status" }],
        mode,
      };
    }

    case "memory.query":
    case "knowledge.search": {
      const query = entities.product ?? entities.query ?? parsed.rawText;
      const r = await adapter.searchKnowledge(query);
      if (!r.ok) return fail("Couldn't search knowledge.", r.error);
      return {
        kind: "answer",
        speech: r.data.length
          ? `I remember ${r.data.length} item${r.data.length === 1 ? "" : "s"}${entities.product ? ` about ${entities.product}` : ""}: ${r.data.map((k) => `${k.id} ${k.title}`).join("; ")}.`
          : "I don't have anything on that yet.",
        section: intent === "memory.query" ? "memory" : "knowledge",
        data: { type: "knowledge", items: r.data },
        actions: [],
        mode,
      };
    }

    default: {
      // unknown
      return {
        kind: "answer",
        speech: "I didn't catch a command there. Try one of these:",
        section: "home",
        data: { type: "none" },
        actions: HELP.map((c) => ({ label: c, command: c })),
        mode,
      };
    }
  }
}
