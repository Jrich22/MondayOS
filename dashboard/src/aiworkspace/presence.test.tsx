/**
 * @vitest-environment jsdom
 *
 * Monday's presence — operational states and the return brief.
 *
 * The theme running through these tests is that the interface must not claim
 * anything it cannot source. A status rail that shows a stale task, or a brief
 * that reports work that did not happen, is worse than one that shows a dash:
 * a display the operator learns to distrust is a display they stop reading.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { createElement } from "react";
import { ACTIVITY, activityFromEvent, brainFor, labelFor } from "./mondayState";
import { ReturnBrief } from "./ReturnBrief";
import type { Briefing } from "./types";

afterEach(cleanup);

const BRIEFING: Briefing = {
  greeting: "Good evening.",
  project: "mondayos",
  conversation_id: "CONV-0004",
  conversation_title: "AI Workspace polish",
  last_active: new Date(Date.now() - 5 * 3600_000).toISOString(),
  away_hours: 5,
  is_return: true,
  active_task: { id: "TASK-0074", title: "Make Monday feel alive", status: "in-progress", priority: "P1" },
  last_completed: { id: "TASK-0073", title: "Interactive AI Workspace", status: "completed", priority: "P2" },
  recent_commits: [
    "deab6b4 Merge pull request #39 from Jrich22/feat/ai-workspace-interactive",
    "6f8c669 MondayOS: document interactive AI Workspace",
  ],
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

describe("operational states", () => {
  it("maps every activity to a Brain mood and a label", () => {
    for (const key of Object.keys(ACTIVITY) as (keyof typeof ACTIVITY)[]) {
      expect(labelFor(key)).toBeTruthy();
      expect(brainFor(key)).toBeTruthy();
    }
  });

  it("keeps the Brain vocabulary smaller than the activity vocabulary", () => {
    // Eleven bespoke animations would be spectacle. The label carries the
    // specificity; the animation carries the mood.
    const activities = Object.keys(ACTIVITY).length;
    const moods = new Set(Object.values(ACTIVITY).map((a) => a.brain)).size;
    expect(activities).toBeGreaterThan(moods);
  });

  it("does not report 'Blocked' as an operational state", () => {
    const labels = Object.values(ACTIVITY).map((a) => a.label);
    expect(labels).not.toContain("Blocked");
    expect(labels).toContain("Reading project");
    expect(labels).toContain("Searching knowledge");
    expect(labels).toContain("Streaming response");
  });

  it("derives activity from real events and falls back to idle", () => {
    expect(activityFromEvent("context", true)).toBe("loading-context");
    expect(activityFromEvent("knowledge", true)).toBe("searching-knowledge");
    expect(activityFromEvent("task", true)).toBe("creating-task");
    expect(activityFromEvent("provider", true)).toBe("thinking");
    // An unrecognised kind must not invent a plausible-looking state.
    expect(activityFromEvent("something-new", true)).toBe("idle");
    expect(activityFromEvent("provider", false)).toBe("error");
  });

  it("marks only in-flight states as live", () => {
    expect(ACTIVITY.idle.live).toBe(false);
    expect(ACTIVITY["waiting-approval"].live).toBe(false);
    expect(ACTIVITY.streaming.live).toBe(true);
    expect(ACTIVITY.thinking.live).toBe(true);
  });
});

describe("return brief", () => {
  it("reports merged PRs derived from the real commit log", () => {
    render(createElement(ReturnBrief, { briefing: BRIEFING, onContinue: vi.fn() }));
    expect(screen.getByText(/PR #39 merged/)).toBeTruthy();
    expect(screen.getByText("While you were away")).toBeTruthy();
  });

  it("reports the last completed task", () => {
    render(createElement(ReturnBrief, { briefing: BRIEFING, onContinue: vi.fn() }));
    expect(screen.getByText(/TASK-0073 · Interactive AI Workspace/)).toBeTruthy();
  });

  it("omits the away section entirely when there is nothing real to report", () => {
    const bare: Briefing = { ...BRIEFING, recent_commits: [], last_completed: null };
    render(createElement(ReturnBrief, { briefing: bare, onContinue: vi.fn() }));
    // An empty section headed with a promise is worse than no section.
    expect(screen.queryByText("While you were away")).toBeNull();
  });

  it("states the basis for its recommendation", () => {
    render(createElement(ReturnBrief, { briefing: BRIEFING, onContinue: vi.fn() }));
    expect(screen.getByText(/already in progress/)).toBeTruthy();
  });

  it("offers no Continue button when there is nothing to continue", () => {
    const nothing: Briefing = {
      ...BRIEFING,
      can_continue: false,
      project: "",
      conversation_id: "",
      notes: ["No conversations recorded yet."],
    };
    render(createElement(ReturnBrief, { briefing: nothing, onContinue: vi.fn() }));
    expect(screen.queryByText("Continue working")).toBeNull();
    expect(screen.getByText("No conversations recorded yet.")).toBeTruthy();
  });

  it("suggests only openers the state can actually answer", () => {
    const onSuggest = vi.fn();
    const bare: Briefing = {
      ...BRIEFING,
      next_step: null,
      recent_commits: [],
      open_task_count: 0,
    };
    render(createElement(ReturnBrief, { briefing: bare, onContinue: vi.fn(), onSuggest }));
    expect(screen.queryByText("What should we do next?")).toBeNull();
    expect(screen.queryByText("What did we build last?")).toBeNull();
  });

  it("sends a suggestion when one is offered and clicked", () => {
    const onSuggest = vi.fn();
    render(createElement(ReturnBrief, { briefing: BRIEFING, onContinue: vi.fn(), onSuggest }));
    fireEvent.click(screen.getByText("What should we do next?"));
    expect(onSuggest).toHaveBeenCalledWith("What should we do next?");
  });

  it("shows a loading state rather than an empty screen before the brief lands", () => {
    render(createElement(ReturnBrief, { briefing: null, onContinue: vi.fn() }));
    expect(screen.getByText(/Reading MondayOS state/)).toBeTruthy();
  });
});
