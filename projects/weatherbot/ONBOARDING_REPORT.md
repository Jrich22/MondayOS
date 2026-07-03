# Weatherbot — MondayOS Onboarding Report

**Generated:** 2026-06-30 01:20 UTC  
**Source path:** `/Users/jrich/AI-Labs/WeatherBot`  
**Description:** CLI weather fetcher and alert system  

---

## Executive Summary

Repository health is Good (85/100) and 3 warning(s). Git: active branch: sprint-3-trading-safety; 31 uncommitted change(s). 4 test file(s) found. Knowledge base: 34 active entries (8 feature, 8 sprint, 6 documentation). Tasks: 10 active task(s).

| Metric | Value |
| --- | --- |
| Health Score | 85/100 (Good) |
| Advisory Confidence | 71% |
| Knowledge Entries in Base | 17 (3 new this run) |
| Sprint Recommendation | Start high-priority work (TASK-0003, TASK-0005) |

---

## What MondayOS Knows

**Knowledge base:** 34 active entries (3 newly imported this run, 14 already present).

**Source documents:**
- `changelog`
- `decisions`
- `session-log`
- `roadmap`
- `workflows`
- `self-hosting`

## Documentation Inventory

- **[OK]** Present: README.md
- **[OK]** Present: docs/CHANGELOG.md
- **[OK]** Present: docs/DECISIONS.md
- **[OK]** No broken internal links found in Markdown files

---

## Knowledge Gaps

**Missing knowledge types:**
- No pattern entries captured
- No bug entries captured
- No knowledge covering task topic: 'Epic 2: Extract shared common/ library'
- No knowledge covering task topic: 'Epic 5: Robustness & correctness cleanup'
- No knowledge covering task topic: 'Rail 4 follow-up: persisted dedup + startup reconc'

---

## Engineering Risks

### [HIGH] Dirty working tree — 31 uncommitted change(s)
**Impact:** This warning, if unaddressed, will compound into a larger issue.

**Recommendation:** Commit or stash all changes before deployment.

### [HIGH] 7 high-priority task(s) unstarted in backlog
**Impact:** High-priority work is not progressing.

**Recommendation:** Start high-priority tasks: TASK-0003, TASK-0005, TASK-0006.

### [MEDIUM] 3 large file(s) over 500 KB
**Impact:** Code quality signals accumulate into larger maintainability problems.

**Recommendation:** Consider moving large binaries or data files to external storage.

### [MEDIUM] 7 high-priority task(s) still in BACKLOG
**Impact:** Task health issues impair delivery predictability.

**Recommendation:** Start high-priority tasks: `monday task start <id>` or `monday workflow run implement-function`.

### [LOW] No pattern entries in knowledge base
**Impact:** Knowledge queries for pattern will return no results.

**Recommendation:** Add pattern entries via `monday learn` or `monday migrate`.

### [LOW] No bug entries in knowledge base
**Impact:** Knowledge queries for bug will return no results.

**Recommendation:** Add bug entries via `monday learn` or `monday migrate`.

## Health Report

**Score:** 85/100 (Good)

**Summary:** 3 warning(s), 9 info

**Top Recommendations:**
1. Commit or stash all changes before deployment.
1. Consider moving large binaries or data files to external storage.
1. Start high-priority tasks: `monday task start <id>` or `monday workflow run implement-function`.
1. Run `pytest --cov` to generate coverage data.
1. Create workflows/definitions/ and add YAML workflow files.

---

## What to Build Next

**Recommended Sprint Goal:** Start high-priority work (TASK-0003, TASK-0005)

7 high-priority task(s) are unstarted: TASK-0003, TASK-0005, TASK-0006. These items have the greatest impact on delivery and should be pulled into active development this sprint.

**Next Actions (ranked by value):**

1. **Start: Epic 0: Rotate leaked secret & lock down dashboard** (hours)
   - TASK-0003 is [P0] and unstarted.
   - `$ monday task start TASK-0003`
2. **Start: Epic 2: Extract shared common/ library** (hours)
   - TASK-0005 is [P1] and unstarted.
   - `$ monday task start TASK-0005`
3. **Commit or stash all changes before deployment** (hours)
   - This warning, if unaddressed, will compound into a larger issue.
   - `$ git status`
4. **Start high-priority tasks: TASK-0003, TASK-0005, TASK-0006** (hours)
   - High-priority work is not progressing.
   - `$ monday task list`
5. **Review full health report** (minutes)
   - Regular health checks catch regressions early.
   - `$ monday doctor --verbose`

---

## Technical Debt

No bug entries in knowledge base (may indicate untracked debt).

- No bug entries in knowledge base (may indicate untracked debt)

---

## Recommended Tasks to Create

The following tasks are recommended based on the advisory analysis. Create them with `monday task create --title "..." --objective "..."` against the weatherbot project root.

- **[P1]** Fix: Dirty working tree — 31 uncommitted change(s)
  - *Objective:* Commit or stash all changes before deployment.
- **[P1]** Fix: 7 high-priority task(s) unstarted in backlog
  - *Objective:* Start high-priority tasks: TASK-0003, TASK-0005, TASK-0006.
- **[P2]** Expand knowledge base
  - *Objective:* Add missing knowledge types: No pattern entries captured, No bug entries captured, No knowledge covering task topic: 'Epic 2: Extract shared common/ library'
- **[P1]** Start: Epic 0: Rotate leaked secret & lock down dashboard
  - *Objective:* TASK-0003 is [P0] and unstarted.

---

*Generated by MondayOS. Run `monday onboard weatherbot` to refresh.*
