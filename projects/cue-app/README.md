# Cue App

An AI-native, portfolio-aware **event-operations platform for VC firms**, built
as the product managed by MondayOS. This directory holds the actual application
code; the product-management source of truth (charter, vision, backlog, DoD)
lives in the MondayOS knowledge base — see [`PRODUCT_WORKSPACE.md`](./PRODUCT_WORKSPACE.md).

## Stack

React 18 · Vite · TypeScript · Tailwind CSS · React Router (see decision `DEC-0004`).
A client-side SPA over a mock data layer — no backend, no auth, no payments in
the MVP (scope guardrails: `DEC-0003`).

## Run it

```bash
cd projects/cue-app
npm install
npm run dev        # http://localhost:5173
```

Other scripts:

```bash
npm run build      # typecheck + production build to dist/
npm run preview    # serve the production build
npm run test       # unit tests (Vitest)
npm run typecheck  # tsc, no emit
```

## Sprint 1 status

| Surface | Task | State |
|---|---|---|
| App shell (nav + layout) | TASK-0030 | scaffolded here (sidebar + topbar + routing) |
| Auth placeholder | TASK-0031 | mock session in `src/lib/session.ts` |
| **Event dashboard** | **TASK-0032** | **built — the landing surface** |
| **Create-event flow** | **TASK-0033** | **built — persists via the store** |
| **Event detail page** | **TASK-0034** | **built — the command center** |
| Guest list model | TASK-0035 | not started |
| AI assistant placeholder | TASK-0036 | placeholder route |
| Seed demo data | TASK-0037 | partial (mock events in `src/lib/data.ts`) |

## Layout

```
src/
  main.tsx                 # entry
  App.tsx                  # shell + routes
  index.css                # Tailwind + base styles
  lib/
    types.ts               # domain model (portfolio-aware, no ticketing)
    data.ts                # seed events
    store.ts               # persistence seam: seed + localStorage, useEvents()
    select.ts              # filter/sort logic (unit-tested)
    create.ts              # draft validation + draft→CueEvent (unit-tested)
    detail.ts              # RSVP/roll-call/health derivations (unit-tested)
    ai.ts                  # offline AI assist generators (unit-tested)
    classification.ts      # event classification metadata
    branding.ts            # branding themes
    format.ts              # date/status/fill helpers (unit-tested)
    session.ts             # mock current user (auth placeholder)
    cn.ts                  # class joiner
  components/
    shell/                 # Sidebar, Topbar
    ui/                    # Badge, Button, Switch, Field, Select, SectionCard
    dashboard/             # EventCard, StatCard, StatusBadge, SegmentFilter
    create/                # ClassificationPicker, BrandingPicker, AiAssist
    detail/                # Panel, AiPanel + tabs/ (Overview, RollCall, Agenda…)
    icons.tsx              # inline SVG icon set
  pages/
    Dashboard.tsx          # TASK-0032 — event dashboard
    CreateEvent.tsx        # TASK-0033 — create-event flow
    EventDetail.tsx        # TASK-0034 — event command center
    ComingSoon.tsx         # placeholder for later surfaces
```
