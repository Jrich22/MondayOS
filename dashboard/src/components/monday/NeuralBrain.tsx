import { useMemo, useRef, type MutableRefObject } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";
import type { BrainVisual } from "./brainState";
import {
  buildBrainCloud,
  buildNeuralNet,
  type BrainTier,
} from "./brainGeometry";

/**
 * The inner brain: a single Points cloud (cortex + volume + core) plus a
 * LineSegments network whose pulses travel along the pathways. All motion —
 * breathing, pulses, the completion wave, the blocked distortion — lives in the
 * vertex/fragment shaders and is driven by uniforms lerped from `visual`, so
 * the CPU cost per frame is just a handful of uniform writes.
 */

const pointsVertex = /* glsl */ `
  uniform float uTime;
  uniform float uSize;
  uniform float uPixelRatio;
  uniform float uBreathe;
  uniform float uBrightness;
  uniform float uCore;
  uniform float uWave;
  uniform float uDistort;
  uniform vec3 uAccent;
  attribute vec3 aColor;
  attribute float aScale;
  attribute float aSeed;
  attribute vec3 aNormal;
  varying vec3 vColor;
  varying float vGlow;
  void main() {
    vec3 pos = position;
    float rad = length(position);
    // Organic breathing along the surface normal.
    float b = sin(uTime * 1.2 + aSeed * 6.2831) * 0.5 + 0.5;
    pos += aNormal * b * 0.045 * uBreathe;
    // Restrained blocked-state jitter.
    pos += aNormal * sin(uTime * 9.0 + aSeed * 3.0) * 0.018 * uDistort;
    // Expanding completion wave.
    float wavefront = fract(uTime * 0.28);
    float w = exp(-pow((rad - wavefront * 1.6) * 3.0, 2.0)) * uWave;
    pos += aNormal * w * 0.11;

    vec4 mv = modelViewMatrix * vec4(pos, 1.0);
    gl_Position = projectionMatrix * mv;
    gl_PointSize = uSize * aScale * uPixelRatio * (1.0 / max(0.1, -mv.z));

    float coreFactor = smoothstep(0.55, 0.0, rad);
    vColor = mix(aColor, uAccent, 0.15) + coreFactor * uCore * 0.45;
    vGlow = uBrightness * (0.55 + coreFactor * uCore * 0.9) + w * 1.5;
  }
`;

const pointsFragment = /* glsl */ `
  varying vec3 vColor;
  varying float vGlow;
  void main() {
    vec2 uv = gl_PointCoord - 0.5;
    float d = length(uv);
    if (d > 0.5) discard;
    float alpha = pow(smoothstep(0.5, 0.0, d), 1.6);
    gl_FragColor = vec4(vColor * vGlow, alpha);
  }
`;

const lineVertex = /* glsl */ `
  attribute vec3 aColor;
  attribute float aLineT;
  attribute float aPhase;
  varying float vLineT;
  varying float vPhase;
  varying vec3 vColor;
  void main() {
    vLineT = aLineT;
    vPhase = aPhase;
    vColor = aColor;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const lineFragment = /* glsl */ `
  uniform float uTime;
  uniform float uPulseSpeed;
  uniform float uConnectivity;
  uniform float uBrightness;
  uniform vec3 uAccent;
  varying float vLineT;
  varying float vPhase;
  varying vec3 vColor;
  void main() {
    // A gaussian pulse travelling from one node to the other.
    float pulsePos = fract(uTime * uPulseSpeed * 0.16 + vPhase);
    float d = abs(vLineT - pulsePos);
    d = min(d, 1.0 - d);
    float pulse = exp(-d * d * 60.0);
    float base = 0.05 * uConnectivity;
    float intensity = base + pulse * uConnectivity;
    vec3 c = mix(vColor, uAccent, 0.3);
    gl_FragColor = vec4(c * (0.6 + pulse * 1.9) * uBrightness, intensity);
  }
`;

interface Props {
  tier: BrainTier;
  visual: MutableRefObject<BrainVisual>;
}

export function NeuralBrain({ tier, visual }: Props) {
  const gl = useThree((s) => s.gl);
  const pixelRatio = Math.min(gl.getPixelRatio(), 2);

  const cloud = useMemo(() => buildBrainCloud(tier), [tier]);
  const net = useMemo(() => buildNeuralNet(cloud.nodePositions), [cloud]);

  const pointsMat = useRef<THREE.ShaderMaterial>(null);
  const lineMat = useRef<THREE.ShaderMaterial>(null);

  const pointsUniforms = useMemo(
    () => ({
      uTime: { value: 0 },
      uSize: { value: 26 },
      uPixelRatio: { value: pixelRatio },
      uBreathe: { value: 0.75 },
      uBrightness: { value: 0.8 },
      uCore: { value: 0.9 },
      uWave: { value: 0 },
      uDistort: { value: 0 },
      uAccent: { value: new THREE.Color(0.13, 0.83, 0.93) },
    }),
    [pixelRatio],
  );

  const lineUniforms = useMemo(
    () => ({
      uTime: { value: 0 },
      uPulseSpeed: { value: 0.35 },
      uConnectivity: { value: 0.5 },
      uBrightness: { value: 0.8 },
      uAccent: { value: new THREE.Color(0.13, 0.83, 0.93) },
    }),
    [],
  );

  useFrame((_, delta) => {
    const v = visual.current;
    const t = performance.now() / 1000;
    if (pointsMat.current) {
      const u = pointsMat.current.uniforms;
      u.uTime.value = t;
      u.uBreathe.value = v.breathe;
      u.uBrightness.value = v.brightness;
      u.uCore.value = v.coreIntensity;
      u.uWave.value = v.wave;
      u.uDistort.value = v.distort;
      (u.uAccent.value as THREE.Color).setRGB(v.accent[0], v.accent[1], v.accent[2]);
    }
    if (lineMat.current) {
      const u = lineMat.current.uniforms;
      u.uTime.value = t;
      u.uPulseSpeed.value = v.pulseSpeed;
      u.uConnectivity.value = v.connectivity;
      u.uBrightness.value = v.brightness;
      (u.uAccent.value as THREE.Color).setRGB(v.accent[0], v.accent[1], v.accent[2]);
    }
    void delta;
  });

  return (
    <group>
      <points>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[cloud.positions, 3]} />
          <bufferAttribute attach="attributes-aColor" args={[cloud.colors, 3]} />
          <bufferAttribute attach="attributes-aScale" args={[cloud.scales, 1]} />
          <bufferAttribute attach="attributes-aSeed" args={[cloud.seeds, 1]} />
          <bufferAttribute attach="attributes-aNormal" args={[cloud.normals, 3]} />
        </bufferGeometry>
        <shaderMaterial
          ref={pointsMat}
          uniforms={pointsUniforms}
          vertexShader={pointsVertex}
          fragmentShader={pointsFragment}
          transparent
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </points>

      <lineSegments>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[net.positions, 3]} />
          <bufferAttribute attach="attributes-aColor" args={[net.colors, 3]} />
          <bufferAttribute attach="attributes-aLineT" args={[net.lineT, 1]} />
          <bufferAttribute attach="attributes-aPhase" args={[net.phase, 1]} />
        </bufferGeometry>
        <shaderMaterial
          ref={lineMat}
          uniforms={lineUniforms}
          vertexShader={lineVertex}
          fragmentShader={lineFragment}
          transparent
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </lineSegments>
    </group>
  );
}
