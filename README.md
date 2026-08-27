# MondayOS

**An AI Operating System for software engineering.**

Version: `1.0.0b1` (Beta) · Python ≥ 3.11 · No database required

MondayOS turns AI models into reliable, long-term engineering collaborators —
participants that carry institutional knowledge, reason about trade-offs,
document their work, and coordinate execution under human oversight. It is
provider-independent: Claude, OpenAI, and local Ollama models plug into the same
abstraction, and the platform never depends on any single one.

> **Beta status.** The platform is feature-complete for single-developer,
> local use and fully tested (773 tests). It is not yet hardened for
> multi-user or production deployment. See [RELEASE.md](RELEASE.md) for the
> full list of features, limitations, and known issues.

---

## Vision

Code is a by-product of engineering decisions; the decisions are the real
product. MondayOS treats the **capture, organisation, and retrieval of
engineering knowledge** as a first-class output, and treats AI agents as
**collaborators with persistent project memory** rather than one-shot tools.

Three commitments shape every design choice:

- **Explainability is non-negotiable.** Every action carries a reasoning trace.
- **Model independence is a hard boundary.** No lock-in to one AI provider.
- **Human oversight is a feature.** Consequential actions stop and ask.

The full vision is in [docs/VISION.md](docs/VISION.md).

---

## Features

| Capability | Command | What it does |
|---|---|---|
| **Knowledge base** | `monday learn` / `search` | Capture and retrieve structured engineering knowledge (decisions, bugs, patterns, runbooks…) |
| **Internal reasoning** | `monday ask` | Answer engineering questions from stored knowledge — no model call required |
| **Task lifecycle** | `monday task` | Create, track, and transition tasks with a full audit trail |
| **Workflows** | `monday workflow` | Run declarative multi-step YAML workflows through the public API |
| **Knowledge migration** | `monday migrate` | Import existing project docs (CHANGELOG, ADRs, roadmaps…) into the knowledge base |
| **Repository doctor** | `monday doctor` | Health inspection across git, tests, code quality, docs, tasks, config |
| **Engineering advisor** | `monday advise` | Synthesise risks, next actions, and a recommended sprint goal |
| **Project management** | `monday project` / `onboard` | Register and onboard external repositories |
| **AI provider layer** | _(config)_ | Interchangeable Claude / OpenAI / Ollama providers behind one interface |
| **Execution orchestrator** | `monday execute` | Delegate a task to an AI provider through a safe, policy-driven pipeline |

Everything is stored as human-readable Markdown/JSON on disk and in Git — no
database, fully auditable, diffable, and offline-capable.

---

## Architecture

MondayOS is a layered platform. Every external caller uses **one** entry point —
the `Monday` class — and internal subsystems communicate through stable
interfaces and an event bus.

```
                          ┌──────────────────────────┐
   CLI  ·  Python API ───▶│      Monday (public)     │   the only public surface
                          └────────────┬─────────────┘
                                       │
        ┌──────────────┬───────────────┼───────────────┬──────────────┐
        ▼              ▼               ▼               ▼              ▼
   ┌─────────┐   ┌──────────┐   ┌────────────┐   ┌─────────┐   ┌────────────┐
   │knowledge│   │  tasks   │   │  workflows │   │ doctor  │   │  advisor   │
   └─────────┘   └──────────┘   └────────────┘   └─────────┘   └────────────┘
        ▼              ▼               ▼               ▼              ▼
   ┌─────────┐   ┌──────────┐   ┌────────────┐   ┌─────────┐   ┌────────────┐
   │ migrate │   │  memory  │   │   search   │   │  brain  │   │orchestrator│
   └─────────┘   └──────────┘   └────────────┘   └────┬────┘   └─────┬──────┘
                                                      │              │
                                               ┌──────▼──────┐       │
                                               │brain.provider│◀──────┘  execute through
                                               │  (AIProvider)│          the abstraction only
                                               └──────┬──────┘
                                       ┌──────────────┼──────────────┐
                                       ▼              ▼              ▼
                                   Anthropic        OpenAI         Ollama
                                    (Claude)        (GPT-*)       (local)

   ┌────────────────────────────── events (audit bus) ──────────────────────────────┐
   └─── core (shared types) ·  Storage: Git + Markdown/JSON files (no database) ─────┘
```

A detailed component diagram and data-flow walkthrough is in
[docs/ARCHITECTURE_DIAGRAM.md](docs/ARCHITECTURE_DIAGRAM.md). The original
layered design rationale is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

**Subsystems** (`*/` packages): `monday` (public API), `brain` (reasoning,
routing, providers), `knowledge`, `memory`, `tasks`, `workflows`, `migrate`,
`doctor`, `advisor`, `orchestrator`, `events`, `search`, `core`.

---

## Quick Start

**Requirements:** Python ≥ 3.11, Git.

```bash
# 1. Clone
git clone <your-fork-url> MondayOS
cd MondayOS

# 2. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install (editable, with dev tools)
pip install -e ".[dev]"

# 4. Verify
monday status
pytest                            # 773 passed, 12 skipped
```

`monday status` should print `MondayOS v1.0.0b1` and a healthy module list.

**Configuring an AI provider (optional).** The knowledge, reasoning, task,
doctor, and advisor features are fully deterministic and need no API key. To use
`monday execute` or AI-enriched advice, export a key for your chosen provider:

```bash
export ANTHROPIC_API_KEY=sk-...     # Claude
export OPENAI_API_KEY=sk-...        # OpenAI
# Ollama: just run `ollama serve` locally — no key needed
```

---

## CLI examples

```bash
# Knowledge
monday learn --title "Homebrew PATH fix" --type bug --content "Add brew to PATH in ~/.zshrc."
monday search "rate limit"
monday ask "Have we seen this Homebrew error before?"

# Tasks
monday task create --title "Add /health endpoint" --objective "Return service status." --priority P1
monday task list
monday task get TASK-0001
monday task complete TASK-0001 --reason "Merged and deployed."

# Repository intelligence
monday doctor                       # health score + findings
monday advise                       # risks, next actions, sprint goal
monday advise --brief

# Workflows
monday workflow list
monday workflow run implement-function --var function_name=parse_config

# Execution (delegates a task to an AI provider — safe by default)
monday execute TASK-0001 --dry-run                 # plan only, no calls, no changes
monday execute TASK-0001                            # review-required (default)
monday execute TASK-0001 --policy highest-capability
monday execute TASK-0001 --mode autonomous --enable-autonomous
```

Full command reference: [docs/CLI.md](docs/CLI.md).

---

## Example: onboarding an external project (WeatherBot)

MondayOS can manage repositories other than itself. Onboarding runs
`migrate` → `doctor` → `advise` against the target and produces a full report.

```bash
# Register an external repo
monday project register weatherbot /path/to/WeatherBot \
  --description "CLI weather fetcher and alert system"

monday project list

# Run the onboarding pipeline (imports docs, inspects health, advises)
monday onboard weatherbot
```

Output:

```
════════════════════════════════════════════════════════════════
  ONBOARDING COMPLETE — WEATHERBOT
════════════════════════════════════════════════════════════════
  Health Score  : 100/100 (Excellent)
  Confidence    : 55%
  Sprint Goal   : Continue forward momentum
────────────────────────────────────────────────────────────────
  Report        : projects/weatherbot/ONBOARDING_REPORT.md
════════════════════════════════════════════════════════════════
```

The generated report answers: what MondayOS knows about the project, what
documentation exists, what knowledge is missing, what engineering risks exist,
what to build next, and what tasks to create. A real example lives at
[projects/weatherbot/ONBOARDING_REPORT.md](projects/weatherbot/ONBOARDING_REPORT.md).

---

## Roadmap

MondayOS is at **Phase 1 (Foundation)** — single developer, local, fully
tested. The path beyond the beta:

- **Phase 2 — Team-scale:** shared instances, concurrent execution (async event
  bus), database backend (SQLite → Postgres), multi-agent orchestration.
- **Phase 3 — Enterprise:** role-based access, compliance audit trails, CI/CD
  and project-management integrations, a read-only dashboard.

The detailed, dated beta-and-beyond plan is in
[docs/BETA_ROADMAP.md](docs/BETA_ROADMAP.md). The long-term product vision is in
[docs/VISION.md](docs/VISION.md).

---

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md) for the
development workflow, coding standards, PR process, and testing requirements.
The short version: branch from `main`, keep changes within existing subsystems,
add tests, and ensure `pytest` is green before opening a PR.

---

## Documentation map

| Document | Purpose |
|---|---|
| [RELEASE.md](RELEASE.md) | Beta features, limitations, known issues, upgrade path |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to develop and contribute |
| [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) | Pre-release verification checklist |
| [docs/ARCHITECTURE_DIAGRAM.md](docs/ARCHITECTURE_DIAGRAM.md) | Component + data-flow diagrams |
| [docs/BETA_ROADMAP.md](docs/BETA_ROADMAP.md) | What's in the beta and what comes next |
| [docs/VISION.md](docs/VISION.md) | Long-term product vision |
| [docs/CLI.md](docs/CLI.md) | Full CLI reference |
| [docs/GROWTH_BOT.md](docs/GROWTH_BOT.md) | Growth Bot service specification |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | Complete version history |

---

*MondayOS — make AI a collaborator, not a tool.*
