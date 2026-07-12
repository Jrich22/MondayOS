import { describe, it, expect } from "vitest";
import { deriveBrainState, taskCounts } from "./brain";
import type { Agent, Approval, Task } from "@/adapter/types";

const agent = (activity: Agent["activity"]): Agent => ({ id: "a", name: "A", role: "", activity });

describe("deriveBrainState", () => {
  it("is idle when nothing is happening", () => {
    expect(deriveBrainState({ agents: [agent("idle")] })).toBe("idle");
  });

  it("shows thinking while a command is being processed (transient override)", () => {
    expect(deriveBrainState({ agents: [agent("executing")], busy: true })).toBe("thinking");
  });

  it("shows completed just after an action finishes (transient override)", () => {
    expect(deriveBrainState({ agents: [agent("executing")], justCompleted: true })).toBe("completed");
  });

  it("prioritises blocked over everything else in steady state", () => {
    expect(deriveBrainState({ agents: [agent("executing"), agent("blocked")] })).toBe("blocked");
    const blockedTask: Task = { id: "T", title: "t", status: "blocked" };
    expect(deriveBrainState({ agents: [agent("idle")], tasks: [blockedTask] })).toBe("blocked");
  });

  it("shows awaiting when an approval is open", () => {
    const ap: Approval = { id: "AP", taskId: "T", teamRunId: "TR", summary: "", status: "open", verdicts: [], affected: [] };
    expect(deriveBrainState({ agents: [agent("idle")], approvals: [ap] })).toBe("awaiting");
  });

  it("orders executing > learning > thinking > idle", () => {
    expect(deriveBrainState({ agents: [agent("executing"), agent("learning")] })).toBe("executing");
    expect(deriveBrainState({ agents: [agent("learning"), agent("thinking")] })).toBe("learning");
    expect(deriveBrainState({ agents: [agent("thinking")] })).toBe("thinking");
  });
});

describe("taskCounts", () => {
  it("counts by status", () => {
    const tasks: Task[] = [
      { id: "1", title: "", status: "active" },
      { id: "2", title: "", status: "blocked" },
      { id: "3", title: "", status: "completed" },
      { id: "4", title: "", status: "active" },
    ];
    const c = taskCounts(tasks);
    expect(c).toEqual({ active: 2, blocked: 1, review: 0, completed: 1 });
  });
});
