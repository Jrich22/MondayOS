/**
 * Pure helpers for visualizing a team workflow (TeamRun / AgentRun). Kept out of
 * the component so the stage-coloring and progress math are unit-testable and
 * the visual layer stays declarative.
 */

import type { AgentRun, RunStatus, TeamRun } from "@/adapter/types";

/** Tailwind status token for a stage, by run status + verdict. */
export function stageTone(run: Pick<AgentRun, "status" | "verdict">): string {
  if (run.status === "blocked" || run.verdict === "fail") return "blocked";
  if (run.status === "awaiting") return "awaiting";
  if (run.status === "completed") return run.verdict === "concerns" ? "awaiting" : "completed";
  if (run.status === "running") return "executing";
  return "idle";
}

export interface WorkflowProgress {
  total: number;
  completed: number;
  /** 0..1 fraction of stages completed. */
  fraction: number;
  /** The stage currently running/awaiting, if any. */
  activeStage?: AgentRun;
  status: RunStatus;
}

export function workflowProgress(run: TeamRun): WorkflowProgress {
  const total = run.stages.length;
  const completed = run.stages.filter((s) => s.status === "completed").length;
  const activeStage = run.stages.find((s) => s.status === "running" || s.status === "awaiting");
  return {
    total,
    completed,
    fraction: total ? completed / total : 0,
    activeStage,
    status: run.status,
  };
}

/** Human elapsed label for a stage. */
export function elapsedLabel(ms?: number): string {
  if (!ms || ms <= 0) return "—";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}
