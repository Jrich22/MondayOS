# MondayOS — Release Checklist

A repeatable checklist for cutting a MondayOS release. The right-hand status
column records the result for the current release; reset it to ☐ when starting
the next one.

**Release under verification:** `1.0.0b1` (first public beta)
**Verified on:** 2026-06-28 · macOS (darwin), Python 3.14.6

---

## 1. Code & tests

| # | Item | Status |
|---|------|--------|
| 1.1 | Full test suite green (`pytest`) | ✅ 773 passed, 12 skipped |
| 1.2 | Suite green from a **clean copy** in a fresh venv | ✅ 773 passed, 12 skipped |
| 1.3 | Lint clean (`ruff check .`) | ☐ run before tag |
| 1.4 | Type check clean (`mypy`) | ☐ run before tag |
| 1.5 | No stray debug prints / TODO blockers in changed files | ✅ |

## 2. Versioning

| # | Item | Status |
|---|------|--------|
| 2.1 | `pyproject.toml` `version` bumped (`1.0.0b1`) | ✅ |
| 2.2 | `Monday.VERSION` / `_VERSION` matches (`1.0.0b1`) | ✅ |
| 2.3 | Version-pinned tests updated (`test_monday`, `test_cli`) | ✅ |
| 2.4 | `monday status` prints the new version | ✅ `MondayOS v1.0.0b1` |
| 2.5 | `docs/CHANGELOG.md` has an entry for this version | ✅ `[1.0.0b1]` |

## 3. Documentation

| # | Item | Status |
|---|------|--------|
| 3.1 | `README.md` present and current (vision, features, arch, quick start, CLI, onboarding, roadmap) | ✅ |
| 3.2 | `RELEASE.md` (features, limitations, known issues, upgrade path) | ✅ |
| 3.3 | `CONTRIBUTING.md` (workflow, standards, PR process, testing) | ✅ |
| 3.4 | `docs/ARCHITECTURE_DIAGRAM.md` | ✅ |
| 3.5 | `docs/BETA_ROADMAP.md` | ✅ |
| 3.6 | `docs/CLI.md` reflects all 12 commands | ✅ (existing reference) |
| 3.7 | Doc cross-links resolve (no dead relative links) | ✅ spot-checked |

## 4. Installation verification (clean room)

Performed against a pristine copy of the source tree in a brand-new virtual
environment (see commands below). A developer with no prior knowledge can:

| # | Step | Status |
|---|------|--------|
| 4.1 | Clone / obtain the source | ✅ |
| 4.2 | `python3 -m venv .venv && source .venv/bin/activate` | ✅ Python 3.14.6 |
| 4.3 | `pip install -e ".[dev]"` | ✅ exit 0 |
| 4.4 | `monday status` → healthy, correct version | ✅ |
| 4.5 | Run the CLI (`task create`, `task list`) | ✅ created TASK-0001 |
| 4.6 | Onboard a project (`project register` + `onboard`) | ✅ report generated |
| 4.7 | `monday execute TASK-0001 --dry-run` | ✅ plan + report persisted |
| 4.8 | `pytest` from the clean copy | ✅ 773 passed, 12 skipped |

**Clean-room commands used:**

```bash
rsync -a --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
  --exclude '*.egg-info' --exclude '.pytest_cache' --exclude '.mypy_cache' \
  --exclude '.ruff_cache' --exclude 'logs/executions' SRC/ CLEAN/src/
python3 -m venv CLEAN/venv
CLEAN/venv/bin/pip install -e "CLEAN/src[dev]"
CLEAN/venv/bin/monday --project-root CLEAN/data status
# ... task create / project register / onboard / execute --dry-run ...
( cd CLEAN/src && CLEAN/venv/bin/python -m pytest )
```

## 5. Repository hygiene

| # | Item | Status |
|---|------|--------|
| 5.1 | Runtime artifacts git-ignored (`logs/`, `config/projects.json`, `logs/executions/`) | ✅ |
| 5.2 | No secrets or machine-specific paths committed | ✅ |
| 5.3 | `LICENSE` file present | ☐ **add before public tag** |
| 5.4 | Git remote configured for publishing | ☐ **add before public tag** |

## 6. Pre-tag commit (release engineering)

> ⚠️ The beta source (orchestrator + these release docs) must be **committed**
> before tagging, so a real `git clone` reflects the beta. The clean-room test
> above was run against a working-tree snapshot of the would-be release.

| # | Item | Status |
|---|------|--------|
| 6.1 | Commit Initiative 012 (Execution Orchestrator) | ☐ pending |
| 6.2 | Commit Initiative 013 (beta release quality) | ☐ pending |
| 6.3 | Working tree clean (`git status`) | ☐ pending |
| 6.4 | Tag `v1.0.0b1` | ☐ pending |
| 6.5 | Push branch + tag to remote | ☐ pending |

---

## Sign-off

- [ ] All ✅ items confirmed by the release owner.
- [ ] All ☐ items either completed or explicitly accepted as out-of-scope for
      this beta (notably 5.3, 5.4, and section 6, which require a publishing
      decision and the pre-tag commit).

**Release owner:** ______________________  **Date:** ______________
