/**
 * @vitest-environment jsdom
 *
 * Project switching must be clean.
 *
 * Two failures are asserted against here, and both are the kind that look fine
 * in casual use:
 *
 *   1. One project's conversations remaining on screen under another project's
 *      name after a switch.
 *   2. A slow in-flight response for the project the operator just left landing
 *      after the switch and repainting the new project's sidebar with old data.
 *
 * The second is the reason `useWorkspace` guards responses against the currently
 * active project rather than trusting arrival order.
 */

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, act, cleanup, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { useWorkspace } from "./useWorkspace";
import type { WorkspaceClient } from "./client";
import type { ConversationSummary, WorkspaceProject } from "./types";

afterEach(cleanup);

const PROJECTS: WorkspaceProject[] = [
  { name: "alpha", display_name: "alpha", description: "", path: "/a", conversation_count: 1 },
  { name: "beta", display_name: "beta", description: "", path: "/b", conversation_count: 1 },
];

const CONVERSATIONS: Record<string, ConversationSummary[]> = {
  alpha: [
    {
      id: "CONV-0001",
      project: "alpha",
      title: "ALPHA_TOPIC",
      status: "active",
      created_at: "2026-09-02T12:00:00Z",
      updated_at: "2026-09-02T12:00:00Z",
      message_count: 2,
    },
  ],
  beta: [
    {
      id: "CONV-0001",
      project: "beta",
      title: "BETA_TOPIC",
      status: "active",
      created_at: "2026-09-02T12:00:00Z",
      updated_at: "2026-09-02T12:00:00Z",
      message_count: 2,
    },
  ],
};

function snapshot(project: string) {
  return {
    id: `CTX-${project}`,
    project,
    created_at: "2026-09-02T12:00:00Z",
    sources: [],
    omitted: [],
    token_estimate: 0,
    char_count: 0,
    truncated: false,
    summary: "",
  };
}

/** A client whose conversation listing can be held open, to force a late reply. */
function makeClient(hold?: { project: string; release: Promise<void> }): WorkspaceClient {
  const base = {
    listProjects: async () => ({ ok: true as const, data: PROJECTS }),
    listConversations: async (project: string) => {
      if (hold && hold.project === project) await hold.release;
      return { ok: true as const, data: CONVERSATIONS[project] ?? [] };
    },
    getContext: async (project: string) => ({ ok: true as const, data: snapshot(project) }),
  };
  // Only the three methods the hook calls on load are implemented; anything else
  // resolves to a refusal, so an accidental extra call fails loudly in a test
  // rather than returning a plausible empty success.
  const fallback = async () => ({ ok: false as const, error: { code: "x", message: "unused" } });
  return new Proxy(base as unknown as object, {
    get(target, prop) {
      const value = Reflect.get(target, prop) as unknown;
      return value ?? fallback;
    },
  }) as WorkspaceClient;
}

/** A probe component that renders the hook's visible state as text. */
function Probe({ client, onReady }: { client: WorkspaceClient; onReady: (a: unknown) => void }) {
  const [state, actions] = useWorkspace("http://localhost:8787", client);
  onReady(actions);
  return createElement(
    "div",
    null,
    createElement("span", { "data-testid": "project" }, state.project),
    createElement(
      "ul",
      { "data-testid": "conversations" },
      state.conversations.map((c) => createElement("li", { key: c.id }, c.title)),
    ),
    createElement("span", { "data-testid": "context" }, state.context?.id ?? "none"),
  );
}

describe("project switching", () => {
  it("replaces one project's conversations with the other's", async () => {
    let actions: { selectProject(p: string): void } | undefined;
    render(
      createElement(Probe, {
        client: makeClient(),
        onReady: (a) => {
          actions = a as { selectProject(p: string): void };
        },
      }),
    );

    await waitFor(() => expect(screen.getByTestId("project").textContent).toBe("alpha"));
    await waitFor(() => expect(screen.getByText("ALPHA_TOPIC")).toBeTruthy());

    await act(async () => {
      actions?.selectProject("beta");
    });

    await waitFor(() => expect(screen.getByText("BETA_TOPIC")).toBeTruthy());
    expect(screen.queryByText("ALPHA_TOPIC")).toBeNull();
    expect(screen.getByTestId("context").textContent).toBe("CTX-beta");
  });

  it("ignores a response that arrives after the operator has switched away", async () => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    let actions: { selectProject(p: string): void } | undefined;

    render(
      createElement(Probe, {
        client: makeClient({ project: "alpha", release: held }),
        onReady: (a) => {
          actions = a as { selectProject(p: string): void };
        },
      }),
    );

    await waitFor(() => expect(screen.getByTestId("project").textContent).toBe("alpha"));

    // Switch away while alpha's listing is still in flight, then let it land.
    await act(async () => {
      actions?.selectProject("beta");
    });
    await act(async () => {
      release();
      await held;
    });

    await waitFor(() => expect(screen.getByText("BETA_TOPIC")).toBeTruthy());
    // The late alpha response must not have repainted beta's sidebar.
    expect(screen.queryByText("ALPHA_TOPIC")).toBeNull();
    expect(screen.getByTestId("project").textContent).toBe("beta");
  });
});
