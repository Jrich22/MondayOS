# Agent Providers & Environment Variables

Agents and the team workflow run on real AI providers. This page lists the
providers, the environment variables and packages each needs, and how MondayOS
behaves when a provider is not ready.

## Role → provider mapping

| Role | Provider | Model (default) | Needs |
|---|---|---|---|
| CPO | `openai` (ChatGPT) | `gpt-4o-mini` | `OPENAI_API_KEY` + `openai` SDK |
| Research | `openai` | `gpt-4o-mini` | `OPENAI_API_KEY` + `openai` SDK |
| Lead Engineer | `anthropic` (Claude) | `claude-sonnet-4-6` | `ANTHROPIC_API_KEY` + `anthropic` SDK |
| QA | `anthropic` | `claude-sonnet-4-6` | `ANTHROPIC_API_KEY` + `anthropic` SDK |
| Security | `anthropic` | `claude-sonnet-4-6` | `ANTHROPIC_API_KEY` + `anthropic` SDK |
| Reviewer | `anthropic` | `claude-sonnet-4-6` | `ANTHROPIC_API_KEY` + `anthropic` SDK |

Every mapping is overridable per agent: `monday agent register --role qa --provider ollama`.

## Required environment variables

| Provider | Env var | Package | Notes |
|---|---|---|---|
| `openai` | `OPENAI_API_KEY` | `pip install openai` | OpenAI-compatible endpoints via `base_url`. |
| `anthropic` | `ANTHROPIC_API_KEY` | `pip install anthropic` | Claude models. |
| `ollama` | *(none)* | *(none — HTTP)* | Local service at `http://localhost:11434` (override with `base_url`). |
| `fake` | *(none)* | *(built in)* | Offline deterministic provider for demos/CI. |

Keys are read from the environment (or an explicit `api_key` in `ProviderConfig`);
they are never written to disk by MondayOS.

```bash
export OPENAI_API_KEY="sk-…"        # CPO, Research
export ANTHROPIC_API_KEY="sk-ant-…" # Lead Engineer, QA, Security, Reviewer
pip install openai anthropic        # provider SDKs (optional — install what you use)
```

## Availability checks

Before a run that would call a provider, MondayOS checks availability (SDK present
**and** key set). See it per agent:

```bash
monday agent list
#   ★ AGENT-0001  cpo            openai      ✓ ready            ChatGPT
#   ★ AGENT-0002  lead-engineer  anthropic   needs ANTHROPIC…  Claude Code
```

`✓ ready` means the SDK is importable and the key is set; otherwise the column
shows the missing requirement.

## Graceful failure when a provider is not ready

A run (or a team stage) that resolves to an unavailable provider **does not call
the API and does not touch the task**. It stops with a clear, actionable message:

```
$ monday agent run TASK-0001 --role cpo
  Status    : unavailable
  Provider unavailable — OPENAI_API_KEY is not set; set OPENAI_API_KEY.
```

```
$ monday team run TASK-0001
  Status  : failed
  Stopped at: cpo
  Pipeline stopped at CPO: Provider unavailable — OPENAI_API_KEY is not set; set OPENAI_API_KEY.
```

To run with no keys configured (demos, CI), use the offline provider:

```bash
monday agent run TASK-0001 --role lead-engineer --provider fake
monday team  run TASK-0001 --provider fake
```

## Provider / model in the logs

The selected provider and model are recorded on every run and shown in output:

- `monday agent run …` prints `Provider : anthropic (claude-sonnet-4-6)`.
- `monday team run …` prints the provider/model for each stage.
- The JSON records carry `provider_used` + `provider_model` (per `AgentRun` /
  `TeamStage`) and `model_used` on the underlying execution report
  (`logs/agents/run-*.json`, `logs/agents/team-*.json`).

## Safety

Review-required is unchanged: real providers only ever produce output that is
captured and moved to REVIEW. No agent commits, pushes, changes secrets, or
executes live — see [APPROVAL_GATES.md](APPROVAL_GATES.md). Missing credentials
fail safe (stop with instructions), never mid-action.
