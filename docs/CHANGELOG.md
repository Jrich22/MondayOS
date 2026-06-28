# MondayOS — Changelog

All notable changes to MondayOS are documented here.

This file follows the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.  
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

---

## [0.7.0] — 2026-06-28 — Sprint 1.6: End-to-End Workflow

### Added
- `workflows/` package — multi-step workflow system with YAML definitions, engine, execution state, and error hierarchy
  - `workflows/definition.py` — `WorkflowDefinition`, `WorkflowStep`, `WorkflowInput`, `StepType` enum (7 step types: `ask`, `search`, `learn`, `task_create`, `task_start`, `task_complete`, `human_approval`)
  - `workflows/execution.py` — `WorkflowExecution`, `StepExecution`, `WorkflowStatus` enum, `StepStatus` enum; `WorkflowExecution.write_log()` serialises to JSON after every run
  - `workflows/engine.py` — `WorkflowEngine`: `list_workflows()`, `get_workflow(name)`, `run(name, inputs, approval_handler)`; `{step_id.output_key}` and `{inputs.variable}` template substitution in all step input fields; `ApprovalHandler` callable protocol for human gate injection
  - `workflows/errors.py` — typed error hierarchy: `WorkflowError`, `WorkflowNotFoundError`, `WorkflowValidationError`, `StepExecutionError`, `ApprovalDenied`
  - `workflows/__init__.py` — clean public exports
- `workflows/definitions/implement_function.yaml` — bundled "implement a function" workflow: research → create-task → human-approval → start-task → capture-pattern → complete-task; exercises all 7 step types and demonstrates the full MondayOS coordination loop
- `Monday.workflow(action, name, inputs, approval_handler)` — public API for workflow management; actions: `list`, `show`, `run`; returns `WorkflowResponse`
- `WorkflowResponse` dataclass — in `monday/types.py`; fields: `action`, `success`, `workflow_name`, `execution_id`, `status`, `data`, `message`
- `Monday.task("start", task_id)` — convenience action that chains BACKLOG → ASSIGNED → IN_PROGRESS in two `update_status` calls; required for workflow `task_start` step type
- CLI `monday workflow list` — lists all available workflow definitions with version and step count
- CLI `monday workflow show <name>` — shows inputs and ordered steps for a named workflow
- CLI `monday workflow run <name> [--var KEY=VALUE ...] [--yes]` — executes a workflow; `--var` passes input variables; `--yes` auto-approves all human gates (non-interactive)
- ADR-010: YAML as the workflow definition format

### Changed
- `monday/__init__.py` — exports `WorkflowResponse`
- `monday/cli.py` — module docstring updated; `_register_workflow()` added; `_build_parser()` registers workflow command
- `pyproject.toml` — `workflows*` added to `packages.find.include` and `coverage.run.source`

### Tests
- `tests/test_workflows.py` — 74 tests across 6 test classes:
  - `TestWorkflowDefinition` (11 tests) — YAML loading, all required fields, bad YAML, unknown step type, missing step fields, non-mapping root
  - `TestWorkflowExecution` (7 tests) — initial state, context seeding, to_dict, write_log creates file, execution_id in log, filename format, directory creation
  - `TestTemplateResolution` (12 tests) — `_resolve_str` simple/dot/unknown/multi, `_resolve_dict` string/nested/list/non-string, `_to_list` list/str/none/other
  - `TestWorkflowEngine` (22 tests) — list empty/missing/populated/skip-malformed, get by name/not-found, run search step, version in execution, log written, context accumulation, inputs/defaults, approval approve/reject/stops-steps, step failure, learn output in context, log step ids, version in log, per-call handler override, bad ask/search steps
  - `TestMondayWorkflow` (11 tests) — list, show, show not found, run, not found, unknown action, full implement-function end-to-end
  - `TestCLIWorkflow` (8 tests) — list, list empty, show, show not found, run --yes, run --var, bad --var format, --help
- **Total: 395 passed, 12 skipped, 0 failures**

---

## [0.6.0] — 2026-06-27 — Sprint 1.5: Monday CLI

### Added
- `monday/cli.py` — `monday` command-line interface; zero business logic; all commands invoke `Monday()` through the public API
  - `monday status` — system health, module status, version, session ID, uptime
  - `monday ask "<prompt>"` — delegates to `Monday.ask()`; prints answer, confidence, engine, sources, supporting entries, related decisions, related tasks, suggested next actions
  - `monday search "<query>" [--limit N]` — delegates to `Monday.search()`; prints ranked results with type and tags
  - `monday learn [--title] [--type] [--tags] [--components] [--content]` — delegates to `Monday.learn()`; supports non-interactive (all flags), stdin pipe, and interactive guided prompts
  - `monday task list [--status] [--priority] [--type]` — delegates to `Monday.task("list")`
  - `monday task create --title --objective [--type] [--priority] [--created-by]` — delegates to `Monday.task("create")`
  - `monday task get TASK-ID` — delegates to `Monday.task("get")`
  - `monday task complete TASK-ID [--reason] [--changed-by]` — delegates to `Monday.task("complete")`
- `docs/CLI.md` — full CLI specification: installation, design principles, all commands, flags, output format, error handling, automation patterns
- `[project.scripts]` in `pyproject.toml` — registers `monday` as a pip-installable entry point (`monday = "monday.cli:main"`)

### Changed
- `pyproject.toml` — added `[project.scripts]`; added `monday*` to `packages.find` include list and coverage source list; fixed build backend from `setuptools.backends.legacy:build` to `setuptools.build_meta`

### Tests
- `tests/test_cli.py` — 52 integration tests across 7 test classes: `TestHelp`, `TestStatusCommand`, `TestAskCommand`, `TestSearchCommand`, `TestLearnCommand`, `TestTaskCommand`, `TestEndToEnd`; all tests invoke `monday.cli.main()` directly with explicit argv; no subprocess required; uses `capsys` and `tmp_path` throughout
- **Total: 321 passed, 12 skipped, 0 failures**

---

## [0.5.0] — 2026-06-27 — Sprint 1.4: Engineering Intelligence

### Added
- `brain/reasoner.py` — `ReasoningEngine`: answers engineering questions using only stored MondayOS knowledge; no external model calls
  - `QuestionIntent` enum — 9 question intent categories (GENERAL, HISTORICAL, TYPE_BUG, TYPE_DECISION, TYPE_TASK, BLOCKED_TASKS, RECENT_CHANGES, SUMMARY, ONBOARDING)
  - `ReasoningResult` dataclass — structured output: answer, sources, model_used, confidence, supporting_entries, related_tasks, related_decisions, suggested_next_actions
  - `_classify_intent()` — keyword pattern matching to route prompts into QuestionIntent
  - `_extract_terms()` — stop-word stripping and punctuation removal for clean search terms
  - `_search_knowledge()` — multi-word + per-term supplement strategy; type filtering per intent; RECENT_CHANGES uses `list_all()` sorted by `updated_at`
  - `_search_tasks()` — BLOCKED_TASKS uses `list_active(status=BLOCKED)`; TYPE_TASK/GENERAL match terms against title and objective
  - `_traverse_relationships()` — depth-1 BFS over entry relationship graph via `KnowledgeStore.get()`
  - `_synthesize()` — intent-specific answer templates
  - `_suggest_actions()` — up to 5 immediately-executable follow-up calls
  - `_calculate_confidence()` — additive signal model (quantity, summary quality, type alignment, relationship richness); hard cap at 0.95
- `docs/REASONING_ENGINE.md` — full specification: question processing pipeline, ranking strategy, relationship traversal, confidence model, future LLM/graph/vector integration points

### Changed
- `AskResponse` — extended with 4 new fields (all default to empty): `supporting_entries`, `related_tasks`, `related_decisions`, `suggested_next_actions`
- `Monday.ask()` — fully implemented: delegates to `ReasoningEngine`, populates all `AskResponse` fields; `model_used` is now `"monday-reasoning/1.0"`
- `Monday.__init__` — composes `ReasoningEngine(knowledge_store, task_manager)` as `__reasoner` (name-mangled, not accessible as public attribute)
- `brain/__init__.py` — exports `ReasoningEngine`, `ReasoningResult`, `QuestionIntent`

### Tests
- `tests/test_monday.py` — `TestAsk` converted to `tmp_path` autouse fixture; 3 previously-skipped tests implemented; 16 new behavioral tests added (empty knowledge, model_used, historical query, summary query, bug filter, decision filter, blocked tasks, confidence growth, sources populated, supporting entries, suggested actions); 1 new encapsulation test for `__reasoner`
- **Total: 269 passed, 12 skipped, 0 failures**

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
