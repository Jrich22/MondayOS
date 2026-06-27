# MondayOS — Changelog

All notable changes to MondayOS are documented here.

This file follows the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.  
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

---

## [0.2.0] — 2026-06-27 — Sprint 1.1: Public API

### Added
- `monday/` package — stable public API boundary for MondayOS
- `Monday` class with five public methods: `ask()`, `learn()`, `search()`, `task()`, `status()`
- `MondayConfig` dataclass for configuring `Monday` instances
- Typed response dataclasses: `AskResponse`, `LearnResponse`, `SearchResponse`, `TaskResponse`, `StatusResponse`, `ModuleStatus`
- `monday/README.md` — external consumer documentation and the "import from monday only" rule
- `tests/test_monday.py` — 80 tests covering the public surface (71 passing, 9 skipped placeholders)
- ADR-007: Monday class as the stable public API boundary
- ADR-008: Typed response dataclasses for all public methods

### Changed
- `monday.status()` is the first substantially-implemented public method; returns live system health, version, session ID, per-module status, and uptime

---

## [0.1.1] — 2026-06-27 — Sprint 1: Package Architecture

### Added
- Six Python packages with clean public interfaces: `brain`, `events`, `knowledge`, `memory`, `search`, `tasks`
- `core/types.py` — shared primitive types (`EntityId`, `Timestamp`, `ModelId`, `ComponentName`)
- `EventBus` — fully implemented synchronous in-process event bus
- `SessionMemory` — fully implemented volatile in-process memory tier
- `Task.can_transition_to()` — status transition graph encoded and enforced
- `pyproject.toml` — project configuration with Ruff, Mypy, and pytest settings
- `conftest.py` — sys.path configuration for test discovery
- `tests/` — 111 test cases across 6 modules (82 passing, 29 skipped placeholders)
- `README.md` for each module documenting responsibility, interface, and boundaries

---

## [0.1.0] — 2026-06-27 — Foundation

### Added
- `docs/PROJECT_OVERVIEW.md` — What MondayOS is, the problem it solves, and core principles
- `docs/VISION.md` — Long-term product philosophy and success criteria
- `docs/ARCHITECTURE.md` — Layered system architecture, component design, data flow, and cross-cutting concerns
- `docs/ENGINEERING_STANDARDS.md` — Code style, type annotation requirements, testing coverage targets, Git standards, and security policy
- `docs/DOCUMENTATION_STANDARDS.md` — Documentation types, writing standards, ADR format, and freshness policy
- `docs/KNOWLEDGE_SYSTEM.md` — Schema and lifecycle for bug, decision, pattern, and runbook knowledge entries
- `docs/MEMORY_SYSTEM.md` — Three-tier memory architecture (session, project, agent) and storage design
- `docs/TASK_SYSTEM.md` — Task file schema, status lifecycle, priority system, and routing design
- `docs/ROADMAP.md` — Phase-based development plan with entry and exit criteria for Phases 1–4
- `docs/DECISIONS.md` — ADR log (ADR-001 through ADR-006): language choice, storage strategy, data format, model abstraction, Git-as-truth, and approval gates

---

## [0.1.0] — 2026-06-27 — Foundation

### Added
- `docs/PROJECT_OVERVIEW.md` — What MondayOS is, the problem it solves, and core principles
- `docs/VISION.md` — Long-term product philosophy and success criteria
- `docs/ARCHITECTURE.md` — Layered system architecture, component design, data flow, and cross-cutting concerns
- `docs/ENGINEERING_STANDARDS.md` — Code style, type annotation requirements, testing coverage targets, Git standards, and security policy
- `docs/DOCUMENTATION_STANDARDS.md` — Documentation types, writing standards, ADR format, and freshness policy
- `docs/KNOWLEDGE_SYSTEM.md` — Schema and lifecycle for bug, decision, pattern, and runbook knowledge entries
- `docs/MEMORY_SYSTEM.md` — Three-tier memory architecture (session, project, agent) and storage design
- `docs/TASK_SYSTEM.md` — Task file schema, status lifecycle, priority system, and routing design
- `docs/ROADMAP.md` — Phase-based development plan with entry and exit criteria for Phases 1–4
- `docs/DECISIONS.md` — ADR log (ADR-001 through ADR-006): language choice, storage strategy, data format, model abstraction, Git-as-truth, and approval gates
