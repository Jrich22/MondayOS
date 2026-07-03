# Contributing to MondayOS

Thanks for your interest in improving MondayOS. This guide covers the
development workflow, coding standards, the pull-request process, and testing
requirements. It assumes no prior knowledge of the codebase.

---

## Getting set up

**Requirements:** Python ≥ 3.11 and Git.

```bash
git clone <your-fork-url> MondayOS
cd MondayOS
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"            # installs runtime + dev tools (pytest, mypy, ruff)
pytest                             # confirm a green baseline: 773 passed, 12 skipped
monday status                      # confirm the CLI runs
```

If `pytest` is green and `monday status` reports `MondayOS v1.0.0b1`, you are
ready to develop.

---

## Development workflow

1. **Create a branch from `main`.** Never commit directly to `main`.
   ```bash
   git checkout -b feature/short-description
   ```
2. **Make a focused change.** One logical change per branch. Keep changes within
   existing subsystems — adding a new top-level package is a deliberate
   architectural decision, not a routine contribution (see *Architecture
   boundaries* below).
3. **Write tests** alongside the change (see *Testing requirements*).
4. **Run the full suite and linters** before pushing.
   ```bash
   pytest
   ruff check .
   mypy            # type checking (see pyproject for the strict config)
   ```
5. **Update documentation.** If you changed the public API or CLI, update the
   relevant doc (`README.md`, `docs/CLI.md`) and add a `docs/CHANGELOG.md` entry
   under `[Unreleased]`.
6. **Open a pull request** against `main`.

### Architecture boundaries

MondayOS is a layered platform with one rule that overrides convenience:

> **Everything external goes through the `Monday` public API.** Internal
> subsystems (`brain`, `knowledge`, `tasks`, `events`, …) are implementation
> details. The CLI and any integration import only `from monday import Monday`.

Practical consequences for contributors:

- **No provider-specific code outside `brain/providers/`.** Anthropic / OpenAI /
  Ollama SDK calls and prompt formatting live only in provider implementations.
  Everything else depends on the `AIProvider` interface.
- **Prefer additive changes within an existing subsystem** over introducing a
  new one. New `*Response` types go in `monday/types.py`; new pipeline pieces go
  inside the subsystem that owns them.
- **Reuse, don't duplicate.** Need tasks, knowledge, or advice from inside a
  subsystem? Go through the Monday public API or the existing manager classes —
  do not re-implement their logic.

---

## Coding standards

The standards are enforced by tooling configured in `pyproject.toml`; match the
surrounding code and you will rarely fight them.

- **Style & linting:** `ruff` (line length 100; rule sets `E, F, I, B, UP, N`).
  Run `ruff check .` and fix what it reports.
- **Typing:** `mypy` in **strict** mode (tests excluded). All new code is fully
  type-annotated. Use `from __future__ import annotations` at the top of modules.
- **Docstrings:** every public module, class, and method has a docstring that
  explains *what* and *why*, not just *how*. Match the existing density and tone.
- **Errors:** raise typed, descriptive errors; never swallow exceptions
  silently. Public API methods return `*Response` objects with a `success`/
  `accepted` flag and a `message` rather than raising for expected failures.
- **Determinism:** keep reasoning, planning, and validation deterministic where
  they are today. AI enrichment is additive and must degrade gracefully (and
  silently) when no provider is configured.
- **Naming:** `snake_case` for functions/variables, `PascalCase` for classes,
  `SCREAMING_SNAKE_CASE` for module constants. Private helpers are prefixed `_`.

Full engineering and documentation conventions:
[docs/ENGINEERING_STANDARDS.md](docs/ENGINEERING_STANDARDS.md) and
[docs/DOCUMENTATION_STANDARDS.md](docs/DOCUMENTATION_STANDARDS.md).

---

## Testing requirements

Tests are not optional. The bar for merging:

- **The full suite passes:** `pytest` is green (`773 passed, 12 skipped` at the
  time of the beta — your change should only increase the pass count).
- **New behaviour is covered.** Every new public method, CLI command, or branch
  of logic gets a test. Match the existing test layout in `tests/`
  (`test_<subsystem>.py`, `unittest.TestCase` classes grouped by unit).
- **Tests are deterministic and offline.** Never call a real AI provider in a
  test. Inject a fake/mock provider (see `tests/test_orchestrator.py` and
  `tests/test_providers.py` for the pattern) and use `tmp_path` /
  `TemporaryDirectory` for filesystem state.
- **Test the public surface.** Prefer driving behaviour through `Monday(...)` and
  asserting on `*Response` objects over reaching into private internals.

Run a focused file while iterating, then the whole suite before pushing:

```bash
pytest tests/test_orchestrator.py -q     # one file
pytest                                   # everything
pytest --cov                             # with coverage (source list in pyproject)
```

---

## Pull request process

1. **Title:** imperative and scoped, e.g. `tasks: add review transition`.
2. **Description:** what changed and why; link any related initiative or issue.
3. **Checklist** (include in the PR body):
   - [ ] `pytest` passes locally
   - [ ] `ruff check .` and `mypy` clean
   - [ ] New/changed behaviour is tested
   - [ ] Docs updated (`README.md` / `docs/CLI.md` / `docs/CHANGELOG.md`) if the
         public API or CLI changed
   - [ ] Change stays within existing subsystems (or the PR explains why a new
         one is justified)
4. **Review:** at least one maintainer approval. Keep the branch up to date with
   `main`. Discussion happens on the PR; push follow-up commits rather than
   force-pushing over review history where possible.
5. **Merge:** no code merges to `main` without a passing suite. Squash-merge is
   preferred to keep history readable; the squash message should follow the
   commit-message convention already in the log (`Sprint X.Y: …` /
   `Initiative NNN: …` / `area: summary`).

---

## Commit messages

- Imperative mood, concise subject line.
- Explain *why* in the body when the change is non-obvious.
- Reference the initiative/sprint when applicable.

---

## Reporting issues

Open an issue with: what you expected, what happened, the exact command, and the
output. Include `monday status` output and your Python version. For security-
sensitive reports, contact a maintainer privately rather than filing publicly.

Welcome aboard — MondayOS gets better one well-tested, well-documented change at
a time.
