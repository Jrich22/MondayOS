import { describe, it, expect } from "vitest";
import { stageTone, workflowProgress, elapsedLabel } from "./workflow";
import type { TeamRun } from "@/adapter/types";

describe("workflow visualization helpers", () => {
  it("colors stages by status and verdict", () => {
    expect(stageTone({ status: "completed", verdict: "pass" })).toBe("completed");
    expect(stageTone({ status: "completed", verdict: "concerns" })).toBe("awaiting");
    expect(stageTone({ status: "blocked" })).toBe("blocked");
    expect(stageTone({ status: "awaiting" })).toBe("awaiting");
    expect(stageTone({ status: "running" })).toBe("executing");
    expect(stageTone({ status: "pending" })).toBe("idle");
  });

  it("computes workflow progress and the active stage", () => {
    const run: TeamRun = {
      id: "TR", taskId: "T", mode: "review-required", status: "awaiting", startedAt: "",
      stages: [
        { id: "1", teamRunId: "TR", stage: "CPO", agent: "A", status: "completed" },
        { id: "2", teamRunId: "TR", stage: "Eng", agent: "B", status: "completed" },
        { id: "3", teamRunId: "TR", stage: "Reviewer", agent: "C", status: "awaiting" },
      ],
    };
    const p = workflowProgress(run);
    expect(p.total).toBe(3);
    expect(p.completed).toBe(2);
    expect(p.fraction).toBeCloseTo(2 / 3);
    expect(p.activeStage?.stage).toBe("Reviewer");
  });

  it("formats elapsed time", () => {
    expect(elapsedLabel(0)).toBe("—");
    expect(elapsedLabel(15000)).toBe("15s");
    expect(elapsedLabel(95000)).toBe("1m 35s");
  });
});
