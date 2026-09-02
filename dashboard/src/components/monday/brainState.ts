/**
 * Monday's Brain — state model and visual language.
 *
 * The brain is a small state machine. Each operational state maps to a target
 * set of *visual* parameters (pulse speed, core brightness, halo velocity, rim
 * colour, streaming direction…). The scene lerps the live parameters toward the
 * active state's targets every frame, so transitions read as the brain
 * *reacting* rather than snapping. Keeping the palette + targets here (not
 * scattered through the R3F components) means the whole mood lives in one place.
 *
 * **Calm is the design constraint, not a setting.** These targets were retuned
 * after the first version read as a flickering bulb: brightness swung far enough
 * between states to strobe, and the per-particle breathing phase made the
 * surface shimmer rather than breathe. Two rules now govern every value here.
 *
 * The **brightness band is narrow** — roughly 0.62 to 0.92 across every state.
 * State is communicated by *motion and hue*, which the eye reads peripherally,
 * not by luminance, which it cannot ignore. A brain that gets much brighter when
 * it thinks competes with the text you are trying to read.
 *
 * **Nothing repeats faster than the eye stops noticing it.** Idle breathes on a
 * ~7-second cycle; even executing stays under one visible pulse per second.
 * Anything faster reads as a warning light.
 */

export type BrainState =
  | "idle"
  | "thinking"
  | "executing"
  | "awaiting"
  | "blocked"
  | "completed"
  | "learning";

/** RGB triplet in 0..1 — the form Three.js shader uniforms want. */
export type RGB = [number, number, number];

const hex = (h: string): RGB => {
  const n = parseInt(h.replace("#", ""), 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
};

/** Core palette — cyan / white / violet / magenta, plus semantic accents. */
export const PALETTE = {
  cyan: hex("#22d3ee"),
  white: hex("#eafcff"),
  violet: hex("#8b5cf6"),
  magenta: hex("#d946ef"),
  indigo: hex("#6366f1"),
  amber: hex("#f59e0b"),
  red: hex("#ef4444"),
  green: hex("#34d399"),
} as const;

/**
 * The full set of animated visual parameters. Every field is a plain number or
 * colour so it can be linearly interpolated frame-to-frame.
 */
export interface BrainVisual {
  /** Speed of neural pulses travelling the internal pathways. */
  pulseSpeed: number;
  /** Overall emissive brightness of the particle brain. */
  brightness: number;
  /** Intensity of the bright intelligence core. */
  coreIntensity: number;
  /** How many pathways glow at once (0..1 → thinning of the network). */
  connectivity: number;
  /** Angular velocity of the orbiting halo. */
  haloSpeed: number;
  /** Independent rotation speed of the outer rings. */
  ringSpeed: number;
  /** Breathing / deformation amplitude of the brain volume. */
  breathe: number;
  /** 0..1 — particles streaming inward (learning). */
  streamIn: number;
  /** 0..1 — particles streaming outward toward agents (executing). */
  streamOut: number;
  /** Fresnel rim colour of the glass shell. */
  rim: RGB;
  /** Accent tint washing across the scene for the current mood. */
  accent: RGB;
  /** Strength of the shell's energy sweep. */
  sweep: number;
  /** Restrained distortion pulse (blocked). */
  distort: number;
  /** Expanding completion wave amount (0 idle → 1 mid-wave). */
  wave: number;
  /** Amber approval ring opacity. */
  approvalRing: number;
  /** Bloom intensity target. */
  bloom: number;
}

/** Per-state visual targets. Everything the scene lerps toward. */
export const STATE_TARGETS: Record<BrainState, BrainVisual> = {
  idle: {
    pulseSpeed: 0.16,
    brightness: 0.62,
    coreIntensity: 0.55,
    connectivity: 0.32,
    haloSpeed: 0.16,
    ringSpeed: 0.14,
    breathe: 0.85,
    streamIn: 0,
    streamOut: 0,
    rim: PALETTE.cyan,
    accent: PALETTE.cyan,
    sweep: 0.14,
    distort: 0,
    wave: 0,
    approvalRing: 0,
    bloom: 0.45,
  },
  thinking: {
    // Faster orbit and denser pathways, barely brighter. Computation reads as
    // movement; making it glow would put it in competition with the answer
    // being streamed beside it.
    pulseSpeed: 0.62,
    brightness: 0.72,
    coreIntensity: 0.85,
    connectivity: 0.72,
    haloSpeed: 0.4,
    ringSpeed: 0.3,
    breathe: 1.0,
    streamIn: 0.08,
    streamOut: 0,
    rim: PALETTE.violet,
    accent: PALETTE.violet,
    sweep: 0.28,
    distort: 0,
    wave: 0,
    approvalRing: 0,
    bloom: 0.62,
  },
  executing: {
    pulseSpeed: 0.9,
    brightness: 0.8,
    coreIntensity: 0.9,
    connectivity: 0.66,
    haloSpeed: 0.72,
    ringSpeed: 0.5,
    breathe: 0.9,
    streamIn: 0,
    streamOut: 0.55,
    rim: PALETTE.cyan,
    accent: PALETTE.indigo,
    sweep: 0.4,
    distort: 0,
    wave: 0,
    approvalRing: 0,
    bloom: 0.7,
  },
  awaiting: {
    // Patient, not urgent. A steady amber rim rather than a pulsing one: the
    // system is waiting on a human, and nagging is not a system state.
    pulseSpeed: 0.2,
    brightness: 0.66,
    coreIntensity: 0.6,
    connectivity: 0.34,
    haloSpeed: 0.13,
    ringSpeed: 0.12,
    breathe: 0.7,
    streamIn: 0,
    streamOut: 0,
    rim: PALETTE.amber,
    accent: PALETTE.amber,
    sweep: 0.16,
    distort: 0,
    wave: 0,
    approvalRing: 0.55,
    bloom: 0.5,
  },
  blocked: {
    // One soft red settle, then back to idle — the transient is scheduled by
    // the caller, not looped here. A permanently red brain is a brain nobody
    // looks at any more.
    pulseSpeed: 0.22,
    brightness: 0.64,
    coreIntensity: 0.6,
    connectivity: 0.3,
    haloSpeed: 0.14,
    ringSpeed: 0.12,
    breathe: 0.8,
    streamIn: 0,
    streamOut: 0,
    rim: PALETTE.red,
    accent: PALETTE.red,
    sweep: 0.14,
    distort: 0.25,
    wave: 0,
    approvalRing: 0,
    bloom: 0.48,
  },
  completed: {
    pulseSpeed: 0.34,
    brightness: 0.74,
    coreIntensity: 0.7,
    connectivity: 0.45,
    haloSpeed: 0.34,
    ringSpeed: 0.24,
    breathe: 0.9,
    streamIn: 0,
    streamOut: 0.12,
    rim: PALETTE.green,
    accent: PALETTE.green,
    sweep: 0.26,
    distort: 0,
    wave: 0.55,
    approvalRing: 0,
    bloom: 0.6,
  },
  learning: {
    pulseSpeed: 0.5,
    brightness: 0.76,
    coreIntensity: 0.95,
    connectivity: 0.68,
    haloSpeed: 0.42,
    ringSpeed: 0.26,
    breathe: 0.95,
    streamIn: 0.7,
    streamOut: 0,
    rim: PALETTE.magenta,
    accent: PALETTE.magenta,
    sweep: 0.3,
    distort: 0,
    wave: 0,
    approvalRing: 0,
    bloom: 0.66,
  },
};

/** Short human labels for the demo controls. */
export const STATE_LABELS: Record<BrainState, string> = {
  idle: "Idle",
  thinking: "Thinking",
  executing: "Executing",
  awaiting: "Awaiting approval",
  blocked: "Blocked",
  completed: "Completed",
  learning: "Learning",
};

/** Make a live, mutable copy of a target set (the scene mutates this in place). */
export function cloneVisual(v: BrainVisual): BrainVisual {
  return {
    ...v,
    rim: [...v.rim] as RGB,
    accent: [...v.accent] as RGB,
  };
}
