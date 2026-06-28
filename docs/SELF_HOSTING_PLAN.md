# MondayOS Self-Hosting Plan

**Initiative:** 006  
**Status:** Design — Not Yet Implemented  
**Author:** Lead Software Engineer  
**Date:** 2026-06-28  
**Version:** 1.0

---

## Executive Summary

MondayOS has built a complete platform for capturing engineering knowledge, managing tasks, reasoning over project history, and orchestrating multi-step workflows. It has not used any of it on itself.

As of Sprint 1.6:

| Platform Capability | Status | Times Used on MondayOS Itself |
|---|---|---|
| `Monday.learn()` — knowledge capture | Production-ready | **0** |
| `Monday.task()` — task management | Production-ready | **0** |
| `Monday.ask()` — engineering questions | Production-ready | **0** |
| `Monday.search()` — knowledge search | Production-ready | **0** |
| `Monday.workflow()` — end-to-end workflows | Production-ready (Sprint 1.6) | **0** |
| Knowledge entries in the knowledge base | — | **0 of any type** |
| Tasks tracked through the API | — | **0 tasks created** |

This is not an oversight to correct retroactively. It is an architectural transition to plan carefully. The transition from "a platform we build" to "the platform we build with" is Initiative 006.

---

## Part 1 — Current State Audit

### 1.1 Where Files Are Written Directly

Every development activity that should produce platform artifacts currently bypasses the public API and writes files by hand. The table below lists every file category that represents an opportunity.

**Sprint Meta-Work (all direct file edits):**

| File | Written by | What it contains | What it should be |
|---|---|---|---|
| `docs/CHANGELOG.md` | Manual edit each sprint | Structured record of all changes | Generated from Sprint + Decision knowledge entries |
| `docs/DECISIONS.md` | Manual edit when ADR written | 10 architectural decision records | 10 `Decision` knowledge entries in the knowledge base |
| `logs/SESSION_LOG.md` | Manual edit each session | Session summaries, decisions, state | `Sprint`, `Lesson`, `Decision` entries via `monday learn` |
| `tasks/ACTIVE.md` | Empty placeholder file | Nothing currently | Active tasks via `monday task list` |
| `tasks/BACKLOG.md` | Empty placeholder file | Nothing currently | Backlog via `monday task list --status backlog` |
| `tasks/DONE.md` | Empty placeholder file | Nothing currently | Completed tasks via `tasks/completed/*.md` |
| `docs/*.md` (new specs) | Manual write | Specification documents | `Documentation` or `Runbook` entries; files retained |

**Internal Infrastructure (correctly bypasses API — do not change):**

All Python source files, `pyproject.toml`, test files, `conftest.py`, `workflows/definitions/*.yaml`. These are the platform itself, not artifacts produced by the platform.

### 1.2 The Scale of the Gap

Seven sprints completed. Ten ADRs written. Multiple sessions with explicit "lessons learned" and "open questions" sections. None of it is in the knowledge base.

Specific artifacts that exist in prose but not in the platform:

**Unrecorded Decision entries (from `docs/DECISIONS.md`):**
ADR-001 through ADR-010 — 10 architectural decisions. Zero exist as `Decision` knowledge entries. `monday ask "why did we choose Python?"` returns nothing.

**Unrecorded Sprint entries (from `docs/CHANGELOG.md` and `logs/SESSION_LOG.md`):**
Sprint 1 through Sprint 1.6 — 7 completed sprints. Zero exist as `Sprint` knowledge entries. `monday ask "what changed last sprint?"` returns nothing.

**Unrecorded Lesson entries (from `logs/SESSION_LOG.md`):**
Each sprint section documents specific lessons — name mangling for encapsulation, `tmp_path` isolation strategy, the Protocol vs ABC choice, YAML frontmatter round-trip gotchas, why `main(argv=None)` not `sys.exit`. Zero exist as `Lesson` entries.

**Unrecorded Bug entries:**
The SESSION_LOG mentions `TestEncapsulation` relies on Python name mangling as a known fragility. The CHANGELOG notes `setuptools.backends.legacy:build` → `setuptools.build_meta` fix. Zero Bug entries.

**Unrecorded Pattern entries:**
The `_Monday__brain` name-mangling encapsulation pattern is used consistently across six internal subsystems. The `autouse fixture with tmp_path` isolation pattern is used across all test classes. Zero Pattern entries.

**Untracked tasks:**
`tasks/active/` directory does not exist. `tasks/.sequences.json` does not exist. The sprint candidates listed at the end of each SESSION_LOG section have never been entered as tasks. The platform has a fully functional task lifecycle — it has never been exercised on its own development.

### 1.3 Why This Happened

The sequence was deliberate: build the platform first, adopt it later. Every sprint was about making the platform real. This was correct. You do not build a hospital by admitting patients before the operating theatre exists.

The operating theatre is now built. Sprint 1.6 completes Phase 1. The transition point is now.

---

## Part 2 — Opportunity Analysis

Opportunities are grouped by the engineering activity they target and ordered by the platform benefit they unlock. "Platform benefit" means: how much richer does `monday ask` get once this is adopted?

### Opportunity 1 — Capture Existing ADRs as Decision Entries  
**Impact: Very High | Effort: Low | Pre-req: None**

This is the highest-value, lowest-effort transition available. Ten complete ADRs exist in prose. `monday learn --type decision` converts each one into a queryable, relatable, searchable entry in the knowledge base.

Once done:
- `monday ask "why did we choose file-based storage?"` returns an actual answer
- `monday ask "what decisions have we made about the API boundary?"` finds ADR-007 and ADR-008
- The `ReasoningEngine` has real decision history to traverse relationships across
- New ADRs can check for conflicts: `monday search "model abstraction"` before writing a new provider decision

**What changes:** Development process. Add `monday learn --type decision` as the step that follows writing a new ADR, before or after the file edit. The `docs/DECISIONS.md` file is retained as the human-readable canonical record.

### Opportunity 2 — Capture Each Sprint as a Sprint Entry  
**Impact: High | Effort: Low | Pre-req: None**

The SESSION_LOG and CHANGELOG together contain everything needed for a `Sprint` knowledge entry: what was built, what tests pass, what changed, what comes next. Converting this to a `Sprint` entry makes it queryable.

Once done:
- `monday ask "what was accomplished in Sprint 1.4?"` works
- `monday ask "what changed recently?"` returns actual sprint history (RECENT_CHANGES intent)
- `monday ask "what should I read first to understand the platform?"` (ONBOARDING intent) surfaces foundational sprints
- Sprint entries link to their Decision entries via relationships: `SPRINT-0001 → implements → DEC-0001`

**What changes:** Sprint completion ritual. Add a `monday learn --type sprint` call at the close of each sprint. The content mirrors the CHANGELOG entry. Relationships are added manually or by the sprint-completion workflow (Opportunity 6).

### Opportunity 3 — Capture Lessons as Lesson Entries  
**Impact: High | Effort: Medium | Pre-req: Opportunity 1**

Every sprint documents specific "why we did it this way" insights that are not architectural decisions — they are implementation patterns, gotchas, and hard-won knowledge. These have a natural home in `Lesson` and `Pattern` knowledge types.

Examples ready to capture from existing SESSION_LOG entries:

| Lesson | Type | Source |
|---|---|---|
| `_Monday__brain` name mangling: encapsulation pattern | Pattern | Sprint 1.1 |
| `autouse fixture with tmp_path` isolates test I/O safely | Pattern | Sprint 1.3 |
| `main(argv=None) -> int` for testable CLI entry points | Pattern | Sprint 1.5 |
| `KnowledgeStore` decoupled from `EventBus` — orchestration at Monday layer | Decision | Sprint 1.2 |
| Draft status exempt from Level 3/4 validation — allows incremental authoring | Decision | Sprint 1.2 |
| Hard confidence cap 0.95 without LLM validation — explicit epistemic humility | Decision | Sprint 1.4 |
| `setuptools.backends.legacy:build` is invalid in current Python | Bug | Sprint 1.5 |
| `TestEncapsulation` relies on name-mangling artifact — document as risk | Bug | Sprint 1.1 |

Once done:
- `monday search "testing"` surfaces test isolation patterns before a new test is written
- `monday ask "have we seen this before?"` on any common error has a real knowledge base to search
- The ONBOARDING intent in `Monday.ask()` finally has entries to rank by connectivity

**What changes:** Development habit. At the close of each session, before writing SESSION_LOG, run `monday learn` for each significant insight. SESSION_LOG becomes a navigation index pointing to entries; not the primary record.

### Opportunity 4 — Track Sprint Work as Tasks  
**Impact: High | Effort: Medium | Pre-req: None**

Every sprint has a defined set of deliverables listed in the roadmap. None have been tracked as tasks. The platform has had a fully functional task lifecycle since Sprint 1.3. It has never been used on its own work.

The value of self-use is concrete here: if Sprint 2.1 is "implement async task execution," then `TASK-0001` through `TASK-0008` represent the specific deliverables. `monday task list --status blocked` surfaces blockers. `monday ask "what tasks are currently blocked?"` returns a real answer. Completed tasks prompt knowledge entry creation automatically (per the roadmap exit criterion for Phase 1.4: "completed tasks prompt knowledge entry creation").

**What changes:** Sprint start ritual. At the beginning of each sprint, create tasks for each deliverable using `monday task create`. At sprint end, complete them using `monday task complete`. The ACTIVE.md, BACKLOG.md, DONE.md placeholder files can be removed — the task system replaces them.

### Opportunity 5 — Replace SESSION_LOG Sections with Structured Entries  
**Impact: Medium | Effort: Medium | Pre-req: Opportunities 1-4**

The SESSION_LOG is a narrative chronicle written in prose. It contains:
- Sprint summaries → `Sprint` entries
- Key decisions → `Decision` entries  
- Lessons learned → `Lesson` entries
- Open questions → tasks (status: BACKLOG)
- Technical debt notes → tasks (status: BACKLOG) or `Bug` entries

Once Opportunities 1-4 are established, SESSION_LOG transforms from the primary record to a navigation document: short, pointing to formal entries rather than duplicating them.

**What changes:** The SESSION_LOG is reformatted. Each section becomes: what was done (one paragraph), links to Sprint/Decision/Lesson entries created, open questions as task IDs. The body stays human-readable; the substance moves into the platform.

### Opportunity 6 — Sprint Completion Workflow  
**Impact: High | Effort: Low (workflow YAML only) | Pre-req: Opportunities 1-4 established**

Design a `sprint-completion` workflow that makes the above repeatable and non-optional. Every sprint ends the same way: the workflow is invoked, it asks a set of questions, it captures a Sprint entry, it completes the sprint tasks, it prompts for Lesson entries.

See Part 3 for the full workflow design.

### Opportunity 7 — Sprint Planning Workflow  
**Impact: Medium | Effort: Low | Pre-req: Opportunity 4**

Design a `sprint-planning` workflow that begins a sprint by creating the standard set of tasks, querying the knowledge base for relevant prior art, and opening a human approval gate for the sprint scope.

See Part 3 for the full workflow design.

### Opportunity 8 — ADR Capture Workflow  
**Impact: Medium | Effort: Low | Pre-req: Opportunity 1**

Design an `adr-capture` workflow that bundles the steps needed to capture a new architectural decision: check for conflicts, get human confirmation of the decision text, write the `Decision` entry, and suggest related entries for the relationships field.

See Part 3 for the full workflow design.

### Opportunity 9 — Release Preparation Workflow  
**Impact: Low (Phase 1 only has internal "releases") | Effort: Medium | Pre-req: Opportunities 1-6**

Design a `release-prep` workflow that aggregates Sprint entries since the last release, checks for open Bug entries, creates a release task, and generates the release notes as a `Documentation` entry.

See Part 3 for the full workflow design.

### Opportunity 10 — CHANGELOG Generation from Knowledge Entries  
**Impact: Medium | Effort: High | Pre-req: Opportunities 1, 2, 6**

`docs/CHANGELOG.md` is currently the primary sprint record. Once Sprint entries are being consistently captured, CHANGELOG.md can be generated from them. This is a Phase 2 capability: it requires a `monday export changelog` command or a `changelog-generation` workflow with an `ask` step that synthesizes sprint entries into the Keep a Changelog format.

**Architectural note:** CHANGELOG.md should not be removed. It is a Git-standard artifact and is visible to external contributors without running the platform. The goal is for it to be generated from the knowledge base rather than hand-edited — making it a derived artifact, not the source of truth.

---

## Part 3 — Workflow Designs

These workflows use only the step types available in Sprint 1.6. No new platform features are required to implement them.

### Workflow A — `sprint-completion`

**Trigger:** Human, after sprint code is committed and tests pass.

**Purpose:** Capture the sprint as a formal knowledge entry, close sprint tasks, and prompt for Lesson entries.

```
inputs:
  sprint_name:    Name of the completed sprint (e.g. "Sprint 1.6")
  sprint_number:  Sprint version string (e.g. "1.6")
  what_was_built: One-paragraph summary of the sprint's deliverables
  test_count:     Total passing tests at sprint close
  key_changes:    Comma-separated list of major changes

steps:
  1. ask        — "What do we know about {sprint_name}? Any prior decisions relevant to this work?"
  2. search     — Search for related decision and pattern entries
  3. task_create — Create "Close Sprint {sprint_name}" task
  4. human_approval — Display summary: sprint name, changes, related findings; confirm capture
  5. learn      — type: sprint; title: "{sprint_name}"; content includes what_was_built, test_count
  6. task_complete — Complete the sprint close task
```

**Outputs:** One Sprint entry. One completed task. Basis for subsequent Lesson capture via `monday learn`.

**Human touchpoints:** One approval gate showing the full sprint summary before it is written.

---

### Workflow B — `sprint-planning`

**Trigger:** Human, at the start of a new sprint.

**Purpose:** Research prior art, create sprint deliverable tasks, establish the sprint's knowledge context.

```
inputs:
  sprint_name:  Name of the sprint being planned (e.g. "Sprint 2.1")
  objective:    One-sentence sprint goal
  deliverables: Comma-separated list of planned deliverables

steps:
  1. ask        — "What patterns and decisions are relevant to {objective}?"
  2. search     — Find related entries from previous sprints
  3. human_approval — Display research findings; confirm sprint scope
  4. task_create — Create sprint planning task
  5. learn      — type: sprint; status: draft; title: "{sprint_name} — Plan"
  6. task_complete — Close planning task
```

**Note:** Individual deliverable tasks are created manually via `monday task create` after the workflow, one per deliverable. A future iteration can take a `deliverables` list and fan out task creation.

---

### Workflow C — `adr-capture`

**Trigger:** Human, when a new architectural decision is made.

**Purpose:** Check for conflicting or related decisions, then capture the ADR as a formal Decision entry.

```
inputs:
  adr_title:    Short title of the decision (e.g. "YAML as workflow definition format")
  adr_context:  What problem this decision addresses
  adr_decision: What was decided
  adr_consequences: Comma-separated list of consequences

steps:
  1. search         — Search for existing decisions related to "{adr_title}"
  2. ask            — "Have we made any decisions that conflict with: {adr_decision}?"
  3. human_approval — Show related findings; confirm no conflicts; approve capture
  4. learn          — type: decision; title: "{adr_title}"; full content from adr_* inputs
```

**Outputs:** One Decision entry. The human continues to update `docs/DECISIONS.md` as the prose record (this workflow does not modify it).

**Relationship note:** After capture, the engineer manually adds relationship links: which sprint this decision came from, which patterns it enables, which bugs it prevents.

---

### Workflow D — `bug-capture`

**Trigger:** Human, when a bug is found during development.

**Purpose:** Capture the bug immediately before the context is lost, and check if it's been seen before.

```
inputs:
  bug_title:    Short description of the bug
  bug_symptom:  What was observed
  bug_cause:    Root cause (if known; otherwise "unknown")
  bug_fix:      How it was fixed (if resolved; otherwise empty)
  component:    Which module was affected

steps:
  1. search         — Search for similar issues: "{bug_title}"
  2. ask            — "Have we seen this before? What do we know about {component} bugs?"
  3. human_approval — Show search results; confirm this is new (or a recurrence)
  4. learn          — type: bug; title: "{bug_title}"; content includes symptom, cause, fix
```

**Value proposition:** Bugs found during Phase 1 development are exactly the bugs Phase 2 development will encounter again. Capturing them now means `monday ask "have we seen this before?"` has an actual answer. The `TestEncapsulation` name-mangling fragility and the `setuptools` build backend fix are both candidates for immediate capture.

---

### Workflow E — `release-prep`

**Trigger:** Human, before tagging a release.

**Purpose:** Confirm all sprint entries are captured, check for open blockers, produce release notes.

```
inputs:
  release_version:  Version string (e.g. "0.7.0")
  since_version:    Previous version (e.g. "0.6.0")

steps:
  1. search         — Search for all sprint entries since {since_version}
  2. search         — Search for any open Bug entries (status: active)
  3. ask            — "What was accomplished between {since_version} and {release_version}?"
  4. human_approval — Display sprint summary and open bugs; confirm release readiness
  5. task_create    — Create "{release_version} release" task
  6. learn          — type: documentation; title: "Release {release_version}"; content: aggregated notes
  7. task_complete  — Close release task
```

**Note:** This workflow surfaces the compounding value of self-hosting. If Sprint entries have been consistently captured, Step 3's `ask` answer will be rich. If they have not, it will be empty — a direct signal of how much self-hosting adoption has been achieved.

---

### Workflow F — `session-retrospective`

**Trigger:** Human, at the close of a development session.

**Purpose:** Capture the session's lessons and open questions before the context is lost.

```
inputs:
  session_summary: What was worked on this session
  key_insight:     The most important thing learned (one sentence)

steps:
  1. ask            — "What do we already know about: {key_insight}?"
  2. human_approval — Show related knowledge; confirm lesson is worth capturing
  3. learn          — type: lesson; title: "{key_insight}"; full content
```

**Design note:** This workflow is intentionally minimal. A session may produce 0-3 lessons. It runs multiple times per session. The ask step prevents duplication by surfacing related entries before capture.

---

## Part 4 — Engineering Activities and Their Workflow Mapping

This table covers all recurring MondayOS engineering activities and whether they should stay manual, move to the platform, or be replaced by a workflow.

| Engineering Activity | Current Method | Recommended | Workflow |
|---|---|---|---|
| Write a new ADR | Edit `docs/DECISIONS.md` | Also run `adr-capture` | Workflow C |
| Complete a sprint | Edit CHANGELOG, SESSION_LOG | Run `sprint-completion` | Workflow A |
| Plan a sprint | Read ROADMAP, write SESSION_LOG | Run `sprint-planning` | Workflow B |
| Create deliverable tasks | Informal / not tracked | `monday task create` per deliverable | Manual CLI |
| Mark a deliverable complete | Not tracked | `monday task complete` | Manual CLI |
| Record a lesson learned | Write SESSION_LOG section | `monday learn --type lesson` or `session-retrospective` | Workflow F |
| Capture a bug found during dev | Write SESSION_LOG note | `monday learn --type bug` or `bug-capture` | Workflow D |
| Document a new pattern | Write SESSION_LOG note or not at all | `monday learn --type pattern` | Manual CLI |
| Prepare a release | Edit CHANGELOG, tag Git | Run `release-prep` | Workflow E |
| Ask "have we seen this before?" | Read SESSION_LOG / CHANGELOG manually | `monday ask "..."` | Platform ready |
| Find relevant prior decisions | grep `docs/DECISIONS.md` | `monday search "..."` | Platform ready |
| Update CHANGELOG.md | Manual edit each sprint | Eventually: generated from Sprint entries | Phase 2 |
| Generate release notes | Manual prose | Eventually: synthesized by `release-prep` workflow | Workflow E |
| Review test strategy | Read SESSION_LOG | `monday ask "what testing patterns do we use?"` | Needs Pattern entries first |

---

## Part 5 — Migration Plan

### Principles

1. **Additive, not disruptive.** No existing file is deleted. No existing process is broken. New platform usage is layered on top of current practice until it is the natural path.

2. **Start with reads.** The highest-value early actions are asking questions of the platform and discovering it has no answers. This creates honest pull toward populating it. Don't skip to generating changelogs from empty sprint entries.

3. **Capture the backlog, then move forward.** Populate existing ADRs and sprints first. Then adopt new workflows for Sprint 2.1 onward.

4. **Measure adoption by asking.** After each migration phase, run `monday ask "what do we know about our own architecture?"` If the answer is rich, the phase succeeded. If it is thin, something is not being captured.

5. **Let the empty platform embarrass you into using it.** Every time `monday ask` returns "No relevant information found," that is feedback — not failure.

---

### Phase A — Backfill: Capture Everything That Already Exists

**Timing:** Before Sprint 2.1 begins. Can be done in a single session.  
**Cost:** ~90 minutes of `monday learn` calls.  
**Benefit:** Transforms the knowledge base from empty to historically grounded.

**Tasks:**

| What | How | Count |
|---|---|---|
| Capture ADR-001 through ADR-010 as Decision entries | `monday learn --type decision --title "..." --content "..."` | 10 entries |
| Capture Sprint 1.1 through Sprint 1.6 as Sprint entries | `monday learn --type sprint --title "Sprint 1.x" --content "..."` | 7 entries |
| Capture key lessons from SESSION_LOG | `monday learn --type lesson` | ~10 entries |
| Capture the 2 known bugs (name-mangling fragility, setuptools fix) | `monday learn --type bug` | 2 entries |
| Capture the 3 known patterns (name mangling, tmp_path, `main(argv=None)`) | `monday learn --type pattern` | 3 entries |
| Create Sprint 2.1 deliverable tasks | `monday task create` × N | N tasks |

**Exit check:** `monday ask "why did we choose Python?"` should return an answer. `monday ask "what changed in Sprint 1.3?"` should return an answer. `monday ask "what testing patterns do we use?"` should return an answer.

---

### Phase B — Forward Adoption: New Work Through the Platform

**Timing:** Sprint 2.1 onward. Permanent change to development practice.  
**Cost:** ~5-10 minutes per sprint, ~2 minutes per decision or lesson.  
**Benefit:** Every sprint is self-documented in the platform. Knowledge compounds automatically.

**Practices:**

1. **Sprint start:** Run `monday workflow run sprint-planning` to research prior art and create sprint tasks.

2. **During sprint:** Each new ADR → immediately `monday workflow run adr-capture`. Each bug found → `monday learn --type bug`. Each new pattern identified → `monday learn --type pattern`. Each deliverable completed → `monday task complete TASK-XXXX`.

3. **Sprint end:** Run `monday workflow run sprint-completion` to capture the Sprint entry and close tasks. Then run `monday learn --type lesson` for each insight not already captured.

4. **CHANGELOG.md:** Continue editing manually. In Phase C, begin generating it from sprint entries instead.

---

### Phase C — Platform as Authority: CHANGELOG Generated, SESSION_LOG Thinned

**Timing:** Sprint 2.3 or 2.4, once Phase B practices are established and ~20 sprint entries exist.  
**Prerequisite:** Phase B adopted for at least 3 sprints.  
**Cost:** Implementation sprint to add `monday export changelog` or a changelog-generation workflow.

**Changes:**

- `docs/CHANGELOG.md` is generated from Sprint knowledge entries, not hand-edited.
- `logs/SESSION_LOG.md` is reformatted: each section becomes a short navigation document pointing to formal knowledge entries by ID rather than duplicating their content.
- `docs/DECISIONS.md` remains as the human-readable prose ADR log but is secondary to the knowledge base. New ADRs are written to the knowledge base first; `DECISIONS.md` is updated second (or generated from it in Phase D).
- `monday ask "what changed last sprint?"` is the first place to check before reading files.

---

### Phase D — Full Self-Hosting: Documentation as Derived Output

**Timing:** Phase 2 (multi-session, multi-agent collaboration).  
**Prerequisite:** Phase C complete and the knowledge base has 50+ entries with rich relationships.

**Changes:**

- CHANGELOG.md, DECISIONS.md, and release notes are all generated from knowledge entries.
- Session retrospectives are captured automatically by a future session-close hook.
- `monday ask` is the first tool consulted when starting any new work — not grep, not reading files.
- The `SESSION_LOG.md` is retired or becomes an index of sessions with entry IDs only.
- Sprint planning `monday ask "what is still blocked from Sprint 2.1?"` returns live task data.

---

## Part 6 — Architectural Benefits of Self-Hosting

Beyond convenience, self-hosting produces specific engineering advantages.

### The Platform Validates Itself

Every sprint that runs through the platform is a customer using the platform. When Sprint 2.1 runs `sprint-planning` and it works well, that is evidence the workflow design is sound. When it breaks or is awkward, that is a bug report with full context — the engineer who found it is also the one fixing it. This is the tightest possible feedback loop.

### Decisions Become Queryable

The 10 ADRs in `docs/DECISIONS.md` are readable but not queryable. Once they are Decision entries, `monday ask "have we made any decisions about storage?"` returns a ranked list with confidence scores. New engineers (or AI agents joining the project) get onboarding answers from `monday ask "what should I read first?"` instead of hunting through prose files.

### Knowledge Compounds Across Sprints

The `ReasoningEngine` traverses the relationship graph. An ADR that links to the sprint that produced it, to the bugs it prevents, and to the patterns it enables creates a dense subgraph. `monday ask "what do we know about the task system?"` returns not just direct matches but entries two hops away via relationships. This compound value does not exist until entries accumulate and are related to each other.

### Sprint History Becomes Searchable

After 20 sprints of consistent capture, `monday ask "what was the decision that led to the current architecture?"` has a real answer. `monday ask "what bugs have we seen in the knowledge system?"` returns a ranked list. Today, the only way to answer these questions is to read several hundred lines of SESSION_LOG manually.

### The Roadmap Exit Criterion for Phase 1.4 Gets Met

The roadmap states: "At least 5 real knowledge entries created from Phase 1.1–1.3 work." This criterion was defined at the foundation. Phase B of the migration plan makes it the natural output of sprint completion — not a separate checklist item.

### MondayOS Becomes Demonstrable

A MondayOS knowledge base with 50+ entries, rich relationships, and answerable engineering questions is a demonstration of the platform's value. The same platform that tracks its own architecture is the one offered to other teams. The dogfood becomes the demo.

---

## Appendix — Parallel Tracking Systems to Retire

The following files were created as placeholders during the Foundation phase. Once Platform adoption begins, they should be gradually retired in favor of the API-managed equivalents.

| File | Placeholder For | API Replacement | Retire When |
|---|---|---|---|
| `tasks/ACTIVE.md` | Active task list | `monday task list` | Phase B |
| `tasks/BACKLOG.md` | Backlog | `monday task list --status backlog` | Phase B |
| `tasks/DONE.md` | Completed tasks | `tasks/completed/*.md` (managed) | Phase B |
| `memory/PROJECT_MEMORY.md` | Project state | `Monday.learn()` + Sprint entries | Phase C |
| `memory/LESSONS_LEARNED.md` | Lesson log | `monday learn --type lesson` | Phase B |
| `memory/AGENT_MEMORY.md` | Agent history | Phase 2: per-agent memory | Phase 2 |

Retirement means: each file gets a one-line tombstone pointing to the replacement, then is committed and not updated further. Files are not deleted — they remain in Git history as artifacts of the pre-adoption period.

---

## Appendix — What Is Out of Scope for Initiative 006

Self-hosting does not mean routing all Python I/O through the public API. These file writes correctly bypass the public API and should remain unchanged:

- `knowledge/store.py` — writing `knowledge/**/*.md` entries to disk  
- `tasks/manager.py` — writing `tasks/active/*.md` and `tasks/completed/*.md`  
- `workflows/execution.py` — writing `logs/workflows/*.json`  
- All test file I/O

These are the platform itself. The platform's internals do not go through the platform's public API. Initiative 006 targets the development process that uses and extends the platform — not the platform's own storage layer.

---

*This document is a design record. Implementation begins with Phase A. No code changes are required to begin Phase A — only the `monday` CLI is needed, which is available now.*
