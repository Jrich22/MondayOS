import { describe, it, expect, beforeEach } from "vitest";
import { selectAdapter } from "./index";
import { createDemoAdapter } from "./demoAdapter";
import { clearActionLog, getActionLog } from "./log";

describe("adapter selection — live vs demo", () => {
  it("falls back to demo when no API is configured", async () => {
    const sel = await selectAdapter();
    expect(sel.mode).toBe("demo");
    expect(sel.adapter.mode).toBe("demo");
    expect(sel.reason).toBeTruthy();
  });

  it("falls back to demo when the API probe fails", async () => {
    const failingFetch = (async () => {
      throw new Error("connection refused");
    }) as unknown as typeof fetch;
    const sel = await selectAdapter({ baseUrl: "http://localhost:9999", fetchImpl: failingFetch });
    expect(sel.mode).toBe("demo");
    expect(sel.reason).toContain("unreachable");
  });

  it("selects the live adapter when the API probe succeeds", async () => {
    const okFetch = (async () => ({ ok: true, json: async () => ({}) })) as unknown as typeof fetch;
    const sel = await selectAdapter({ baseUrl: "http://localhost:8080", fetchImpl: okFetch });
    expect(sel.mode).toBe("live");
    expect(sel.adapter.mode).toBe("live");
  });
});

describe("demo adapter — read surface", () => {
  const adapter = createDemoAdapter();

  it("lists products", async () => {
    const r = await adapter.listProducts();
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.data.map((p) => p.key)).toContain("cue");
  });

  it("filters tasks by status", async () => {
    const r = await adapter.listTasks({ status: "blocked" });
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.data.every((t) => t.status === "blocked")).toBe(true);
  });

  it("returns a structured not-found for a missing task", async () => {
    const r = await adapter.getTask("NOPE-0000");
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe("not-found");
  });

  it("searches knowledge", async () => {
    const r = await adapter.searchKnowledge("cue");
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.data.length).toBeGreaterThan(0);
  });
});

describe("demo adapter — write surface (Phase 2)", () => {
  beforeEach(() => clearActionLog());

  it("runTeam is not executed in demo and is logged", async () => {
    const r = await createDemoAdapter().runTeam({ taskId: "TASK-0046" });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe("not-implemented");
    expect(getActionLog().some((e) => e.op === "runTeam" && !e.ok)).toBe(true);
  });
});
