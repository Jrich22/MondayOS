# sourcingBOT — Architecture

## Position within MondayOS

sourcingBOT is a **managed MondayOS product**, sibling to Cue App:

```
MondayOS/
├── projects/
│   ├── cue-app/       event operations for VC firms
│   └── sourcingbot/   ← this product
└── config/projects.json   product registry
```

It shares MondayOS *conventions* — never Cue's code. There is no import in
either direction, no shared package, and no shared storage key. The two are
independently buildable and independently deployable; the only coupling is that
both follow the same house style.

> **Deviation from the TASK-0053 design output.** RES-0106/RES-0107 proposed a
> separate GitHub repository, PostgreSQL/MongoDB, and a `products/sourcingbot/`
> tree importing `@mondayos/core/product`. No such core package exists in this
> repository, and a separate repo contradicts "managed MondayOS product". This
> increment follows the actual Cue convention instead. Recorded as
> [ADR-001](DECISIONS.md#adr-001).

## Stack

Chosen to match Cue exactly, so a MondayOS engineer moving between products
finds the same tools.

| Concern | Choice |
|---|---|
| Build | Vite 5 |
| UI | React 18 + react-router-dom 6 |
| Language | TypeScript 5, `strict`, `noUnusedLocals`, `noUnusedParameters` |
| Styling | Tailwind 3, design tokens in `tailwind.config.ts` |
| Tests | Vitest 2 + Testing Library, jsdom per-file pragma |
| Persistence | `localStorage` behind a store seam |
| Dev port | 5174 (Cue holds 5173, so both run at once) |

## Layers

```
┌──────────────────────────────────────────────────────────┐
│ pages/            Workspace · ReqDetail · Candidates ·   │
│                   CandidateProfile                       │
│ components/       shell/AppShell · ui/Primitives         │
├──────────────────────────────────────────────────────────┤
│ lib/store.ts      the ONLY persistence seam              │
│                   useSyncExternalStore + localStorage    │
├──────────────────────────────────────────────────────────┤
│ lib/  req.ts · brief.ts · candidate.ts ·                 │
│       req-candidate.ts · linkedin.ts                     │
│       pure, React-free, individually unit tested         │
└──────────────────────────────────────────────────────────┘
```

**The domain layer imports nothing from React.** Every rule — stage transitions,
fit scoring, brief readiness, supervision gates — is a pure function testable
without rendering. This is Cue's `lib/` convention and the reason 89 of the 99
tests need no DOM.

**Surfaces never touch storage.** They call `useWorkspace()` and the store's
mutators. Swapping `localStorage` for an API means rewriting `load` and `persist`
in one file.

## Module map

| Module | Owns | Key exports |
|---|---|---|
| `lib/types.ts` | Every entity shape | `Req` `SourcingBrief` `Candidate` `ReqCandidate` `SourcingSession` |
| `lib/ids.ts` | Id minting, timestamps | `newId` `nowIso` |
| `lib/req.ts` | Requisition lifecycle | `newReq` `transition` `acceptsSourcing` `sortForWorkspace` |
| `lib/brief.ts` | Structured brief + versioning | `newBrief` `reviseBrief` `isSourcingReady` `isStaleAgainst` |
| `lib/candidate.ts` | The persistent person | `newCandidate` `identityKey` `findPossibleDuplicates` `talentConcentration` |
| `lib/req-candidate.ts` | Req-scoped evaluation | `newReqCandidate` `advance` `computeFitScore` `reqHistoryFor` `joinPipeline` `assertNoIdentityDuplication` |
| `lib/linkedin.ts` | Supervision boundary | `startSession` `recordManualCapture` `PROHIBITED_CAPABILITIES` |
| `lib/store.ts` | Persistence + subscriptions | `useWorkspace` `addReqCandidate` `__resetStore` |
| `lib/seed.ts` | Synthetic demo data | `seedState` |

## State shape

Five **flat, normalized collections**. Candidates are not nested inside reqs, and
pipeline rows hold only ids:

```ts
interface WorkspaceState {
  reqs: Req[];
  briefs: SourcingBrief[];
  candidates: Candidate[];       // persistent people
  reqCandidates: ReqCandidate[]; // evaluations, holding candidateId + reqId
  sessions: SourcingSession[];
}
```

Normalization is what makes the model survive serialization. A nested shape would
force a copy of the person into every requisition on save — reintroducing exactly
the fragmentation the model exists to prevent. A test asserts that serialized
`reqCandidates` contain no person's name.

The two records meet only at render time, via `joinPipeline`, which produces a
throwaway view object that is never persisted.

## Storage

- **Key:** `sourcingbot.workspace.v1` (versioned; a future migration bumps it)
- **Scope:** one browser profile. No sync, no multi-device, no server.
- **Failure mode:** a full or blocked quota is swallowed — the in-memory copy
  still works for the session rather than crashing the app mid-edit.
- **Absent storage:** falls back to the seeded demo workspace.

### Test-environment note

Node 22+ defines its own `localStorage` global that stays `undefined` unless the
process gets `--localstorage-file`. Under vitest's jsdom environment
`window === globalThis`, so that undefined built-in shadows jsdom's
implementation and both `localStorage` and `window.localStorage` read as
undefined.

Cue never hit this because its tests never touch storage. sourcingBOT installs an
in-memory `Storage` in `src/test/setup.ts` so the persistence round trip is
genuinely exercised rather than silently skipped. This is the one deliberate
config difference from Cue — see [ADR-005](DECISIONS.md#adr-005).

## What is deliberately absent

No backend, no auth, no LinkedIn workflow, no outreach sending, no AI scoring, no
bulk operations. Each is deferred with a reason in [ROADMAP](ROADMAP.md).
`lib/linkedin.ts` ships the *gate* the future workflow must pass through, so the
boundary exists before the feature that will need it.
