import { describe, it, expect, beforeEach } from "vitest";
import { classifyCommand } from "./parser";
import { runCommand } from "./execute";
import { createDemoAdapter } from "@/adapter/demoAdapter";
import { clearActionLog, getActionLog } from "@/adapter/log";

const adapter = createDemoAdapter();
const run = (text: string) => runCommand(classifyCommand(text), adapter);

describe("runCommand — command → action mapping", () => {
  beforeEach(() => clearActionLog());

  it("executes a read-only command immediately and returns an answer", async () => {
    const out = await run("Show blocked tasks.");
    expect(out.kind).toBe("answer");
    if (out.kind === "answer") {
      expect(out.data.type).toBe("tasks");
      if (out.data.type === "tasks") expect(out.data.tasks.every((t) => t.status === "blocked")).toBe(true);
      expect(out.section).toBe("tasks");
      expect(out.mode).toBe("demo");
    }
  });

  it("returns a recommendation with actionable follow-ups", async () => {
    const out = await run("What should we work on next?");
    expect(out.kind).toBe("answer");
    if (out.kind === "answer") {
      expect(out.speech.length).toBeGreaterThan(0);
      expect(out.actions.some((a) => a.command || a.section)).toBe(true);
    }
  });

  it("requires confirmation for a write action (never auto-executes)", async () => {
    const out = await run("Run the team on TASK-0046.");
    expect(out.kind).toBe("confirm");
    if (out.kind === "confirm") {
      expect(out.parsed.intent).toBe("team.run");
      expect(out.actions.some((a) => a.confirm)).toBe(true);
      expect(out.actions.some((a) => a.label.toLowerCase() === "cancel")).toBe(true);
    }
    // A confirm outcome must not have written anything.
    expect(getActionLog().length).toBe(0);
  });

  it("blocks a gated action and never bypasses ApprovalGate", async () => {
    const out = await run("Publish the roadmap to Confluence.");
    expect(out.kind).toBe("blocked");
    if (out.kind === "blocked") expect(out.reason.toLowerCase()).toContain("confluence");
    expect(getActionLog().length).toBe(0);
  });

  it("blocks git/deploy/secrets/trading", async () => {
    for (const cmd of ["push to main", "deploy to production", "reveal the secret token", "execute a live trade"]) {
      expect((await run(cmd)).kind).toBe("blocked");
    }
  });

  it("navigates to a product workspace", async () => {
    const out = await run("Show Cue App progress.");
    expect(out.kind).toBe("answer");
    if (out.kind === "answer") {
      expect(out.section).toBe("products");
      expect(out.product).toBe("cue");
      expect(out.data.type).toBe("product");
    }
  });

  it("surfaces an adapter error without throwing", async () => {
    const out = await run("Open TASK-9999.");
    // TASK-9999 doesn't exist → error outcome, gracefully.
    expect(out.kind).toBe("error");
    if (out.kind === "error") expect(out.error.code).toBe("not-found");
  });

  it("handles unknown input with help", async () => {
    const out = await run("zxcv qwer asdf");
    expect(out.kind).toBe("answer");
    if (out.kind === "answer") expect(out.actions.length).toBeGreaterThan(0);
  });
});

describe("write execution is deferred to Phase 2 (demo adapter)", () => {
  beforeEach(() => clearActionLog());

  it("createTask returns not-implemented and is logged", async () => {
    const r = await adapter.createTask({ title: "x", objective: "y" });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe("not-implemented");
    expect(getActionLog().some((e) => e.op === "createTask")).toBe(true);
  });
});
