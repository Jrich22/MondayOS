# Agent Roles

Work in MondayOS is routed to a **role**, not to a specific person or model. Each
role is pure data in `agents/roles.py` — a slug, responsibilities, a default
provider, advertised capabilities, and the gated actions it would escalate. The
registry resolves a role to a concrete agent at run time.

Adding a role is a single entry in `ROLES`; nothing in the runtime changes.

## The six roles

| Role (`slug`) | Responsibilities | Default provider | Key capabilities |
|---|---|---|---|
| **CPO** (`cpo`) | Product strategy, roadmap, acceptance criteria, prioritization | `openai` (ChatGPT) | strategy, roadmap, acceptance-criteria, prioritization |
| **Lead Engineer** (`lead-engineer`) | Implementation, code changes, architecture execution | `anthropic` (Claude Code) | implementation, code-changes, architecture, refactoring |
| **QA** (`qa`) | Tests, regression, validation, coverage, bug reproduction | `anthropic` | testing, regression, validation, coverage, bug-repro |
| **Security** (`security`) | Secrets/credential review, risky diffs, live-trading safety, dependency risk | `anthropic` | secrets-review, credential-audit, risk-assessment, dependency-audit, live-trading-safety |
| **Research** (`research`) | Data analysis, experiment design, research reports, edge discovery | `openai` | data-analysis, experiment-design, research-reports, edge-discovery |
| **Reviewer** (`reviewer`) | Code review, PR review, risk assessment | `anthropic` | code-review, pr-review, risk-assessment |

Provider defaults are exactly that — defaults. Every agent's provider is
overridable at registration: `monday agent register --name … --role … --provider ollama`.
`CPO → openai` and `Lead Engineer → anthropic` are the pinned mappings that make
"ChatGPT is the CPO agent" and "Claude is the Lead Engineer agent" true out of the box.

## Roles vs. agents

- A **role** is a stable job description (this file).
- An **agent** is a registered instance that fulfils a role using a provider. The
  registry seeds one **default** agent per role on first use:

  | Agent | Role | Provider |
  |---|---|---|
  | ChatGPT | cpo | openai |
  | Claude Code | lead-engineer | anthropic |
  | QA Agent | qa | anthropic |
  | Security Agent | security | anthropic |
  | Research Agent | research | openai |
  | Reviewer Agent | reviewer | anthropic |

You can register additional agents for any role; `monday agent run --role R`
picks the role's default agent (or the first active one).

## Gated actions per role

Each role documents the `gated_actions` it would need escalated (a subset of the
global gated set `commit, push, secrets, live_trade, destructive`):

- **Lead Engineer** → `commit`, `push`
- **Security** → `secrets`
- Others → none

These are documentation of intent. The actual enforcement happens per run against
the actions a run declares (`--action`), and every gated action requires human
approval regardless of role — see [APPROVAL_GATES.md](APPROVAL_GATES.md).

## Routing

- `monday agent assign TASK-ID --role qa` sets the task's `assigned_to` to
  `role:qa` — the task is owned by a role, not a person or model.
- `monday agent run TASK-ID --role qa` resolves the role to its agent/provider and
  executes under the approval gate.

QA, Security, Research, and Reviewer agents can each be run independently against
any task — e.g. run the Lead Engineer to produce work, then the Reviewer and
Security agents to assess it.
