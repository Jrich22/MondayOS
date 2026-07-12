/**
 * Brain state derivation — the mapping from real MondayOS activity to the one
 * visual state Monday's Brain should show. Pure and unit-tested. The priority
 * order reflects what most demands the operator's attention, with two transient
 * overrides (a command being processed → thinking; a just-finished action →
 * completed) layered on top of the steady-state derivation.
 */

import type { Agent, Approval, Task } from "@/adapter/types";
import type { BrainState } from "@/components/monday";

export interface BrainInputs {
  agents: Agent[];
  approvals?: Approval[];
  tasks?: Task[];
  /** A command is being classified / context retrieved / provider pending. */
  busy?: boolean;
  /** An action or workflow just completed (transient green wave). */
  justCompleted?: boolean;
  /** Knowledge/decision/summary being captured (transient inward stream). */
  learning?: boolean;
}

export function deriveBrainState(input: BrainInputs): BrainState {
  const { agents, approvals = [], tasks = [], busy, justCompleted, learning } = input;

  // Transient overrides first.
  if (busy) return "thinking";
  if (justCompleted) return "completed";

  const acts = agents.map((a) => a.activity);
  const openApprovals = approvals.some((a) => a.status === "open");
  const blockedTask = tasks.some((t) => t.status === "blocked");

  if (acts.includes("blocked") || blockedTask) return "blocked";
  if (acts.includes("awaiting") || openApprovals) return "awaiting";
  if (acts.includes("executing")) return "executing";
  if (learning || acts.includes("learning")) return "learning";
  if (acts.includes("thinking")) return "thinking";
  return "idle";
}

/** Count tasks by status — shared by the header and the tasks workspace. */
export function taskCounts(tasks: Task[]) {
  const c = { active: 0, blocked: 0, review: 0, completed: 0 };
  for (const t of tasks) c[t.status]++;
  return c;
}
