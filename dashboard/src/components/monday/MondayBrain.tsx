import {
  memo,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { Canvas } from "@react-three/fiber";
import { BrainScene } from "./BrainScene";
import { BrainFallback } from "./BrainFallback";
import { TIERS } from "./brainGeometry";
import type { BrainState } from "./brainState";

/**
 * Monday's Brain — a living holographic intelligence inside a glass energy
 * chamber. This is the only component consumers import: it detects capability
 * (WebGL + device tier), respects prefers-reduced-motion, pauses when the tab
 * is hidden, wires pointer parallax and click-to-activate, and falls back to a
 * 2D canvas when WebGL is unavailable.
 *
 * Usage:
 *   <MondayBrain state="thinking" onActivate={() => openCommandInterface()} />
 */

export type QualityTier = "auto" | "high" | "mid" | "low";

export interface MondayBrainProps {
  /** Operational state driving the whole visual mood. */
  state?: BrainState;
  /** Fired on click/Enter — open the Monday command interface here. */
  onActivate?: () => void;
  /** Force a particle-budget tier; "auto" detects from the device. */
  quality?: QualityTier;
  /** Enable pointer parallax + click affordance (default true). */
  interactive?: boolean;
  className?: string;
  style?: CSSProperties;
  /** Accessible label for the activation control. */
  ariaLabel?: string;
}

/** Feature-detect a usable WebGL context. */
function detectWebGL(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return !!(
      window.WebGLRenderingContext &&
      (canvas.getContext("webgl2") || canvas.getContext("webgl"))
    );
  } catch {
    return false;
  }
}

/** Pick a particle budget from device signals. */
function detectTier(): "high" | "mid" | "low" {
  if (typeof navigator === "undefined") return "mid";
  const cores = navigator.hardwareConcurrency ?? 4;
  // deviceMemory is non-standard but widely available in Chromium.
  const mem = (navigator as unknown as { deviceMemory?: number }).deviceMemory ?? 4;
  const mobile = /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent);
  const small = typeof window !== "undefined" && Math.min(window.innerWidth, window.innerHeight) < 560;
  if (mobile || small || cores <= 4 || mem <= 4) return cores <= 2 ? "low" : "mid";
  if (cores >= 8 && mem >= 8) return "high";
  return "mid";
}

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);
  return reduced;
}

/** Pause the render loop while the tab is hidden. */
function usePageVisible(): boolean {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const onChange = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);
  return visible;
}

function MondayBrainImpl({
  state = "idle",
  onActivate,
  quality = "auto",
  interactive = true,
  className,
  style,
  ariaLabel = "Monday's Brain — open command interface",
}: MondayBrainProps) {
  const [webgl, setWebgl] = useState<boolean | null>(null);
  const reducedMotion = usePrefersReducedMotion();
  const pageVisible = usePageVisible();
  const pointer = useRef({ x: 0, y: 0 });
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => setWebgl(detectWebGL()), []);

  const tierName = useMemo(
    () => (quality === "auto" ? detectTier() : quality),
    [quality],
  );
  const tier = TIERS[tierName];
  // Reduced motion already trims work; also trim the halo for the fallback path.
  const haloCount = reducedMotion ? Math.round(tier.halo * 0.5) : tier.halo;

  const handlePointerMove = (e: React.PointerEvent) => {
    if (!interactive || reducedMotion) return;
    const el = containerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    pointer.current.x = ((e.clientX - r.left) / r.width) * 2 - 1;
    pointer.current.y = ((e.clientY - r.top) / r.height) * 2 - 1;
  };
  const resetPointer = () => {
    pointer.current.x = 0;
    pointer.current.y = 0;
  };

  const activate = () => onActivate?.();

  const interactiveProps = onActivate
    ? {
        role: "button" as const,
        tabIndex: 0,
        "aria-label": ariaLabel,
        onClick: activate,
        onKeyDown: (e: React.KeyboardEvent) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            activate();
          }
        },
      }
    : { "aria-hidden": true as const };

  return (
    <div
      ref={containerRef}
      className={className}
      style={{ position: "relative", cursor: onActivate ? "pointer" : "default", ...style }}
      onPointerMove={handlePointerMove}
      onPointerLeave={resetPointer}
      {...interactiveProps}
    >
      {webgl === null ? null : webgl ? (
        <Canvas
          dpr={reducedMotion ? 1 : [1, 2]}
          frameloop={pageVisible ? "always" : "never"}
          camera={{ position: [0, 0, 4.6], fov: 45 }}
          gl={{ antialias: true, alpha: true, powerPreference: "high-performance" }}
          style={{ width: "100%", height: "100%" }}
          onCreated={({ gl }) => gl.setClearColor(0x000000, 0)}
        >
          <Suspense fallback={null}>
            <BrainScene
              state={state}
              tier={tier}
              haloCount={haloCount}
              reducedMotion={reducedMotion}
              pointer={pointer}
            />
          </Suspense>
        </Canvas>
      ) : (
        <BrainFallback state={state} reducedMotion={reducedMotion} />
      )}
    </div>
  );
}

/**
 * The Brain sits at the persistent center of Mission Control, above a store that
 * re-renders on every live event (health pings, activity/revision refreshes, SSE
 * ticks). Wrapping the component in `memo` keeps that churn from reaching the R3F
 * Canvas: it only re-renders when a prop it actually cares about changes (chiefly
 * `state`), so the WebGL context and postprocessing pipeline are never torn down
 * and rebuilt by an unrelated snapshot refresh — that teardown was the LIVE-mode
 * "failing light bulb" flicker. A genuine `state` change still flows through as a
 * prop update, which the scene eases into via uniforms rather than a remount.
 */
export const MondayBrain = memo(MondayBrainImpl);
