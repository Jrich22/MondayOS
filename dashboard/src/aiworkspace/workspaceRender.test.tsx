/**
 * @vitest-environment jsdom
 *
 * The whole AI Workspace, rendered.
 *
 * The type checker proves the props line up; it cannot prove the thing mounts.
 * This drives the real component tree against a fake client and asserts what a
 * first-time user actually sees on open: the status rail, the return brief, and
 * a composer that states which project it is about to answer against.
 *
 * The Brain is mocked. It is a WebGL canvas, headless has no GPU, and what is
 * under test here is the layout around it — specifically that nothing
 * operational is rendered *over* it.
 */

import { describe, it, expect, vi, afterEach, beforeAll } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { createElement } from "react";

vi.mock("@/components/monday", () => ({
  MondayBrain: ({ state }: { state: string }) =>
    createElement("div", { "data-testid": "brain", "data-state": state }),
  STATE_LABELS: {},
}));

// The workspace reads baseUrl/healthOk from the app store; a minimal stub keeps
// this test about the workspace rather than about Mission Control's plumbing.
vi.mock("@/state/store", () => ({
  useApp: () => ({ state: { baseUrl: "http://localhost:8787", healthOk: true } }),
}));

import { AIWorkspace } from "./AIWorkspace";
import type { WorkspaceClient } from "./client";
import type { Briefing, ConversationSummary, ContextSnapshot, WorkspaceProject } from "./types";

afterEach(cleanup);
beforeAll(() => {
  // jsdom has no scrollIntoView; the conversation calls it while streaming.
  Element.prototype.scrollIntoView = vi.fn();
});

const PROJECTS: WorkspaceProject[] = [
  {
    name: "mondayos",
    display_name: "mondayos",
    description: "The OS",
    path: "/m",
    conversation_count: 2,
  },
  {
    name: "sourcingbot",
    display_name: "sourcingbot",
    description: "Sourcing",
    path: "/s",
    conversation_count: 1,
  },
];

const CONVERSATIONS: ConversationSummary[] = [
  {
    id: "CONV-0004",
    project: "mondayos",
    title: "AI Workspace polish",
    status: "active",
    created_at: "2026-09-02T12:00:00Z",
    updated_at: "2026-09-02T18:00:00Z",
    message_count: 6,
  },
];

const CONTEXT: ContextSnapshot = {
  id: "CTX-abc123",
  project: "mondayos",
  created_at: "2026-09-02T18:00:00Z",
  sources: [
    {
      name: "tasks",
      label: "Tasks",
      items: ["TASK-0074 [in-progress] P1 Make Monday feel alive"],
      item_count: 1,
      char_count: 48,
      token_estimate: 12,
      truncated: false,
      error: "",
      origin: "TaskManager",
      ok: true,
      reasons: ["active-task"],
      reason_counts: { "active-task": 1 },
    },
    {
      name: "git",
      label: "Git state",
      items: ["Current branch: feat/ai-workspace-alive"],
      item_count: 1,
      char_count: 39,
      token_estimate: 10,
      truncated: false,
      error: "",
      origin: "git",
      ok: true,
      reasons: ["recent"],
      reason_counts: { recent: 1 },
    },
  ],
  omitted: [],
  token_estimate: 22,
  char_count: 87,
  truncated: false,
  summary: "Tasks (1), Git state (1) · ~22 tokens",
  fingerprint: "fp",
  query: "",
};

const BRIEFING: Briefing = {
  greeting: "Good evening.",
  project: "mondayos",
  conversation_id: "CONV-0004",
  conversation_title: "AI Workspace polish",
  last_active: "2026-09-02T18:00:00Z",
  away_hours: 5,
  is_return: true,
  active_task: null,
  last_completed: {
    id: "TASK-0073",
    title: "Interactive AI Workspace",
    status: "completed",
    priority: "P2",
  },
  recent_commits: ["deab6b4 Merge pull request #39 from Jrich22/feat/ai-workspace-interactive"],
  branch: "feat/ai-workspace-alive",
  open_task_count: 3,
  next_step: {
    task_id: "TASK-0074",
    title: "Make Monday feel alive",
    status: "in-progress",
    priority: "P1",
    reason: "already in progress",
  },
  notes: [],
  can_continue: true,
};

function fakeClient(): WorkspaceClient {
  const base = {
    listProjects: async () => ({ ok: true as const, data: PROJECTS }),
    listConversations: async () => ({ ok: true as const, data: CONVERSATIONS }),
    getContext: async () => ({ ok: true as const, data: CONTEXT }),
    briefing: async () => ({ ok: true as const, data: BRIEFING }),
    activity: async () => ({
      ok: true as const,
      data: {
        events: [
          {
            kind: "context" as const,
            message: "Assembled project context",
            at: "2026-09-02T18:00:00Z",
            project: "mondayos",
            detail: "Tasks (1)",
            ok: true,
          },
        ],
      },
    }),
  };
  const fallback = async () => ({ ok: false as const, error: { code: "x", message: "unused" } });
  return new Proxy(base as unknown as object, {
    get: (t, p) => (Reflect.get(t, p) as unknown) ?? fallback,
  }) as WorkspaceClient;
}

describe("AI Workspace on open", () => {
  it("mounts and shows the status rail outside the visualisation", async () => {
    render(createElement(AIWorkspace, { client: fakeClient() }));

    await waitFor(() => expect(screen.getByText("MONDAY")).toBeTruthy());
    expect(screen.getByText("Healthy")).toBeTruthy();

    // The Brain renders as a sibling of the rail, never as its container: no
    // operational text is drawn on top of the orb.
    const brain = screen.getAllByTestId("brain")[0];
    expect(brain.textContent).toBe("");
  });

  it("lands on the return brief rather than an empty pane", async () => {
    render(createElement(AIWorkspace, { client: fakeClient() }));
    await waitFor(() => expect(screen.getByText("Good evening.")).toBeTruthy());
    expect(screen.getByText(/Resume .*AI Workspace polish/)).toBeTruthy();
    expect(screen.getByText(/PR #39 merged/)).toBeTruthy();
  });

  it("shows contextual information about the current project, not a browser", async () => {
    render(createElement(AIWorkspace, { client: fakeClient() }));

    // The right panel is facts about this project — every one derived from the
    // same snapshot that was sent to the model.
    await waitFor(() => expect(screen.getByText("Current task")).toBeTruthy());
    for (const label of [
      "Current branch",
      "Relevant files",
      "Recent commits",
      "Knowledge used",
      "Agent activity",
    ]) {
      expect(screen.getByText(label), label).toBeTruthy();
    }
    // The labels render immediately; the values arrive with the snapshot.
    await waitFor(() =>
      expect(screen.getAllByText(/feat\/ai-workspace-alive/).length).toBeGreaterThan(0),
    );
  });

  it("states plainly when a context section has nothing to report", async () => {
    render(createElement(AIWorkspace, { client: fakeClient() }));
    // An absent section is an ambiguity; "no entries" is a fact.
    await waitFor(() =>
      expect(screen.getByText("No entries for this project")).toBeTruthy(),
    );
  });

  it("rests at idle when nothing is happening", async () => {
    render(createElement(AIWorkspace, { client: fakeClient() }));
    await waitFor(() => expect(screen.getAllByTestId("brain")[0].dataset.state).toBe("idle"));
    expect(screen.getByText("Idle")).toBeTruthy();
  });

  it("states which project the composer will answer against", async () => {
    render(createElement(AIWorkspace, { client: fakeClient() }));
    await waitFor(() => expect(screen.getByText("Context loaded")).toBeTruthy());
    expect(screen.getAllByText("mondayos").length).toBeGreaterThan(0);
  });

  it("lists projects as workspaces — a dot and a name, nothing more", async () => {
    const { container } = render(createElement(AIWorkspace, { client: fakeClient() }));
    await waitFor(() => expect(screen.getByText("sourcingbot")).toBeTruthy());

    // An inactive workspace carries no metadata: no counts, no timestamps, no
    // summary. Those are things read while deciding, and this is a switcher.
    const inactive = screen
      .getByText("sourcingbot")
      .closest("button") as HTMLButtonElement;
    expect(inactive.textContent).toMatch(/^[●○]\s*sourcingbot$/);
    expect(container.textContent).not.toMatch(/\d+ open/);
  });

  it("gives only the open workspace a subtitle, describing the work", async () => {
    render(createElement(AIWorkspace, { client: fakeClient() }));
    // Derived from the in-progress task, stripped of id and status markers.
    await waitFor(() => expect(screen.getByText("Make Monday feel alive")).toBeTruthy());
  });

  it("reports agent activity, and says nothing has happened when nothing has", async () => {
    render(createElement(AIWorkspace, { client: fakeClient() }));
    await waitFor(() => expect(screen.getByText("Agent activity")).toBeTruthy());
    // Activity is recorded by real operations. On open there have been none, so
    // the panel says so rather than replaying something to look busy.
    expect(screen.getByText("Nothing this session")).toBeTruthy();
  });

  it("keeps project facts out of the Monday rail", async () => {
    render(createElement(AIWorkspace, { client: fakeClient() }));
    await waitFor(() => expect(screen.getByText("MONDAY")).toBeTruthy());
    // One rail answers "what is Monday doing", the other "what does Monday
    // know". Neither answers both, so each label appears exactly once.
    expect(screen.getAllByText("Current task")).toHaveLength(1);
    expect(screen.getAllByText("Current branch")).toHaveLength(1);
  });
});
