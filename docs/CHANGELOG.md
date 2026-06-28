# MondayOS — Changelog

All notable changes to MondayOS are documented here.

This file follows the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.  
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

---

## [0.4.0] — 2026-06-27 — Sprint 1.3: Task Capture

### Added
- `tasks/errors.py` — typed error hierarchy: `TaskError`, `TaskNotFoundError`, `TaskValidationError`, `InvalidTransitionError`, `TaskParseError`
- `tasks/parser.py` — `TaskParser.parse()` and `TaskParser.serialize()`: Markdown + YAML frontmatter I/O for `Task` objects; round-trips cleanly; handles `status_history` with `from_status: null` for initial creation; alphabetically sorted keys for determinism
- `TaskManager.create()` — validates required fields, assigns stable ID (`TASK-NNNN`), builds initial `StatusTransition`, persists to `tasks/active/{ID}.md`, returns `Task`
- `TaskManager.get()` — retrieves by ID from `tasks/active/` then `tasks/completed/`; raises `TaskNotFoundError` if absent
- `TaskManager.update_status()` — validates transition via `Task.can_transition_to()`, appends `StatusTransition` to history, moves file to `tasks/completed/` on terminal status
- `TaskManager.list_active()` — scans `tasks/active/`, parses each file, applies optional filters (status, priority, assigned_to, task_type)
- `TaskManager.assign()`, `TaskManager.block()`, `TaskManager.append_work_log()`, `TaskManager.archive()` — convenience wrappers
- Sequence tracking in `tasks/.sequences.json` — survives restarts, consistent across instances
- `Monday.task(action='create')` — validates and delegates to `TaskManager.create()`; publishes `TASK_CREATED` event; returns `TaskResponse`
- `Monday.task(action='get')` — retrieves task by `task_id`; returns `TaskResponse` with full task dict
- `Monday.task(action='list')` / `action='list_active'` — lists active tasks; optional filters via kwargs; returns `TaskResponse` with `data.tasks` list
- `Monday.task(action='complete')` — transitions task to COMPLETED via `TaskManager.update_status()`; publishes `TASK_COMPLETED` event; returns `TaskResponse`

### Changed
- `tasks/__init__.py` — now exports all error types (`TaskError`, `TaskNotFoundError`, `TaskValidationError`, `InvalidTransitionError`, `TaskParseError`) and `TaskParser`
- `Monday.__init__` — `TaskManager` now receives `project_root` so task files are written to the configured project directory
- `Monday.task()` — fully implemented for `create`, `get`, `list`/`list_active`, and `complete`; unknown actions return `success=False` with descriptive message

### Tests
- `tests/test_tasks.py` — removed two `NotImplementedError` stubs; added `TestTaskParser` (7 tests) and `TestTaskManager` (19 tests) with full disk I/O via `tmp_path`; total 46 task tests
- `tests/test_monday.py` — `TestTask` converted from `setup_method` to `autouse` fixture with `tmp_path`; 7 new tests: create/get/list/complete end-to-end; removed 2 skipped stubs

### Total test counts
- **250 passed**, **15 skipped**, **0 failures**

---

## [0.3.0] — 2026-06-27 — Sprint 1.2: Knowledge Capture

### Added
- `knowledge/errors.py` — typed errors: `KnowledgeParseError`, `KnowledgeNotFoundError`, `KnowledgeConflictError`, `KnowledgeValidationError`
- `KnowledgeType` enum — 12 types from MKS 1.0 (Bug, Decision, Task, Sprint, Feature, Lesson, Pattern, Runbook, Documentation, Research, Weather, Experiment)
- `LifecycleStatus` enum — 5 lifecycle states (Draft, Active, Deprecated, Superseded, Archived)
- `RelationType` enum — 13 typed relationship directions
- `Relationship` dataclass — typed, directional link between entries
- `KnowledgeParser` — parses Markdown + YAML frontmatter into `KnowledgeEntry`; serialize round-trips cleanly
- `KnowledgeLoader` — walks knowledge directory; skips non-frontmatter files silently
- `KnowledgeIndex` — in-memory index with `build()`, `add()`, `lookup()`, `by_type()`, `by_tag()`, `by_component()`, `all_active()`
- `KnowledgeStore` — Markdown-on-disk backend: `add()`, `get()`, `search()`, `supersede()`, `list_all()`; ID sequence tracking via `.sequences.json`
- `Monday.learn()` — end-to-end implementation: validates type, builds CKO, persists via `KnowledgeStore`, publishes `KNOWLEDGE_ENTRY_CREATED` event, returns `LearnResponse`
- `Monday.search()` — keyword search across knowledge base; results include id, title, summary, entry_type, tags

### Changed
- `KnowledgeEntry` — aligned to MKS CKO schema: `created_at` (renamed from `created`), added `version`, `summary`, `created_by`, `updated_at`, `updated_by`, `relationships`, `type_fields`; `confidence` changed from `str` to `float` (default `1.0`)
- `KnowledgeStore.__init__` — now accepts `project_root: Path`; loads existing entries on init
- `Monday.__init__` — passes `project_root` to `KnowledgeStore`
- `EntryType`, `EntryStatus` — preserved as backward-compat aliases for `KnowledgeType` and `LifecycleStatus`

### Tests
- `tests/test_knowledge.py` — 64 tests (0 skipped); covers entry model, relationships, index, parser (with round-trip), and store (with disk I/O via `tmp_path`)
- `tests/test_monday.py` — 89 tests (5 skipped); `TestLearn` and `TestSearch` use isolated `tmp_path`; learn→search round-trip verified

### Total test counts
- **217 passed**, **22 skipped**, **0 failures**

---

## [0.2.1] — 2026-06-27 — Sprint 1.2 Pre-work: MKS

### Added
- `docs/MKS.md` — MondayOS Knowledge Specification v1.0; the canonical contract for all knowledge stored by MondayOS
- ADR-009: Decision to establish MKS as the formal product specification before Sprint 1.2 implementation

### Changed
- `docs/KNOWLEDGE_SYSTEM.md` superseded by MKS 1.0 (retained for reference)

### Notes
- MKS defines 12 knowledge types (Bug, Decision, Task, Sprint, Feature, Lesson, Pattern, Runbook, Documentation, Research, Weather, Experiment)
- MKS defines CKO base schema, ID rules, relationship graph model, lifecycle, versioning, 20 validation rules, and migration protocol across 5 storage backends
- Implementation of Sprint 1.2 (`KnowledgeParser`, `KnowledgeLoader`, `KnowledgeIndex`, `KnowledgeStore`) begins after this checkpoint

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
