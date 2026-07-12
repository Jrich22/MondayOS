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

## Live vs demo (Phase 2)

The dashboard talks to MondayOS through one seam — the **adapter**
(`src/adapter/`). Two implementations share the `MondayAdapter` interface:

- **`realAdapter`** → the MondayOS dashboard API (`../dashboard_api`, a
  localhost HTTP bridge). Timeouts on every request, read-only retries, no
  fallback once a write begins, structured-error parsing, and a health signal
  that drives the **LIVE / DEGRADED** badge.
- **`demoAdapter`** → the offline demo dataset (the **DEMO DATA** badge).

`selectAdapter()` probes `VITE_MONDAYOS_API/health`; reachable → LIVE, otherwise
→ demo. So the app works offline out of the box, and goes live with no code
change:

```bash
python -m dashboard_api                 # start the API (repo root)
cp dashboard/.env.example dashboard/.env.local   # sets VITE_MONDAYOS_API
npm run dev                             # dashboard now runs LIVE
```

Live updates use **SSE** (`/events`) with **polling** (`/revision`) as a
documented fallback. Writes (create task, run team, approve/reject) preview →
confirm → execute through MondayOS, which enforces its own ApprovalGate — the
Brain never bypasses it.

## State layers

`state/store.tsx` owns client state + a read cache; `command/` classifies and
executes commands; `adapter/` is the only path to MondayOS. No business logic
lives in components.
