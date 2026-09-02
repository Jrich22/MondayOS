/**
 * Streaming transport tests.
 *
 * The lifecycle that matters is the one nobody sees when it works: a stopped
 * stream must abort the request (so the server persists the partial), and a
 * frame split across two network reads must not be dropped or half-parsed.
 */

import { describe, it, expect, vi } from "vitest";
import { createWorkspaceClient } from "./client";
import type { StreamEvent } from "./types";

const BASE = "http://localhost:8787";

/** Build an SSE response body from frames, optionally split at arbitrary points. */
function sseResponse(frames: string[], chunkAt?: number[]): Response {
  const text = frames.join("");
  const encoder = new TextEncoder();
  const pieces: Uint8Array[] = [];
  if (chunkAt?.length) {
    let last = 0;
    for (const at of chunkAt) {
      pieces.push(encoder.encode(text.slice(last, at)));
      last = at;
    }
    pieces.push(encoder.encode(text.slice(last)));
  } else {
    pieces.push(encoder.encode(text));
  }

  let i = 0;
  const stream = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i < pieces.length) controller.enqueue(pieces[i++]);
      else controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

const frame = (type: string, data: unknown) =>
  `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;

async function collect(iterable: AsyncIterable<StreamEvent>): Promise<StreamEvent[]> {
  const out: StreamEvent[] = [];
  for await (const e of iterable) out.push(e);
  return out;
}

describe("streamMessage", () => {
  it("yields typed events in order", async () => {
    const body = [
      frame("user", { type: "user", message: { id: "MSG-0001" } }),
      frame("context", { type: "context", context: { id: "CTX-1" } }),
      frame("delta", { type: "delta", text: "hello " }),
      frame("delta", { type: "delta", text: "world" }),
      frame("done", { type: "done", message: { id: "MSG-0002" }, conversation: {} }),
    ];
    const f = vi.fn(async () => sseResponse(body)) as unknown as typeof fetch;
    const { events } = createWorkspaceClient({ baseUrl: BASE, fetchImpl: f }).streamMessage(
      "alpha",
      "CONV-0001",
      "hi",
    );
    const received = await collect(events);
    expect(received.map((e) => e.type)).toEqual(["user", "context", "delta", "delta", "done"]);
  });

  it("reassembles a frame split across network reads", async () => {
    // The failure this guards: a naive split on "\n\n" per read drops the
    // trailing partial frame, losing a delta the user already waited for.
    const body = [
      frame("delta", { type: "delta", text: "first" }),
      frame("delta", { type: "delta", text: "second" }),
    ];
    const full = body.join("");
    const f = vi.fn(async () =>
      sseResponse(body, [10, Math.floor(full.length / 2), full.length - 4]),
    ) as unknown as typeof fetch;

    const { events } = createWorkspaceClient({ baseUrl: BASE, fetchImpl: f }).streamMessage(
      "alpha",
      "CONV-0001",
      "hi",
    );
    const received = await collect(events);
    expect(received).toHaveLength(2);
    expect(received.map((e) => (e.type === "delta" ? e.text : ""))).toEqual(["first", "second"]);
  });

  it("stop aborts the request so the server can persist the partial", async () => {
    let signal: AbortSignal | undefined;
    const f = vi.fn(async (_url: string | URL, init?: RequestInit) => {
      signal = init?.signal ?? undefined;
      return sseResponse([frame("delta", { type: "delta", text: "partial" })]);
    }) as unknown as typeof fetch;

    const { events, stop } = createWorkspaceClient({
      baseUrl: BASE,
      fetchImpl: f,
    }).streamMessage("alpha", "CONV-0001", "hi");

    await collect(events);
    expect(signal?.aborted).toBe(false);
    stop();
    expect(signal?.aborted).toBe(true);
  });

  it("an abort before the response arrives ends quietly, not as an error", async () => {
    const f = vi.fn(async (_url: string | URL, init?: RequestInit) => {
      (init?.signal as AbortSignal & { throwIfAborted?: () => void })?.throwIfAborted?.();
      throw new DOMException("aborted", "AbortError");
    }) as unknown as typeof fetch;

    const client = createWorkspaceClient({ baseUrl: BASE, fetchImpl: f });
    const { events, stop } = client.streamMessage("alpha", "CONV-0001", "hi");
    stop();
    // Stopping is a normal outcome. Surfacing it as an error would put a red
    // banner in front of the user for doing exactly what the button offers.
    expect(await collect(events)).toEqual([]);
  });

  it("surfaces a non-2xx as a structured error event", async () => {
    const f = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: { code: "not-found", message: "gone" } }), {
          status: 404,
          headers: { "Content-Type": "application/json" },
        }),
    ) as unknown as typeof fetch;

    const { events } = createWorkspaceClient({ baseUrl: BASE, fetchImpl: f }).streamMessage(
      "alpha",
      "CONV-0001",
      "hi",
    );
    const received = await collect(events);
    expect(received).toHaveLength(1);
    expect(received[0]).toMatchObject({ type: "error", code: "not-found" });
  });

  it("ignores heartbeat comments and unparsable frames", async () => {
    const body = [
      ": heartbeat\n\n",
      "event: delta\ndata: {not json}\n\n",
      frame("delta", { type: "delta", text: "real" }),
    ];
    const f = vi.fn(async () => sseResponse(body)) as unknown as typeof fetch;
    const { events } = createWorkspaceClient({ baseUrl: BASE, fetchImpl: f }).streamMessage(
      "alpha",
      "CONV-0001",
      "hi",
    );
    const received = await collect(events);
    expect(received).toHaveLength(1);
    expect(received[0]).toMatchObject({ type: "delta", text: "real" });
  });

  it("sends the project and content in the request body", async () => {
    let sent: Record<string, unknown> = {};
    const f = vi.fn(async (_url: string | URL, init?: RequestInit) => {
      sent = JSON.parse(String(init?.body));
      return sseResponse([]);
    }) as unknown as typeof fetch;

    const { events } = createWorkspaceClient({ baseUrl: BASE, fetchImpl: f }).streamMessage(
      "growth-bot",
      "CONV-0009",
      "what shipped?",
    );
    await collect(events);
    expect(sent).toEqual({ project: "growth-bot", content: "what shipped?" });
  });
});
