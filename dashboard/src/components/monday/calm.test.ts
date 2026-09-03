/**
 * Regression guards for the Brain's calm.
 *
 * The first version of this visualisation read as a flickering bulb, and the
 * causes were all numbers in `STATE_TARGETS` and the tier table rather than
 * anything structural. Numbers drift. These tests pin the constraints that make
 * the difference, so a future tweak that reintroduces the strobe fails here
 * rather than in someone's eyes.
 *
 * They deliberately assert *bounds*, not exact values — the mood is free to be
 * retuned, the calm is not.
 */

import { describe, it, expect } from "vitest";
import { STATE_TARGETS, STATE_LABELS, type BrainState } from "./brainState";
import { TIERS } from "./brainGeometry";

const STATES = Object.keys(STATE_TARGETS) as BrainState[];

describe("brain calm constraints", () => {
  it("keeps brightness in a narrow band across every state", () => {
    // State is communicated by motion and hue, which the eye reads
    // peripherally — not by luminance, which it cannot ignore. A wide band is
    // what let the brain flash between states.
    const values = STATES.map((s) => STATE_TARGETS[s].brightness);
    expect(Math.min(...values)).toBeGreaterThanOrEqual(0.55);
    expect(Math.max(...values)).toBeLessThanOrEqual(0.85);
    expect(Math.max(...values) - Math.min(...values)).toBeLessThanOrEqual(0.25);
  });

  it("never lets a state pulse fast enough to read as a warning light", () => {
    for (const state of STATES) {
      expect(STATE_TARGETS[state].pulseSpeed, `${state} pulseSpeed`).toBeLessThanOrEqual(1.0);
    }
  });

  it("keeps idle genuinely at rest", () => {
    const idle = STATE_TARGETS.idle;
    expect(idle.pulseSpeed).toBeLessThanOrEqual(0.2);
    expect(idle.haloSpeed).toBeLessThanOrEqual(0.2);
    expect(idle.streamIn).toBe(0);
    expect(idle.streamOut).toBe(0);
    // Idle is the state the interface sits in almost all the time, so it must
    // be the dimmest thing on screen.
    for (const state of STATES) {
      expect(idle.brightness).toBeLessThanOrEqual(STATE_TARGETS[state].brightness);
    }
  });

  it("keeps the core glow from washing out adjacent text", () => {
    for (const state of STATES) {
      expect(STATE_TARGETS[state].coreIntensity, `${state} coreIntensity`).toBeLessThanOrEqual(1.0);
      expect(STATE_TARGETS[state].bloom, `${state} bloom`).toBeLessThanOrEqual(0.8);
      // Per-state bloom is a multiplier on an already-small pass; the pass
      // itself is capped in BrainScene.

    }
  });

  it("settles an error rather than holding it lit", () => {
    // Blocked must not be the brightest or busiest state: a permanently loud
    // error is one the operator learns to ignore.
    expect(STATE_TARGETS.blocked.brightness).toBeLessThanOrEqual(STATE_TARGETS.executing.brightness);
    expect(STATE_TARGETS.blocked.distort).toBeLessThanOrEqual(0.4);
  });

  it("keeps awaiting patient rather than nagging", () => {
    expect(STATE_TARGETS.awaiting.pulseSpeed).toBeLessThanOrEqual(0.3);
    expect(STATE_TARGETS.awaiting.approvalRing).toBeLessThanOrEqual(0.7);
  });

  it("has a label for every state", () => {
    for (const state of STATES) expect(STATE_LABELS[state]).toBeTruthy();
  });
});

describe("particle budget", () => {
  it("keeps density at atmosphere level, not object level", () => {
    // ~2.5% of the original budget. At ~22px a dense cloud resolves to a solid
    // glowing dot: the density bought brightness, not detail.
    expect(TIERS.high.surface).toBeLessThanOrEqual(250);
    expect(TIERS.high.halo).toBeLessThanOrEqual(150);
    expect(TIERS.high.nodes).toBeLessThanOrEqual(16);
  });

  it("orders the tiers so lower is always cheaper", () => {
    for (const key of ["surface", "volume", "core", "nodes", "halo"] as const) {
      expect(TIERS.high[key]).toBeGreaterThan(TIERS.mid[key]);
      expect(TIERS.mid[key]).toBeGreaterThan(TIERS.low[key]);
    }
  });

  it("keeps edge count down, since edges carry the pulses", () => {
    // Every node is an edge endpoint, and the travelling pulses along those
    // edges were what made the surface shimmer.
    expect(TIERS.high.nodes).toBeLessThan(TIERS.high.surface / 10);
  });
});
