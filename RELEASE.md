# MondayOS v1.0.0b1 — Beta Release Notes

**Release:** `1.0.0b1` (first public beta)
**Date:** 2026-06-28
**Status:** Beta — feature-complete for single-developer local use; not yet
hardened for production or multi-user deployment.

This is the first beta-quality release of MondayOS. It focuses on **product
quality** — documentation, coherence, and verification — over new capability.
The platform has been built incrementally across 13 initiatives and is covered
by 773 passing automated tests.

---

## Features

### Knowledge & reasoning
- **Knowledge base** (`monday learn` / `monday search`) — capture structured,
  typed knowledge entries (decision, bug, pattern, runbook, feature, lesson,
  documentation, research, and more) as human-readable Markdown.
- **Internal reasoning engine** (`monday ask`) — answers engineering questions
  from stored knowledge and active tasks with a confidence score and sources.
  Fully deterministic; no model call required.

### Work coordination
- **Task system** (`monday task`) — create, retrieve, list, start, review, and
  complete tasks. Every status transition is validated and recorded as an audit
  trail. Tasks are never deleted, only archived.
- **Workflow engine** (`monday workflow`) — run declarative multi-step YAML
  workflows; each step flows through the Monday public API with optional human
  approval gates.

### Repository intelligence
- **Knowledge migration** (`monday migrate`) — import existing project documents
  (CHANGELOG, ADRs/decisions, roadmaps, session logs, workflows) into the
  knowledge base. Idempotent, with rollback.
- **Repository doctor** (`monday doctor`) — health inspection across seven
  analyzers (git, tests, code quality, knowledge, documentation, tasks, config)
  producing a 0–100 score, grade, and ranked recommendations.
- **Engineering advisor** (`monday advise`) — synthesises repository health,
  knowledge, tasks, and workflow history into risks, ranked next actions, a
  recommended sprint goal, and a confidence score.

### Project management
- **Project registry & onboarding** (`monday project` / `monday onboard`) —
  register external repositories and run a full onboarding pipeline
  (migrate → doctor → advise) that produces a comprehensive Markdown report.

### AI provider layer
- **Provider abstraction** (`AIProvider`) with interchangeable implementations
  for **Anthropic (Claude)**, **OpenAI (GPT-\*)**, and **Ollama (local)**.
  Providers are selected by configuration; no provider-specific code exists
  outside the provider implementations.

### Execution orchestrator
- **Execution orchestrator** (`monday execute`) — delegates a task to an AI
  provider through a safe, policy-driven pipeline: advisor prioritises → plan →
  queue → provider selection → provider executes → validation → knowledge
  capture → task update → persisted execution report.
- **Three safety modes:** Dry Run, Review Required (default), Autonomous.
  Autonomous file modification requires explicit enablement.
- **Policy-based provider selection:** prefer-local, lowest-cost,
  highest-capability, or manual override.

### Cross-cutting
- **Event bus** — a synchronous in-process audit bus; significant actions
  (task changes, knowledge writes, model calls) publish typed events.
- **Storage** — Git + Markdown/JSON files. No database. Everything is
  human-readable, diffable, and offline-capable.
- **CLI** — a single `monday` command with 12 subcommands.

---

## Limitations

These are intentional Phase-1 boundaries, not bugs:

- **Single-user, single-process.** `Monday` instances are not thread-safe and
  there is no concurrency control. One developer, one machine.
- **No database.** Storage is flat files in Git. Search is keyword-based, not
  semantic; performance is fine for thousands of entries, untested beyond.
- **Synchronous event bus.** Events are delivered in-process, in registration
  order. There is no async queue, persistence of events, or replay.
- **The orchestrator does not yet write code to disk.** It coordinates AI
  providers and captures their output as knowledge; applying generated changes
  to files is out of scope for the beta (the `files_changed` field exists for
  forward compatibility and stays empty).
- **No dashboard / UI.** Interaction is via CLI and log inspection only.
- **No authentication or access control.** Anyone with filesystem access has
  full control. Do not expose a MondayOS working directory to untrusted users.
- **Provider configuration is programmatic.** `provider_config` is set via
  `MondayConfig`; there is not yet a config-file/env loader for it (planned —
  see Upgrade path).

---

## Known issues

- **`Monday.VERSION` is the single source of truth** for the version string;
  documentation version references are maintained by hand and may lag a patch.
- **Running the test suite or CLI from a directory that itself contains a
  `tasks/` data folder** can shadow the `tasks` package on `sys.path`. Run from
  the project root, or pass an absolute `--project-root`. (Does not affect
  installed use.)
- **`monday advise` / `onboard` run the full doctor inspection**, which can take
  a noticeable moment on large repositories. This is expected.
- **12 tests are skipped** by design (environment-dependent paths); 773 pass.
- **The onboarding registry (`config/projects.json`) stores absolute paths** and
  is intentionally git-ignored as machine-local runtime state.

---

## Upgrade path

This is the first beta; there is no prior published version to upgrade from.
For developers tracking `main`:

1. **From the 0.x development line → 1.0.0b1.** No data migration is required —
   on-disk formats (knowledge, tasks, logs) are unchanged. Pull `main`, then
   re-run `pip install -e ".[dev]"` to pick up the new `orchestrator` package,
   and run `pytest` to confirm a green suite.
2. **Public API stability.** The `Monday` class and its `*Response` dataclasses
   are the stable contract. Methods are additive across the 0.x → 1.0 line; no
   existing method signature was removed. Code written against `Monday` continues
   to work.
3. **Looking ahead to 1.0 final / Phase 2.** Planned changes that may affect
   integrators (tracked in [docs/BETA_ROADMAP.md](docs/BETA_ROADMAP.md)):
   - A config-file / environment loader for `provider_config`.
   - An optional SQLite storage backend behind the existing interfaces.
   - An async event bus for concurrent execution.
   These will be introduced behind the current public interfaces wherever
   possible; breaking changes will be called out in the changelog with a
   migration note.

See [docs/CHANGELOG.md](docs/CHANGELOG.md) for the complete version history.
