# sourcingBOT — Setup & Runbook

## Prerequisites

- Node 18+ (developed against Node 22/24; see the localStorage note below)
- npm

## Setup

```bash
cd projects/sourcingbot
npm install
```

Dependencies are local to this product. sourcingBOT does not share a
`node_modules` with Cue App or the MondayOS Python packages.

## Commands

| Command | Purpose | Expected |
|---|---|---|
| `npm run dev` | Dev server, opens a browser | http://localhost:5174 |
| `npm test` | Vitest suite | 99 passed (6 files) |
| `npm run typecheck` | TypeScript, strict | no output |
| `npm run build` | Production build | `dist/`, ~192 kB JS (61 kB gzip) |
| `npm run preview` | Serve the built bundle | http://localhost:4173 |

Port **5174** is deliberate — Cue App holds 5173, so both products run side by
side.

## Registering with MondayOS

sourcingBOT is a managed product and should appear in the MondayOS registry:

```bash
cd /Users/jrich/AI-Labs/MondayOS
.venv/bin/monday project register sourcingbot \
  "$(pwd)/projects/sourcingbot" \
  --description "Supervised recruiting sourcing workspace"

.venv/bin/monday project list
```

The registry (`config/projects.json`) holds machine-specific absolute paths and
is gitignored, so **each developer registers locally**. Registration is not
required to build or run the app.

## Data

Everything persists to `localStorage` under **`sourcingbot.workspace.v1`**, scoped
to one browser profile. No backend, no sync, no multi-device.

**Reset to the seeded demo workspace** — DevTools console:

```js
localStorage.removeItem("sourcingbot.workspace.v1");
location.reload();
```

**Inspect current state:**

```js
JSON.parse(localStorage.getItem("sourcingbot.workspace.v1"));
```

The seed is synthetic: invented people at invented companies, no LinkedIn URLs on
any record. Priya Raman appears on two requisitions with different stages and
scores — that is intentional, demonstrating the persistent-person model.

## Troubleshooting

**`localStorage is not available because --localstorage-file was not provided`**

Harmless warning during tests. Node 22+ defines its own `localStorage` global
that stays undefined without that flag; `src/test/setup.ts` installs an in-memory
implementation. Tests pass regardless. See
[ADR-005](DECISIONS.md#adr-005).

**Tests fail with `Cannot read properties of undefined (reading 'clear')`**

`setupFiles` is not loading. Confirm `vite.config.ts` imports `defineConfig` from
`vitest/config` (not `vite`) and includes:

```ts
test: { setupFiles: ["./src/test/setup.ts"] }
```

**Port 5174 already in use**

Another instance is running, or the port is taken. Change `server.port` in
`vite.config.ts`, or `npx vite --port 5175`.

**Changes to `tailwind.config.ts` not applying**

Restart the dev server — the Tailwind config is not hot-reloaded.

**Build fails on unused variables**

`noUnusedLocals` and `noUnusedParameters` are on, matching Cue. Prefix a
deliberately unused parameter with `_`.

**App renders empty after a schema change**

A stored workspace from an older shape may not match current types. Clear the
storage key as above. In production this is what the `v1` suffix exists for — a
breaking change bumps it and adds a migration.

## Test layout

| File | Covers | Env |
|---|---|---|
| `lib/req.test.ts` | Req lifecycle, brief structure/versioning | node |
| `lib/candidate.test.ts` | Persistent person, duplicates, concentration | node |
| `lib/req-candidate.test.ts` | **The model rule**, stages, fit scoring | node |
| `lib/linkedin.test.ts` | Supervision boundary, prohibited capabilities | node |
| `lib/store.test.ts` | Persistence, normalization, duplicate guard | jsdom |
| `pages/Workspace.test.tsx` | Shell, surfaces, model rule end to end | jsdom |

Domain tests need no DOM — `lib/` is React-free by design.

## Running alongside MondayOS

sourcingBOT is TypeScript and is not part of the MondayOS Python suite. A full
check touches both:

```bash
cd /Users/jrich/AI-Labs/MondayOS
.venv/bin/python -m pytest          # MondayOS regression
cd projects/sourcingbot && npm test # sourcingBOT
```

Neither imports the other; the Python suite is unaffected by changes here.
