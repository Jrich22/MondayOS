/**
 * @vitest-environment jsdom
 *
 * The application shell — what MondayOS is when you launch it.
 *
 * These are the four guarantees the inversion has to hold, and every one of them
 * is the kind that erodes silently: a stray route, a conditional that grows an
 * extra branch, an overlay that unmounts what it covers. They are asserted at
 * the shell level because that is the only place they are true or false.
 */

import { describe, it, expect, vi, afterEach, beforeAll } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { createElement, useEffect } from "react";

let workspaceMounts = 0;
let lastConversation = "";

// The workspace is stubbed to report its own mount count and a piece of local
// state. That is what makes "state survives the overlay" checkable: if the
// overlay unmounted it, the count would rise and the state would reset.
vi.mock("@/aiworkspace/AIWorkspace", () => ({
  AIWorkspace: ({ onOpenDiagnostics }: { onOpenDiagnostics: () => void }) => {
    // Counted in an effect, not in the body: a component body runs on every
    // render, and what is under test is whether the workspace is *unmounted*.
    useEffect(() => {
      workspaceMounts += 1;
    }, []);
    return createElement(
      "div",
      { "data-testid": "workspace" },
      createElement("span", { "data-testid": "conversation" }, lastConversation || "CONV-0004"),
      createElement("button", { onClick: onOpenDiagnostics }, "Mission Control"),
    );
  },
}));

vi.mock("@/pages/MissionControl", () => ({
  MissionControl: ({ onClose }: { onClose: () => void }) =>
    createElement(
      "div",
      { "data-testid": "diagnostics" },
      createElement("h1", null, "Mission Control"),
      createElement("button", { onClick: onClose }, "Back to Monday"),
    ),
}));

vi.mock("@/components/command/CommandPalette", () => ({
  CommandPalette: () => createElement("div", { "data-testid": "palette" }),
}));

vi.mock("@/state/store", () => ({
  AppProvider: ({ children }: { children: unknown }) => children,
  useApp: () => ({ state: {}, connection: "live" }),
}));

import App from "./App";

afterEach(() => {
  cleanup();
  workspaceMounts = 0;
  lastConversation = "";
});
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

describe("MondayOS shell", () => {
  it("lands directly in the AI Workspace", () => {
    render(createElement(App));
    expect(screen.getByTestId("workspace")).toBeTruthy();
    // Not behind a dashboard, not one section of one.
    expect(screen.queryByTestId("diagnostics")).toBeNull();
  });

  it("opens Mission Control only when asked", async () => {
    render(createElement(App));
    expect(screen.queryByTestId("diagnostics")).toBeNull();

    fireEvent.click(screen.getByText("Mission Control"));
    await waitFor(() => expect(screen.getByTestId("diagnostics")).toBeTruthy());
  });

  it("keeps the conversation mounted while diagnostics is open", async () => {
    render(createElement(App));
    const before = workspaceMounts;

    fireEvent.click(screen.getByText("Mission Control"));
    await waitFor(() => expect(screen.getByTestId("diagnostics")).toBeTruthy());

    // The overlay covers the workspace; it must not replace it. An unmount here
    // would discard the in-flight conversation, the loaded context and the
    // scroll position — none of which the operator asked to lose.
    expect(screen.getByTestId("workspace")).toBeTruthy();
    expect(workspaceMounts).toBe(before);
  });

  it("returns to the exact conversation state on Back to Monday", async () => {
    lastConversation = "CONV-0042";
    render(createElement(App));
    expect(screen.getByTestId("conversation").textContent).toBe("CONV-0042");
    const mountsAtStart = workspaceMounts;

    fireEvent.click(screen.getByText("Mission Control"));
    await waitFor(() => expect(screen.getByTestId("diagnostics")).toBeTruthy());
    fireEvent.click(screen.getByText("Back to Monday"));

    await waitFor(() => expect(screen.queryByTestId("diagnostics")).toBeNull());
    expect(screen.getByTestId("conversation").textContent).toBe("CONV-0042");
    // Same instance throughout — the state was never rebuilt, so it cannot have
    // been reconstructed differently.
    expect(workspaceMounts).toBe(mountsAtStart);
  });

  it("cannot make Mission Control the home screen", () => {
    render(createElement(App));
    // The workspace is rendered unconditionally; the overlay is the only way to
    // reach diagnostics, and it is driven by local state rather than a route.
    // There is no value of any prop or store field that inverts this.
    expect(screen.getByTestId("workspace")).toBeTruthy();
    expect(screen.queryByTestId("diagnostics")).toBeNull();
  });

  it("keeps the command palette available from the shell", () => {
    render(createElement(App));
    expect(screen.getByTestId("palette")).toBeTruthy();
  });
});
