# Agent Team Workflow

The team workflow makes the registered agents collaborate on a single task from
start to finish, running each role in a fixed sequence and passing every stage
the summaries of the stages before it:

```
CPO → Lead Engineer → QA → Security → Reviewer → Human Approval
```

It is a thin coordinator on top of the [Multi-Agent Runtime](AGENTS.md): each
stage is an ordinary role run (logged as an `AgentRun`), so all the per-role
safety — the approval gate, review-required default, and logging — applies
unchanged. The team engine adds the sequencing, the shared context, early stop,
and one parent record tying the child runs together.

> **Review-required, no live execution.** Nothing is committed, pushed, or
> executed live. On a full pass the task is moved to `REVIEW` and waits for a
> human to approve. Autonomous mode is not offered for team runs.

For real repository changes, use the separate [Autonomous Build
Workflow](BUILD_WORKFLOW.md). `Monday.build()` applies controlled patches in an
isolated worktree, runs deterministic validation, requires a fresh ChatGPT review
of the exact artifact, and stops after opening a pull request. It does not change
the guarantees of `Monday.team()` described here.

## The pipeline

| Stage | Role | Purpose | Blocking? |
|---|---|---|---|
| 1 | CPO | Strategy, acceptance criteria, prioritization | no |
| 2 | Lead Engineer | Implementation / architecture plan | no |
| 3 | QA | Tests, regression, validation | **yes** |
| 4 | Security | Secrets, risky diffs, live-trading safety | **yes** |
| 5 | Reviewer | Code review, risk assessment | **yes** |
| 6 | Human | Final approval → completes the task | — |

- **Prior-stage context.** Each stage receives a "Prior stage summaries" block
  (the result excerpt of every earlier stage) injected into its prompt context,
  so later roles build on earlier ones.
- **Early stop.** QA, Security, and Reviewer are gatekeepers. If any returns a
  **block** verdict, the pipeline stops immediately — later stages do not run and
  the task is left in progress for rework. A stage that fails to execute (e.g. no
  provider available) also stops the pipeline.
- **Verdict.** Every provider is instructed to return a JSON verdict object with
  `verdict` set to `pass`, `needs_changes`, or `block`. MondayOS parses that
  structured object and never infers approval from reassuring prose. For QA,
  Security, and Reviewer, only an explicit valid `pass` advances the pipeline;
  `needs_changes`, `block`, an execution failure, or a missing, malformed, or
  truncated verdict stops it. CPO and Lead Engineer verdicts are retained for
  audit but do not veto the workflow.

- **Provider boundary.** Productive roles use their role defaults and ordered
  failover across the configured provider pool. A whole-team provider pin applies
  only to those productive roles. The final independent Reviewer requires
  OpenAI/ChatGPT and fails closed if it is unavailable. The deterministic `fake`
  provider is permitted only for offline workflow tests.

## Task lifecycle

The team owns the task lifecycle so stages don't churn it:

1. The task is moved to `IN_PROGRESS` once, at the start.
2. Every stage runs with `update_task=False` — it calls its provider and captures
   knowledge, but does **not** move the task. Each stage's status is `executed`.
3. On a full pass the task is moved to `REVIEW` once, at the end.
4. A human approves to complete it.

## CLI

```bash
monday team run TASK-0001                 # run the full pipeline (review-required)
monday team run TASK-0001 --provider fake # offline run (no API keys)
monday team run TASK-0001 --mode dry-run  # plan every stage, no provider calls
monday team run TASK-0001 --json          # machine-readable TeamRun record
monday team history --task TASK-0001      # past team runs
```

A completed run prints each stage, its verdict, and the approval command:

```
  TEAM RUN — team-abc123def456
  Task    : TASK-0001
  Mode    : review
  Status  : awaiting-approval
  ✓ cpo            [executed ] pass
  ✓ lead-engineer  [executed ] pass
  ✓ qa             [executed ] pass
  ✓ security       [executed ] pass
  ✓ reviewer       [executed ] pass
  All stages passed. Task is at REVIEW awaiting human approval —
  approve with: monday agent review run-… --approve
```

### Human approval

The final step is a human. When the pipeline reaches `awaiting-approval`, approve
(or reject) the run surfaced as `approval_run_id`:

```bash
monday agent review <approval_run_id> --approve   # → task completed
monday agent review <approval_run_id> --reject     # → left for rework
```

## Public API

```python
from monday import Monday, MondayConfig
m = Monday(MondayConfig(project_root="."))

r = m.team("run", task_id="TASK-0001")     # TeamResponse
r.status            # running | awaiting-approval | completed | interrupted |
                    # blocked | changes-requested | invalid-verdict | failed |
                    # dry-run | rejected
r.stages            # per-stage: role, run_id, status, verdict, summary
r.approval_run_id   # approve this to complete the task
m.team("get", team_run_id=r.team_run_id)
m.team("interrupt", team_run_id=r.team_run_id,
       reason="worker stopped")             # abandoned running records only
m.team("history", task_id="TASK-0001")
```

Remote callers may reserve an idempotent `team_run_id`, observe best-effort
progress with `progress_callback`, and durably record the initial identity with
`checkpoint_callback`. If that required checkpoint callback raises, no task or
provider work begins and the public `monday.TeamCheckpointError` propagates so
the caller can retry from its own durable state.

## Records

- **Parent:** `logs/agents/team-*.json` — one `TeamRun` per pipeline, with its
  ordered `stages` and `child_run_ids`, the stop point, and the approval run.
- **Children:** `logs/agents/run-*.json` — one `AgentRun` per stage (the same
  records `monday agent history` shows).

## Guarantees

- Runs are **review-required**; no commit, push, secret write, or live execution
  happens in a team run.
- QA / Security / Reviewer can each **veto** and stop the pipeline early.
- The whole run is one parent record referencing every child stage run — fully
  logged and reviewable.
- Backed by fake-agent tests covering the full pipeline, early stops, dry-run,
  and the autonomous guard (`tests/test_team.py`).
