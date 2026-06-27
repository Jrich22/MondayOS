# MondayOS — Knowledge System

**Version:** 0.1.0  
**Status:** Active  
**Last Updated:** 2026-06-27

---

## Purpose

The Knowledge System is the mechanism by which MondayOS learns from experience. It accumulates structured records of bugs, decisions, patterns, and operational procedures so that no problem is ever solved twice and no decision is made without the context of what has come before.

This is not documentation for humans to browse. It is a queryable, structured knowledge base that AI agents use to inform their reasoning in real time.

---

## Core Principle

> Every bug that is resolved, every decision that is made, every pattern that is discovered creates a knowledge entry. The system learns. Problems are not repeated.

The knowledge system is what separates MondayOS from a stateless AI tool. A stateless AI forgets everything between sessions. MondayOS accumulates.

---

## Knowledge Entry Types

### 1. Bug Knowledge Entry

Captures everything needed to understand a bug that has already been resolved — so that if the same or a similar symptom appears, the system can retrieve the resolution immediately.

**Schema:**

```markdown
---
id: BUG-{NNNN}
type: bug
title: {short description of the symptom}
status: resolved | active | wont-fix
created: {YYYY-MM-DD}
resolved: {YYYY-MM-DD or null}
severity: critical | high | medium | low
components: [{list of affected modules}]
tags: [{searchable tags}]
---

## Symptom

{What did the failure look like to an observer? Error message, behavior description, reproducible state.}

## Root Cause

{What was actually wrong? Be specific — "import ordering" is not a root cause; "Python resolves relative imports before package installs, causing stale cached bytecode to shadow the updated module" is.}

## Resolution

{What was changed to fix it? Reference the commit SHA.}
Commit: {sha}

## Prevention

{What would prevent this class of bug from occurring in the future? A test? A lint rule? An architectural change? A documentation update?}

## Related Entries

- [{ID}]({relative link}) — {brief description of relationship}
```

---

### 2. Decision Entry

Captures significant engineering decisions at any scope — not just architecture-level (those are ADRs), but also patterns, approach choices, and tradeoffs made during implementation.

**Schema:**

```markdown
---
id: DEC-{NNNN}
type: decision
title: {what was decided}
status: active | superseded-by | deprecated
created: {YYYY-MM-DD}
deciders: [{human or agent names}]
components: [{affected modules}]
tags: [{searchable tags}]
---

## Context

{What situation required a decision? What constraints or goals were in play?}

## Decision

{What was decided?}

## Rationale

{Why this option over alternatives? What would need to change for this decision to be revisited?}

## Alternatives Considered

| Alternative | Reason Not Chosen |
|---|---|
| {option} | {reason} |

## Consequences

{What does this make easier? What does it make harder? What do we now need to watch for?}
```

---

### 3. Pattern Entry

Captures a reusable solution to a recurring problem. Where a bug entry documents failure, a pattern entry documents proven approaches.

**Schema:**

```markdown
---
id: PAT-{NNNN}
type: pattern
title: {name of the pattern}
status: active | deprecated
created: {YYYY-MM-DD}
components: [{where this pattern applies}]
tags: [{searchable tags}]
---

## Problem

{What recurring situation does this pattern address?}

## Solution

{How to apply the pattern. Be concrete. Include code examples.}

## When to Use

{Conditions under which this pattern is appropriate.}

## When NOT to Use

{Conditions under which this pattern would be a mistake.}

## Known Applications

{Where in the codebase is this pattern currently applied?}
```

---

### 4. Runbook Entry

Captures an operational procedure — a step-by-step process for a task that happens infrequently enough that it cannot be done from memory.

**Schema:**

```markdown
---
id: RUN-{NNNN}
type: runbook
title: {name of the procedure}
status: active | deprecated
created: {YYYY-MM-DD}
last-verified: {YYYY-MM-DD}
components: [{relevant modules or systems}]
tags: [{searchable tags}]
---

## When to Use This Runbook

{What condition or event triggers this procedure?}

## Prerequisites

{What must be true before starting? Access required, state required.}

## Steps

1. {Step with expected outcome}
2. {Step with expected outcome}
...

## Verification

{How do you know the procedure succeeded?}

## Rollback

{If something goes wrong, how do you undo it?}

## Known Failure Modes

{What has gone wrong before, and what was the fix?}
```

---

## Knowledge Entry Lifecycle

```
Problem Encountered
      │
      ▼
Search Knowledge Base (by symptom, tag, component)
      │
      ├── Match Found → Apply Known Resolution → Log Application
      │
      └── No Match Found
              │
              ▼
         Resolve Problem
              │
              ▼
         Write Knowledge Entry (type: bug, decision, pattern, or runbook)
              │
              ▼
         Rebuild Index
              │
              ▼
         Entry Available for Future Retrieval
```

---

## Directory Structure

```
knowledge/
├── bugs/
│   ├── BUG-0001.md
│   ├── BUG-0002.md
│   └── ...
├── decisions/
│   ├── DEC-0001.md
│   └── ...
├── patterns/
│   ├── PAT-0001.md
│   └── ...
├── runbooks/
│   ├── RUN-0001.md
│   └── ...
└── index.md          ← Auto-generated; do not edit manually
```

---

## The Index

`knowledge/index.md` is the searchable entry point to the entire knowledge base. It is regenerated automatically whenever an entry is added or updated. It contains:

- A table of all entries (ID, type, title, status, components, tags)
- A tag cloud
- A component index (all entries affecting each component)

**Do not edit `index.md` manually.** Changes will be overwritten by the next regeneration.

---

## Retrieval

AI agents query the knowledge base at the start of any task. The query process:

1. Extract key signals from the task description: symptom keywords, component names, error codes, tags.
2. Search the index for matching entries.
3. Retrieve the full entry for each match.
4. Include relevant entries in the agent's context before reasoning about the task.

Retrieval is logged. Every knowledge query — what was searched, what was found, what was used — becomes part of the task's audit trail.

---

## Writing Quality Standards

A knowledge entry is a permanent record. It must meet these standards:

**Self-contained:** A reader who has never seen the original problem should be able to understand the entry completely without needing to reference code, PRs, or conversations.

**Specific:** "The API call failed" is not a root cause. "The Anthropic API returns a 429 after 60 requests per minute; the retry logic was not respecting the `retry-after` header" is a root cause.

**Actionable:** Each entry must include something actionable — a prevention step, a future test to add, a pattern to follow, or a procedure to run.

**Honest:** If the root cause is not fully understood, say so explicitly. An honest entry that says "cause is uncertain, workaround applied, further investigation needed" is better than a confident entry that is wrong.

---

## AI Agent Authorship

AI agents write knowledge entries as part of task completion. An agent-authored entry must:

1. Be reviewed by a human before being marked `status: active` (in Phase 1).
2. Include `authored_by: {model_id}` in the frontmatter.
3. Include a `confidence: high | medium | low` field in the frontmatter reflecting the agent's certainty about the content.

In Phase 2, agents with a sufficient track record of accurate entries may be granted the ability to publish entries directly without human review, subject to audit.

---

## Governance

- The knowledge base is append-first. Entries are not deleted; they are superseded or deprecated.
- Superseding an entry requires creating a new entry that explicitly references the one it replaces.
- Deprecated entries remain searchable but are labeled clearly as no longer applicable.
- The total count of entries is a lagging indicator of the system's learning. Review the entry rate monthly — if it is low, it means problems are either not being encountered (good) or not being documented (bad).
