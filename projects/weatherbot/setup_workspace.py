#!/usr/bin/env python3
"""
WeatherBot Product Workspace — Initiative 014.

Populates MondayOS with the complete project-management structure for WeatherBot
*entirely through the Monday public API* — no hand-authored documentation that
could instead live as structured knowledge or tasks.

What this script creates (all in the MondayOS knowledge + task stores, scoped to
the `weatherbot` component):

  Knowledge (Monday.learn):
    - Project Overview & current state   (what WeatherBot is / what's built)
    - Project Charter                    (vision, goals, constraints, metrics)
    - Product Roadmap                    (milestones, releases, priority order)
    - Risk Register                      (technical, product, operational risks)
    - Definition of Done
    - Engineering Backlog overview       (epic → feature → task map + deps)
    - Sprint Zero                        (goal, selected work, exit criteria)

  Tasks (Monday.task):
    - 5 epics + 13 features/tasks with dependencies expressed in context

  Project registry (Monday.project):
    - WeatherBot registered as an external project

  Workflow (Monday.workflow):
    - Lists the available engineering workflows
    - Runs `implement-function` once to demonstrate the managed loop end-to-end

It is idempotent-guarded: if the workspace already exists it refuses to run
again unless `--force` is passed. Re-running is the supported way to rebuild the
workspace from this single structured source of truth.

Usage:
    python projects/weatherbot/setup_workspace.py [--project-root PATH] [--force]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from monday import Monday, MondayConfig

# WeatherBot lives next to MondayOS in the AI-Labs workspace.
_DEFAULT_WEATHERBOT_PATH = "/Users/jrich/AI-Labs/WeatherBot"
_COMPONENT = "weatherbot"
_OWNER = "human:weatherbot-product-owner"
_ENG = "human:weatherbot-eng-lead"
_SENTINEL_TITLE = "WeatherBot Project Charter"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Knowledge content (structured — captured via Monday.learn)
# ---------------------------------------------------------------------------

OVERVIEW = """\
# WeatherBot — Overview & Current State

## What it is
WeatherBot is a command-line application that fetches current weather conditions
and forecasts and raises proactive severe-weather alerts — all from the terminal,
with no GUI and minimal dependencies.

## Why it exists
Developers and power users want fast, scriptable weather data without leaving the
shell or trusting a heavyweight desktop/mobile app. WeatherBot is the dependable,
composable weather primitive for the command line.

## What has been built (v0.3.1)
- `client.py`   — weather API client
- `models.py`   — typed weather data models
- `cache.py`    — local response cache
- `alerts.py`   — severe-weather alert logic
- `cli.py`      — command-line entry point
- Tests: `test_models.py`, `test_client.py`
- Docs: CHANGELOG (4 versions), DECISIONS (5 ADRs), ROADMAP (4 phases)

## Known gaps (from MondayOS onboarding)
- No tests yet for `cache.py` or `alerts.py`
- 3 outstanding TODO/FIXME markers
- No retry/backoff or graceful degradation on API failure
- Not yet packaged for distribution (PyPI)
"""

CHARTER = """\
# WeatherBot Project Charter

## Vision
A fast, dependable command-line companion that delivers accurate current
conditions, forecasts, and proactive severe-weather alerts without ever leaving
the terminal.

## Goals
1. **Reliable retrieval** — weather data is fetched robustly and degrades
   gracefully when the upstream API is slow or unavailable.
2. **Actionable alerting** — surface severe-weather conditions that matter,
   without alert fatigue.
3. **Zero-config, fast UX** — sensible defaults; sub-second responses on warm
   cache; scriptable output.
4. **Easy distribution** — installable in one command from PyPI.

## Constraints
- Python CLI only — no GUI, single-user.
- Depends on a third-party weather API (rate limits, outages, key management).
- Minimal dependency footprint; offline cache fallback required.
- Must remain composable (clean exit codes, machine-readable output mode).

## Success Metrics
- p95 command latency < 1s on warm cache; < 3s cold.
- ≥ 90% line coverage on `client`, `cache`, `alerts`.
- Zero unhandled upstream failures — every API error path degrades gracefully.
- Published to PyPI and installable via `pip install weatherbot`.
- Alert quality: ≥ 0.9 precision on severe-weather alerts (no fatigue).
"""

ROADMAP = """\
# WeatherBot Product Roadmap

Priority order: **M1 → M2 → M3**. Reliability before features; distribution last.

## M1 — Reliability Hardening   → release v0.4.0
Make WeatherBot trustworthy under real-world API conditions.
- Retry with exponential backoff
- Graceful degradation using cached data
- Configurable cache TTL (stale-while-revalidate)
- Tests for `cache.py` and `alerts.py`; resolve outstanding TODOs

## M2 — Forecast & Alerting Depth   → release v0.5.0
Deepen the product's core value.
- Multi-day forecast support
- Configurable severe-weather alert thresholds
- Alert deduplication / cooldown (anti-fatigue)

## M3 — Distribution & Observability   → release v1.0.0
Make it shippable and operable.
- Package and publish to PyPI
- `--json` machine-readable output
- Structured logging + basic error reporting
- Runbook: weather API key rotation

## Priority rationale
Reliability (M1) gates everything: features and distribution are worthless if the
core retrieval is flaky. Distribution (M3) is deferred until quality gates pass.
"""

RISK_REGISTER = """\
# WeatherBot Risk Register

Each risk: **likelihood × impact → mitigation**.

## Technical risks
- **Upstream API instability / rate limits** — High × High →
  retry/backoff, caching, graceful degradation (M1).
- **Cache staleness serving wrong conditions** — Med × Med →
  configurable TTL + stale-while-revalidate; show data age.
- **Low test coverage on `cache`/`alerts`** — High × High →
  add unit tests in Sprint Zero / M1; enforce coverage in Definition of Done.
- **Outstanding TODO/FIXME markers (3)** — Med × Med →
  triage and resolve in Sprint Zero.

## Product risks
- **Alert fatigue** — Med × High →
  configurable thresholds + dedup/cooldown (M2); measure precision.
- **Unclear differentiation vs. existing weather apps** — Med × Med →
  lean into CLI/scriptability and reliability as the wedge.
- **CLI-only limits audience** — Low × Med →
  accepted constraint; revisit post-1.0 based on demand.

## Operational risks
- **API key management & rotation** — Med × High →
  keys via environment only; runbook for rotation (M3).
- **No observability** — Med × Med →
  structured logging + error reporting (M3).
- **Single-maintainer bus factor** — Med × High →
  capture all decisions/patterns in MondayOS; Definition of Done requires
  knowledge capture so context survives turnover.
- **No release automation** — Low × Med →
  scripted, documented PyPI release as part of M3.
"""

DEFINITION_OF_DONE = """\
# WeatherBot — Definition of Done

A unit of work is **Done** only when ALL of the following hold:

1. Code is reviewed and approved.
2. Unit tests are written and passing; coverage on touched core modules is
   maintained at ≥ 90%.
3. Public functions/classes have docstrings; no new untracked TODO/FIXME markers.
4. The change runs correctly via the WeatherBot CLI.
5. `docs/CHANGELOG.md` is updated; notable decisions are recorded as an ADR.
6. Knowledge is captured in MondayOS (pattern/lesson/decision as appropriate)
   so the rationale survives team turnover.
7. The associated MondayOS task is moved to its terminal state with a reason.

This Definition of Done is binding for every task in the WeatherBot backlog.
"""


# ---------------------------------------------------------------------------
# Backlog definition (created via Monday.task)
# ---------------------------------------------------------------------------

# Epics: (key, title, priority, objective)
EPICS = [
    ("quality",      "[EPIC] Quality & Test Coverage", "P1",
     "Raise WeatherBot to a trustworthy quality bar: tests, coverage, and zero untracked debt."),
    ("reliability",  "[EPIC] Reliability & Resilience", "P1",
     "Make weather retrieval robust under real-world API latency, rate limits, and outages."),
    ("forecast",     "[EPIC] Forecast & Alerting", "P2",
     "Deepen core value with multi-day forecasts and high-quality severe-weather alerting."),
    ("observability", "[EPIC] Observability & Operations", "P2",
     "Make WeatherBot operable: logging, error reporting, and operational runbooks."),
    ("distribution", "[EPIC] Distribution & Packaging", "P2",
     "Ship WeatherBot: package, publish, and provide machine-readable output."),
]

# Features: (epic_key, title, type, priority, objective, [dep_keys], feature_key)
FEATURES = [
    ("quality", "Resolve 3 outstanding TODO/FIXME markers", "fix", "P1",
     "Triage and resolve the three outstanding TODO/FIXME markers surfaced by MondayOS doctor.",
     [], "todos"),
    ("quality", "Add unit tests for cache.py", "feature", "P1",
     "Add comprehensive unit tests for the cache module to reach the coverage bar.",
     [], "test_cache"),
    ("quality", "Add unit tests for alerts.py", "feature", "P1",
     "Add comprehensive unit tests for the alerts module, including threshold edge cases.",
     [], "test_alerts"),
    ("reliability", "Add retry with exponential backoff to the API client", "fix", "P1",
     "Wrap upstream weather API calls in retry-with-exponential-backoff to survive transient failures.",
     [], "retry"),
    ("reliability", "Graceful degradation using cached data on API outage", "feature", "P1",
     "When the upstream API is unavailable, serve the most recent cached data and clearly mark its age.",
     ["retry"], "degrade"),
    ("reliability", "Configurable cache TTL with stale-while-revalidate", "feature", "P2",
     "Make cache TTL configurable and implement stale-while-revalidate to balance freshness and speed.",
     ["retry"], "cache_ttl"),
    ("forecast", "Multi-day forecast support", "feature", "P2",
     "Add multi-day forecast retrieval and rendering to the CLI.",
     ["retry"], "multiday"),
    ("forecast", "Configurable severe-weather alert thresholds", "feature", "P2",
     "Allow users to configure the thresholds that trigger severe-weather alerts.",
     [], "thresholds"),
    ("forecast", "Alert deduplication / cooldown (anti-fatigue)", "feature", "P2",
     "Suppress duplicate alerts within a cooldown window to prevent alert fatigue.",
     ["thresholds"], "dedup"),
    ("observability", "Structured logging across modules", "ops", "P2",
     "Introduce structured logging across client, cache, and alerts for debuggability.",
     [], "logging"),
    ("observability", "Runbook: weather API key rotation", "docs", "P3",
     "Document the operational procedure for rotating the weather API key safely.",
     [], "runbook"),
    ("distribution", "Add --json machine-readable output mode to the CLI", "feature", "P3",
     "Add a --json flag so WeatherBot output can be consumed by other tools and scripts.",
     [], "json_out"),
    ("distribution", "Package and publish to PyPI", "ops", "P2",
     "Package WeatherBot and publish it to PyPI so it installs via `pip install weatherbot`.",
     ["test_cache", "test_alerts", "runbook"], "pypi"),
]


# ---------------------------------------------------------------------------
# Population
# ---------------------------------------------------------------------------

def _learn(m: Monday, title: str, content: str, entry_type: str, tags: list[str]) -> str:
    r = m.learn(
        content=content,
        title=title,
        entry_type=entry_type,
        tags=["weatherbot", *tags],
        components=[_COMPONENT],
    )
    if not r.accepted:
        raise RuntimeError(f"learn() rejected {title!r}: {r.message}")
    print(f"  knowledge  {r.entry_id:<10} {title}")
    return r.entry_id


def _task(m: Monday, title: str, objective: str, task_type: str, priority: str,
          context: str, criteria: list[str], created_by: str) -> str:
    r = m.task(
        "create",
        title=title,
        objective=objective,
        task_type=task_type,
        priority=priority,
        created_by=created_by,
        context=context,
        acceptance_criteria=criteria,
    )
    if not r.success:
        raise RuntimeError(f"task create rejected {title!r}: {r.message}")
    print(f"  task       {r.task_id:<10} [{priority}] {title}")
    return r.task_id


def populate(m: Monday, weatherbot_path: str) -> dict:
    ids: dict = {"knowledge": {}, "epics": {}, "features": {}, "workflow": {}}

    # ── Project registry (Monday.project) ────────────────────────────────
    pr = m.project(
        "register", name="weatherbot", path=weatherbot_path,
        description="CLI weather fetcher and alert system", overwrite=True,
    )
    print(f"  project    weatherbot → {pr.data.get('source_path', weatherbot_path)}")

    # ── Foundational knowledge (Monday.learn) ────────────────────────────
    print("\nKnowledge — charter, roadmap, risks, definition of done:")
    ids["knowledge"]["overview"] = _learn(m, "WeatherBot — Overview & Current State", OVERVIEW, "documentation", ["overview", "onboarding"])
    ids["knowledge"]["charter"] = _learn(m, _SENTINEL_TITLE, CHARTER, "documentation", ["charter", "vision"])
    ids["knowledge"]["roadmap"] = _learn(m, "WeatherBot Product Roadmap", ROADMAP, "documentation", ["roadmap", "milestones"])
    ids["knowledge"]["risks"] = _learn(m, "WeatherBot Risk Register", RISK_REGISTER, "documentation", ["risk", "register"])
    ids["knowledge"]["dod"] = _learn(m, "WeatherBot — Definition of Done", DEFINITION_OF_DONE, "decision", ["definition-of-done", "standard"])

    # ── Engineering backlog (Monday.task) ────────────────────────────────
    print("\nBacklog — epics:")
    for key, title, priority, objective in EPICS:
        ids["epics"][key] = _task(
            m, title, objective, "feature", priority,
            context=f"WeatherBot epic. Component: {_COMPONENT}.",
            criteria=["All child features meet the WeatherBot Definition of Done."],
            created_by=_OWNER,
        )

    print("\nBacklog — features/tasks:")
    for epic_key, title, ttype, priority, objective, dep_keys, fkey in FEATURES:
        epic_id = ids["epics"][epic_key]
        dep_ids = [ids["features"][d] for d in dep_keys if d in ids["features"]]
        ctx = f"Epic: {epic_id} ({epic_key}). Component: {_COMPONENT}."
        if dep_ids:
            ctx += " Depends on: " + ", ".join(dep_ids) + "."
        ids["features"][fkey] = _task(
            m, title, objective, ttype, priority,
            context=ctx,
            criteria=["Meets the WeatherBot Definition of Done.",
                      "Knowledge captured in MondayOS."],
            created_by=_ENG,
        )

    # ── Workflow demonstration (Monday.workflow) ─────────────────────────
    print("\nWorkflow — available engineering workflows:")
    wf_list = m.workflow("list")
    available = wf_list.data.get("workflows", [])
    for wf in available:
        print(f"  workflow   {wf['name']} v{wf['version']} ({wf['steps']} steps)")

    print("\nWorkflow — running implement-function to verify the managed loop:")
    run = m.workflow(
        "run", name="implement-function",
        inputs={"function_name": "cache_read_through", "component": "weatherbot"},
        approval_handler=lambda msg, ctx: True,  # auto-approve the gate (setup demo)
    )
    if run.success:
        steps = run.data.get("steps", [])
        created = next((s["output"].get("task_id") for s in steps if s["step_id"] == "create-task"), "")
        pattern = next((s["output"].get("entry_id") for s in steps if s["step_id"] == "capture-pattern"), "")
        ids["workflow"] = {"execution_id": run.execution_id, "task_id": created, "pattern_id": pattern}
        print(f"  workflow   completed  exec={run.execution_id}  task={created}  pattern={pattern}")
    else:
        print(f"  workflow   WARNING: run did not complete: {run.message}")

    # ── Derived knowledge that references the created IDs ─────────────────
    print("\nKnowledge — backlog overview + sprint zero (reference real IDs):")
    ids["knowledge"]["backlog"] = _learn(
        m, "WeatherBot Engineering Backlog", _backlog_overview(ids), "documentation",
        ["backlog", "epics"],
    )
    ids["knowledge"]["sprint_zero"] = _learn(
        m, "WeatherBot Sprint Zero", _sprint_zero(ids, available), "sprint",
        ["sprint-zero", "kickoff"],
    )

    return ids


def _backlog_overview(ids: dict) -> str:
    lines = ["# WeatherBot Engineering Backlog", "",
             "The live backlog lives as MondayOS tasks. This is the structural map:",
             "epic → features, with dependencies. Query live state with `monday task list`.", ""]
    epic_titles = {k: t for k, t, _, _ in EPICS}
    feat_by_epic: dict[str, list] = {k: [] for k, *_ in EPICS}
    for epic_key, title, ttype, priority, objective, dep_keys, fkey in FEATURES:
        feat_by_epic[epic_key].append((fkey, title, priority, dep_keys))
    for epic_key, etitle, *_ in EPICS:
        lines.append(f"## {etitle}  — `{ids['epics'][epic_key]}`")
        for fkey, ftitle, priority, dep_keys in feat_by_epic[epic_key]:
            fid = ids["features"][fkey]
            dep_ids = [ids["features"][d] for d in dep_keys if d in ids["features"]]
            dep_str = f"  ⟵ depends on {', '.join(dep_ids)}" if dep_ids else ""
            lines.append(f"- `{fid}` [{priority}] {ftitle}{dep_str}")
        lines.append("")
    return "\n".join(lines)


def _sprint_zero(ids: dict, workflows: list) -> str:
    f = ids["features"]
    selected = [
        ("todos", f.get("todos")),
        ("retry", f.get("retry")),
        ("test_cache", f.get("test_cache")),
        ("test_alerts", f.get("test_alerts")),
        ("degrade", f.get("degrade")),
    ]
    wf_names = ", ".join(f"{w['name']} v{w['version']}" for w in workflows) or "(none)"
    wf = ids.get("workflow", {})
    lines = [
        "# WeatherBot Sprint Zero", "",
        "## Goal",
        "Stand up the MondayOS-managed workspace, groom the backlog, and complete the",
        "foundational reliability and quality groundwork that every later milestone depends on.",
        "",
        "## Selected work (Milestone M1 foundation)",
    ]
    for key, tid in selected:
        lines.append(f"- `{tid}`  ({key})")
    lines += [
        "",
        "## Engineering process",
        f"Available MondayOS workflows for feature work: {wf_names}.",
        "Feature implementation follows the `implement-function` workflow: research → "
        "task → human approval → implement → capture pattern → close.",
    ]
    if wf.get("task_id"):
        lines += [
            "",
            "## Process verification",
            "The managed engineering loop was verified end-to-end during setup by running",
            f"`implement-function` (execution `{wf.get('execution_id','')}`), which created task",
            f"`{wf.get('task_id','')}` and captured pattern `{wf.get('pattern_id','')}`.",
        ]
    lines += [
        "",
        "## Exit criteria",
        "- Backlog groomed and prioritised in MondayOS (epics + features created).",
        "- WeatherBot Definition of Done adopted.",
        "- All Sprint Zero tasks meet the Definition of Done.",
        "- Reliability foundation (retry/backoff + graceful degradation) in place for M1.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Committed index (durable map of the workspace)
# ---------------------------------------------------------------------------

def write_index(ids: dict, out_path: Path) -> None:
    k = ids["knowledge"]
    lines = [
        "# WeatherBot Product Workspace — Index",
        "",
        "WeatherBot is the first project managed entirely by MondayOS. Its complete",
        "project-management structure lives as **structured knowledge and tasks** in the",
        "MondayOS stores, created via the public API by",
        "[`setup_workspace.py`](setup_workspace.py). This file is a generated index — the",
        "source of truth is the MondayOS knowledge base and task system.",
        "",
        "## Onboard to WeatherBot using MondayOS alone",
        "```bash",
        "monday search weatherbot                 # everything about WeatherBot",
        "monday ask \"What is WeatherBot and why does it exist?\"",
        "monday task list                          # the live engineering backlog",
        "monday advise                             # what to work on next",
        "monday onboard weatherbot                 # regenerate the onboarding report",
        "```",
        "",
        "## Knowledge entries",
        f"- Overview & current state — `{k.get('overview','')}`",
        f"- Project Charter (vision, goals, constraints, metrics) — `{k.get('charter','')}`",
        f"- Product Roadmap — `{k.get('roadmap','')}`",
        f"- Risk Register — `{k.get('risks','')}`",
        f"- Definition of Done — `{k.get('dod','')}`",
        f"- Engineering Backlog map — `{k.get('backlog','')}`",
        f"- Sprint Zero — `{k.get('sprint_zero','')}`",
    ]
    if ids.get("workflow", {}).get("pattern_id"):
        lines.append(f"- Implementation pattern (process demo) — `{ids['workflow']['pattern_id']}`")
    lines += ["", "## Backlog — epics"]
    epic_titles = {key: title for key, title, _, _ in EPICS}
    for key, title, priority, _ in EPICS:
        lines.append(f"- `{ids['epics'][key]}` [{priority}] {title}")
    lines += ["", f"Plus {len(FEATURES)} features/tasks across the epics "
                  "(see the Engineering Backlog knowledge entry for the full map with dependencies).",
              "", "## Sprint Zero"]
    if ids.get("workflow", {}).get("task_id"):
        lines.append(f"Managed loop verified via workflow execution `{ids['workflow']['execution_id']}` "
                     f"(task `{ids['workflow']['task_id']}`).")
    lines += ["",
              "*Regenerate this workspace with* `python projects/weatherbot/setup_workspace.py --force`.",
              ""]
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nIndex written: {out_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Populate the WeatherBot product workspace in MondayOS.")
    parser.add_argument("--project-root", default=str(_repo_root()),
                        help="MondayOS project root (default: the repo root).")
    parser.add_argument("--weatherbot-path", default=_DEFAULT_WEATHERBOT_PATH,
                        help="Path to the WeatherBot source repository.")
    parser.add_argument("--force", action="store_true",
                        help="Repopulate even if the workspace already exists.")
    args = parser.parse_args(argv)

    root = Path(args.project_root)
    m = Monday(MondayConfig(project_root=root))

    existing = m.search(_SENTINEL_TITLE, limit=5)
    if any(r.get("title") == _SENTINEL_TITLE for r in existing.results) and not args.force:
        print("WeatherBot workspace already exists in this MondayOS instance.")
        print("Re-run with --force to rebuild it.")
        return 0

    print(f"Populating WeatherBot product workspace in: {root}")
    ids = populate(m, args.weatherbot_path)
    write_index(ids, _repo_root() / "projects" / "weatherbot" / "PRODUCT_WORKSPACE.md")

    n_tasks = len(ids["epics"]) + len(ids["features"]) + (1 if ids.get("workflow", {}).get("task_id") else 0)
    n_know = len(ids["knowledge"]) + (1 if ids.get("workflow", {}).get("pattern_id") else 0)
    print(f"\nDone. {n_know} knowledge entries, {n_tasks} tasks. "
          f"Onboard with: monday --project-root {root} ask \"What is WeatherBot?\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
