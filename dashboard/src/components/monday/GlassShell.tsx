import { useMemo, useRef, type MutableRefObject } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import type { BrainVisual } from "./brainState";
import { PALETTE } from "./brainState";

/**
 * The translucent energy chamber around the brain: a fresnel-rimmed glass
 * sphere, an energy sweep band rotating across its surface, independently
 * one slow ring, an amber approval ring that forms on
 * demand, and a green completion wave that expands up through the shell. No real
 * refraction (that needs a transmission pass and a scene render target — too
 * costly here); the holographic read comes from additive fresnel + sweeps.
 */

const shellVertex = /* glsl */ `
  varying vec3 vNormal;
  varying vec3 vView;
  varying vec3 vWorld;
  void main() {
    vec4 wp = modelMatrix * vec4(position, 1.0);
    vWorld = wp.xyz;
    vNormal = normalize(mat3(modelMatrix) * normal);
    vView = normalize(cameraPosition - wp.xyz);
    gl_Position = projectionMatrix * viewMatrix * wp;
  }
`;

const shellFragment = /* glsl */ `
  uniform float uTime;
  uniform vec3 uRim;
  uniform vec3 uAccent;
  uniform vec3 uWaveColor;
  uniform float uSweep;
  uniform float uDistort;
  uniform float uWave;
  varying vec3 vNormal;
  varying vec3 vView;
  varying vec3 vWorld;
  void main() {
    float fres = pow(1.0 - abs(dot(normalize(vNormal), normalize(vView))), 2.5);
    vec3 col = uRim * fres * 1.5 + uAccent * 0.045;

    // Rotating energy sweep band across the shell.
    vec3 dir = normalize(vec3(sin(uTime * 0.6), 0.3, cos(uTime * 0.6)));
    float band = dot(normalize(vWorld), dir);
    float sweep = exp(-pow(band * 3.5, 2.0)) * uSweep;
    col += uAccent * sweep * 0.85;

    // Green completion wave rising up the shell.
    float wf = fract(uTime * 0.28);
    float wy = (vWorld.y / 1.55) * 0.5 + 0.5;
    float wave = exp(-pow((wy - wf) * 6.0, 2.0)) * uWave;
    col += uWaveColor * wave * 1.6;

    // Restrained blocked mottling — never a full red wash.
    float dpulse = (sin(uTime * 3.0) * 0.5 + 0.5) * uDistort;
    col += uRim * dpulse * fres * 0.55;

    float alpha = fres * 0.9 + sweep * 0.4 + wave * 0.6 + 0.02;
    gl_FragColor = vec4(col, clamp(alpha, 0.0, 1.0));
  }
`;

interface Props {
  visual: MutableRefObject<BrainVisual>;
}

export function GlassShell({ visual }: Props) {
  const shellMat = useRef<THREE.ShaderMaterial>(null);
  const rings = useRef<THREE.Group>(null);
  const approvalRing = useRef<THREE.Mesh>(null);
  const approvalMat = useRef<THREE.MeshBasicMaterial>(null);

  const uniforms = useMemo(
    () => ({
      uTime: { value: 0 },
      uRim: { value: new THREE.Color(...PALETTE.cyan) },
      uAccent: { value: new THREE.Color(...PALETTE.cyan) },
      uWaveColor: { value: new THREE.Color(...PALETTE.green) },
      uSweep: { value: 0.35 },
      uDistort: { value: 0 },
      uWave: { value: 0 },
    }),
    [],
  );

  useFrame((_, delta) => {
    const v = visual.current;
    const t = performance.now() / 1000;
    if (shellMat.current) {
      const u = shellMat.current.uniforms;
      u.uTime.value = t;
      (u.uRim.value as THREE.Color).setRGB(v.rim[0], v.rim[1], v.rim[2]);
      (u.uAccent.value as THREE.Color).setRGB(v.accent[0], v.accent[1], v.accent[2]);
      u.uSweep.value = v.sweep;
      u.uDistort.value = v.distort;
      u.uWave.value = v.wave;
    }
    if (rings.current) {
      rings.current.rotation.y += delta * 0.25 * v.ringSpeed;
      rings.current.rotation.x += delta * 0.12 * v.ringSpeed;
    }
    if (approvalRing.current && approvalMat.current) {
      approvalRing.current.rotation.z += delta * 0.4;
      approvalMat.current.opacity = v.approvalRing * 0.85;
      approvalRing.current.visible = v.approvalRing > 0.01;
    }
  });

  return (
    <group>
      {/* Glass shell — double-sided so the rim reads front and back. */}
      <mesh>
        <sphereGeometry args={[1.55, 64, 64]} />
        <shaderMaterial
          ref={shellMat}
          uniforms={uniforms}
          vertexShader={shellVertex}
          fragmentShader={shellFragment}
          transparent
          depthWrite={false}
          side={THREE.DoubleSide}
          blending={THREE.AdditiveBlending}
        />
      </mesh>

      {/* One slow ring. Three plus an arc read as instrumentation. */}
      {/* One faint ring, where there were three plus an arc.
          Concentric spinning rings are the visual grammar of a system monitor —
          they read as instrumentation, and instrumentation asks to be watched. A
          single slow ring gives the volume an axis without claiming attention. */}
      <group ref={rings}>
        <mesh rotation={[Math.PI / 2.2, 0, 0]}>
          <torusGeometry args={[1.9, 0.004, 6, 96]} />
          <meshBasicMaterial
            color={new THREE.Color(...PALETTE.cyan)}
            transparent
            opacity={0.18}
            blending={THREE.AdditiveBlending}
            depthWrite={false}
          />
        </mesh>
      </group>

      {/* Amber approval ring — hidden until the awaiting state forms it. */}
      <mesh ref={approvalRing} rotation={[Math.PI / 2, 0, 0]} visible={false}>
        <torusGeometry args={[1.95, 0.02, 12, 160]} />
        <meshBasicMaterial
          ref={approvalMat}
          color={new THREE.Color(...PALETTE.amber)}
          transparent
          opacity={0}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
        />
      </mesh>
    </group>
  );
}
