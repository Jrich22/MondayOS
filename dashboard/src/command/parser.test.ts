import { describe, it, expect } from "vitest";
import { classifyCommand } from "./parser";
import type { Intent, RiskLevel } from "./intents";

describe("classifyCommand — intent classification", () => {
  const cases: [string, Intent, RiskLevel][] = [
    ["What should we work on next?", "next.recommendation", "read"],
    ["Show Cue App progress.", "product.progress", "read"],
    ["Show blocked tasks.", "tasks.blocked", "read"],
    ["Show tasks awaiting approval.", "tasks.awaitingApproval", "read"],
    ["Run the team on TASK-0046.", "team.run", "write"],
    ["Open the latest agent run.", "agentRuns.latest", "read"],
    ["Publish the roadmap to Confluence.", "publish.confluence", "gated"],
    ["Show open pull requests.", "pr.list", "read"],
    ["What changed today?", "activity.today", "read"],
    ["What does Monday remember about Cue App?", "memory.query", "read"],
    ["Switch to Storm Edge.", "product.switch", "read"],
    ["Create a task for vendor management.", "task.create", "write"],
  ];

  it.each(cases)("%s → %s (%s)", (text, intent, risk) => {
    const p = classifyCommand(text);
    expect(p.intent).toBe(intent);
    expect(p.risk).toBe(risk);
  });

  it("extracts a task id", () => {
    expect(classifyCommand("Run the team on TASK-0046.").entities.taskId).toBe("TASK-0046");
  });

  it("resolves product aliases", () => {
    expect(classifyCommand("Show Cue App progress.").entities.product).toBe("cue");
    expect(classifyCommand("Switch to Storm Edge.").entities.product).toBe("storm-edge");
  });

  it("classifies gated git/deploy/secrets as gated", () => {
    expect(classifyCommand("commit and push my changes").risk).toBe("gated");
    expect(classifyCommand("deploy to production").risk).toBe("gated");
    expect(classifyCommand("show me the api key").risk).toBe("gated");
  });

  it("returns unknown for empty / unrecognized input", () => {
    expect(classifyCommand("").intent).toBe("unknown");
    expect(classifyCommand("asdfqwer zxcv").intent).toBe("unknown");
  });

  it("assigns a navigation section to read intents", () => {
    expect(classifyCommand("Show blocked tasks.").section).toBe("tasks");
    expect(classifyCommand("Show tasks awaiting approval.").section).toBe("approvals");
  });
});
