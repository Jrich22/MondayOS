/**
 * @vitest-environment jsdom
 *
 * Composer behaviour — the slash menu, Stop, and the context indicator.
 *
 * The context indicator is tested because it is a correctness surface, not
 * decoration: it tells the operator which project their question is about to be
 * answered against, and being wrong there is how someone asks sourcingBOT a
 * question about Growth Bot.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { createElement } from "react";
import { Composer } from "./Composer";

afterEach(cleanup);

function setup(overrides: Partial<Parameters<typeof Composer>[0]> = {}) {
  const props = {
    onSend: vi.fn(),
    onCommand: vi.fn(),
    onStop: vi.fn(),
    sending: false,
    project: "sourcingbot",
    contextLoaded: true,
    provider: "anthropic",
    disabled: false,
    ...overrides,
  };
  render(createElement(Composer, props));
  return props;
}

const box = () => screen.getByRole("textbox");

describe("composer", () => {
  it("states the loaded project and provider", () => {
    setup();
    expect(screen.getByText("sourcingbot")).toBeTruthy();
    expect(screen.getByText("Context loaded")).toBeTruthy();
    expect(screen.getByText("anthropic")).toBeTruthy();
  });

  it("warns when no context is loaded", () => {
    setup({ contextLoaded: false });
    expect(screen.getByText("No context")).toBeTruthy();
  });

  it("sends on Enter and clears the draft", () => {
    const props = setup();
    fireEvent.change(box(), { target: { value: "what shipped?" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    expect(props.onSend).toHaveBeenCalledWith("what shipped?");
    expect((box() as HTMLTextAreaElement).value).toBe("");
  });

  it("does not send on Shift+Enter", () => {
    const props = setup();
    fireEvent.change(box(), { target: { value: "line one" } });
    fireEvent.keyDown(box(), { key: "Enter", shiftKey: true });
    expect(props.onSend).not.toHaveBeenCalled();
  });

  it("opens the slash menu and filters as you type", () => {
    setup();
    fireEvent.change(box(), { target: { value: "/" } });
    expect(screen.getByText("/status")).toBeTruthy();
    fireEvent.change(box(), { target: { value: "/co" } });
    expect(screen.getByText("/continue")).toBeTruthy();
    expect(screen.queryByText("/status")).toBeNull();
  });

  it("closes the slash menu on Escape", () => {
    setup();
    fireEvent.change(box(), { target: { value: "/" } });
    fireEvent.keyDown(box(), { key: "Escape" });
    expect(screen.queryByText("/status")).toBeNull();
  });

  it("dispatches a command rather than a message", () => {
    const props = setup();
    fireEvent.change(box(), { target: { value: "/switch growth-bot" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    expect(props.onCommand).toHaveBeenCalledWith("switch", "growth-bot");
    expect(props.onSend).not.toHaveBeenCalled();
  });

  it("shows Stop while sending, and Escape stops", () => {
    const props = setup({ sending: true });
    expect(screen.getByText("Stop")).toBeTruthy();
    expect(screen.queryByText("Send")).toBeNull();
    fireEvent.keyDown(box(), { key: "Escape" });
    expect(props.onStop).toHaveBeenCalled();
  });

  it("recalls the previous input with Up from an empty composer", () => {
    setup();
    fireEvent.change(box(), { target: { value: "first question" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    fireEvent.keyDown(box(), { key: "ArrowUp" });
    expect((box() as HTMLTextAreaElement).value).toBe("first question");
  });

  it("leaves a non-empty draft alone on Up", () => {
    setup();
    fireEvent.change(box(), { target: { value: "sent" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    fireEvent.change(box(), { target: { value: "mid-edit" } });
    fireEvent.keyDown(box(), { key: "ArrowUp" });
    expect((box() as HTMLTextAreaElement).value).toBe("mid-edit");
  });
});
