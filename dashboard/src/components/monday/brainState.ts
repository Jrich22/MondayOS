/**
 * Monday's Brain — state model and visual language.
 *
 * The brain is a small state machine. Each operational state maps to a target
 * set of *visual* parameters (pulse speed, core brightness, halo velocity, rim
 * colour, streaming direction…). The scene lerps the live parameters toward the
 * active state's targets every frame, so transitions read as the brain
 * *reacting* rather than snapping. Keeping the palette + targets here (not
 * scattered through the R3F components) means the whole mood lives in one place.
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
    pulseSpeed: 0.35,
    brightness: 0.78,
    coreIntensity: 0.9,
    connectivity: 0.5,
    haloSpeed: 0.35,
    ringSpeed: 0.35,
    breathe: 0.75,
    streamIn: 0,
    streamOut: 0,
    rim: PALETTE.cyan,
    accent: PALETTE.cyan,
    sweep: 0.35,
    distort: 0,
    wave: 0,
    approvalRing: 0,
    bloom: 0.9,
  },
  thinking: {
    pulseSpeed: 1.5,
    brightness: 1.05,
    coreIntensity: 1.7,
    connectivity: 1,
    haloSpeed: 0.7,
    ringSpeed: 0.55,
    breathe: 1.1,
    streamIn: 0.1,
    streamOut: 0,
    rim: PALETTE.violet,
    accent: PALETTE.violet,
    sweep: 0.6,
    distort: 0,
    wave: 0,
    approvalRing: 0,
    bloom: 1.25,
  },
  executing: {
    pulseSpeed: 2.1,
    brightness: 1.1,
    coreIntensity: 1.5,
    connectivity: 0.9,
    haloSpeed: 1.6,
    ringSpeed: 1.2,
    breathe: 0.9,
    streamIn: 0,
    streamOut: 1,
    rim: PALETTE.cyan,
    accent: PALETTE.indigo,
    sweep: 1,
    distort: 0,
    wave: 0,
    approvalRing: 0,
    bloom: 1.15,
  },
  awaiting: {
    pulseSpeed: 0.5,
    brightness: 0.82,
    coreIntensity: 0.95,
    connectivity: 0.5,
    haloSpeed: 0.28,
    ringSpeed: 0.3,
    breathe: 0.6,
    streamIn: 0,
    streamOut: 0,
    rim: PALETTE.amber,
    accent: PALETTE.amber,
    sweep: 0.3,
    distort: 0,
    wave: 0,
    approvalRing: 1,
    bloom: 0.95,
  },
  blocked: {
    pulseSpeed: 0.55,
    brightness: 0.7,
    coreIntensity: 0.8,
    connectivity: 0.4,
    haloSpeed: 0.3,
    ringSpeed: 0.25,
    breathe: 0.7,
    streamIn: 0,
    streamOut: 0,
    rim: PALETTE.red,
    accent: PALETTE.red,
    sweep: 0.25,
    distort: 1,
    wave: 0,
    approvalRing: 0,
    bloom: 0.85,
  },
  completed: {
    pulseSpeed: 0.9,
    brightness: 0.92,
    coreIntensity: 1.0,
    connectivity: 0.7,
    haloSpeed: 0.8,
    ringSpeed: 0.6,
    breathe: 0.9,
    streamIn: 0,
    streamOut: 0.2,
    rim: PALETTE.green,
    accent: PALETTE.green,
    sweep: 0.5,
    distort: 0,
    wave: 1,
    approvalRing: 0,
    bloom: 1.0,
  },
  learning: {
    pulseSpeed: 1.3,
    brightness: 1.15,
    coreIntensity: 1.9,
    connectivity: 1,
    haloSpeed: 0.9,
    ringSpeed: 0.5,
    breathe: 1.0,
    streamIn: 1,
    streamOut: 0,
    rim: PALETTE.magenta,
    accent: PALETTE.magenta,
    sweep: 0.7,
    distort: 0,
    wave: 0,
    approvalRing: 0,
    bloom: 1.35,
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
