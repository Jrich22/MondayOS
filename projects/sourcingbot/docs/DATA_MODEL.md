# sourcingBOT — Data Model

## The central rule

```
Candidate      a persistent PERSON, across every requisition, for years
ReqCandidate   that Candidate's evaluation and status for ONE requisition
```

### Why the separation exists

Consider Priya Raman, evaluated for a Staff Platform Engineer role and, three
weeks later, for a Senior ML Engineer role.

**Collapsed model** (candidate row per req) — the usual design:

| id | name | req | stage | score |
|---|---|---|---|---|
| 1 | Priya Raman | REQ-014 | responded | 79 |
| 2 | Priya Raman | REQ-018 | rejected | 0 |

Two humans now exist where there is one. Her email lives twice and can drift.
"How many people do we know at Northwind Cloud?" double-counts her. Opening a
third req surfaces two Priyas with contradictory stages and no indication they
are the same person.

**sourcingBOT model:**

```
Candidate  cand_priya   Priya Raman · Northwind Cloud · Kubernetes, Go

ReqCandidate  rc_priya_infra  → cand_priya · REQ-014 · responded · 79 · brief v3
ReqCandidate  rc_priya_ml     → cand_priya · REQ-018 · rejected  ·  0 · brief v1
```

One person. Two independent evaluations, both simultaneously true. Concentration
analytics count her once. A future req finds her with both prior evaluations and
their reasoning attached.

### How it is enforced

Documentation erodes. This rule is mechanical:

```ts
const CANDIDATE_OWNED_FIELDS = [
  "fullName", "headline", "email", "linkedInUrl",
  "location", "roles", "skills", "origin",
];

export function assertNoIdentityDuplication(value: object): void {
  const leaked = CANDIDATE_OWNED_FIELDS.filter((f) => f in value);
  if (leaked.length > 0) throw new IdentityDuplicationError(leaked);
}
```

Denormalizing a name onto a pipeline row "just for rendering" throws, and a test
asserts it. A second test serializes the store and asserts no person's name
appears anywhere in `reqCandidates`.

---

## Entities

### Req

The unit of work. Every brief, session, and evaluation hangs off exactly one.

```
draft ──→ open ──→ on-hold ──→ closed
  └────────┴─────────┴───────────┘   (closed is terminal)
```

Sourcing is accepted **only** while `open` — `acceptsSourcing()` gates session
creation, so a draft or closed req cannot accrue supervised sessions.

### SourcingBrief

The structured, reusable definition of what this search wants. One per req.

Requirements are **addressable objects**, not prose, so an assessment can cite
the exact requirement it answers:

```ts
{ id: "rq_infra_2", label: "Owned Kubernetes in production",
  kind: "required", weight: 4 }
```

- `required` — disqualifying if absent
- `preferred` — weighted 1–5, never blocking

**Versioning.** Every content change routes through `reviseBrief`, which bumps
`version`. A ReqCandidate records the `briefVersion` it was evaluated against, so
`needsReassessment()` can flag evaluations made against a superseded bar instead
of silently rewriting history when the brief moves.

**Readiness.** `isSourcingReady()` requires a headline and ≥1 required
requirement. Without a discriminating requirement every candidate trivially
"fits", which is worse than having no brief.

### Candidate — the persistent person

Owns identity, contact, career history, skills, origin, and notes *about the
human*. Knows nothing about requisitions; `lib/candidate.ts` never imports a req
type, and a test asserts no `reqId`/`stage`/`fitScore` field exists.

**Duplicate detection** is advisory, never automatic. `identityKey()` prefers
email; absent that, normalized name + current company. `findPossibleDuplicates`
reports candidates for a human to confirm and **never merges** — wrongly merging
two people is far more damaging than carrying a duplicate for a day.

**`origin`** records how the person entered: `manual-entry`, `referral`,
`inbound`, or `supervised-linkedin`. It is audit-relevant — only the last may be
recorded during a supervised session.

### ReqCandidate — one evaluation

```
identified → reviewing → contacted → responded → advanced
     └────────────┴───────────┴───────────┴────────→ rejected
                                            rejected → reviewing (revisit)
```

Stage skips throw. Every transition appends a `StageEvent` with actor, timestamp,
and reason, so the pipeline carries its own audit trail.

### Fit scoring

```
any REQUIRED requirement not met  →  0
all required met, none preferred judged  →  100
otherwise  →  round(earned preferred weight / judged preferred weight × 100)
```

Two decisions worth stating:

- **A single unmet required item caps the score at 0.** "Required" is
  disqualifying by definition; letting preferred matches average it away would
  produce a confidently wrong number.
- **`unknown` counts as unmet for required items** (absence of evidence is not
  evidence) but is *excluded* from the preferred pool rather than penalized —
  not having asked yet should not look like a negative finding.

### SourcingSession

A record that **a human did something** — not an automation state machine. It
holds the operator, an explicit policy acknowledgement, timestamps, and a count
of manually captured candidates. It drives no browser and issues no requests.
See [LINKEDIN_POLICY](LINKEDIN_POLICY.md).

## Relationships

```
Req 1───1 SourcingBrief
 │
 │ 1───N ReqCandidate ──N───1 Candidate
 │                              (a Candidate has N ReqCandidates
 │                               across N different Reqs)
 └ 1───N SourcingSession
```

`(candidateId, reqId)` is unique — `addReqCandidate` throws
`DuplicateReqCandidateError` on a second row, since duplicates would split one
evaluation and corrupt pipeline counts.

## Invariants

1. A ReqCandidate never holds Candidate-owned fields. *(enforced + tested)*
2. `(candidateId, reqId)` is unique. *(enforced + tested)*
3. A brief's `version` increases on every content change. *(all mutations route through `reviseBrief`)*
4. Stage changes follow the graph and append history. *(enforced + tested)*
5. A session requires a named operator and an explicit acknowledgement. *(enforced + tested)*
6. Collections persist normalized; no person data inside a pipeline row. *(tested against the serialized form)*
