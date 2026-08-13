# sourcingBOT

A supervised recruiting sourcing workspace, managed as a MondayOS product.

sourcingBOT gives recruiters one place to run a requisition end to end: a
structured **Sourcing Brief** that says what "good" means, a **persistent talent
pool** where a person exists once and forever, and a **per-requisition
pipeline** that keeps each evaluation independent.

**Status:** product workspace foundation (TASK-0054). The LinkedIn sourcing
workflow is deliberately **not** built yet — only the supervision boundary it
must pass through. See [`docs/ROADMAP.md`](docs/ROADMAP.md).

---

## The one rule that shapes everything

```
Candidate      a persistent PERSON — exists once, across every requisition,
               for years. Owns identity, contact, career history, skills.

ReqCandidate   that person's EVALUATION AND STATUS for ONE requisition.
               Owns stage, assessment, fit score, and req-scoped history.
               Holds candidateId + reqId, and nothing else about the person.
```

Most applicant systems collapse these into one "candidate per req" row. That
fragments the human being: the same person sourced for three roles becomes three
records, cross-req history disappears, and talent concentration analytics become
impossible to compute correctly.

Here the separation is **enforced, not just documented** —
`assertNoIdentityDuplication` throws if a Candidate-owned field appears on a
ReqCandidate, and a test asserts it. Full rationale in
[`docs/DATA_MODEL.md`](docs/DATA_MODEL.md).

## LinkedIn boundary

Every LinkedIn interaction is **human-initiated and human-supervised**. This
product does not implement, and will not implement, unattended scraping,
scheduled crawling, rate-limit bypass, or automation evasion. A sourcing session
cannot even be created without a named operator who has acknowledged the
supervision policy. See [`docs/LINKEDIN_POLICY.md`](docs/LINKEDIN_POLICY.md).

## Quick start

```bash
cd projects/sourcingbot
npm install
npm run dev        # http://localhost:5174
```

| Command | What it does |
|---|---|
| `npm run dev` | Dev server on port 5174 (Cue uses 5173) |
| `npm test` | Vitest suite |
| `npm run typecheck` | TypeScript, strict |
| `npm run build` | Production build |

Full setup and troubleshooting: [`docs/RUNBOOK.md`](docs/RUNBOOK.md).

## Documentation

| Document | Covers |
|---|---|
| [VISION](docs/VISION.md) | Who it is for and what problem it solves |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | Layers, module map, state and persistence |
| [DATA_MODEL](docs/DATA_MODEL.md) | Entities, the Candidate/ReqCandidate rule, fit scoring |
| [ROADMAP](docs/ROADMAP.md) | What ships when, and what is deferred |
| [DECISIONS](docs/DECISIONS.md) | Architecture decision records |
| [LINKEDIN_POLICY](docs/LINKEDIN_POLICY.md) | The supervision boundary and its enforcement |
| [RUNBOOK](docs/RUNBOOK.md) | Setup, commands, data reset, troubleshooting |

## This increment

**Built:** premium application shell · Req Workspace · structured Sourcing Brief
with versioning · persistent Candidate model · separate ReqCandidate model ·
per-req pipeline with stage machine and fit scoring · talent concentration ·
supervised-session boundary · local persistence · 99 tests.

**Not built:** the LinkedIn browser workflow, any backend or real database,
authentication, bulk operations, AI scoring, outreach sending.

Data lives in `localStorage` under `sourcingbot.workspace.v1` and is seeded with
synthetic demo content on first run. No real people, no scraped data, and no
profile URLs on any seeded record.
