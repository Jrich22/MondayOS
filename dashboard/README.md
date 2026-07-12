# MondayOS — Mission Control

The operating-system dashboard for MondayOS. Its centerpiece is **Monday's
Brain**: a real-time, procedurally generated holographic neural brain inside a
translucent energy chamber (React Three Fiber + Three.js + bloom
postprocessing), whose state is **derived from live OS activity** rather than
hand-set.

> **This is the OS surface, not a product.** Products managed by MondayOS live
> under `../projects/` (e.g. `projects/cue-app`). Keep them separate — never put
> Monday's Brain or OS-dashboard code inside a product app.

## Run

```bash
cd dashboard
npm install
npm run dev        # http://localhost:5273
```

## Scripts

| Script | What it does |
| --- | --- |
| `npm run dev` | Start the Vite dev server (port 5273) |
| `npm run build` | Type-check + production build to `dist/` |
| `npm run preview` | Serve the production build |
| `npm run test` | Run the Vitest suite |
| `npm run typecheck` | Type-check only |

## Layout

```
src/
  components/
    monday/        # Monday's Brain — self-contained R3F component set
    mission/       # Mission Control panels (command interface, …)
  lib/
    os-data.ts     # MondayOS runtime model (mock) + deriveBrainState()
  pages/
    MissionControl.tsx   # the dashboard
```

### Monday's Brain

`import { MondayBrain } from "@/components/monday"`

```tsx
<MondayBrain state="thinking" onActivate={() => openCommandInterface()} />
```

States: `idle · thinking · executing · awaiting · blocked · completed ·
learning`. The component detects WebGL, tiers particle counts by device, honors
`prefers-reduced-motion`, pauses when the tab is hidden, and falls back to a 2D
canvas when WebGL is unavailable.

`os-data.ts` is the single seam to the backend: today it returns a deterministic
mock snapshot; when a MondayOS web API lands, only this module changes.
