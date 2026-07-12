/**
 * @vitest-environment jsdom
 *
 * Regression: LIVE-mode flicker ("failing light bulb").
 *
 * In live mode the store re-renders on every health ping, activity/revision
 * refresh, and SSE tick. If that churn reaches the R3F <Canvas>, the WebGL
 * context + postprocessing pipeline are torn down and rebuilt, which flashes the
 * scene. These tests pin the two guarantees that stop it:
 *
 *   1. A parent re-render that does NOT change the Brain's props must not
 *      re-render (let alone remount) the Canvas subtree — `memo` on MondayBrain.
 *   2. A genuine Brain *state* change updates the scene via a prop, and must not
 *      remount the Canvas — the scene eases the new state, it does not rebuild.
 *
 * The real R3F Canvas and Three scene are mocked so this runs headless: we only
 * assert React's mount/render behavior, which is where the flicker lived.
 */
import { describe, it, expect, beforeAll, afterEach, vi } from "vitest";
import { render, act, cleanup } from "@testing-library/react";
import { useState, useEffect, createElement } from "react";
import type { BrainState } from "./brainState";

// Shared counters, hoisted so the vi.mock factories below can see them.
const spy = vi.hoisted(() => ({ canvasMounts: 0, sceneRenders: 0, lastState: "" as string }));

// Mock the R3F Canvas: count how many times it mounts, render children plainly.
vi.mock("@react-three/fiber", () => ({
  Canvas: ({ children }: { children?: unknown }) => {
    useEffect(() => {
      spy.canvasMounts += 1;
    }, []);
    return createElement("div", { "data-testid": "canvas" }, children as never);
  },
}));

// Mock the Three scene: record every render and the `state` it received.
vi.mock("./BrainScene", () => ({
  BrainScene: (props: { state: BrainState }) => {
    spy.sceneRenders += 1;
    spy.lastState = props.state;
    return createElement("div", { "data-testid": "scene" });
  },
}));

import { MondayBrain } from "./MondayBrain";

// Stable callback identity — an inline `() => {}` would change every render and
// (correctly) defeat memo, which would mask the very thing we're testing.
const noop = () => {};

// Handles the harness exposes so a test can drive re-renders from outside.
let bumpRefresh: () => void = () => {};
let setBrain: (s: BrainState) => void = () => {};

/**
 * Stand-in for BrainStage: `refresh` models an unrelated store snapshot update
 * (activity/health/revision) that re-renders the parent WITHOUT changing the
 * Brain's props; `brain` models a real Brain-state transition.
 */
function Harness() {
  const [, setRefresh] = useState(0);
  const [brain, setBrainState] = useState<BrainState>("thinking");
  bumpRefresh = () => setRefresh((n) => n + 1);
  setBrain = setBrainState;
  return (
    <MondayBrain state={brain} onActivate={noop} className="brain" ariaLabel="Monday" />
  );
}

beforeAll(() => {
  // MondayBrain feature-detects WebGL + reduced motion; jsdom has neither, so
  // stub just enough to take the Canvas path (not the 2D fallback).
  (window as unknown as { WebGLRenderingContext: unknown }).WebGLRenderingContext = function () {};
  HTMLCanvasElement.prototype.getContext = (() => ({})) as never;
  if (!window.matchMedia) {
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      onchange: null,
      dispatchEvent: () => false,
    })) as never;
  }
});

afterEach(() => {
  cleanup();
  spy.canvasMounts = 0;
  spy.sceneRenders = 0;
  spy.lastState = "";
});

describe("MondayBrain — no remount on live refresh", () => {
  it("mounts the Canvas exactly once after WebGL detection", () => {
    render(<Harness />);
    expect(spy.canvasMounts).toBe(1);
    expect(spy.sceneRenders).toBe(1);
    expect(spy.lastState).toBe("thinking");
  });

  it("does not re-render or remount the Canvas when the store refreshes", () => {
    render(<Harness />);
    const rendersAfterMount = spy.sceneRenders;

    // Simulate several live snapshot refreshes (SSE revision / polling / health).
    act(() => bumpRefresh());
    act(() => bumpRefresh());
    act(() => bumpRefresh());

    // memo blocks the churn: the scene never re-rendered and the Canvas is still
    // the same single mount — no teardown, no flash.
    expect(spy.sceneRenders).toBe(rendersAfterMount);
    expect(spy.canvasMounts).toBe(1);
  });

  it("updates the scene on a real state change without remounting the Canvas", () => {
    render(<Harness />);

    act(() => setBrain("executing"));

    // The new state reached the scene as a prop update (so it can ease the
    // transition), and the Canvas was NOT recreated.
    expect(spy.lastState).toBe("executing");
    expect(spy.canvasMounts).toBe(1);
  });
});
