/**
 * AI Workspace transport tests.
 *
 * These cover the client's contract with `dashboard_api`: the project is on
 * every request (MondayOS scopes by project), reads may retry but writes never
 * do, and the API's error envelope is parsed rather than swallowed.
 *
 * The write-retry test is the one that matters most: a retried send-message
 * would post the same user turn twice, and the duplicate would be
 * indistinguishable from the operator having said it again.
 */

import { describe, it, expect, vi } from "vitest";
import { createWorkspaceClient } from "./client";

const BASE = "http://localhost:8787";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function client(fetchImpl: typeof fetch) {
  return createWorkspaceClient({ baseUrl: BASE, fetchImpl, timeoutMs: 500 });
}

describe("workspace client — request shape", () => {
  it("passes the project on every scoped read", async () => {
    const calls: string[] = [];
    const f = vi.fn(async (url: string | URL) => {
      calls.push(String(url));
      return jsonResponse([]);
    }) as unknown as typeof fetch;

    const c = client(f);
    await c.listConversations("sourcingbot");
    await c.getContext("sourcingbot");
    await c.getConversation("sourcingbot", "CONV-0001");

    expect(calls[0]).toContain("project=sourcingbot");
    expect(calls[1]).toContain("/workspace/context/sourcingbot");
    expect(calls[2]).toContain("project=sourcingbot");
  });

  it("encodes a project name that would otherwise break the URL", async () => {
    const calls: string[] = [];
    const f = vi.fn(async (url: string | URL) => {
      calls.push(String(url));
      return jsonResponse([]);
    }) as unknown as typeof fetch;

    await client(f).listConversations("a project/../other");
    expect(calls[0]).not.toContain("/../");
    expect(calls[0]).toContain("a%20project%2F..%2Fother");
  });

  it("sends the project in the body on writes", async () => {
    let sent: Record<string, unknown> = {};
    const f = vi.fn(async (_url: string | URL, init?: RequestInit) => {
      sent = JSON.parse(String(init?.body));
      return jsonResponse({ id: "CONV-0001" }, 201);
    }) as unknown as typeof fetch;

    await client(f).createConversation("cue-app", "Planning");
    expect(sent).toEqual({ project: "cue-app", title: "Planning" });
  });
});

describe("workspace client — transport policy", () => {
  it("does not retry a write", async () => {
    let attempts = 0;
    const f = vi.fn(async () => {
      attempts++;
      throw new Error("network down");
    }) as unknown as typeof fetch;

    const result = await client(f).sendMessage("alpha", "CONV-0001", "hello");
    expect(attempts).toBe(1);
    expect(result.ok).toBe(false);
  });

  it("retries a read on a transient failure", async () => {
    let attempts = 0;
    const f = vi.fn(async () => {
      attempts++;
      if (attempts < 2) throw new Error("flaky");
      return jsonResponse([{ name: "alpha" }]);
    }) as unknown as typeof fetch;

    const result = await client(f).listProjects();
    expect(attempts).toBe(2);
    expect(result.ok).toBe(true);
  });

  it("does not retry a 4xx read — it is an answer, not a blip", async () => {
    let attempts = 0;
    const f = vi.fn(async () => {
      attempts++;
      return jsonResponse({ error: { code: "not-found", message: "no such project" } }, 404);
    }) as unknown as typeof fetch;

    const result = await client(f).getContext("ghost");
    expect(attempts).toBe(1);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error.code).toBe("not-found");
  });

  it("parses the API error envelope", async () => {
    const f = vi.fn(async () =>
      jsonResponse({ error: { code: "bad-request", message: "'project' is required." } }, 400),
    ) as unknown as typeof fetch;

    const result = await client(f).createConversation("", "");
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.code).toBe("bad-request");
      expect(result.error.message).toContain("required");
    }
  });

  it("degrades to a structured error when the body is not the envelope", async () => {
    const f = vi.fn(
      async () => new Response("<html>gateway</html>", { status: 502 }),
    ) as unknown as typeof fetch;

    const result = await client(f).listProjects();
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error.code).toBe("http-error");
  });
});

describe("workspace client — failed turns", () => {
  it("returns a recorded provider failure as data, not as an error", async () => {
    // The API returns 200 with an assistant message carrying `error`: the user's
    // message persisted and the turn is retryable, so the client must surface the
    // conversation rather than an error envelope.
    const f = vi.fn(async () =>
      jsonResponse({
        conversation: { id: "CONV-0001", messages: [] },
        user_message: { id: "MSG-0001" },
        assistant_message: { id: "MSG-0002", error: "provider unavailable", content: "" },
        context: null,
      }),
    ) as unknown as typeof fetch;

    const result = await client(f).sendMessage("alpha", "CONV-0001", "hi");
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data.assistant_message.error).toBe("provider unavailable");
    }
  });
});
