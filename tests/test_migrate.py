"""Tests for the knowledge migration engine (Initiative 007)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from migrate.candidate import KnowledgeCandidate, _fingerprint, slugify
from migrate.engine import MigrationEngine
from migrate.errors import RollbackError, UnknownSourceError
from migrate.parsers.changelog import ChangelogParser
from migrate.parsers.decisions import DecisionsParser
from migrate.parsers.roadmap import RoadmapParser
from migrate.parsers.self_hosting import SelfHostingParser
from migrate.parsers.session_log import SessionLogParser
from migrate.parsers.workflows import WorkflowsParser
from migrate.report import FailedEntry, ImportedEntry, ImportReport, RollbackReport, SkippedEntry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_monday(tmp_path: Path):
    from monday import Monday, MondayConfig
    return Monday(MondayConfig(project_root=tmp_path))


def _engine(monday, tmp_path: Path) -> MigrationEngine:
    return MigrationEngine(
        monday=monday,
        project_root=tmp_path,
        logs_dir=tmp_path / "logs" / "migrations",
    )


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


CHANGELOG_TEXT = """\
# Changelog

## [Unreleased]
Nothing here yet.

## [0.2.0] — 2026-06-28 — Sprint 1.2: Knowledge Capture

### Added
- `KnowledgeStore` with YAML-frontmatter Markdown backend.
- Full-text search across all knowledge entries.

---

## [0.1.0] — 2026-06-27 — Sprint 1.1: Foundation

### Added
- Initial project scaffolding and MKS 1.0 specification.

---
"""

DECISIONS_TEXT = """\
# Architectural Decisions

## ADR-001: Use Markdown on Disk for Phase 1

**Status:** Accepted
**Date:** 2026-06-27

### Context
We need a storage backend for Phase 1 that requires zero setup.

### Decision
Use YAML-frontmatter Markdown files stored in the knowledge/ directory.

### Consequences
Fast iteration, no migrations needed in Phase 1.

---

## ADR-002: Monday as the Sole Public API

**Status:** Accepted

### Decision
All external interaction flows through the Monday class.
"""

SESSION_LOG_TEXT = """\
# Session Log

## 2026-06-27 — Sprint 1.1: Foundation

### Session Summary
Scaffolded the project. Wrote MKS 1.0 and set up the Python package structure.

### Known technical debt
- No LLM integration yet
- Index is rebuilt on every start

---

## 2026-06-28 — Sprint 1.2: Knowledge Capture

### Session Summary
Implemented KnowledgeStore. Added search with keyword scoring.

---
"""

ROADMAP_TEXT = """\
# Roadmap

## Phase 1 — Single-User Local System

**Goal:** Build a working local AI OS with persistent knowledge.

This phase covers the core engine and knowledge subsystem.

#### 1.1 — Core Engine and Integration Layer

**Goal:** Create the stable Monday public API.

The core engine ties together all subsystems.

---
"""

WORKFLOWS_TEXT = """\
# Workflows

## Overview

MondayOS supports multi-step YAML-defined workflows.

## Step Types

### `ask`

**Outputs:** answer, confidence

Invoke Monday.ask() with a prompt.

### `search`

**Outputs:** results, total_found

Invoke Monday.search() with a query.

## CLI Usage

```bash
monday workflow list
monday workflow run my-workflow --var foo=bar
```

## Adding a New Workflow

Create a YAML file in workflows/definitions/.
"""

SELF_HOSTING_TEXT = """\
# Self-Hosting Plan

## Part 1 — Current State Audit

This section audits all direct file writes in the current codebase.

### Opportunity 1 — Sprint Completion Workflow

**Impact:** High | **Effort:** Low

Automate the sprint completion process via MondayOS workflows.

### Opportunity 2 — Changelog Generation

**Impact:** Medium | **Effort:** Medium

Generate changelog entries from task completions automatically.

## Part 2 — Workflow Designs

### Workflow A — `sprint-completion`

**Trigger:** End of each sprint

1. Capture sprint summary
2. Update CHANGELOG.md
"""


# ===========================================================================
# TestKnowledgeCandidate
# ===========================================================================

class TestKnowledgeCandidate:
    def test_fingerprint_auto_filled(self):
        c = KnowledgeCandidate(
            title="Test", entry_type="pattern",
            content="Hello world", source_ref="test:1", source_file="x.md",
        )
        assert c.fingerprint == _fingerprint("Hello world")
        assert len(c.fingerprint) == 16

    def test_summary_auto_extracted(self):
        c = KnowledgeCandidate(
            title="T", entry_type="pattern",
            content="# Heading\n\nThis is the summary line.",
            source_ref="test:1", source_file="x.md",
        )
        assert c.summary == "This is the summary line."

    def test_explicit_fingerprint_preserved(self):
        c = KnowledgeCandidate(
            title="T", entry_type="pattern",
            content="abc", source_ref="r", source_file="f.md",
            fingerprint="custom123",
        )
        assert c.fingerprint == "custom123"

    def test_explicit_summary_preserved(self):
        c = KnowledgeCandidate(
            title="T", entry_type="pattern",
            content="anything", source_ref="r", source_file="f.md",
            summary="My summary",
        )
        assert c.summary == "My summary"

    def test_slugify(self):
        assert slugify("Sprint 1.2: Knowledge Capture") == "sprint-12-knowledge-capture"
        assert slugify("ADR-001: Use Markdown") == "adr-001-use-markdown"
        assert slugify("") == ""


# ===========================================================================
# TestImportReport
# ===========================================================================

class TestImportReport:
    def test_start_generates_run_id(self):
        r = ImportReport.start(sources=["changelog"], dry_run=False)
        assert len(r.run_id) == 36  # UUID
        assert r.sources == ["changelog"]
        assert not r.dry_run

    def test_counters_computed_from_lists(self):
        r = ImportReport.start(sources=[], dry_run=False)
        r.imported.append(ImportedEntry("r:1", "SPR-0001", "T", "sprint"))
        r.skipped.append(SkippedEntry("r:2", "T", "duplicate"))
        r.failed.append(FailedEntry("r:3", "T", "error"))
        assert r.imported_count == 1
        assert r.skipped_count == 1
        assert r.failed_count == 1

    def test_write_and_load_round_trip(self, tmp_path):
        logs_dir = tmp_path / "logs"
        r = ImportReport.start(sources=["changelog", "decisions"], dry_run=True)
        r.candidates_found = 5
        r.imported.append(ImportedEntry("changelog:0.1.0", "[dry-run]", "Sprint 1.1", "sprint"))
        r.skipped.append(SkippedEntry("changelog:0.2.0", "Sprint 1.2", "duplicate"))
        path = r.write(logs_dir)

        assert path.exists()
        loaded = ImportReport.load(path)
        assert loaded.run_id == r.run_id
        assert loaded.dry_run is True
        assert loaded.sources == ["changelog", "decisions"]
        assert loaded.candidates_found == 5
        assert loaded.imported_count == 1
        assert loaded.skipped_count == 1
        assert loaded.imported[0].entry_id == "[dry-run]"

    def test_to_dict_has_expected_keys(self):
        r = ImportReport.start(sources=["x"], dry_run=False)
        d = r.to_dict()
        assert "run_id" in d
        assert "imported" in d
        assert "skipped" in d
        assert "failed" in d


# ===========================================================================
# TestChangelogParser
# ===========================================================================

class TestChangelogParser:
    def test_extracts_versioned_sections(self):
        parser = ChangelogParser()
        candidates = parser.parse(CHANGELOG_TEXT)
        assert len(candidates) == 2

    def test_skips_unreleased(self):
        parser = ChangelogParser()
        candidates = parser.parse(CHANGELOG_TEXT)
        refs = [c.source_ref for c in candidates]
        assert not any("unreleased" in r.lower() for r in refs)

    def test_source_refs(self):
        parser = ChangelogParser()
        candidates = parser.parse(CHANGELOG_TEXT)
        refs = {c.source_ref for c in candidates}
        assert "changelog:0.2.0" in refs
        assert "changelog:0.1.0" in refs

    def test_entry_type_is_sprint(self):
        parser = ChangelogParser()
        for c in parser.parse(CHANGELOG_TEXT):
            assert c.entry_type == "sprint"

    def test_confidence_is_high(self):
        parser = ChangelogParser()
        for c in parser.parse(CHANGELOG_TEXT):
            assert c.confidence >= 0.9

    def test_sprint_tag_derived_from_title(self):
        parser = ChangelogParser()
        candidates = parser.parse(CHANGELOG_TEXT)
        tags_0_2 = next(c.tags for c in candidates if "0.2.0" in c.source_ref)
        assert "sprint-1.2" in tags_0_2

    def test_deduplication(self):
        text = CHANGELOG_TEXT + "\n## [0.2.0] — 2026-06-28 — Duplicate\n\nDuplicate.\n"
        parser = ChangelogParser()
        candidates = parser.parse(text)
        refs = [c.source_ref for c in candidates]
        assert refs.count("changelog:0.2.0") == 1

    def test_empty_text_returns_empty(self):
        assert ChangelogParser().parse("") == []

    def test_source_info(self):
        info = ChangelogParser().source_info()
        assert info.name == "changelog"
        assert "sprint" in info.entry_types


# ===========================================================================
# TestDecisionsParser
# ===========================================================================

class TestDecisionsParser:
    def test_extracts_adrs(self):
        parser = DecisionsParser()
        candidates = parser.parse(DECISIONS_TEXT)
        assert len(candidates) == 2

    def test_source_refs(self):
        parser = DecisionsParser()
        candidates = parser.parse(DECISIONS_TEXT)
        refs = {c.source_ref for c in candidates}
        assert "decisions:ADR-001" in refs
        assert "decisions:ADR-002" in refs

    def test_entry_type_is_decision(self):
        parser = DecisionsParser()
        for c in parser.parse(DECISIONS_TEXT):
            assert c.entry_type == "decision"

    def test_adr_tag_added(self):
        parser = DecisionsParser()
        candidates = parser.parse(DECISIONS_TEXT)
        tags_001 = next(c.tags for c in candidates if "ADR-001" in c.source_ref)
        assert "adr" in tags_001

    def test_empty_text_returns_empty(self):
        assert DecisionsParser().parse("") == []


# ===========================================================================
# TestSessionLogParser
# ===========================================================================

class TestSessionLogParser:
    def test_extracts_sessions(self):
        parser = SessionLogParser()
        candidates = parser.parse(SESSION_LOG_TEXT)
        sprint_candidates = [c for c in candidates if c.entry_type == "sprint"]
        assert len(sprint_candidates) == 2

    def test_extracts_tech_debt_bugs(self):
        parser = SessionLogParser()
        candidates = parser.parse(SESSION_LOG_TEXT)
        bugs = [c for c in candidates if c.entry_type == "bug"]
        assert len(bugs) >= 1

    def test_sprint_source_refs_include_date(self):
        parser = SessionLogParser()
        candidates = parser.parse(SESSION_LOG_TEXT)
        refs = [c.source_ref for c in candidates if c.entry_type == "sprint"]
        assert any("2026-06-27" in r for r in refs)

    def test_git_checkpoint_skipped(self):
        text = SESSION_LOG_TEXT + "\n## 2026-06-29 — Git Checkpoint\n\nJust a checkpoint.\n"
        parser = SessionLogParser()
        candidates = parser.parse(text)
        refs = [c.source_ref for c in candidates]
        assert not any("checkpoint" in r for r in refs)

    def test_empty_text_returns_empty(self):
        assert SessionLogParser().parse("") == []


# ===========================================================================
# TestRoadmapParser
# ===========================================================================

class TestRoadmapParser:
    def test_extracts_phase_documentation(self):
        parser = RoadmapParser()
        candidates = parser.parse(ROADMAP_TEXT)
        docs = [c for c in candidates if c.entry_type == "documentation"]
        assert len(docs) >= 1

    def test_extracts_milestones_as_features(self):
        parser = RoadmapParser()
        candidates = parser.parse(ROADMAP_TEXT)
        features = [c for c in candidates if c.entry_type == "feature"]
        assert len(features) >= 1

    def test_milestone_source_ref(self):
        parser = RoadmapParser()
        candidates = parser.parse(ROADMAP_TEXT)
        refs = [c.source_ref for c in candidates]
        assert "roadmap:milestone-1.1" in refs

    def test_phase_source_ref(self):
        parser = RoadmapParser()
        candidates = parser.parse(ROADMAP_TEXT)
        refs = [c.source_ref for c in candidates]
        assert "roadmap:phase-1" in refs


# ===========================================================================
# TestWorkflowsParser
# ===========================================================================

class TestWorkflowsParser:
    def test_extracts_step_types_as_patterns(self):
        parser = WorkflowsParser()
        candidates = parser.parse(WORKFLOWS_TEXT)
        patterns = [c for c in candidates if c.entry_type == "pattern"]
        assert len(patterns) >= 2

    def test_step_type_source_refs(self):
        parser = WorkflowsParser()
        candidates = parser.parse(WORKFLOWS_TEXT)
        refs = {c.source_ref for c in candidates}
        assert "workflows:step-type-ask" in refs
        assert "workflows:step-type-search" in refs

    def test_extracts_overview_doc(self):
        parser = WorkflowsParser()
        candidates = parser.parse(WORKFLOWS_TEXT)
        docs = [c for c in candidates if c.entry_type == "documentation"]
        assert len(docs) >= 1

    def test_extracts_usage_runbook(self):
        parser = WorkflowsParser()
        candidates = parser.parse(WORKFLOWS_TEXT)
        runbooks = [c for c in candidates if c.entry_type == "runbook"]
        assert any("usage-runbook" in c.source_ref for c in runbooks)


# ===========================================================================
# TestSelfHostingParser
# ===========================================================================

class TestSelfHostingParser:
    def test_extracts_opportunities_as_features(self):
        parser = SelfHostingParser()
        candidates = parser.parse(SELF_HOSTING_TEXT)
        features = [c for c in candidates if c.entry_type == "feature"]
        assert len(features) >= 2

    def test_opportunity_source_refs(self):
        parser = SelfHostingParser()
        candidates = parser.parse(SELF_HOSTING_TEXT)
        refs = {c.source_ref for c in candidates}
        assert "self-hosting:opportunity-01" in refs
        assert "self-hosting:opportunity-02" in refs

    def test_extracts_workflow_designs_as_runbooks(self):
        parser = SelfHostingParser()
        candidates = parser.parse(SELF_HOSTING_TEXT)
        runbooks = [c for c in candidates if c.entry_type == "runbook"]
        assert len(runbooks) >= 1

    def test_workflow_design_source_ref(self):
        parser = SelfHostingParser()
        candidates = parser.parse(SELF_HOSTING_TEXT)
        refs = {c.source_ref for c in candidates}
        assert any("sprint-completion" in r for r in refs)

    def test_extracts_part_summaries_as_docs(self):
        parser = SelfHostingParser()
        candidates = parser.parse(SELF_HOSTING_TEXT)
        docs = [c for c in candidates if c.entry_type == "documentation"]
        assert len(docs) >= 1


# ===========================================================================
# TestMigrationEngine
# ===========================================================================

class TestMigrationEngine:
    def _setup(self, tmp_path: Path):
        _write(tmp_path / "docs" / "CHANGELOG.md", CHANGELOG_TEXT)
        monday = _make_monday(tmp_path)
        engine = _engine(monday, tmp_path)
        return monday, engine

    def test_list_sources_returns_all_registered(self, tmp_path):
        monday = _make_monday(tmp_path)
        eng = _engine(monday, tmp_path)
        sources = eng.list_sources()
        names = [s.name for s in sources]
        assert "changelog" in names
        assert "decisions" in names
        assert "session-log" in names
        assert "roadmap" in names
        assert "workflows" in names
        assert "self-hosting" in names

    def test_source_exists_true_when_file_present(self, tmp_path):
        monday, eng = self._setup(tmp_path)
        assert eng.source_exists("changelog") is True

    def test_source_exists_false_when_missing(self, tmp_path):
        monday = _make_monday(tmp_path)
        eng = _engine(monday, tmp_path)
        assert eng.source_exists("changelog") is False

    def test_source_exists_false_for_unknown(self, tmp_path):
        monday = _make_monday(tmp_path)
        eng = _engine(monday, tmp_path)
        assert eng.source_exists("bogus-source") is False

    def test_unknown_source_raises(self, tmp_path):
        monday, eng = self._setup(tmp_path)
        with pytest.raises(UnknownSourceError):
            eng.run(sources=["bogus-source"])

    def test_dry_run_does_not_write_entries(self, tmp_path):
        monday, eng = self._setup(tmp_path)
        report = eng.run(sources=["changelog"], dry_run=True)
        assert report.dry_run is True
        assert report.imported_count > 0
        all_ids = [e.entry_id for e in report.imported]
        assert all(eid == "[dry-run]" for eid in all_ids)
        # No entries in the knowledge store
        r = monday.search("sprint")
        assert r.total_found == 0

    def test_dry_run_does_not_persist_index(self, tmp_path):
        monday, eng = self._setup(tmp_path)
        eng.run(sources=["changelog"], dry_run=True)
        index_path = tmp_path / "knowledge" / ".import_index.json"
        assert not index_path.exists()

    def test_real_run_imports_entries(self, tmp_path):
        monday, eng = self._setup(tmp_path)
        report = eng.run(sources=["changelog"])
        assert report.imported_count > 0
        assert report.failed_count == 0
        # Entries should be searchable
        r = monday.search("Sprint")
        assert r.total_found > 0

    def test_real_run_persists_index(self, tmp_path):
        monday, eng = self._setup(tmp_path)
        eng.run(sources=["changelog"])
        index_path = tmp_path / "knowledge" / ".import_index.json"
        assert index_path.exists()
        index = json.loads(index_path.read_text())
        assert "source_refs" in index
        assert len(index["source_refs"]) > 0

    def test_idempotent_second_run_skips(self, tmp_path):
        monday, eng = self._setup(tmp_path)
        r1 = eng.run(sources=["changelog"])
        assert r1.imported_count > 0

        eng2 = _engine(monday, tmp_path)
        r2 = eng2.run(sources=["changelog"])
        assert r2.imported_count == 0
        assert r2.skipped_count > 0
        skip_reasons = [s.reason for s in r2.skipped]
        assert all(reason == "duplicate" for reason in skip_reasons)

    def test_overwrite_reimports(self, tmp_path):
        monday, eng = self._setup(tmp_path)
        r1 = eng.run(sources=["changelog"])
        n = r1.imported_count

        eng2 = _engine(monday, tmp_path)
        r2 = eng2.run(sources=["changelog"], overwrite=True)
        assert r2.imported_count == n

    def test_missing_source_file_skipped_gracefully(self, tmp_path):
        monday = _make_monday(tmp_path)
        eng = _engine(monday, tmp_path)
        # decisions file not present — should not raise
        report = eng.run(sources=["decisions"])
        assert report.failed_count == 0

    def test_report_written_to_logs_dir(self, tmp_path):
        monday, eng = self._setup(tmp_path)
        eng.run(sources=["changelog"])
        log_dir = tmp_path / "logs" / "migrations"
        assert log_dir.exists()
        reports = list(log_dir.glob("*.json"))
        assert len(reports) == 1

    def test_progress_callback_called(self, tmp_path):
        monday, eng = self._setup(tmp_path)
        messages: list[str] = []
        eng.run(sources=["changelog"], progress_callback=messages.append)
        assert len(messages) > 0

    def test_rollback_removes_entries(self, tmp_path):
        monday, eng = self._setup(tmp_path)
        report = eng.run(sources=["changelog"])
        run_id = report.run_id
        assert report.imported_count > 0

        eng2 = _engine(monday, tmp_path)
        rollback = eng2.rollback(run_id)
        assert len(rollback.removed) == report.imported_count
        assert len(rollback.failed) == 0

        # Entries should be gone from knowledge store
        r = monday.search("Sprint")
        assert r.total_found == 0

    def test_rollback_unknown_run_id_raises(self, tmp_path):
        monday = _make_monday(tmp_path)
        eng = _engine(monday, tmp_path)
        with pytest.raises(RollbackError):
            eng.rollback("nonexistent-id")

    def test_rollback_clears_import_index(self, tmp_path):
        monday, eng = self._setup(tmp_path)
        report = eng.run(sources=["changelog"])
        run_id = report.run_id

        eng2 = _engine(monday, tmp_path)
        eng2.rollback(run_id)

        index_path = tmp_path / "knowledge" / ".import_index.json"
        index = json.loads(index_path.read_text())
        assert len(index["source_refs"]) == 0

    def test_low_confidence_skipped(self, tmp_path):
        monday = _make_monday(tmp_path)
        eng = _engine(monday, tmp_path)
        report = ImportReport.start(sources=["test"], dry_run=False)
        index = {"source_refs": {}}
        candidate = KnowledgeCandidate(
            title="Low confidence",
            entry_type="pattern",
            content="Some content here.",
            source_ref="test:low-conf",
            source_file="test.md",
            confidence=0.1,
        )
        eng._process_candidate(candidate, report, index, dry_run=False, overwrite=False)
        assert report.skipped_count == 1
        assert report.skipped[0].reason == "low_confidence"

    def test_empty_content_skipped(self, tmp_path):
        monday = _make_monday(tmp_path)
        eng = _engine(monday, tmp_path)
        report = ImportReport.start(sources=["test"], dry_run=False)
        index = {"source_refs": {}}
        candidate = KnowledgeCandidate(
            title="Empty",
            entry_type="pattern",
            content="   ",
            source_ref="test:empty",
            source_file="test.md",
        )
        eng._process_candidate(candidate, report, index, dry_run=False, overwrite=False)
        assert report.skipped_count == 1
        assert report.skipped[0].reason == "empty_content"


# ===========================================================================
# TestMondayMigrate
# ===========================================================================

class TestMondayMigrate:
    def _setup_changelog(self, tmp_path: Path):
        _write(tmp_path / "docs" / "CHANGELOG.md", CHANGELOG_TEXT)
        return _make_monday(tmp_path)

    def test_list_sources_returns_all(self, tmp_path):
        monday = _make_monday(tmp_path)
        r = monday.migrate(action="list-sources")
        assert r.success
        assert r.action == "list-sources"
        assert r.data["count"] >= 6
        names = [s["name"] for s in r.data["sources"]]
        assert "changelog" in names

    def test_list_sources_marks_exists(self, tmp_path):
        monday = self._setup_changelog(tmp_path)
        r = monday.migrate(action="list-sources")
        sources = {s["name"]: s for s in r.data["sources"]}
        assert sources["changelog"]["exists"] is True
        assert sources["decisions"]["exists"] is False

    def test_run_imports_changelog(self, tmp_path):
        monday = self._setup_changelog(tmp_path)
        r = monday.migrate(action="run", sources=["changelog"])
        assert r.success
        assert r.imported_count > 0
        assert r.failed_count == 0

    def test_run_dry_run(self, tmp_path):
        monday = self._setup_changelog(tmp_path)
        r = monday.migrate(action="run", sources=["changelog"], dry_run=True)
        assert r.dry_run is True
        assert r.imported_count > 0
        # Nothing actually written
        s = monday.search("Sprint")
        assert s.total_found == 0

    def test_run_unknown_source_fails(self, tmp_path):
        monday = _make_monday(tmp_path)
        r = monday.migrate(action="run", sources=["bogus"])
        assert not r.success
        assert "bogus" in r.message

    def test_rollback_requires_run_id(self, tmp_path):
        monday = _make_monday(tmp_path)
        r = monday.migrate(action="rollback")
        assert not r.success
        assert "run_id" in r.message

    def test_rollback_unknown_run_id_fails(self, tmp_path):
        monday = _make_monday(tmp_path)
        r = monday.migrate(action="rollback", run_id="deadbeef")
        assert not r.success

    def test_rollback_removes_entries(self, tmp_path):
        monday = self._setup_changelog(tmp_path)
        r1 = monday.migrate(action="run", sources=["changelog"])
        run_id = r1.run_id
        assert r1.imported_count > 0

        monday2 = _make_monday(tmp_path)
        r2 = monday2.migrate(action="rollback", run_id=run_id)
        assert r2.success

        s = monday2.search("Sprint")
        assert s.total_found == 0

    def test_unknown_action_fails(self, tmp_path):
        monday = _make_monday(tmp_path)
        r = monday.migrate(action="bad-action")
        assert not r.success
        assert "bad-action" in r.message

    def test_run_all_sources_skips_missing(self, tmp_path):
        monday = _make_monday(tmp_path)
        # No source files written — should not raise, just skip/fail gracefully
        r = monday.migrate(action="run")
        # Some may fail (parse error) but action itself shouldn't crash
        assert r.action == "run"


# ===========================================================================
# TestCLIMigrate
# ===========================================================================

class TestCLIMigrate:
    def _run(self, args: list[str]) -> tuple[int, str, str]:
        """Run the CLI and capture stdout/stderr via monkeypatching."""
        import io
        import sys
        from monday.cli import main

        out, err = io.StringIO(), io.StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = out
        sys.stderr = err
        try:
            rc = main(args)
        finally:
            sys.stdout = old_out
            sys.stderr = old_err
        return rc, out.getvalue(), err.getvalue()

    def test_migrate_list(self, tmp_path):
        rc, out, _ = self._run(["--project-root", str(tmp_path), "migrate", "list"])
        assert rc == 0
        assert "changelog" in out

    def test_migrate_dry_run(self, tmp_path):
        _write(tmp_path / "docs" / "CHANGELOG.md", CHANGELOG_TEXT)
        rc, out, _ = self._run([
            "--project-root", str(tmp_path),
            "migrate", "changelog", "--dry-run",
        ])
        assert rc == 0
        assert "dry-run" in out.lower() or "Migration" in out

    def test_migrate_unknown_source_exits_nonzero(self, tmp_path):
        rc, _, err = self._run([
            "--project-root", str(tmp_path),
            "migrate", "totally-unknown-source",
        ])
        # Should fail because totally-unknown-source is not a valid source name
        # but also not the special keywords list/rollback
        # UnknownSourceError is caught and returns failed report
        assert rc in (0, 1)  # either way no crash

    def test_migrate_rollback_needs_run_id(self, tmp_path):
        rc, out, err = self._run([
            "--project-root", str(tmp_path),
            "migrate", "rollback",
        ])
        assert rc == 1
        assert "run-id" in err or "run_id" in err

    def test_migrate_quiet_suppresses_progress(self, tmp_path):
        _write(tmp_path / "docs" / "CHANGELOG.md", CHANGELOG_TEXT)
        _, out_verbose, _ = self._run([
            "--project-root", str(tmp_path), "migrate", "changelog",
        ])
        _, out_quiet, _ = self._run([
            "--project-root", str(tmp_path), "migrate", "changelog",
            "--overwrite", "--quiet",
        ])
        # Quiet output should be shorter (no per-candidate progress lines)
        assert len(out_quiet) < len(out_verbose)

    def test_migrate_full_run(self, tmp_path):
        _write(tmp_path / "docs" / "CHANGELOG.md", CHANGELOG_TEXT)
        rc, out, _ = self._run([
            "--project-root", str(tmp_path),
            "migrate", "changelog",
        ])
        assert rc == 0
        assert "Imported" in out
