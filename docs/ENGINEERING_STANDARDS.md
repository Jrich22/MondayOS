# MondayOS — Engineering Standards

**Version:** 0.1.0  
**Status:** Active  
**Last Updated:** 2026-06-27

---

## Purpose

This document defines the engineering standards that govern all code written for MondayOS — by humans and by AI agents alike. Standards exist not to create bureaucracy but to protect the team from compounding technical debt, production incidents, and lost institutional knowledge.

Every standard in this document includes the reasoning behind it. When the reasoning no longer applies, the standard should be updated.

---

## Language and Runtime

### Primary Language: Python 3.11+

**Why Python:**
- The AI/ML ecosystem is Python-native. All major model SDKs (Anthropic, OpenAI, Ollama) have first-class Python clients.
- The team's existing expertise is primarily Python.
- Hiring and community resources are plentiful.
- Tooling (type checkers, linters, test frameworks) is mature.

**Minimum Python Version:** 3.11  
**Why 3.11:** Structural pattern matching (3.10+), `tomllib` in stdlib (3.11+), significant performance improvements over 3.9/3.10, and active security support through 2027.

### Type Annotations

All function signatures and class attributes must include type annotations. Use `from __future__ import annotations` in every module.

**Why:** Type annotations are the cheapest form of documentation. They make AI-generated code easier to review and catch an entire class of bugs at static analysis time rather than runtime.

```python
# Correct
def route_task(task: Task, context: ExecutionContext) -> AgentAssignment:
    ...

# Not acceptable
def route_task(task, context):
    ...
```

---

## Code Style

### Formatter: Ruff

All code is formatted with Ruff before commit. Configuration lives in `pyproject.toml`. CI enforces format compliance — unformatted code does not merge.

**Why Ruff over Black + isort + Flake8:** Single tool, significantly faster, and covers formatting + linting in one pass. Reduces toolchain complexity.

### Linter: Ruff

All code passes Ruff linting with the rule set defined in `pyproject.toml`. The rule set includes:
- `E`, `F` — pycodestyle and pyflakes (baseline correctness)
- `I` — isort (import ordering)
- `B` — flake8-bugbear (common footguns)
- `UP` — pyupgrade (modernize syntax)
- `N` — pep8-naming (naming conventions)

Disabling a lint rule inline requires a comment explaining why:
```python
result = some_function()  # noqa: B006 — intentionally mutable default; reset in __init__
```

### Type Checker: Mypy

All code passes `mypy --strict` (or project-configured equivalent). Type errors must be resolved, not suppressed. Suppressions require a comment:
```python
value = external_lib.get_value()  # type: ignore[assignment] — external lib returns Any; narrowed below
```

---

## Naming Conventions

| Construct | Convention | Example |
|---|---|---|
| Module | `snake_case` | `task_router.py` |
| Package | `snake_case` | `integrations/claude/` |
| Class | `PascalCase` | `TaskRouter` |
| Function / Method | `snake_case` | `route_task()` |
| Variable | `snake_case` | `agent_assignment` |
| Constant | `UPPER_SNAKE_CASE` | `MAX_RETRY_ATTEMPTS` |
| Type alias | `PascalCase` | `AgentId = str` |
| Private | `_leading_underscore` | `_internal_state` |

**Naming Guidance:**
- Names must be unambiguous without reading the implementation.
- Avoid abbreviations unless they are universally understood (`id`, `url`, `api`).
- Prefer clarity over brevity. `task_execution_context` is better than `ctx`.

---

## Comments and Documentation

### Inline Comments

Default to writing no inline comments. A well-named function and well-named variables communicate intent without prose.

Write a comment only when the WHY is genuinely non-obvious:
- A constraint imposed by an external system
- A subtle invariant that would be broken by a seemingly reasonable change
- A workaround for a specific bug in a dependency
- Behavior that would surprise a senior engineer reading the code for the first time

```python
# Correct — explains a non-obvious constraint
# Anthropic API rejects batches larger than 100 items; split before calling
chunks = split_into_chunks(items, max_size=100)

# Not acceptable — restates what the code already says
# Split items into chunks
chunks = split_into_chunks(items, max_size=100)
```

### Docstrings

Public module, class, and function interfaces receive a single-line docstring if the name alone is insufficient. Multi-paragraph docstrings are not used — they belong in the documentation system, not the source file.

```python
def route_task(task: Task, context: ExecutionContext) -> AgentAssignment:
    """Select the best agent and model for the given task."""
    ...
```

---

## File and Module Structure

### One Responsibility Per Module

Each module has one clearly stated purpose. If you cannot describe a module's purpose in a single sentence, it has too many responsibilities.

### Public Interface Is Explicit

Every package exposes its public API through `__init__.py`. Callers import from the package, not from internal submodules.

```python
# Correct
from orchestrator import route_task, AgentAssignment

# Not acceptable
from orchestrator.router.internal.v2 import _route_task_impl
```

### No Circular Imports

Circular imports indicate that module boundaries are wrong. Resolve by extracting shared types into a `types.py` module or restructuring responsibility.

---

## Error Handling

### Typed Errors

All errors in MondayOS code are typed. Use the hierarchy defined in `core/errors.py`. Never raise bare `Exception`.

```python
# Correct
raise ModelRateLimitError(model="claude-opus-4-8", retry_after=30)

# Not acceptable
raise Exception("rate limited")
```

### Fail Loudly

Never swallow exceptions silently. If an error cannot be handled meaningfully at the current layer, let it propagate. Log it at the layer where it is caught and re-raise or convert to a typed error.

```python
# Not acceptable
try:
    result = call_model(prompt)
except Exception:
    pass

# Correct
try:
    result = call_model(prompt)
except ModelError as e:
    logger.error("model call failed", task_id=task.id, error=e)
    raise TaskExecutionError(task_id=task.id, cause=e) from e
```

### Recoverable vs Non-Recoverable

All typed errors must set `recoverable: bool`. The retry and escalation logic in `core/executor.py` uses this field to decide whether to retry, pause, or fail the task.

---

## Testing Standards

### Coverage Requirements

| Layer | Minimum Coverage |
|---|---|
| Core engine | 90% |
| Memory layer | 85% |
| Knowledge layer | 85% |
| Task system | 85% |
| Integration layer | 80% (external calls are mocked at boundary) |
| Orchestrator | 80% |
| Workflows | 70% |

**Why not 100%:** 100% coverage is achievable by testing trivial code. The minimums above reflect meaningful coverage of logic branches, not line-count padding.

### Test Types

**Unit tests** (`tests/unit/`) — test a single function or class in isolation. All external dependencies are mocked. Fast (< 1ms per test).

**Integration tests** (`tests/integration/`) — test interaction between two or more components. External APIs are replaced with recorded response fixtures. Moderate speed (< 100ms per test).

**End-to-end tests** (`tests/e2e/`) — test a complete task execution path against real or staging infrastructure. Slow (seconds). Run in CI on merge to `main`, not on every PR.

### Test Naming

```
test_{what_is_being_tested}_{scenario}_{expected_outcome}

# Examples
test_route_task_with_privacy_flag_selects_local_model
test_task_executor_on_approval_required_pauses_execution
test_memory_write_persists_across_session_restart
```

### No Tests, No Merge

Features without tests that cover their core behavior do not merge to `main`. AI-generated code is held to the same standard as human-written code.

---

## Git Standards

### Branch Naming

```
{type}/{short-description}

# Examples
feature/task-routing-logic
fix/memory-persistence-on-crash
docs/architecture-v2
refactor/integration-layer-interface
```

### Commit Messages

Follow Conventional Commits format:

```
{type}({scope}): {short description}

{optional body — explain WHY, not WHAT}

{optional footer — references to tasks, decisions}
```

**Types:** `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`

```
feat(orchestrator): add model selection based on task privacy flag

Tasks marked privacy_sensitive=True are now always routed to the local
Ollama integration regardless of quality heuristics. This prevents
sensitive prompt content from being sent to external APIs.

Closes TASK-0042
```

### Commit Granularity

Each commit represents a single logical change. A commit that fixes a bug and adds a feature is two commits. Atomic commits make rollback, bisect, and review significantly easier.

### Main Branch Protection

- `main` is always deployable.
- No direct commits to `main`. All changes go through a pull request.
- At minimum one approval required (human or designated AI reviewer agent in future phases).
- CI must pass before merge.

---

## Dependency Management

### `pyproject.toml` Is the Source of Truth

All dependencies and their version constraints are defined in `pyproject.toml`. No package is installed that is not declared here.

### Pin with Ranges, Lock with `requirements.lock`

Dependencies are declared with compatible-release constraints (`~=`) in `pyproject.toml` and pinned exactly in a `requirements.lock` file generated by the package manager. Production deployments use the lock file; development uses the ranges.

**Why:** Ranges allow security patches to flow automatically; the lock file ensures exact reproducibility across environments.

### Minimize Dependencies

Before adding a dependency, ask: can this be done with the standard library? Can it be done in < 50 lines? If yes, do not add the dependency.

**Why:** Every dependency is a supply chain risk, a maintenance burden, and a potential source of breaking changes. Dependencies compound over time.

### Audit Before Adding

New dependencies must be reviewed for:
- Active maintenance (last commit < 6 months)
- License compatibility (MIT, Apache-2.0, BSD preferred)
- Security advisories (check PyPI safety database)
- Download volume (proxy for community trust)

---

## Security Standards

### Secrets Management

- No secrets in source files, config files, or commit history. Ever.
- Secrets are loaded from environment variables at runtime.
- `.env` files are for local development only and are listed in `.gitignore`.
- If a secret is committed by mistake: rotate it immediately, then remove it from history.

### Input Validation

All inputs from external sources (user input, AI model outputs, external APIs) are validated before use. AI model output is treated as untrusted input — validate its schema before acting on it.

### Prompt Injection Awareness

AI agents that act on content from external sources (web, email, user-provided documents) must treat that content as potentially adversarial. Content from external sources is never interpolated directly into system prompts without sanitization and scope limiting.

### Least Privilege

Agents are granted only the permissions required for their assigned task. An agent that only needs to read the knowledge base does not receive write permissions.

---

## AI-Generated Code Standards

AI-generated code is held to the same standards as human-written code: it must pass type checking, linting, tests, and code review before it merges.

Additional requirements for AI-generated code:

1. The commit message must note that the code was AI-generated and which model produced it.
2. A human must review and approve AI-generated code before it merges to `main`.
3. AI-generated code that is later found to contain a bug creates a knowledge entry documenting the failure pattern so the AI system can learn from it.
