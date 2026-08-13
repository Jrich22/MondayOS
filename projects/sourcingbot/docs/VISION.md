# sourcingBOT — Product Vision

## The problem

Recruiting sourcing tools optimize for filling *this* requisition. The moment a
role closes, the work evaporates: the people you found, the judgement you formed
about them, and the reasoning behind a rejection all become inert rows attached
to a dead req.

Six months later the same team opens a similar role and the recruiter starts
over — re-finding people they already evaluated, re-forming opinions they
already had, and often re-contacting someone who declined for a specific,
recorded reason nobody can now retrieve.

The root cause is a data-model choice. Most systems key a candidate to a
requisition. One human being becomes N records, and everything that would
compound across requisitions is fragmented at the point of capture.

## The bet

**Sourcing knowledge compounds if — and only if — the person is the durable
unit, not the application.**

sourcingBOT stores a person once. Every requisition they are considered for adds
an evaluation *about* that person without duplicating them. That single choice is
what makes the interesting things possible:

- A recruiter opening a new req sees who they already know, with the reasoning
  from every prior evaluation attached.
- "We keep losing candidates to Northwind Cloud" becomes a computable fact
  rather than a hallway impression.
- A rejection carries its reason forward, so the next recruiter reads *why*
  rather than re-deriving it.

## Who it is for

**Primary — the recruiter running requisitions.** Wants less re-work, a defensible
record of judgement, and no spreadsheet reconciliation.

**Secondary — the hiring manager.** Wants to see pipeline honestly: who is in it,
how they were assessed, against what bar.

**Tertiary — the talent leader.** Wants concentration and conversion patterns
across reqs, which only exist if people are stored once.

## What sourcingBOT is not

- **Not an ATS.** It ends where a formal application begins. No offers, no
  interview scheduling, no compliance workflow.
- **Not a scraper.** LinkedIn is a place a human works, supervised, with the
  product recording what they did. See [LINKEDIN_POLICY](LINKEDIN_POLICY.md).
- **Not an autonomous agent.** It does not decide who to contact, write outreach
  unattended, or advance anyone without a person choosing to.

## Principles

1. **The person is the unit.** Anything that fragments a human across
   requisitions is a bug, however convenient.
2. **Judgement is recorded, not implied.** A stage change carries who moved it
   and why. A fit score cites the brief version it was computed against.
3. **The tool never claims a human acted when they did not.** Supervision is
   enforced at construction, not asserted in a banner.
4. **Structure over prose.** Briefs are addressable requirements, so an
   evaluation can cite exactly what it answers.
5. **Refuse rather than degrade.** Where an operation would produce a misleading
   record, it throws.

## What success looks like

A recruiter opens a new requisition and immediately sees eleven people they have
already evaluated, each with prior reasoning attached — and contacts three of
them the same day, because the work from the last two years was never thrown
away.
