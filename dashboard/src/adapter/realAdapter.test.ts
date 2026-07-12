import { describe, it, expect, vi } from "vitest";
import { createRealAdapter, probe } from "./realAdapter";

/** Build a fake `fetch` from a queue of responder functions (one per call). */
function fakeFetch(responders: Array<() => Promise<Response> | Response>) {
  let i = 0;
  const calls: { url: string; init?: RequestInit }[] = [];
  const impl = (async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const r = responders[Math.min(i, responders.length - 1)];
    i++;
    return r();
  }) as unknown as typeof fetch;
  return { impl, calls: () => calls, count: () => i };
}

const json = (status: number, body: unknown): Response =>
  ({ ok: status >= 200 && status < 300, status, json: async () => body }) as Response;

const baseUrl = "http://127.0.0.1:8787";

describe("realAdapter — transport", () => {
  it("returns typed data on a 200 read and reports health", async () => {
    const health = vi.fn();
    const f = fakeFetch([() => json(200, [{ id: "TASK-1", title: "x", status: "active" }])]);
    const a = createRealAdapter({ baseUrl, fetchImpl: f.impl, onHealth: health });
    const r = await a.listTasks();
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.data[0].id).toBe("TASK-1");
    expect(health).toHaveBeenCalledWith(true);
  });

  it("parses the API's structured error envelope on a 4xx", async () => {
    const f = fakeFetch([() => json(404, { error: { code: "not-found", message: "No task" } })]);
    const a = createRealAdapter({ baseUrl, fetchImpl: f.impl });
    const r = await a.getTask("TASK-9");
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.error.code).toBe("not-found");
      expect(r.mode).toBe("live");
    }
    expect(f.count()).toBe(1); // 4xx is deterministic → no retry
  });

  it("retries a transient read failure, then succeeds", async () => {
    const f = fakeFetch([
      () => Promise.reject(new Error("ECONNREFUSED")),
      () => Promise.reject(new Error("ECONNREFUSED")),
      () => json(200, { version: "3", healthy: true, sessionId: "s", uptimeSeconds: 1, provider: "fake", model: "fake" }),
    ]);
    const a = createRealAdapter({ baseUrl, fetchImpl: f.impl, readRetries: 2 });
    const r = await a.getSystemStatus();
    expect(r.ok).toBe(true);
    expect(f.count()).toBe(3);
  });

  it("reports DEGRADED health after a read exhausts retries", async () => {
    const health = vi.fn();
    const f = fakeFetch([() => Promise.reject(new Error("down"))]);
    const a = createRealAdapter({ baseUrl, fetchImpl: f.impl, readRetries: 1, onHealth: health });
    const r = await a.listProducts();
    expect(r.ok).toBe(false);
    expect(f.count()).toBe(2); // initial + 1 retry
    expect(health).toHaveBeenLastCalledWith(false);
  });

  it("never retries a write, and never silently falls back to demo", async () => {
    const f = fakeFetch([() => Promise.reject(new Error("down"))]);
    const a = createRealAdapter({ baseUrl, fetchImpl: f.impl });
    const r = await a.runTeam({ taskId: "TASK-1" });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.mode).toBe("live"); // still live — no demo fallback
    expect(f.count()).toBe(1); // exactly one attempt
  });

  it("approve posts to the approve endpoint and returns the approval", async () => {
    const f = fakeFetch([() => json(200, { id: "run-1", taskId: "T", teamRunId: "TR", summary: "", status: "approved", verdicts: [], affected: [] })]);
    const a = createRealAdapter({ baseUrl, fetchImpl: f.impl });
    const r = await a.approveRun("run-1");
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.data.status).toBe("approved");
    expect(f.calls()[0].url).toContain("/agent-runs/run-1/approve");
    expect(f.calls()[0].init?.method).toBe("POST");
  });

  it("probe is true on healthy /health and false on failure", async () => {
    const okF = fakeFetch([() => json(200, { ok: true })]);
    expect(await probe({ baseUrl, fetchImpl: okF.impl })).toBe(true);
    const badF = fakeFetch([() => Promise.reject(new Error("x"))]);
    expect(await probe({ baseUrl, fetchImpl: badF.impl })).toBe(false);
  });
});
