# Agent Providers & Environment Variables

Agents and the team workflow run on real AI providers. This page lists the
providers, the environment variables and packages each needs, and how MondayOS
behaves when a provider is not ready.

## AI Workspace provider

The AI Workspace resolves its provider from the environment at startup. There is
no separate configuration and no new secrets system — it reads the variables
already listed below.

Selection order, when `MONDAYOS_PROVIDER` is unset:

1. `anthropic` — `ANTHROPIC_API_KEY` set and the SDK importable
2. `openai` — `OPENAI_API_KEY` set and the SDK importable
3. `deepseek` — `DEEPSEEK_API_KEY` set and the OpenAI SDK importable
4. `ollama` — a local daemon answering on `OLLAMA_HOST`
5. none — the workspace says so rather than pretending to be configured

Set `MONDAYOS_PROVIDER` to pin one explicitly. An explicit choice is honoured
even if it cannot run: being told "you asked for anthropic and the key is not
set" is more useful than silently answering with a different model.

A project-local `.env` is read if present, filling only variables the shell has
**not** already set — an exported value is a deliberate act, a file is a default.
The key never enters MondayOS's own config object: each provider reads its own
variable directly, so a secret cannot surface in a repr, a log line or a crash
dump.

The startup banner states the choice without the key:

```
AI Workspace provider: anthropic · claude-sonnet-4-5 · streams natively — ANTHROPIC_API_KEY is set
```

**Streaming is reported honestly.** A provider that streams natively does; one
that does not still works, delivering the whole answer as a single chunk, and
reports `supports_streaming = False` so the interface can show the difference
rather than animating one chunk to look like tokens arriving.

| Provider | Native streaming |
|---|---|
| `anthropic` | yes |
| `openai` | not yet — single chunk |
| `deepseek` | not yet — single chunk |
| `ollama` | not yet — single chunk |

---

## Role → provider mapping

| Role | Provider | Model (default) | Needs |
|---|---|---|---|
| CPO | `openai` (ChatGPT) | `gpt-4o-mini` | `OPENAI_API_KEY` + `openai` SDK |
| Research | `openai` | `gpt-4o-mini` | `OPENAI_API_KEY` + `openai` SDK |
| Lead Engineer | `anthropic` (Claude) | `claude-sonnet-4-6` | `ANTHROPIC_API_KEY` + `anthropic` SDK |
| QA | `anthropic` | `claude-sonnet-4-6` | `ANTHROPIC_API_KEY` + `anthropic` SDK |
| Security | `anthropic` | `claude-sonnet-4-6` | `ANTHROPIC_API_KEY` + `anthropic` SDK |
| Reviewer | `openai` (ChatGPT) | `gpt-4o-mini` | `OPENAI_API_KEY` + `openai` SDK |

Productive-role mappings are overridable per agent:
`monday agent register --role qa --provider ollama`. The Reviewer mapping is a
runtime integrity rule: custom registry metadata is preserved, but review still
executes through OpenAI/ChatGPT.

## Required environment variables

| Provider | Env var | Package | Notes |
|---|---|---|---|
| `openai` | `OPENAI_API_KEY` | `pip install openai` | OpenAI-compatible endpoints via `base_url`. |
| `anthropic` | `ANTHROPIC_API_KEY` | `pip install anthropic` | Claude models. |
| `deepseek` | `DEEPSEEK_API_KEY` | `pip install openai` | DeepSeek's OpenAI-compatible API. |
| `ollama` | *(none)* | *(none — HTTP)* | Local service at `http://localhost:11434` (override with `base_url`). |
| `fake` | *(none)* | *(built in)* | Offline deterministic provider for demos/CI. |

Keys are read from the environment (or an explicit `api_key` in `ProviderConfig`);
they are never written to disk by MondayOS.

Optional model and local-host overrides:

| Variable | Purpose |
|---|---|
| `MONDAYOS_OPENAI_MODEL` | OpenAI model used by agent roles. |
| `MONDAYOS_ANTHROPIC_MODEL` | Anthropic model used by agent roles. |
| `MONDAYOS_DEEPSEEK_MODEL` | DeepSeek model used by agent roles. |
| `MONDAYOS_OLLAMA_MODEL` | Installed Ollama model used by agent roles. |
| `OLLAMA_HOST` | Ollama base URL; a bare `host:port` is normalized to HTTP. |

```bash
export OPENAI_API_KEY="sk-…"        # CPO, Research, Reviewer
export ANTHROPIC_API_KEY="sk-ant-…" # Lead Engineer, QA, Security
export DEEPSEEK_API_KEY="sk-…"      # Coding and automatic fallback
pip install openai anthropic        # provider SDKs (optional — install what you use)
```

## Automatic failover

When more than one provider is supplied to the Execution Orchestrator, MondayOS
ranks them using the selected policy. If the preferred provider is unavailable,
rate-limited, or fails during generation, the next eligible provider continues
the task. Every attempt and failure reason is recorded in the execution report.
An explicit manual provider remains pinned and does not silently switch models.

The dashboard automatically builds this pool from every configured provider key.
For example, setting `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and
`DEEPSEEK_API_KEY` makes all three eligible without copying credentials into a
MondayOS configuration file.

Agent and team runs use the same idea with role-aware ordering. The role's
registered provider is primary; configured candidates then follow in deterministic
OpenAI, Anthropic, DeepSeek, Ollama order with duplicates removed. A normal team
therefore preserves its division of labor but can continue when Claude or another
hosted provider reaches a limit. The final Reviewer is the exception: OpenAI/
ChatGPT is mandatory and an outage stops the approval path. `--provider NAME`
remains a strict pin for productive stages and never falls through to a different
model, but a whole-team pin cannot replace the final OpenAI reviewer.

## Availability checks

Before a run that would call a provider, MondayOS checks availability (SDK present
**and** key set). See it per agent:

```bash
monday agent list
#   ★ AGENT-0001  cpo            openai→deepseek       ✓ ready   ChatGPT
#   ★ AGENT-0002  lead-engineer  anthropic→deepseek    ✓ ready   Claude Code
#   ★ AGENT-0006  reviewer       openai                needs setup Reviewer Agent
```

The provider column shows `registered→effective` when the first currently usable
fallback differs from the registry preference. `✓ ready` describes that effective
provider. The Reviewer never falls back: it remains `openai` and reports setup as
needed when OpenAI/ChatGPT is unavailable.

## Graceful failure when a provider is not ready

An explicitly pinned standalone agent run that resolves to an unavailable
provider **does not call the API and does not touch the task**. It stops with a
clear, actionable message:

```
$ monday agent run TASK-0001 --role cpo --provider openai
  Status    : unavailable
  Provider unavailable — OPENAI_API_KEY is not set; set OPENAI_API_KEY.
```

A team run claims its task by moving it to `IN_PROGRESS` before the first stage.
If no provider can execute a stage, the team stops as `failed` and leaves the
task in progress for recovery; it never reports that the stage ran successfully.

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
are skipped before a call; if no eligible provider succeeds, the run stops and
records every attempt.
