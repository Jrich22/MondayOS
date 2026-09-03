/**
 * Procedural generation for Monday's Brain.
 *
 * Nothing here is a loaded model or a texture — the brain, its neural pathways
 * and the orbiting halo are all generated from a seeded PRNG so the shape is
 * deterministic (stable across reloads / SSR) yet organic. The output is plain
 * typed arrays ready to hand straight to BufferGeometry attributes.
 *
 * Shape strategy for a *recognisable* brain (not an orb):
 *  - sample directions on a sphere, map onto a front-back-elongated ellipsoid;
 *  - carve a longitudinal fissure at the midline so two hemispheres read;
 *  - displace the surface by layered sinusoids → gyrus/sulcus wrinkling;
 *  - add slight left/right asymmetry so it never looks machined;
 *  - salt in a sparse interior volume + a dense bright core.
 */

import { PALETTE, type RGB } from "./brainState";

/** Deterministic PRNG (mulberry32) — no Math.random, stable geometry. */
function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Cheap layered "wrinkle" field over the sphere — sums of sinusoids. */
function wrinkle(x: number, y: number, z: number): number {
  return (
    0.5 * Math.sin(3.1 * x + 1.7 * z) * Math.cos(2.6 * y) +
    0.3 * Math.sin(6.3 * z - 2.2 * y) * Math.cos(5.1 * x) +
    0.2 * Math.sin(9.4 * y + 4.3 * x) * Math.cos(8.2 * z)
  );
}

export interface BrainTier {
  surface: number;
  volume: number;
  core: number;
  nodes: number;
  halo: number;
}

/** Particle budgets by device capability. */
// About 2.5% of the original budget.
//
// The brain is no longer an object on the screen; it is atmosphere at the edge
// of one. At the size it now renders — roughly 22 pixels — a dense point cloud
// resolves to a solid glowing dot, so the density was not buying detail, it was
// buying brightness the conversation had to compete with.
//
// These counts are chosen for what survives at that scale: enough points to
// suggest volume and depth, few enough that each one is a distinct speck rather
// than part of a mass. Node count falls hardest because every node anchors
// edges, and edges are the moving parts.
export const TIERS: Record<"high" | "mid" | "low", BrainTier> = {
  high: { surface: 180, volume: 64, core: 22, nodes: 10, halo: 105 },
  mid: { surface: 120, volume: 42, core: 16, nodes: 8, halo: 70 },
  low: { surface: 70, volume: 24, core: 10, nodes: 6, halo: 40 },
};

// Ellipsoid radii: longer front-back (z), narrower top-bottom (y) → brain-ish.
const RX = 1.02;
const RY = 0.82;
const RZ = 1.16;

function lerp(a: RGB, b: RGB, t: number): RGB {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

export interface BrainCloud {
  positions: Float32Array;
  colors: Float32Array;
  scales: Float32Array;
  seeds: Float32Array;
  /** Unit surface normal per point — used for breathing displacement. */
  normals: Float32Array;
  count: number;
  /** A subset of surface positions used as network nodes. */
  nodePositions: number[][];
}

/**
 * Build the brain particle cloud. Returns everything three separate point sets
 * (surface / interior volume / core) need, already flattened into one buffer so
 * the whole brain draws in a single draw call.
 */
export function buildBrainCloud(tier: BrainTier, seed = 1337): BrainCloud {
  const rnd = mulberry32(seed);
  const total = tier.surface + tier.volume + tier.core;
  const positions = new Float32Array(total * 3);
  const colors = new Float32Array(total * 3);
  const scales = new Float32Array(total);
  const seeds = new Float32Array(total);
  const normals = new Float32Array(total * 3);
  const nodePositions: number[][] = [];
  const nodeStride = Math.max(1, Math.floor(tier.surface / tier.nodes));

  let i = 0;

  const put = (
    x: number,
    y: number,
    z: number,
    nx: number,
    ny: number,
    nz: number,
    col: RGB,
    scale: number,
  ) => {
    positions[i * 3] = x;
    positions[i * 3 + 1] = y;
    positions[i * 3 + 2] = z;
    normals[i * 3] = nx;
    normals[i * 3 + 1] = ny;
    normals[i * 3 + 2] = nz;
    colors[i * 3] = col[0];
    colors[i * 3 + 1] = col[1];
    colors[i * 3 + 2] = col[2];
    scales[i] = scale;
    seeds[i] = rnd() * 100;
    i++;
  };

  // ---- Surface shell: the wrinkled cortex --------------------------------
  for (let s = 0; s < tier.surface; s++) {
    // Uniform direction on the unit sphere.
    const u = rnd() * 2 - 1;
    const theta = rnd() * Math.PI * 2;
    const r = Math.sqrt(1 - u * u);
    let dx = r * Math.cos(theta);
    let dy = u;
    let dz = r * Math.sin(theta);

    // Longitudinal fissure: push points off the x=0 midline and depress the
    // top-centre so the two hemispheres separate.
    const midline = Math.exp(-(dx * dx) / 0.015);
    dx += Math.sign(dx || 1) * midline * 0.12;
    dy -= midline * Math.max(0, dy) * 0.35;

    // Map onto the ellipsoid, then wrinkle along the normal.
    let px = dx * RX;
    let py = dy * RY;
    let pz = dz * RZ;
    const fold = wrinkle(px, py, pz);
    const disp = 1 + fold * 0.055;
    px *= disp;
    py *= disp;
    pz *= disp;

    // Subtle left/right asymmetry so it never reads as a machined solid.
    if (dx > 0) {
      px *= 1.04;
      py *= 0.98;
    }

    const len = Math.hypot(px, py, pz) || 1;

    // Colour: cyan base, violet toward the back, magenta salted into folds,
    // white on the raised gyri (positive fold).
    let col = lerp(PALETTE.cyan, PALETTE.violet, (dz + 1) / 2 * 0.7 + rnd() * 0.15);
    if (fold > 0.35) col = lerp(col, PALETTE.white, 0.45);
    if (rnd() > 0.93) col = lerp(col, PALETTE.magenta, 0.6);

    const bright = rnd() > 0.9;
    put(px, py, pz, px / len, py / len, pz / len, col, bright ? 2.1 : 1);

    if (s % nodeStride === 0 && nodePositions.length < tier.nodes) {
      nodePositions.push([px, py, pz]);
    }
  }

  // ---- Interior volume: sparse dim points giving the brain body ----------
  for (let v = 0; v < tier.volume; v++) {
    const u = rnd() * 2 - 1;
    const theta = rnd() * Math.PI * 2;
    const rad = Math.cbrt(rnd()) * 0.92;
    const r = Math.sqrt(1 - u * u);
    const px = r * Math.cos(theta) * RX * rad;
    const py = u * RY * rad;
    const pz = r * Math.sin(theta) * RZ * rad;
    const len = Math.hypot(px, py, pz) || 1;
    const col = lerp(PALETTE.indigo, PALETTE.violet, rnd());
    put(px, py, pz, px / len, py / len, pz / len, col, 0.7);
  }

  // ---- Intelligence core: dense bright cluster near centre ----------------
  for (let c = 0; c < tier.core; c++) {
    const u = rnd() * 2 - 1;
    const theta = rnd() * Math.PI * 2;
    const rad = Math.pow(rnd(), 1.6) * 0.34;
    const r = Math.sqrt(1 - u * u);
    const px = r * Math.cos(theta) * rad;
    const py = u * rad * 0.85;
    const pz = r * Math.sin(theta) * rad;
    const len = Math.hypot(px, py, pz) || 1;
    const col = lerp(PALETTE.white, PALETTE.cyan, rnd() * 0.6);
    put(px, py, pz, px / len, py / len, pz / len, col, 1.6);
  }

  return { positions, colors, scales, seeds, normals, count: i, nodePositions };
}

export interface NeuralNet {
  positions: Float32Array; // 2 endpoints per segment
  colors: Float32Array;
  /** Per-vertex: 0 at segment start, 1 at end — drives the travelling pulse. */
  lineT: Float32Array;
  /** Per-vertex random phase so pulses don't fire in lock-step. */
  phase: Float32Array;
  count: number; // vertex count
}

/**
 * Wire the network nodes into pathways: connect each node to a couple of nearby
 * neighbours. O(nodes²) but nodes is a few hundred, so it's cheap and one-off.
 */
export function buildNeuralNet(nodes: number[][], seed = 71): NeuralNet {
  const rnd = mulberry32(seed);
  const links: [number, number][] = [];
  // One neighbour per node, not two. Halving the edge count removes most of the
  // moving lines: what remains reads as a few quiet connections rather than a
  // network diagram.
  const K = 1;
  for (let a = 0; a < nodes.length; a++) {
    // Find nearest neighbours to node a.
    const dists: [number, number][] = [];
    for (let b = 0; b < nodes.length; b++) {
      if (a === b) continue;
      const dx = nodes[a][0] - nodes[b][0];
      const dy = nodes[a][1] - nodes[b][1];
      const dz = nodes[a][2] - nodes[b][2];
      dists.push([dx * dx + dy * dy + dz * dz, b]);
    }
    dists.sort((p, q) => p[0] - q[0]);
    for (let k = 0; k < K; k++) {
      const b = dists[k][1];
      if (a < b) links.push([a, b]);
      else if (rnd() > 0.5) links.push([b, a]);
    }
  }

  const count = links.length * 2;
  const positions = new Float32Array(count * 3);
  const colors = new Float32Array(count * 3);
  const lineT = new Float32Array(count);
  const phase = new Float32Array(count);

  links.forEach((link, idx) => {
    const [a, b] = link;
    const ph = rnd() * 100;
    const col = lerp(PALETTE.cyan, PALETTE.violet, rnd());
    for (let e = 0; e < 2; e++) {
      const n = nodes[e === 0 ? a : b];
      const vi = idx * 2 + e;
      positions[vi * 3] = n[0];
      positions[vi * 3 + 1] = n[1];
      positions[vi * 3 + 2] = n[2];
      colors[vi * 3] = col[0];
      colors[vi * 3 + 1] = col[1];
      colors[vi * 3 + 2] = col[2];
      lineT[vi] = e;
      phase[vi] = ph;
    }
  });

  return { positions, colors, lineT, phase, count };
}

export interface HaloCloud {
  /** Orbit basis vector A (in the orbit plane). */
  basisA: Float32Array;
  /** Orbit basis vector B (in the orbit plane, ⟂ A). */
  basisB: Float32Array;
  radius: Float32Array;
  theta0: Float32Array;
  speed: Float32Array;
  size: Float32Array;
  colors: Float32Array;
  /** -1 → can stream inward (learning), +1 → can stream outward (executing). */
  role: Float32Array;
  seeds: Float32Array;
  count: number;
}

/**
 * Thousands of particles orbiting the shell across layered depths and speeds.
 * Each gets its own tilted orbit plane (basisA/basisB) so the halo reads as a
 * 3D swarm, not a flat ring.
 */
export function buildHalo(count: number, seed = 913): HaloCloud {
  const rnd = mulberry32(seed);
  const basisA = new Float32Array(count * 3);
  const basisB = new Float32Array(count * 3);
  const radius = new Float32Array(count);
  const theta0 = new Float32Array(count);
  const speed = new Float32Array(count);
  const size = new Float32Array(count);
  const colors = new Float32Array(count * 3);
  const role = new Float32Array(count);
  const seeds = new Float32Array(count);

  for (let n = 0; n < count; n++) {
    // Random orbit-plane normal, then build an orthonormal basis in the plane.
    const u = rnd() * 2 - 1;
    const t = rnd() * Math.PI * 2;
    const rr = Math.sqrt(1 - u * u);
    const nx = rr * Math.cos(t);
    const ny = u;
    const nz = rr * Math.sin(t);
    // basisA = normal × up (guard against parallel), basisB = normal × basisA.
    let upx = 0,
      upy = 1,
      upz = 0;
    if (Math.abs(ny) > 0.95) {
      upx = 1;
      upy = 0;
    }
    let ax = ny * upz - nz * upy;
    let ay = nz * upx - nx * upz;
    let az = nx * upy - ny * upx;
    const al = Math.hypot(ax, ay, az) || 1;
    ax /= al;
    ay /= al;
    az /= al;
    const bx = ny * az - nz * ay;
    const by = nz * ax - nx * az;
    const bz = nx * ay - ny * ax;

    basisA[n * 3] = ax;
    basisA[n * 3 + 1] = ay;
    basisA[n * 3 + 2] = az;
    basisB[n * 3] = bx;
    basisB[n * 3 + 1] = by;
    basisB[n * 3 + 2] = bz;

    // Layered depths: most particles hug the shell, some drift far out.
    const layer = rnd();
    radius[n] = 1.55 + layer * layer * 1.7;
    theta0[n] = rnd() * Math.PI * 2;
    speed[n] = (0.15 + rnd() * 0.5) * (rnd() > 0.5 ? 1 : -1);
    size[n] = 0.7 + rnd() * 1.8;
    const col = lerp(PALETTE.cyan, PALETTE.violet, rnd());
    colors[n * 3] = col[0];
    colors[n * 3 + 1] = col[1];
    colors[n * 3 + 2] = col[2];
    role[n] = rnd() > 0.55 ? 1 : rnd() > 0.5 ? -1 : 0;
    seeds[n] = rnd() * 100;
  }

  return { basisA, basisB, radius, theta0, speed, size, colors, role, seeds, count };
}
