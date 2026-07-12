import { useEffect, useMemo, useRef, type MutableRefObject } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import { EffectComposer, Bloom } from "@react-three/postprocessing";
import * as THREE from "three";
import {
  STATE_TARGETS,
  cloneVisual,
  type BrainState,
  type BrainVisual,
  type RGB,
} from "./brainState";
import type { BrainTier } from "./brainGeometry";
import { NeuralBrain } from "./NeuralBrain";
import { ParticleHalo } from "./ParticleHalo";
import { GlassShell } from "./GlassShell";

/**
 * Scene root. Owns the single live `visual` object that every sub-component
 * reads: each frame it eases the live values toward the active state's targets
 * (so state changes feel like the brain *reacting*), applies a gentle idle
 * float, and nudges the whole rig with the pointer for parallax. Children pull
 * from `visual.current` in their own useFrame — this component's frame callback
 * is registered first (hooks run before children mount), so the ease happens
 * before anyone reads it.
 */

function easeColor(cur: RGB, target: RGB, k: number) {
  cur[0] += (target[0] - cur[0]) * k;
  cur[1] += (target[1] - cur[1]) * k;
  cur[2] += (target[2] - cur[2]) * k;
}

interface Props {
  state: BrainState;
  tier: BrainTier;
  haloCount: number;
  reducedMotion: boolean;
  pointer: MutableRefObject<{ x: number; y: number }>;
}

export function BrainScene({ state, tier, haloCount, reducedMotion, pointer }: Props) {
  const visual = useRef<BrainVisual>(cloneVisual(STATE_TARGETS.idle));
  const rig = useRef<THREE.Group>(null);
  const bloomRef = useRef<{ intensity: number } | null>(null);
  const invalidate = useThree((s) => s.invalidate);

  // Keep rendering while mounted (frameloop is "always" by default, but this is
  // explicit and harmless if the loop is ever gated).
  useEffect(() => void invalidate(), [invalidate]);

  // The postprocessing pipeline is built exactly once and never rebuilt. A brain
  // *state* change re-renders this component (the `state` prop is new), but the
  // EffectComposer/Bloom must NOT be recreated with it — recreating the composer
  // drops a frame of unprocessed (bloom-less) rendering, which reads as a flash.
  // Bloom intensity is driven every frame through `bloomRef` below, so the pass
  // needs no reactive props; a stable element keeps it mounted across renders.
  const postProcessing = useMemo(
    () => (
      <EffectComposer>
        <Bloom
          ref={bloomRef as never}
          intensity={0.9}
          luminanceThreshold={0.08}
          luminanceSmoothing={0.9}
          mipmapBlur
          radius={0.7}
        />
      </EffectComposer>
    ),
    [],
  );

  useFrame((_, delta) => {
    const v = visual.current;
    const target = STATE_TARGETS[state];
    // Frame-rate independent easing.
    const k = 1 - Math.pow(0.0025, delta);

    v.pulseSpeed += (target.pulseSpeed - v.pulseSpeed) * k;
    v.brightness += (target.brightness - v.brightness) * k;
    v.coreIntensity += (target.coreIntensity - v.coreIntensity) * k;
    v.connectivity += (target.connectivity - v.connectivity) * k;
    v.haloSpeed += (target.haloSpeed - v.haloSpeed) * k;
    v.ringSpeed += (target.ringSpeed - v.ringSpeed) * k;
    v.breathe += (target.breathe - v.breathe) * k;
    v.streamIn += (target.streamIn - v.streamIn) * k;
    v.streamOut += (target.streamOut - v.streamOut) * k;
    v.sweep += (target.sweep - v.sweep) * k;
    v.distort += (target.distort - v.distort) * k;
    v.wave += (target.wave - v.wave) * k;
    v.approvalRing += (target.approvalRing - v.approvalRing) * k;
    v.bloom += (target.bloom - v.bloom) * k;
    easeColor(v.rim, target.rim, k);
    easeColor(v.accent, target.accent, k);

    if (bloomRef.current) bloomRef.current.intensity = v.bloom;

    if (rig.current) {
      const t = performance.now() / 1000;
      if (!reducedMotion) {
        // Slow idle float + breathing scale.
        rig.current.rotation.y += delta * 0.12;
        rig.current.position.y = Math.sin(t * 0.6) * 0.03;
        const s = 1 + Math.sin(t * 0.9) * 0.012 * v.breathe;
        rig.current.scale.setScalar(s);
      }
      // Pointer parallax — ease the rig tilt toward the cursor.
      const px = pointer.current.x;
      const py = pointer.current.y;
      rig.current.rotation.x += (py * 0.28 - rig.current.rotation.x) * Math.min(1, delta * 3);
      const targetZ = -px * 0.32;
      rig.current.rotation.z += (targetZ - rig.current.rotation.z) * Math.min(1, delta * 3);
    }
  });

  return (
    <>
      <ambientLight intensity={0.4} />
      <group ref={rig}>
        <NeuralBrain tier={tier} visual={visual} />
        <GlassShell visual={visual} />
        <ParticleHalo count={haloCount} visual={visual} />
      </group>
      {postProcessing}
    </>
  );
}
