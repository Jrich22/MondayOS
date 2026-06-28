# MondayOS — Session Log

Chronological record of engineering sessions. Each entry captures what was built, decisions made, and what comes next. Maintained by the Lead Software Engineer (human or AI).

---

## 2026-06-27 — Foundation + Sprint 1 + Sprint 1.1

### Session Summary

Three successive engineering sessions in a single day, establishing MondayOS from a blank slate through a complete public API surface.

---

### Session 1: Foundation Documentation

**Goal:** Establish the engineering foundation before writing any Python.

**Completed:**
- Wrote all 9 foundation documents in `docs/`:
  - `PROJECT_OVERVIEW.md`, `VISION.md`, `ARCHITECTURE.md`
  - `ENGINEERING_STANDARDS.md`, `DOCUMENTATION_STANDARDS.md`
  - `KNOWLEDGE_SYSTEM.md`, `MEMORY_SYSTEM.md`, `TASK_SYSTEM.md`
  - `ROADMAP.md`
- Wrote `DECISIONS.md` with ADR-001 through ADR-006 capturing all foundational choices
- Initialized `CHANGELOG.md`

**Key decisions:**
- Python 3.11+, Ruff, Mypy strict
- File-based storage for Phase 1 (no database)
- Markdown + YAML frontmatter as universal data format
- Git as source of truth for all persistent state
- Human approval gates are non-bypassable at the code level

**State at end:** 9 foundation documents, 0 Python files.

---

### Session 2: Sprint 1 — Package Architecture

**Goal:** Build the architectural skeleton for all six core modules. No business logic yet.

**Completed:**
- Created 6 Python packages: `brain`, `events`, `knowledge`, `memory`, `search`, `tasks`
- Created `core/types.py` with shared primitive types
- **Fully implemented:** `EventBus` (subscribe/publish/history), `SessionMemory` (read/write/expire/clear/snapshot)
- **Fully implemented:** `Task.can_transition_to()` using an explicit transition graph
- Wrote placeholder implementations for all other classes with specific `TODO` comments
- Created 111 unit tests (82 passing, 29 skipped placeholders)
- Added `pyproject.toml`, `conftest.py`, `.venv`
- Wrote `README.md` for each module

**Key decisions:**
- `MemoryStore` as a `Protocol` (structural typing, not ABC) — any class with the right methods satisfies it
- `EventBus` history is recorded before dispatch — event is always in log even if handler raises
- `Task` transition graph encoded as a module-level dict, validated via `can_transition_to()`
- Test placeholders use `pytest.skip("TODO: ...")` — they define the contract, not just defer it

**State at end:** 39 Python files, 111 tests (82 pass).

---

### Session 3: Sprint 1.1 — Public API

**Goal:** Create a stable public interface so external consumers never need to import internal modules.

**Completed:**
- Created `monday/` package with `Monday` class, `MondayConfig`, and six typed response types
- **Fully implemented:** `Monday.status()` — returns live system health, version, session ID, uptime, and per-module status
- **Typed placeholders:** `Monday.ask()`, `Monday.learn()`, `Monday.search()`, `Monday.task()`
- All internal subsystems composed inside `Monday.__init__()` using Python name mangling (`__brain`, etc.) to prevent accidental public access
- Created `tests/test_monday.py` with 80 tests (71 passing, 9 skipped placeholders)
- Added ADR-007 (Monday as public API boundary) and ADR-008 (typed response dataclasses)
- Updated `CHANGELOG.md` with versions 0.1.0, 0.1.1, 0.2.0

**Key decisions:**
- Internal subsystems accessed via `self.__name` (name-mangled) — `hasattr(monday, '_brain')` returns False
- `status()` is the one method implemented now because it reads only from instance state (no I/O required)
- Response types use dataclass defaults so stubs are always constructable
- The rule "external code imports from `monday`, never from internal modules" is documented in `monday/README.md` and enforced by test

**State at end:** 46 Python files, 191 tests (153 pass, 38 skipped).

---

---

## 2026-06-27 — Git Checkpoint

### Checkpoint: "Foundation: establish MondayOS public API and core architecture"

**Test results at commit:**
- **153 passed**
- **38 skipped** (documented placeholders for Sprint 1.2)
- **0 failures**

**What is in this commit:**
- Foundation documentation (9 docs, 6 ADRs)
- Sprint 1 package architecture (6 modules, `core/`, `conftest.py`, `pyproject.toml`)
- Sprint 1.1 Monday public API (`monday/` package, 5 public methods, 6 typed response types)
- `Monday.status()` implemented and tested
- `EventBus` and `SessionMemory` fully implemented and tested
- `Task.can_transition_to()` transition graph implemented and tested
- All remaining methods are typed placeholders with specific `TODO` comments
- `.gitignore` excluding `.venv/`, `__pycache__/`, `.pytest_cache/`, secrets

**Sprint 1.2 work (next session):**
1. `KnowledgeParser` — parse Markdown + YAML frontmatter into `KnowledgeEntry`
2. `KnowledgeLoader` — walk `knowledge/` directory and load all entries
3. `KnowledgeIndex.build()` — populate in-memory index from entries
4. `KnowledgeStore.add()` / `get()` / `search()` — wire knowledge persistence
5. `Monday.learn()` — delegate to `KnowledgeStore.add()`
6. `Monday.search()` — delegate to `SearchEngine.search()` (basic keyword)

**Open questions for next session:**
- Should `Monday.ask()` in Phase 1 route to a real model call (Claude) or remain a stub until the integration layer is built?
- Integration layer (`integrations/claude/`, `integrations/openai/`, `integrations/ollama/`) — is this Sprint 1.2 or Sprint 1.3?

**Known technical debt (tracked, not urgent):**
- `TestEncapsulation` relies on Python name mangling (`self.__brain` → `_Monday__brain`). Document as a knowledge entry.
- `Brain.__init__()` silently does nothing (subsystem wiring is commented out). Will fail loudly when `execute_task()` is first called — acceptable for now.

---

## 2026-06-27 — Sprint 1.2 Pre-work: MKS

### Session Summary

Before implementing any Sprint 1.2 code, wrote the formal MondayOS Knowledge Specification (MKS) as a product specification — not documentation.

### Completed

- Wrote `docs/MKS.md` — MKS v1.0 (1,000+ lines)
  - Canonical Knowledge Object (CKO) with all fields, type rationale, and constraints
  - 12 knowledge types: Bug, Decision, Task, Sprint, Feature, Lesson, Pattern, Runbook, Documentation, Research, Weather, Experiment
  - Each type: Purpose, mandatory `type_fields`, optional `type_fields`, canonical relationships, required body structure, validation rules, example
  - ID specification: `{PREFIX}-{NNNN}` format, type prefix registry, generation rules, validation rules
  - Relationship specification: 13 forward/inverse relation types, full directed graph model, relationship validation rules
  - Lifecycle specification: 5 states (DRAFT/ACTIVE/DEPRECATED/SUPERSEDED/ARCHIVED), transition table, terminal state rules
  - Versioning specification: mutation definition, increment protocol, audit trail approach, supersession vs. versioning distinction
  - Validation specification: 4 levels (schema, constraint, semantic, referential), 20 validation rules (VAL-001–VAL-020), draft exception
  - Storage backend specification: Markdown (Phase 1), SQLite (Phase 2), PostgreSQL (Phase 3), Neo4j (graph), Vector DB (semantic retrieval), `StorageBackend` protocol, migration protocol
  - Conformance requirements: 9 criteria for MKS conformance
- Added ADR-009 to `docs/DECISIONS.md` — decision to establish MKS before implementation
- Updated `docs/CHANGELOG.md` with v0.2.1

### Key Design Decisions

- **Relationships are first-class from day one.** Every CKO has a `relationships` array. This makes the Neo4j migration a straight import — the graph is already encoded in the data, not something that must be retrofitted.
- **`metadata: {}` as forward compatibility.** New field candidates incubate in metadata before promotion; this prevents premature schema lock-in.
- **Draft status exempt from Level 3/4 validation.** Allows incremental entry authoring across multiple sessions; full validation runs on ACTIVE promotion.
- **`StorageBackend` protocol, not inheritance.** The `KnowledgeStore` accepts any object with the right seven methods; no base class required. Matches the `MemoryStore` Protocol pattern established in Sprint 1.
- **SUPERSEDED is a permanent terminal state.** Once an entry is superseded, it is read-only. The succession chain is traversable; history is never destroyed.
- **Vector DB is a retrieval layer, not the source of truth.** Vectors are derived from the canonical store; the vector index is invalidated and regenerated on migration.

### State at End

- `docs/MKS.md` written (MKS v1.0)
- `docs/DECISIONS.md` updated (ADR-009 added)
- `docs/CHANGELOG.md` updated (v0.2.1)
- 0 new Python files written (MKS is a pre-implementation specification)
- Test count unchanged: 153 passed, 38 skipped, 0 failures

### Sprint 1.2 Implementation (Next)

With MKS complete, the implementation order is:

1. **`knowledge/entry.py`** — Expand `EntryType` enum from 4 to 12 types; add `Relationship` dataclass and `RelationType` enum; add `LifecycleStatus` enum; align `KnowledgeEntry` fields with CKO base schema
2. **`knowledge/parser.py`** — `KnowledgeParser.parse(path)` → reads Markdown + YAML frontmatter; validates against MKS rules; returns `KnowledgeEntry`; `KnowledgeParser.serialize(entry, path)` → inverse
3. **`knowledge/loader.py`** — `KnowledgeLoader.load_all(knowledge_dir)` → walks the 12 subdirectories; calls parser on each file; returns `list[KnowledgeEntry]`
4. **`knowledge/index.py`** — `KnowledgeIndex.build(entries)` → populates in-memory maps (by_id, by_type, by_tag, by_component, by_status); `lookup(id)`, `by_type(type)`, `by_tag(tag)`, `by_component(name)`
5. **`knowledge/store.py`** — `KnowledgeStore.add(entry)` → validates + writes via backend; `get(id)` → lookup; `search(query)` → keyword match against title/summary/tags; `supersede(old_id, new_entry)` → transition protocol
6. **`monday/api.py`** — Wire `Monday.learn()` → `KnowledgeStore.add()`; wire `Monday.search()` → `SearchEngine.search()` (or direct `KnowledgeStore.search()` for Phase 1)

**Acceptance criteria for Sprint 1.2:**
- All 38 skipped knowledge tests pass
- `Monday.learn(content="...", title="...", entry_type="bug", ...)` writes a conforming `BUG` entry to `knowledge/bugs/BUG-0001.md`
- `Monday.search("rate limit")` returns that entry
- A round-trip (learn → read file → parse → compare) produces an identical `KnowledgeEntry`

---

## 2026-06-27 — Sprint 1.2: Knowledge Capture

### Session Summary

Implemented the first production-ready capability: `Monday.learn()` and `Monday.search()` end-to-end. Knowledge is now persisted to disk, indexed in memory, and searchable immediately after being learned.

### Completed

**`knowledge/errors.py`** — New: typed exception hierarchy (`KnowledgeError`, `KnowledgeParseError`, `KnowledgeNotFoundError`, `KnowledgeConflictError`, `KnowledgeValidationError`)

**`knowledge/entry.py`** — Expanded to full MKS 1.0 CKO schema:
- `KnowledgeType` enum: 12 types
- `LifecycleStatus` enum: 5 states
- `RelationType` enum: 13 typed directions
- `Relationship` dataclass: typed, directional link
- `KnowledgeEntry` fields: added `version`, `summary`, `created_by`, `updated_at`, `updated_by`, `relationships`, `type_fields`; renamed `created` → `created_at`; `confidence` changed from `str` to `float`
- `EntryType`, `EntryStatus` preserved as backward-compat aliases

**`knowledge/parser.py`** — Implemented fully:
- `parse(raw, source_path)` — splits `---...---\n` frontmatter, yaml.safe_load, validates required fields, builds typed `KnowledgeEntry`
- `serialize(entry)` — builds YAML frontmatter dict, yaml.dump, round-trips cleanly
- Extra frontmatter fields forwarded to `metadata` for forward compatibility

**`knowledge/loader.py`** — Implemented fully:
- `load_all()` — rglob for `*.md`, skips README/index/non-frontmatter files silently, delegates to parser
- `load_file(path)` — reads and parses a single file

**`knowledge/index.py`** — Implemented fully:
- `build(entries)` — rebuilds all dicts; `_by_id` holds ALL entries; secondary indexes hold only ACTIVE
- `add(entry)` — incremental update; removes existing entry from secondary indexes before re-indexing
- `lookup()`, `by_type()`, `by_tag()` (case-insensitive), `by_component()`, `all_active()`

**`knowledge/store.py`** — Implemented fully (Markdown backend):
- `__init__(project_root)` — creates loader/parser/index; calls `_boot()` to load existing entries
- `add(entry)` — assigns ID via per-type sequence counter; writes `{type_dir}/{ID}.md`; updates index
- `get(entry_id)` — O(1) index lookup; raises `KnowledgeNotFoundError` if absent
- `search(query, ...)` — keyword scoring: title +3, tag +2, summary +1, body +0.5; sorted descending
- `supersede(old_id, new_entry)` — persists new entry; marks old as SUPERSEDED; updates index
- `list_all(entry_type)` — returns all ACTIVE entries, optionally filtered
- Sequence tracking in `knowledge/.sequences.json` — survives restarts

**`monday/api.py`** — Wired:
- `Monday.__init__` — now passes `project_root` to `KnowledgeStore`
- `Monday.learn()` — validates type, builds CKO, calls `KnowledgeStore.add()`, publishes `KNOWLEDGE_ENTRY_CREATED` event, returns `LearnResponse`
- `Monday.search()` — calls `KnowledgeStore.search()`, converts entries to result dicts, returns `SearchResponse`

**Test results:**
- `tests/test_knowledge.py` — **64 tests, 0 skipped, 0 failures**
- `tests/test_monday.py` — **89 tests, 5 skipped, 0 failures**
- **Total: 217 passed, 22 skipped, 0 failures**

### Architectural Decisions Made

**`Monday.search()` sources_queried contract preserved:** The existing test `test_no_sources_gives_empty_sources_queried` expects `sources_queried == []` when no `sources` arg is given. This was preserved — `sources_queried` echoes whatever the caller passed (or `[]`). It does NOT report what was actually searched. This is intentional: the contract stabilises now; the actual search routing is Phase 2.

**`KnowledgeStore` not passed the EventBus:** `Monday.learn()` publishes the event after `KnowledgeStore.add()` returns. This keeps `KnowledgeStore` ignorant of the event system — it only knows how to persist and retrieve. `Monday` is the orchestrator responsible for cross-cutting concerns (events, logging, session correlation).

**Incremental index update after write:** `KnowledgeIndex.add()` removes the old version from secondary indexes before re-indexing. This means `supersede()` correctly removes the old entry from search results without a full rebuild.

**`EntryType` / `EntryStatus` preserved as aliases:** All existing tests written against Sprint 1 schema continue to pass. `EntryType = KnowledgeType` and `EntryStatus = LifecycleStatus` are defined in both `entry.py` and `__init__.py`.

**Loader skips non-frontmatter `.md` files silently:** The `knowledge/README.md` is skipped without a warning. Only files that begin with `---` (a YAML frontmatter delimiter) are attempted. Files that begin with `---` but have malformed YAML emit a warning and are skipped.

### State at End

- 217 tests pass, 22 skipped (all skipped tests are appropriate future-sprint placeholders)
- `Monday.learn()` is production-ready for the 12 MKS knowledge types
- `Monday.search()` provides keyword search across the knowledge base
- `Monday.ask()` and `Monday.task()` remain typed stubs

### Sprint 1.3 Candidates

1. `SearchEngine` — unify knowledge + task search under one interface
2. `Monday.learn()` — type_fields population per MKS schema (currently stored in body only)
3. `Monday.learn()` — MKS validation: VAL-001 through VAL-020
4. `KnowledgeIndex.write_markdown_index()` — generate `knowledge/index.md` after every write
5. `Monday.task()` — wire `TaskManager.create()`, `list_active()`, `get()`

---

## 2026-06-27 — Sprint 1.5: Monday CLI

### Session Summary

Implemented the `monday` command-line interface — the canonical interface for humans, Claude Code, and future automation. The CLI contains zero business logic: every command invokes `Monday()` through the public API. All five public methods are now reachable from the terminal.

### Completed

**`monday/cli.py`** — New: full CLI via `argparse`
- `main(argv=None) -> int` — accepts optional argv list for testability; returns exit code without calling `sys.exit()` directly
- `monday status` — prints version, session, uptime, health, per-module ok/FAIL
- `monday ask "<prompt>"` — prints answer block, confidence, engine, sources, supporting entries, related decisions, related tasks, suggested next actions
- `monday search "<query>" [--limit N]` — prints ranked results with ID, title, type, tags, and summary excerpt
- `monday learn [--title] [--type] [--tags] [--components] [--content]` — three input modes: all-flags (non-interactive), stdin pipe (detected via `sys.stdin.isatty()`), interactive guided prompts
- `monday task list [--status] [--priority] [--type]` — tabular active task list with ID, priority, status, title
- `monday task create --title --objective [opts]` — creates task, prints ID and key fields
- `monday task get TASK-ID` — prints full task detail block
- `monday task complete TASK-ID [--reason] [--changed-by]` — completes task; rejects invalid transitions with error

**`pyproject.toml`** — Updated:
- Added `[project.scripts]` → `monday = "monday.cli:main"`
- Added `monday*` to `packages.find` include list and coverage source
- Fixed build backend from `setuptools.backends.legacy:build` → `setuptools.build_meta` (required for current Python/setuptools versions)

**`docs/CLI.md`** — New: full specification covering installation, design principles, all commands with flags/examples/output, error handling, automation patterns

**Test results:**
- `tests/test_cli.py` — **52 tests, 0 skipped, 0 failures**
- **Total: 321 passed, 12 skipped, 0 failures**

### Architectural Decisions Made

**Zero business logic in CLI:** The CLI does not import `KnowledgeStore`, `TaskManager`, `ReasoningEngine`, or any other internal class. It imports only `Monday` and `MondayConfig`. This enforces the public API boundary and means the CLI can evolve independently of internal implementations.

**`main(argv=None) -> int` signature:** Standard argparse pattern — accepts optional argv list so tests can call `main([...])` without touching `sys.argv`. Returns int exit code instead of calling `sys.exit()` directly; the setuptools entry-point wrapper calls `sys.exit(main())`.

**`--project-root` as a global flag:** Placed on the top-level parser (before the subcommand) so a single `--project-root` configures all commands. The alternative (per-subcommand flag) would be verbose. In all tests, `--project-root str(tmp_path)` provides isolation.

**Learn command: three input modes:** Non-interactive when all flags are provided; stdin pipe when `not sys.stdin.isatty()`; interactive prompts otherwise. Tests use the flag-based mode exclusively (stdin.isatty() is False in pytest; interactive mode requires real terminal).

**Integration tests via `main([argv])` not subprocess:** Tests import `monday.cli.main` and call it directly. This is faster than subprocess, gives access to `capsys` and `monkeypatch`, and doesn't require the package to be installed (works with the editable install). The test file imports nothing from internal modules — all assertions are on CLI text output.

### State at End

- 321 tests pass, 12 skipped, 0 failures
- `monday` binary installed and verified working
- All five public API methods accessible from the terminal
- Pipe-safe, automation-friendly output

### Sprint 1.6 Candidates

1. `monday learn --file PATH` — read content from a file rather than stdin or flag
2. `monday task update TASK-ID --status in-progress` — status transitions via CLI
3. `monday ask` — LLM integration (Claude API) as step 7 of reasoning pipeline
4. `monday search --type bug --tags homebrew` — filtered search via CLI flags
5. `monday status --json` — machine-readable JSON output for automation

---

## 2026-06-27 — Sprint 1.4: Engineering Intelligence

### Session Summary

Implemented `Monday.ask()` as an internal reasoning engine over the existing knowledge base and task system. No external model calls. MondayOS can now answer engineering questions using its own accumulated knowledge.

### Completed

**`brain/reasoner.py`** — New: `ReasoningEngine` class with full pipeline:
- `QuestionIntent` enum — 9 categories: GENERAL, HISTORICAL, TYPE_BUG, TYPE_DECISION, TYPE_TASK, BLOCKED_TASKS, RECENT_CHANGES, SUMMARY, ONBOARDING
- `ReasoningResult` dataclass — structured output with all `AskResponse` fields
- `_classify_intent()` — keyword pattern matching; longer patterns checked first to prevent false positives
- `_extract_terms()` — stop-word removal + punctuation stripping; yields clean search tokens
- `_search_knowledge()` — multi-word search first, per-term supplement if < 3 results; type filter for BUG/DECISION intents; RECENT_CHANGES uses `list_all()` sorted by `updated_at`
- `_search_tasks()` — BLOCKED_TASKS → `list_active(status=BLOCKED)`; TYPE_TASK/GENERAL → term match against title and objective
- `_traverse_relationships()` — depth-1 BFS; silently skips missing targets
- `_synthesize()` — 9 intent-specific answer templates producing deterministic plain-text output
- `_suggest_actions()` — up to 5 immediately-executable `monday.*` calls; no results → `learn()` suggestion
- `_calculate_confidence()` — base (0.10 × results, cap 0.70) + summary bonus (0.04 × top-3 with summary) + type alignment (0.05 × intent-matched entries) + relationship richness (0.02 × relationships); hard cap 0.95

**`brain/__init__.py`** — now exports `ReasoningEngine`, `ReasoningResult`, `QuestionIntent`

**`monday/types.py`** — `AskResponse` extended with 4 new fields (all default empty): `supporting_entries`, `related_tasks`, `related_decisions`, `suggested_next_actions`

**`monday/api.py`** — `Monday.ask()` fully implemented:
- Composes `__reasoner = ReasoningEngine(knowledge_store, task_manager)` in `__init__`
- Delegates to `__reasoner.answer(prompt, context)` and maps `ReasoningResult` → `AskResponse`
- `model_used` is now `"monday-reasoning/1.0"`

**`docs/REASONING_ENGINE.md`** — New: full specification covering question processing pipeline, term extraction, search strategy per intent, relationship traversal, ranking, confidence calculation, answer synthesis templates, suggested actions, storage independence, and future LLM/graph/vector integration points

**Test results:**
- `tests/test_monday.py` — **110 tests, 0 skipped, 0 failures** (was 94 tests, 3 skipped)
- **Total: 269 passed, 12 skipped, 0 failures**

### Architectural Decisions Made

**No LLM calls — templates only:** Answer synthesis uses intent-specific string templates. This produces deterministic, testable output. The pipeline is designed so an LLM drops in at the synthesis step only, with steps 1–6 (classification through ranking) unchanged. `model_used = "monday-reasoning/1.0"` is the signal callers can check to know no model was consulted.

**ReasoningEngine is storage-agnostic:** It depends only on `KnowledgeStore.search()`, `.get()`, `.list_all()` and `TaskManager.list_active()`. It does not import `KnowledgeParser`, `KnowledgeLoader`, `KnowledgeIndex`, or `TaskParser`. Swapping to SQLite or Neo4j requires no changes to the reasoning layer.

**Decisions partitioned from supporting entries:** `related_decisions` is a separate field from `supporting_entries`. This lets callers render the ADR log and general knowledge differently in a UI — they are the same type (`KnowledgeEntry`) but logically distinct in most question contexts.

**Hard confidence cap at 0.95:** Without LLM validation of the synthesized answer against source material, claiming certainty would be misleading. The 0.05 gap is reserved for the LLM grounding sprint.

**`__reasoner` is name-mangled:** Follows the established pattern (`__brain`, `__bus`, `__knowledge`, etc.). Not accessible as `monday.reasoner` or `monday._reasoner`. Test added to `TestEncapsulation`.

**`TestAsk` upgraded to `tmp_path`:** Moved from `setup_method` + `Monday()` (non-deterministic if `./knowledge/` exists) to `autouse` fixture with isolated `tmp_path`. Every behavioral test now seeds its own knowledge before asserting.

### State at End

- 269 tests pass, 12 skipped (all skipped are appropriate future-sprint placeholders)
- `Monday.ask()` is production-ready for internal knowledge reasoning
- `Monday.learn()`, `Monday.search()`, `Monday.task()` remain production-ready
- `Monday.status()` remains production-ready

### Sprint 1.5 Candidates

1. `Monday.ask()` — wire LLM call (Claude via API) as step 7 replacement; `model_used` becomes model ID
2. `SearchEngine` — unified search across knowledge + tasks behind one interface
3. `Monday.search()` — add `entry_type`, `tags`, `components` filter kwargs
4. `Monday.learn()` — type_fields population per MKS schema (currently only body stored)
5. `Monday.learn()` — MKS validation rules VAL-001 through VAL-020

---

## 2026-06-27 — Sprint 1.3: Task Capture

### Session Summary

Implemented the second production-ready capability: `Monday.task()` for create, get, list, and complete. Tasks now persist to disk with a full audit trail (status_history), survive restarts via sequence tracking, and move to `tasks/completed/` on terminal status.

### Completed

**`tasks/errors.py`** — New: typed exception hierarchy (`TaskError`, `TaskNotFoundError`, `TaskValidationError`, `InvalidTransitionError`, `TaskParseError`)

**`tasks/parser.py`** — Implemented fully:
- `parse(raw, source_path)` — parses YAML frontmatter into `Task`; validates required fields; deserializes `status_history` list including `from_status: null` for initial creation
- `serialize(task)` — builds deterministic YAML frontmatter (alphabetical key sort); round-trips cleanly

**`tasks/manager.py`** — Implemented fully (Markdown-on-disk backend):
- `__init__(project_root)` — sets up `tasks/active/` and `tasks/completed/` dirs; loads sequence counter from `tasks/.sequences.json`
- `create(...)` — validates title/objective; assigns `TASK-NNNN` ID; builds `StatusTransition` with `from_status=None`; writes to `tasks/active/`
- `get(task_id)` — searches `active/` then `completed/`; raises `TaskNotFoundError` if not found
- `update_status(task_id, new_status, changed_by, reason)` — validates via `task.can_transition_to()`; raises `InvalidTransitionError` on illegal transition; appends transition to `status_history`; moves file to `tasks/completed/` if terminal
- `list_active(...)` — scans `tasks/active/*.md`, parses each, applies optional filters; skips README/index files silently
- `assign()`, `block()`, `append_work_log()`, `archive()` — convenience methods implemented

**`tasks/__init__.py`** — Exports expanded to include all error types and `TaskParser`

**`monday/api.py`** — Wired:
- `Monday.__init__` — `TaskManager` now receives `project_root`
- `Monday.task(action='create')` — validates, creates via `TaskManager`, publishes `TASK_CREATED`, returns `TaskResponse`
- `Monday.task(action='get')` — retrieves by `task_id`, returns full task dict in `data`
- `Monday.task(action='list'/'list_active')` — lists active tasks, echoes original action string in response
- `Monday.task(action='complete')` — transitions to COMPLETED, publishes `TASK_COMPLETED`, returns `TaskResponse`
- Unknown actions return `success=False` with descriptive message

**Test results:**
- `tests/test_tasks.py` — **46 tests, 0 skipped, 0 failures** (removed 2 NotImplementedError stubs; added TestTaskParser + full TestTaskManager)
- `tests/test_monday.py` — **97 tests, 3 skipped, 0 failures** (TestTask upgraded to tmp_path autouse fixture; 7 new end-to-end tests)
- **Total: 250 passed, 15 skipped, 0 failures**

### Architectural Decisions Made

**`TaskManager` is unaware of EventBus:** Same pattern as `KnowledgeStore` — `Monday` orchestrates all cross-cutting concerns. `TaskManager` creates/reads/updates/archives; `Monday.task()` publishes events after the store operation succeeds.

**`tasks/active/` and `tasks/completed/` are separate directories:** Terminal tasks (COMPLETED, CANCELLED) move from `active/` to `completed/` so `list_active()` only needs to scan one directory. `get()` checks both locations to support full retrieval.

**Action echo in `_task_list`:** When caller invokes `task("list")`, the response returns `action="list"` (not `"list_active"`), preserving the caller's original action string in the response for API stability.

**`from_status: null` in initial StatusTransition:** The first `StatusTransition` in `status_history` always has `from_status=None` — this records the creation event without implying a prior state. The YAML roundtrip preserves `null` cleanly.

### State at End

- 250 tests pass, 15 skipped (all skipped are appropriate future-sprint placeholders)
- `Monday.task()` is production-ready for create, get, list, and complete
- `Monday.learn()` and `Monday.search()` remain production-ready from Sprint 1.2
- `Monday.ask()` remains a typed stub

### Sprint 1.4 Candidates

1. `Monday.task(action='update')` — partial field updates (title, priority, context, acceptance_criteria)
2. `Monday.task(action='assign')` — assign a task to an agent or human
3. `SearchEngine` — unified search across knowledge + tasks
4. `Monday.learn()` — type_fields population per MKS schema (currently only body stored)
5. `Monday.learn()` — MKS validation rules VAL-001 through VAL-020
6. `Monday.ask()` — integrate Brain + model call (requires integration layer)
7. `KnowledgeStore.search()` — filter by type, tags, components (already supported; wire through `Monday.search()`)

---

## 2026-06-27 — Git Checkpoint

### Checkpoint: "Sprint 1.2: implement knowledge capture and search"

**Commit:** `67f1ef7`

**Test results at commit:**
- **217 passed**
- **22 skipped** (documented placeholders for Sprint 1.3+)
- **0 failures**

**What is in this commit:**
- `docs/MKS.md` — MondayOS Knowledge Specification v1.0 (formal product specification)
- `docs/DECISIONS.md` — ADR-009: MKS as canonical contract before implementation
- `knowledge/errors.py` — typed exception hierarchy
- `knowledge/entry.py` — expanded CKO schema: 12 types, 5 lifecycle states, 13 relationship types, `Relationship` dataclass
- `knowledge/parser.py` — YAML frontmatter parser; round-trips cleanly; forward-compat metadata passthrough
- `knowledge/loader.py` — directory walker; silent skip on non-frontmatter files
- `knowledge/index.py` — in-memory index with incremental `add()` and full `build()`
- `knowledge/store.py` — Markdown backend: `add()`, `get()`, `search()`, `supersede()`, `list_all()`; sequence tracking in `.sequences.json`
- `monday/api.py` — `Monday.learn()` and `Monday.search()` fully wired end-to-end
- `tests/test_knowledge.py` — 64 tests, 0 skipped (complete coverage of entry model, parser, index, store)
- `tests/test_monday.py` — 89 tests, 5 skipped (`ask` and `task` stubs deferred to future sprints)

**What works:**
- `Monday.learn()` — persists to `knowledge/{type}/{ID}.md`, publishes `KNOWLEDGE_ENTRY_CREATED` event, returns `LearnResponse` with assigned ID
- `Monday.search()` — keyword search with title/tag/summary/body scoring, returns ranked results immediately after learn
- `KnowledgeStore` boots from existing files on init — state survives restarts
- `KnowledgeParser` round-trips serialize→parse cleanly
- `TestEncapsulation` still passes — all 6 internal subsystems remain hidden

**No technical debt introduced:**
- All tests use isolated `tmp_path`; no test artifacts written to project directory
- Backward-compat aliases `EntryType = KnowledgeType` and `EntryStatus = LifecycleStatus` preserve Sprint 1 contracts
- `KnowledgeStore` decoupled from `EventBus` — `Monday` orchestrates cross-cutting concerns

**Sprint 1.3 recommended scope:**
1. `Monday.task()` — wire `TaskManager.create()`, `list_active()`, `get()`; publish task lifecycle events
2. `Monday.search()` — expose type/tag/component filters via public API
3. MKS validation layer — enforce VAL-001 through VAL-020 on every write
4. `KnowledgeIndex.write_markdown_index()` — auto-generate `knowledge/index.md` after every write
