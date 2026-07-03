# MondayOS — Beta Roadmap

**Current release:** `1.0.0b1` (Beta)
**Last updated:** 2026-06-28

This roadmap describes what is in the beta, what is required to reach `1.0.0`
final, and the phases beyond it. For the long-term product vision see
[VISION.md](VISION.md); for the complete history see [CHANGELOG.md](CHANGELOG.md).

---

## Where we are: Phase 1 (Foundation) — ✅ complete

A single engineer can run MondayOS locally to capture knowledge, manage tasks,
inspect repositories, get engineering advice, and delegate task execution to AI
providers — with every action auditable and no database required.

Delivered across 13 initiatives / sprints:

| Area | Status | Shipped in |
|---|---|---|
| Public API + package architecture | ✅ | 0.1.x – 0.2.x |
| Knowledge capture & search (MKS) | ✅ | 0.3.0 |
| Task capture & lifecycle | ✅ | 0.4.0 |
| Internal reasoning engine (`ask`) | ✅ | 0.5.0 |
| Monday CLI | ✅ | 0.6.0 |
| End-to-end workflow engine | ✅ | 0.7.0 |
| Knowledge migration engine | ✅ | 0.8.0 |
| Repository doctor | ✅ | 0.9.0 |
| Engineering advisor | ✅ | 0.10.0 |
| External project onboarding | ✅ | 0.11.0 |
| AI provider layer (Claude/OpenAI/Ollama) | ✅ | 0.12.0 |
| Execution orchestrator | ✅ | 0.13.0 |
| Beta release quality (docs, verification) | ✅ | 1.0.0b1 |

**Test coverage at beta:** 773 passing, 12 skipped.

---

## Beta → 1.0.0 final

The goal between beta and final is *hardening and ergonomics*, not new
subsystems. Candidate work, roughly in priority order:

### Must-have for 1.0.0
- [ ] **Provider configuration loader.** Read `provider_config` from
      `config/monday.toml` and environment variables (today it is set
      programmatically via `MondayConfig`).
- [ ] **Beta feedback pass.** Triage issues from beta users; fix correctness and
      usability bugs surfaced in real use.
- [ ] **Documentation completeness.** Keep `README`, `CLI.md`, and the
      architecture docs in lock-step with any API change; add a short
      troubleshooting guide.
- [ ] **Packaging polish.** Confirm a clean install on Linux, macOS, and Windows;
      pin a tested dependency range; add a `LICENSE`.

### Should-have for 1.0.0
- [ ] **`monday execute` apply step (opt-in).** Allow autonomous mode to apply a
      provider's proposed changes to files behind the existing approval gate,
      populating `files_changed`.
- [ ] **Follow-up task generation.** Let the orchestrator create follow-up tasks
      from validation findings (currently the report field exists but is unused).
- [ ] **Richer validation.** Optional provider-assisted self-review
      (`AIProvider.review`) layered on top of the deterministic checks.

### Nice-to-have
- [ ] **CLI quality-of-life:** shell completion, `--json` everywhere, colourised
      output toggles.
- [ ] **Coverage gate in CI.**

---

## Phase 2 — Team-scale

A small team shares one MondayOS instance and accumulates collective knowledge.
Introduced *behind the existing public interfaces* wherever possible.

- **Concurrency.** Replace the synchronous in-process event bus with an async
  queue; make execution safe under concurrent tasks.
- **Database backend.** Optional SQLite (then Postgres) behind the current store
  interfaces, for query performance beyond the flat-file comfort zone. Files
  remain the source of truth / export format.
- **Multi-agent orchestration.** Build on the provider abstraction + execution
  queue to coordinate multiple agents/providers on a single objective.
- **Shared task & knowledge model.** Assignment, ownership, and notifications
  across team members.
- **Semantic search.** Embeddings-based retrieval alongside keyword search.

---

## Phase 3 — Enterprise-scale

- **Access control.** Role-based permissions; per-tenant data isolation.
- **Compliance.** Exportable, tamper-evident audit trails.
- **Integrations.** CI/CD hooks, project-management connectors, chat-based
  approvals.
- **Dashboard.** A read-only web surface for observability and approvals
  (CLI + logs remain fully supported).

---

## Durable constraints

These hold across every phase and bound the roadmap (from
[VISION.md](VISION.md)):

- **Explainability** — no feature that makes AI actions less auditable.
- **Model independence** — the provider abstraction is a hard boundary.
- **Human oversight** — approval gates for production-impacting actions never
  disappear; they only get lower-friction.
- **Data ownership** — operators own their knowledge, memory, and logs.
- **Simplicity** — features that add disproportionate complexity are deferred.

---

*This roadmap is indicative, not a commitment of dates. Priorities adjust with
beta feedback.*
