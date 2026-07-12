import { useEffect, useRef } from "react";
import { PALETTE, type BrainState, type RGB } from "./brainState";

/**
 * Pure-2D-canvas fallback for when WebGL is unavailable or fails to
 * initialise. It can't be the volumetric brain, but it keeps the *language* —
 * a luminous core, a rotating particle halo, a rimmed shell and state-tinted
 * glow — so the surface never collapses to a dead box. Honors reduced-motion by
 * rendering a single static frame.
 */

const STATE_TINT: Record<BrainState, RGB> = {
  idle: PALETTE.cyan,
  thinking: PALETTE.violet,
  executing: PALETTE.indigo,
  awaiting: PALETTE.amber,
  blocked: PALETTE.red,
  completed: PALETTE.green,
  learning: PALETTE.magenta,
};

const rgba = (c: RGB, a: number) =>
  `rgba(${Math.round(c[0] * 255)},${Math.round(c[1] * 255)},${Math.round(c[2] * 255)},${a})`;

interface Props {
  state: BrainState;
  reducedMotion: boolean;
}

export function BrainFallback({ state, reducedMotion }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef(state);
  stateRef.current = state;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    const particles = Array.from({ length: 90 }, (_, i) => ({
      angle: (i / 90) * Math.PI * 2,
      radius: 0.55 + (i % 5) * 0.09,
      speed: 0.15 + (i % 7) * 0.05,
      size: 1 + (i % 3),
    }));

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const r = canvas.getBoundingClientRect();
      canvas.width = r.width * dpr;
      canvas.height = r.height * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    window.addEventListener("resize", resize);

    const draw = (time: number) => {
      const r = canvas.getBoundingClientRect();
      const w = r.width;
      const h = r.height;
      const cx = w / 2;
      const cy = h / 2;
      const R = Math.min(w, h) * 0.42;
      const tint = STATE_TINT[stateRef.current];
      const t = reducedMotion ? 0 : time / 1000;

      ctx.clearRect(0, 0, w, h);

      // Core glow.
      const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, R);
      core.addColorStop(0, rgba(PALETTE.white, 0.9));
      core.addColorStop(0.25, rgba(tint, 0.5));
      core.addColorStop(1, rgba(tint, 0));
      ctx.fillStyle = core;
      ctx.beginPath();
      ctx.arc(cx, cy, R, 0, Math.PI * 2);
      ctx.fill();

      // Halo particles.
      ctx.save();
      ctx.globalCompositeOperation = "lighter";
      for (const p of particles) {
        const a = p.angle + t * p.speed;
        const rr = R * (0.9 + p.radius);
        const x = cx + Math.cos(a) * rr;
        const y = cy + Math.sin(a) * rr * 0.6;
        ctx.fillStyle = rgba(tint, 0.7);
        ctx.beginPath();
        ctx.arc(x, y, p.size, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();

      // Shell rim.
      ctx.strokeStyle = rgba(tint, 0.6);
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(cx, cy, R * 1.02, 0, Math.PI * 2);
      ctx.stroke();

      if (!reducedMotion) raf = requestAnimationFrame(draw);
    };

    raf = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
    };
  }, [reducedMotion]);

  return (
    <canvas
      ref={canvasRef}
      className="h-full w-full"
      aria-hidden="true"
      style={{ display: "block" }}
    />
  );
}
