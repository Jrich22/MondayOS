# Generated Knowledge Index

Machine-generated knowledge captured by the Execution Orchestrator. The
records themselves live in `knowledge/runtime/research/`, which is gitignored:
**provenance decides what is source-controlled, not entry type.** Human-authored
research stays in `knowledge/research/` and **is** committed.

They remain fully loaded, searchable, and counted by MondayOS — the knowledge
loader rglobs `knowledge/`, so nothing changed functionally. This index keeps
the ledger auditable in version control without committing the payloads, which
are model output rather than durable project truth.

## Why this split exists

Every agent run captured a research record into the source-controlled tree and
bumped a sequence counter. Doctor counted those as "uncommitted changes",
Advise escalated them to a HIGH engineering risk, and the planner fed that back
into the next run's prompt as repository state — so QA failed tasks for the mess
the pipeline had just made, with the count growing on every rerun.

20 records.

| ID | Captured | Task | Provider | Title |
|---|---|---|---|---|
| RES-0106 | 2026-08-13T01:36:04Z | TASK-0053 | openai | Execution result: sourcingBOT: Product Workspace Foundation |
| RES-0107 | 2026-08-13T01:36:35Z | TASK-0053 | anthropic | Execution result: sourcingBOT: Product Workspace Foundation |
| RES-0108 | 2026-08-13T01:37:03Z | TASK-0053 | anthropic | Execution result: sourcingBOT: Product Workspace Foundation |
| RES-0109 | 2026-08-13T01:37:31Z | TASK-0053 | anthropic | Execution result: sourcingBOT: Product Workspace Foundation |
| RES-0110 | 2026-08-13T01:38:02Z | TASK-0053 | anthropic | Execution result: sourcingBOT: Product Workspace Foundation |
| RES-0111 | 2026-08-13T02:03:48Z | TASK-0054 | anthropic | Execution result: sourcingBOT: Implement Product Workspace Foundation |
| RES-0112 | 2026-08-13T02:08:20Z | TASK-0054 | anthropic | Execution result: sourcingBOT: Implement Product Workspace Foundation |
| RES-0113 | 2026-08-13T02:09:43Z | TASK-0054 | anthropic | Execution result: sourcingBOT: Implement Product Workspace Foundation |
| RES-0114 | 2026-08-13T02:19:37Z | TASK-0055 | openai | Execution result: MondayOS: Harden Structured Reviewer Verdicts |
| RES-0115 | 2026-08-13T02:20:09Z | TASK-0055 | anthropic | Execution result: MondayOS: Harden Structured Reviewer Verdicts |
| RES-0116 | 2026-08-13T02:20:42Z | TASK-0055 | anthropic | Execution result: MondayOS: Harden Structured Reviewer Verdicts |
| RES-0117 | 2026-08-13T02:21:10Z | TASK-0055 | anthropic | Execution result: MondayOS: Harden Structured Reviewer Verdicts |
| RES-0118 | 2026-08-13T02:21:44Z | TASK-0055 | anthropic | Execution result: MondayOS: Harden Structured Reviewer Verdicts |
| RES-0119 | 2026-08-13T02:36:44Z | TASK-0055 | anthropic | Execution result: MondayOS: Harden Structured Reviewer Verdicts |
| RES-0120 | 2026-08-13T02:48:40Z | TASK-0056 | openai | Execution result: sourcingBOT: Req Workspace Authoring |
| RES-0121 | 2026-08-13T02:49:22Z | TASK-0056 | anthropic | Execution result: sourcingBOT: Req Workspace Authoring |
| RES-0122 | 2026-08-13T02:49:59Z | TASK-0056 | anthropic | Execution result: sourcingBOT: Req Workspace Authoring |
| RES-0123 | 2026-08-13T02:57:12Z | TASK-0056 | openai | Execution result: sourcingBOT: Req Workspace Authoring |
| RES-0124 | 2026-08-13T02:57:50Z | TASK-0056 | anthropic | Execution result: sourcingBOT: Req Workspace Authoring |
| RES-0125 | 2026-08-13T02:58:25Z | TASK-0056 | anthropic | Execution result: sourcingBOT: Req Workspace Authoring |

## Note on RES-0105

`RES-0105` is generated but was committed to `knowledge/research/` before this
convention existed (PR #19). Left in place deliberately: moving it would create
a deletion diff that belongs with a broader migration, not this fix.

## Reactivating a record

Nothing is deleted. To bring a record back under version control, move it from
`knowledge/runtime/research/` to `knowledge/research/` and commit it.
