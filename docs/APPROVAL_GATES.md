# Approval Gates

The Multi-Agent Runtime is **review-required by default**. An agent may plan and
produce output, but MondayOS will not let it finalize work — or take a sensitive
action — without an explicit human approval. This document describes that policy
and where it is enforced.

> This is the "start with review-required orchestration" posture. **Autonomous
> live execution is not implemented.** The runtime only calls the provider,
> captures knowledge, and moves the task to `REVIEW`; it never edits source,
> commits, pushes, writes secrets, or places trades.

## Two layers of safety

1. **Structural.** The runtime has no code path that commits, pushes, handles
   secrets, or trades. In the default `review` mode the orchestrator makes no file
   modifications at all — it executes the provider and stops the task at `REVIEW`.
   So agents are safe by construction, not just by policy.

2. **Policy (the gate).** `agents/gates.py::ApprovalGate` is the single, testable
   place the approval decision lives. It makes the safety explicit and
   future-proof: when a run declares a sensitive intent, or asks to auto-complete,
   the gate blocks it unless a human has approved.

## Gated actions

```
GATED_ACTIONS = { commit, push, secrets, live_trade, destructive }
```

A run can declare intended actions with `--action` (repeatable). If any declared
action is gated, the run is **blocked** unless `--approve` is supplied:

```bash
monday agent run TASK-0001 --role lead-engineer --action commit
#   → BLOCKED: Human approval required before an agent may commit.

monday agent run TASK-0001 --role lead-engineer --action commit --approve
#   → permitted (a human has signed off)
```

## Execution modes

| Mode | Behaviour | Gate |
|---|---|---|
| `dry-run` | Plan and select a provider; no provider call, no changes. | Always allowed (gated actions still surface). |
| `review` *(default)* | Execute via the provider, capture knowledge, move the task to `REVIEW`. No file changes. | Allowed. Awaits human review. |
| `autonomous` | Complete the task automatically. | Requires **both** `--enable-autonomous` **and** `--approve`. |

The default `require_human_approval = True` (from `MondayConfig`) means even a
fully-enabled autonomous run must carry an explicit `--approve`. Without it the
task stops at `REVIEW`. Concretely, to auto-complete you must pass all three:
`--autonomous --enable-autonomous --approve`.

### Block conditions (in order)

1. Any declared action is gated and no approval is present → **blocked**.
2. `autonomous` requested without `--enable-autonomous` → **blocked**.
3. `autonomous` requested, enabled, but no approval while approval is required →
   **blocked**.

A blocked run does **not** call the provider and does **not** touch the task; it is
still logged (status `blocked`) with the gate's reason.

## The review loop

Every run is written to `logs/agents/run-*.json` and is reviewable:

```bash
monday agent run    TASK-0001 --role lead-engineer   # → task REVIEW, run logged
monday agent history --task TASK-0001                # inspect the run
monday agent review run-abc123 --approve             # human approves → task completed
monday agent review run-abc123 --reject --note "…"   # human rejects → left for rework
```

- **Approve** records the decision on the run and completes the task.
- **Reject** records the decision and leaves the task at `REVIEW`.

## Guarantees

- No agent commits, pushes, touches secrets, or live-trades without a human
  approval — structurally and by the gate.
- No task is auto-completed under the default posture without an explicit approval.
- Every run — allowed or blocked — is logged and reviewable.
- The policy lives in one module (`agents/gates.py`) and is covered by tests
  (`tests/test_agents.py`), so it can be audited and changed in one place.
