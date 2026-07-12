import { useMemo, useRef, type MutableRefObject } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";
import type { BrainVisual } from "./brainState";
import { buildHalo } from "./brainGeometry";

/**
 * Thousands of particles orbiting the shell. Each particle's orbit — a tilted
 * plane, radius, phase and speed — is baked into vertex attributes and the
 * position is evaluated entirely in the vertex shader, so the swarm costs one
 * draw call and no per-particle CPU work. `role` lets a subset stream inward
 * (learning) or outward toward agents (executing) as those states engage.
 */

const haloVertex = /* glsl */ `
  uniform float uTime;
  uniform float uHaloSpeed;
  uniform float uStreamIn;
  uniform float uStreamOut;
  uniform float uSize;
  uniform float uPixelRatio;
  uniform float uBrightness;
  uniform vec3 uAccent;
  attribute vec3 aBasisA;
  attribute vec3 aBasisB;
  attribute float aRadius;
  attribute float aTheta0;
  attribute float aSpeed;
  attribute float aSize;
  attribute vec3 aColor;
  attribute float aRole;
  attribute float aSeed;
  varying vec3 vColor;
  varying float vGlow;
  void main() {
    float theta = aTheta0 + uTime * aSpeed * uHaloSpeed;
    float r = aRadius;
    float streamPhase = fract(uTime * 0.4 + aSeed);
    float glow = 1.0;
    if (aRole < -0.5) {           // inward stream (learning)
      r = mix(r, 0.55, uStreamIn * streamPhase);
      glow = mix(1.0, 0.25 + 0.75 * (1.0 - streamPhase), uStreamIn);
    } else if (aRole > 0.5) {     // outward stream (executing)
      r = mix(r, r + 2.6, uStreamOut * streamPhase);
      glow = mix(1.0, 1.0 - streamPhase * 0.7, uStreamOut);
    }
    vec3 pos = (cos(theta) * aBasisA + sin(theta) * aBasisB) * r;
    vec4 mv = modelViewMatrix * vec4(pos, 1.0);
    gl_Position = projectionMatrix * mv;
    gl_PointSize = uSize * aSize * uPixelRatio * (1.0 / max(0.1, -mv.z));
    vColor = mix(aColor, uAccent, 0.25);
    vGlow = uBrightness * glow;
  }
`;

const haloFragment = /* glsl */ `
  varying vec3 vColor;
  varying float vGlow;
  void main() {
    vec2 uv = gl_PointCoord - 0.5;
    float d = length(uv);
    if (d > 0.5) discard;
    float alpha = pow(smoothstep(0.5, 0.0, d), 1.5);
    gl_FragColor = vec4(vColor * vGlow, alpha * 0.9);
  }
`;

interface Props {
  count: number;
  visual: MutableRefObject<BrainVisual>;
}

export function ParticleHalo({ count, visual }: Props) {
  const gl = useThree((s) => s.gl);
  const pixelRatio = Math.min(gl.getPixelRatio(), 2);
  const halo = useMemo(() => buildHalo(count), [count]);
  const mat = useRef<THREE.ShaderMaterial>(null);

  const uniforms = useMemo(
    () => ({
      uTime: { value: 0 },
      uHaloSpeed: { value: 0.35 },
      uStreamIn: { value: 0 },
      uStreamOut: { value: 0 },
      uSize: { value: 34 },
      uPixelRatio: { value: pixelRatio },
      uBrightness: { value: 0.8 },
      uAccent: { value: new THREE.Color(0.13, 0.83, 0.93) },
    }),
    [pixelRatio],
  );

  useFrame(() => {
    const v = visual.current;
    if (!mat.current) return;
    const u = mat.current.uniforms;
    u.uTime.value = performance.now() / 1000;
    u.uHaloSpeed.value = v.haloSpeed;
    u.uStreamIn.value = v.streamIn;
    u.uStreamOut.value = v.streamOut;
    u.uBrightness.value = v.brightness;
    (u.uAccent.value as THREE.Color).setRGB(v.accent[0], v.accent[1], v.accent[2]);
  });

  return (
    <points>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[new Float32Array(count * 3), 3]} />
        <bufferAttribute attach="attributes-aBasisA" args={[halo.basisA, 3]} />
        <bufferAttribute attach="attributes-aBasisB" args={[halo.basisB, 3]} />
        <bufferAttribute attach="attributes-aRadius" args={[halo.radius, 1]} />
        <bufferAttribute attach="attributes-aTheta0" args={[halo.theta0, 1]} />
        <bufferAttribute attach="attributes-aSpeed" args={[halo.speed, 1]} />
        <bufferAttribute attach="attributes-aSize" args={[halo.size, 1]} />
        <bufferAttribute attach="attributes-aColor" args={[halo.colors, 3]} />
        <bufferAttribute attach="attributes-aRole" args={[halo.role, 1]} />
        <bufferAttribute attach="attributes-aSeed" args={[halo.seeds, 1]} />
      </bufferGeometry>
      <shaderMaterial
        ref={mat}
        uniforms={uniforms}
        vertexShader={haloVertex}
        fragmentShader={haloFragment}
        transparent
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </points>
  );
}
