import { describe, it, expect } from "vitest";
import { OS_STATE, taskCounts, deriveBrainState, type OSState } from "./os-data";

describe("os-data", () => {
  it("counts tasks by status", () => {
    const c = taskCounts(OS_STATE);
    expect(c.active + c.blocked + c.review + c.completed).toBe(OS_STATE.tasks.length);
    expect(c.blocked).toBeGreaterThan(0);
  });

  it("derives the dominant brain state by attention priority", () => {
    // No agent is blocked in the snapshot; Publish is awaiting approval, which
    // is the highest-priority live activity → the brain rests in "awaiting".
    expect(deriveBrainState(OS_STATE)).toBe("awaiting");
  });

  it("lets a blocked agent override everything else", () => {
    const s: OSState = {
      ...OS_STATE,
      agents: [
        { id: "a", name: "A", role: "", activity: "blocked" },
        { id: "b", name: "B", role: "", activity: "executing" },
      ],
    };
    expect(deriveBrainState(s)).toBe("blocked");
  });

  it("prioritises awaiting over executing when nothing is blocked", () => {
    const s: OSState = {
      ...OS_STATE,
      agents: [
        { id: "x", name: "X", role: "", activity: "executing" },
        { id: "y", name: "Y", role: "", activity: "awaiting" },
      ],
    };
    expect(deriveBrainState(s)).toBe("awaiting");
  });

  it("falls back to idle when all agents are idle", () => {
    const s: OSState = {
      ...OS_STATE,
      agents: [{ id: "x", name: "X", role: "", activity: "idle" }],
    };
    expect(deriveBrainState(s)).toBe("idle");
  });
});
