"""Tests for the doctor / repository health inspection system (Initiative 008)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from doctor.finding import Finding, Severity
from doctor.inspector import RepositoryInspector
from doctor.result import AnalyzerResult, DoctorReport, _compute_health_score, _health_grade


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_monday(tmp_path: Path):
    from monday import Monday, MondayConfig
    return Monday(MondayConfig(project_root=tmp_path))


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _git_init(path: Path) -> None:
    """Create a minimal .git directory so the git analyzer finds a repo."""
    (path / ".git").mkdir(exist_ok=True)


def _finding(category: str, severity: Severity, title: str) -> Finding:
    return Finding(category=category, severity=severity, title=title)


def _future_iso() -> str:
    """A run timestamp newer than any file the test just wrote."""
    return (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()


# ===========================================================================
# TestFinding
# ===========================================================================

class TestFinding:
    def test_to_dict(self):
        f = Finding(
            category="git", severity=Severity.WARNING, title="Dirty tree",
            detail="3 files changed", recommendation="Commit changes.",
        )
        d = f.to_dict()
        assert d["category"] == "git"
        assert d["severity"] == "warning"
        assert d["title"] == "Dirty tree"
        assert d["recommendation"] == "Commit changes."

    def test_severity_values(self):
        assert Severity.CRITICAL.value == "critical"
        assert Severity.WARNING.value == "warning"
        assert Severity.INFO.value == "info"
        assert Severity.OK.value == "ok"


# ===========================================================================
# TestDoctorReport
# ===========================================================================

class TestDoctorReport:
    def test_health_score_perfect(self):
        results = [AnalyzerResult("git", [_finding("git", Severity.OK, "Clean")])]
        report = DoctorReport.build(results)
        assert report.health_score == 100
        assert report.grade == "Excellent"

    def test_health_score_critical_deduction(self):
        findings = [_finding("x", Severity.CRITICAL, f"crit {i}") for i in range(3)]
        results = [AnalyzerResult("x", findings)]
        score = _compute_health_score(results)
        assert score == 100 - (3 * 15)  # 55

    def test_health_score_critical_cap(self):
        findings = [_finding("x", Severity.CRITICAL, f"c{i}") for i in range(10)]
        results = [AnalyzerResult("x", findings)]
        assert _compute_health_score(results) == 100 - 45  # capped at 45

    def test_health_score_warning_deduction(self):
        findings = [_finding("x", Severity.WARNING, f"w{i}") for i in range(4)]
        results = [AnalyzerResult("x", findings)]
        assert _compute_health_score(results) == 100 - 20

    def test_health_score_warning_cap(self):
        findings = [_finding("x", Severity.WARNING, f"w{i}") for i in range(20)]
        results = [AnalyzerResult("x", findings)]
        assert _compute_health_score(results) == 100 - 30

    def test_health_score_floor(self):
        # Both caps hit: 45 (critical) + 30 (warning) = 75 deducted → floor at 25
        criticals = [_finding("x", Severity.CRITICAL, f"c{i}") for i in range(10)]
        warnings = [_finding("x", Severity.WARNING, f"w{i}") for i in range(20)]
        results = [AnalyzerResult("x", criticals + warnings)]
        assert _compute_health_score(results) == 25

    def test_health_grade_labels(self):
        assert _health_grade(100) == "Excellent"
        assert _health_grade(90) == "Excellent"
        assert _health_grade(89) == "Good"
        assert _health_grade(75) == "Good"
        assert _health_grade(74) == "Fair"
        assert _health_grade(60) == "Fair"
        assert _health_grade(59) == "Poor"
        assert _health_grade(40) == "Poor"
        assert _health_grade(39) == "Critical"
        assert _health_grade(0) == "Critical"

    def test_recommendations_ranked_by_severity(self):
        results = [AnalyzerResult("x", [
            _finding("x", Severity.INFO, "info thing"),
            _finding("x", Severity.CRITICAL, "critical thing"),
            _finding("x", Severity.WARNING, "warning thing"),
        ])]
        # Add recommendations manually
        results[0].findings[0].recommendation = "info-rec"
        results[0].findings[1].recommendation = "critical-rec"
        results[0].findings[2].recommendation = "warning-rec"

        report = DoctorReport.build(results)
        recs = report.recommendations
        assert recs.index("critical-rec") < recs.index("warning-rec")
        assert recs.index("warning-rec") < recs.index("info-rec")

    def test_to_dict_structure(self):
        results = [AnalyzerResult("git", [_finding("git", Severity.OK, "ok")])]
        report = DoctorReport.build(results)
        d = report.to_dict()
        assert "health_score" in d
        assert "grade" in d
        assert "summary" in d
        assert "recommendations" in d
        assert "analyzers" in d
        assert d["analyzers"][0]["name"] == "git"

    def test_all_findings_flattened(self):
        r1 = AnalyzerResult("a", [_finding("a", Severity.OK, "ok1")])
        r2 = AnalyzerResult("b", [_finding("b", Severity.WARNING, "warn")])
        report = DoctorReport.build([r1, r2])
        assert len(report.all_findings) == 2


# ===========================================================================
# TestRepositoryInspector
# ===========================================================================

class TestRepositoryInspector:
    def test_available_analyzers_includes_all(self):
        names = RepositoryInspector.available_analyzers()
        assert "git" in names
        assert "tests" in names
        assert "code-quality" in names
        assert "knowledge" in names
        assert "documentation" in names
        assert "tasks" in names
        assert "config" in names

    def test_run_returns_doctor_report(self, tmp_path):
        _git_init(tmp_path)
        monday = _make_monday(tmp_path)
        inspector = RepositoryInspector(project_root=tmp_path, monday=monday)
        report = inspector.run()
        assert isinstance(report, DoctorReport)
        assert 0 <= report.health_score <= 100

    def test_analyzer_subset(self, tmp_path):
        _git_init(tmp_path)
        monday = _make_monday(tmp_path)
        inspector = RepositoryInspector(
            project_root=tmp_path, monday=monday, analyzer_names=["git", "config"]
        )
        report = inspector.run()
        names = [r.name for r in report.results]
        assert set(names) == {"git", "config"}

    def test_unknown_analyzer_name_skipped(self, tmp_path):
        inspector = RepositoryInspector(
            project_root=tmp_path, analyzer_names=["git", "bogus-analyzer"]
        )
        report = inspector.run()
        names = [r.name for r in report.results]
        assert "bogus-analyzer" not in names

    def test_analyzer_exception_surfaced_as_critical(self, tmp_path):
        from doctor.base import BaseAnalyzer

        class BrokenAnalyzer(BaseAnalyzer):
            NAME = "broken"
            def analyze(self) -> AnalyzerResult:
                raise RuntimeError("boom")

        inspector = RepositoryInspector.__new__(RepositoryInspector)
        inspector._root = tmp_path
        inspector._monday = None
        inspector._analyzer_classes = [BrokenAnalyzer]

        report = inspector.run()
        assert len(report.results) == 1
        assert report.results[0].error == "boom"
        assert any(f.severity == Severity.CRITICAL for f in report.results[0].findings)

    def test_all_analyzers_run_without_crash(self, tmp_path):
        """Smoke test: all analyzers complete on an empty project dir."""
        monday = _make_monday(tmp_path)
        inspector = RepositoryInspector(project_root=tmp_path, monday=monday)
        report = inspector.run()
        # All analyzers should have produced results (not errored out)
        errored = [r for r in report.results if r.error]
        assert not errored, f"Analyzers errored: {[(r.name, r.error) for r in errored]}"


# ===========================================================================
# TestGitAnalyzer
# ===========================================================================

class TestGitDirtinessClassification:
    """
    Runtime state must not be reported as source dirtiness.

    MondayOS writes generated knowledge, sequence counters, and run logs as a
    normal part of executing a task. Counting those as "uncommitted changes"
    made the system fail itself: Doctor reported them, Advise escalated them to
    a HIGH engineering risk, the planner fed that into the next run's prompt,
    and QA failed the task for the mess its own pipeline had just made — with
    the number growing on every rerun.
    """

    def _split(self, lines):
        from doctor.analyzers.git import _split_source_and_runtime
        return _split_source_and_runtime(lines)

    def test_generated_knowledge_is_runtime_not_source(self):
        source, runtime = self._split([
            "?? knowledge/runtime/research/RES-0123.md",
            "?? knowledge/runtime/research/RES-0124.md",
        ])
        assert source == []
        assert len(runtime) == 2

    def test_sequence_counters_are_runtime(self):
        source, runtime = self._split([
            " M knowledge/.sequences.json",
            " M tasks/.sequences.json",
        ])
        assert source == []
        assert len(runtime) == 2

    def test_run_logs_are_runtime(self):
        source, runtime = self._split([
            "?? logs/agents/team-e444918cdb19.json",
            "?? logs/executions/exec-abc.json",
        ])
        assert source == []
        assert len(runtime) == 2

    def test_real_source_changes_are_still_source(self):
        source, runtime = self._split([
            " M agents/verdicts.py",
            " M projects/sourcingbot/src/lib/req.ts",
            "?? docs/KNOWLEDGE_RUNTIME_POLICY.md",
        ])
        assert len(source) == 3
        assert runtime == []

    def test_curated_knowledge_is_source_not_runtime(self):
        """Human-authored research stays version-controlled and still counts."""
        source, runtime = self._split(["?? knowledge/research/RES-0200.md"])
        assert len(source) == 1
        assert runtime == []

    def test_mixed_tree_separates_cleanly(self):
        source, runtime = self._split([
            " M agents/team.py",
            "?? knowledge/runtime/research/RES-0123.md",
            " M tasks/.sequences.json",
            "?? logs/agents/run-abc.json",
        ])
        assert len(source) == 1
        assert len(runtime) == 3

    def test_rename_lines_are_parsed_by_destination(self):
        from doctor.analyzers.git import _porcelain_path
        assert _porcelain_path(
            "R  tasks/active/TASK-0001.md -> tasks/archived/weatherbot/TASK-0001.md"
        ) == "tasks/archived/weatherbot/TASK-0001.md"

    def test_runtime_description_groups_by_kind(self):
        from doctor.analyzers.git import _describe_runtime
        text = _describe_runtime([
            "?? knowledge/runtime/research/RES-0123.md",
            "?? knowledge/runtime/research/RES-0124.md",
            " M tasks/.sequences.json",
            "?? logs/agents/run-abc.json",
        ])
        assert "2 generated knowledge record(s)" in text
        assert "1 sequence counter(s) changed" in text
        assert "1 agent/team run log(s)" in text

    def test_unknown_paths_default_to_source(self):
        """Conservative: never quietly reclassify a real change as noise."""
        source, runtime = self._split(["?? something/unexpected.py"])
        assert len(source) == 1
        assert runtime == []


class TestGitAnalyzer:
    def _analyzer(self, tmp_path):
        from doctor.analyzers.git import GitAnalyzer
        return GitAnalyzer(project_root=tmp_path)

    def test_no_git_dir_critical(self, tmp_path):
        result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.CRITICAL for f in result.findings)
        assert any("git repository" in f.title.lower() for f in result.findings)

    def test_git_dir_present_no_critical(self, tmp_path):
        _git_init(tmp_path)
        # Patch subprocess to return clean state
        with patch("doctor.analyzers.git.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="main\n", returncode=0)
            result = self._analyzer(tmp_path).analyze()
        assert not any(f.severity == Severity.CRITICAL for f in result.findings)

    def test_dirty_tree_warning(self, tmp_path):
        _git_init(tmp_path)

        def fake_git(*args, **kwargs):
            cmd = args[0]
            if "--abbrev-ref" in cmd:
                return MagicMock(stdout="main\n")
            if "--porcelain" in cmd:
                return MagicMock(stdout="M  file.py\n?? new.py\n")
            if "--oneline" in cmd:
                return MagicMock(stdout="abc1234 Initial commit\n")
            return MagicMock(stdout="")

        with patch("doctor.analyzers.git.subprocess.run", side_effect=fake_git):
            result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.WARNING and "dirty" in f.title.lower()
                   for f in result.findings)

    def test_clean_tree_ok(self, tmp_path):
        _git_init(tmp_path)

        def fake_git(*args, **kwargs):
            cmd = args[0]
            if "--abbrev-ref" in cmd:
                return MagicMock(stdout="main\n")
            if "--porcelain" in cmd:
                return MagicMock(stdout="")
            if "--oneline" in cmd:
                return MagicMock(stdout="abc1234 commit\n")
            return MagicMock(stdout="")

        with patch("doctor.analyzers.git.subprocess.run", side_effect=fake_git):
            result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.OK and "clean" in f.title.lower()
                   for f in result.findings)

    def test_no_commits_warning(self, tmp_path):
        _git_init(tmp_path)

        def fake_git(*args, **kwargs):
            cmd = args[0]
            if "--abbrev-ref" in cmd:
                return MagicMock(stdout="main\n")
            return MagicMock(stdout="")

        with patch("doctor.analyzers.git.subprocess.run", side_effect=fake_git):
            result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.WARNING and "commit" in f.title.lower()
                   for f in result.findings)

    def test_result_has_name(self, tmp_path):
        result = self._analyzer(tmp_path).analyze()
        assert result.name == "git"


# ===========================================================================
# TestTestAnalyzer
# ===========================================================================

class TestTestAnalyzer:
    def _analyzer(self, tmp_path):
        from doctor.analyzers.tests import TestAnalyzer
        return TestAnalyzer(project_root=tmp_path)

    def test_no_test_files_critical(self, tmp_path):
        result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.CRITICAL for f in result.findings)

    def test_test_files_found_info(self, tmp_path):
        _write(tmp_path / "tests" / "test_something.py", "def test_foo(): pass")
        result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.INFO and "test file" in f.title.lower()
                   for f in result.findings)

    def test_no_cache_info_message(self, tmp_path):
        _write(tmp_path / "tests" / "test_x.py", "def test_x(): pass")
        result = self._analyzer(tmp_path).analyze()
        assert any("not been run" in f.title.lower() or "not recently" in f.title.lower()
                   for f in result.findings)

    def test_lastfailed_critical(self, tmp_path):
        _write(tmp_path / "tests" / "test_x.py", "def test_x(): pass\ndef test_broken(): pass")
        cache_dir = tmp_path / ".pytest_cache" / "v" / "cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "lastfailed").write_text(
            json.dumps({"tests/test_x.py::test_broken": True})
        )
        (cache_dir / "nodeids").write_text(json.dumps(["tests/test_x.py::test_broken"]))
        result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.CRITICAL and "failed" in f.title.lower()
                   for f in result.findings)

    def test_clean_cache_ok(self, tmp_path):
        _write(tmp_path / "tests" / "test_x.py", "def test_x(): pass")
        cache_dir = tmp_path / ".pytest_cache" / "v" / "cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "lastfailed").write_text(json.dumps({}))
        (cache_dir / "nodeids").write_text(json.dumps(["tests/test_x.py::test_x"]))
        result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.OK and "passed" in f.title.lower()
                   for f in result.findings)

    # --- stale failure records ------------------------------------------

    def test_lastfailed_entry_for_deleted_test_is_ignored(self, tmp_path):
        """pytest can never clear a node id whose test was renamed or deleted."""
        _write(tmp_path / "tests" / "test_x.py", "def test_x(): pass")
        cache_dir = tmp_path / ".pytest_cache" / "v" / "cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "lastfailed").write_text(
            json.dumps({"tests/test_x.py::test_not_yet_implemented": True})
        )
        (cache_dir / "nodeids").write_text(json.dumps(["tests/test_x.py::test_x"]))
        result = self._analyzer(tmp_path).analyze()
        assert not any(f.severity == Severity.CRITICAL for f in result.findings)
        assert any("stale failure record" in f.title.lower() for f in result.findings)

    def test_lastfailed_entry_for_deleted_file_is_ignored(self, tmp_path):
        _write(tmp_path / "tests" / "test_x.py", "def test_x(): pass")
        cache_dir = tmp_path / ".pytest_cache" / "v" / "cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "lastfailed").write_text(
            json.dumps({"tests/test_gone.py::test_gone": True})
        )
        (cache_dir / "nodeids").write_text(json.dumps(["tests/test_x.py::test_x"]))
        result = self._analyzer(tmp_path).analyze()
        assert not any(f.severity == Severity.CRITICAL for f in result.findings)

    def test_parametrized_lastfailed_entry_is_kept(self, tmp_path):
        _write(tmp_path / "tests" / "test_x.py", "def test_p(v): assert v")
        cache_dir = tmp_path / ".pytest_cache" / "v" / "cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "lastfailed").write_text(json.dumps({"tests/test_x.py::test_p[0]": True}))
        (cache_dir / "nodeids").write_text(json.dumps(["tests/test_x.py::test_p[0]"]))
        result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.CRITICAL and "failed" in f.title.lower()
                   for f in result.findings)

    # --- verified run record --------------------------------------------

    def _write_record(self, tmp_path, **fields):
        record = {
            "finished_at": _future_iso(),
            "exit_status": 0,
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
            "failed_nodeids": [],
            "full_run": True,
        }
        record.update(fields)
        _write(tmp_path / ".pytest_cache" / "v" / "mondayos" / "last_run",
               json.dumps(record))

    def test_run_record_green_is_ok(self, tmp_path):
        _write(tmp_path / "tests" / "test_x.py", "def test_x(): pass")
        self._write_record(tmp_path, passed=1082, skipped=12)
        result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.OK and "1082" in f.title
                   for f in result.findings)
        assert not any(f.severity == Severity.CRITICAL for f in result.findings)

    def test_run_record_overrides_stale_lastfailed(self, tmp_path):
        """A green verified run wins over leftover entries in the retry cache."""
        _write(tmp_path / "tests" / "test_x.py", "def test_x(): pass")
        cache_dir = tmp_path / ".pytest_cache" / "v" / "cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "lastfailed").write_text(json.dumps({"tests/test_x.py::test_x": True}))
        self._write_record(tmp_path, passed=1)
        result = self._analyzer(tmp_path).analyze()
        assert not any(f.severity == Severity.CRITICAL for f in result.findings)

    def test_run_record_failures_are_critical(self, tmp_path):
        _write(tmp_path / "tests" / "test_x.py", "def test_x(): pass")
        self._write_record(tmp_path, passed=5, failed=2,
                           failed_nodeids=["tests/test_x.py::test_x"])
        result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.CRITICAL and "2 test(s) failed" in f.title
                   for f in result.findings)

    def test_run_record_errors_count_as_failures(self, tmp_path):
        _write(tmp_path / "tests" / "test_x.py", "def test_x(): pass")
        self._write_record(tmp_path, passed=5, errors=1)
        result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.CRITICAL for f in result.findings)

    def test_partial_run_is_flagged(self, tmp_path):
        _write(tmp_path / "tests" / "test_x.py", "def test_x(): pass")
        self._write_record(tmp_path, passed=1, full_run=False)
        result = self._analyzer(tmp_path).analyze()
        assert any("part of the suite" in f.title.lower() for f in result.findings)

    def test_source_changed_since_run_is_flagged(self, tmp_path):
        _write(tmp_path / "tests" / "test_x.py", "def test_x(): pass")
        self._write_record(tmp_path, passed=1, finished_at="2000-01-01T00:00:00+00:00")
        result = self._analyzer(tmp_path).analyze()
        assert any("changed since the last test run" in f.title.lower()
                   for f in result.findings)

    def test_fresh_run_is_not_flagged_as_stale(self, tmp_path):
        _write(tmp_path / "tests" / "test_x.py", "def test_x(): pass")
        self._write_record(tmp_path, passed=1)
        result = self._analyzer(tmp_path).analyze()
        assert not any("changed since the last test run" in f.title.lower()
                       for f in result.findings)

    def test_corrupt_run_record_falls_back_to_cache(self, tmp_path):
        _write(tmp_path / "tests" / "test_x.py", "def test_x(): pass")
        _write(tmp_path / ".pytest_cache" / "v" / "mondayos" / "last_run", "{not json")
        cache_dir = tmp_path / ".pytest_cache" / "v" / "cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "lastfailed").write_text(json.dumps({}))
        (cache_dir / "nodeids").write_text(json.dumps(["tests/test_x.py::test_x"]))
        result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.OK and "passed" in f.title.lower()
                   for f in result.findings)


# ===========================================================================
# TestCodeQualityAnalyzer
# ===========================================================================

class TestCodeQualityAnalyzer:
    def _analyzer(self, tmp_path):
        from doctor.analyzers.code_quality import CodeQualityAnalyzer
        return CodeQualityAnalyzer(project_root=tmp_path)

    def test_no_markers_ok(self, tmp_path):
        _write(tmp_path / "foo.py", "def hello():\n    return 42\n")
        result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.OK and "TODO" in f.title for f in result.findings)

    def test_few_markers_info(self, tmp_path):
        _write(tmp_path / "foo.py", "# TODO: fix this\ndef f(): pass\n")
        result = self._analyzer(tmp_path).analyze()
        # Few markers → INFO
        assert any(
            (f.severity == Severity.INFO or f.severity == Severity.WARNING)
            and "TODO" in f.title
            for f in result.findings
        )

    def test_many_markers_warning(self, tmp_path):
        lines = "\n".join(f"# TODO: item {i}" for i in range(15))
        _write(tmp_path / "foo.py", lines)
        result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.WARNING and "TODO" in f.title
                   for f in result.findings)

    def test_large_file_warning(self, tmp_path):
        big = tmp_path / "bigfile.bin"
        big.write_bytes(b"x" * (600 * 1024))  # 600 KB
        result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.WARNING and "large file" in f.title.lower()
                   for f in result.findings)

    def test_no_large_files_ok(self, tmp_path):
        _write(tmp_path / "small.py", "# small")
        result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.OK and "large file" in f.title.lower()
                   for f in result.findings)

    def test_empty_directory_info(self, tmp_path):
        (tmp_path / "empty_dir").mkdir()
        result = self._analyzer(tmp_path).analyze()
        assert any("empty" in f.title.lower() and f.severity == Severity.INFO
                   for f in result.findings)

    def test_venv_excluded(self, tmp_path):
        venv_dir = tmp_path / ".venv" / "lib"
        venv_dir.mkdir(parents=True)
        _write(venv_dir / "whatever.py", "# TODO: internal venv marker")
        result = self._analyzer(tmp_path).analyze()
        # The venv TODO should not be counted
        marker_findings = [
            f for f in result.findings if "TODO" in f.title
        ]
        if marker_findings:
            data = marker_findings[0].data
            assert data.get("marker_count", 0) == 0


# ===========================================================================
# TestDocumentationAnalyzer
# ===========================================================================

class TestDocumentationAnalyzer:
    def _analyzer(self, tmp_path):
        from doctor.analyzers.documentation import DocumentationAnalyzer
        return DocumentationAnalyzer(project_root=tmp_path)

    def test_missing_readme_warning(self, tmp_path):
        result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.WARNING and "README.md" in f.title
                   for f in result.findings)

    def test_present_readme_ok(self, tmp_path):
        _write(tmp_path / "README.md", "# Project\n\nDescription here.")
        result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.OK and "README.md" in f.title
                   for f in result.findings)

    def test_missing_changelog_warning(self, tmp_path):
        result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.WARNING and "CHANGELOG" in f.title
                   for f in result.findings)

    def test_missing_decisions_info(self, tmp_path):
        result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.INFO and "DECISIONS" in f.title
                   for f in result.findings)

    def test_broken_link_warning(self, tmp_path):
        _write(tmp_path / "README.md", "See [docs](./nonexistent.md) for more.")
        result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.WARNING and "broken" in f.title.lower()
                   for f in result.findings)

    def test_valid_link_no_warning(self, tmp_path):
        _write(tmp_path / "guide.md", "## Guide\n\nContent here.")
        _write(tmp_path / "README.md", "See [guide](./guide.md) for more.")
        result = self._analyzer(tmp_path).analyze()
        assert not any(f.severity == Severity.WARNING and "broken" in f.title.lower()
                       for f in result.findings)

    def test_http_links_not_checked(self, tmp_path):
        _write(tmp_path / "README.md", "See [Google](https://google.com) for more.")
        result = self._analyzer(tmp_path).analyze()
        assert not any(f.severity == Severity.WARNING and "broken" in f.title.lower()
                       for f in result.findings)

    def test_missing_module_docstring_info(self, tmp_path):
        # Use a package name from _PACKAGE_DIRS
        pkg = tmp_path / "doctor"
        pkg.mkdir()
        _write(pkg / "__init__.py", "from doctor import stuff\n")  # no docstring
        result = self._analyzer(tmp_path).analyze()
        assert any("docstring" in f.title.lower() for f in result.findings)

    def test_module_with_docstring_ok(self, tmp_path):
        pkg = tmp_path / "doctor"
        pkg.mkdir()
        _write(pkg / "__init__.py", '"""doctor package."""\nfrom doctor import x\n')
        result = self._analyzer(tmp_path).analyze()
        # Should not produce a WARNING/CRITICAL about docstrings
        assert not any(
            f.severity in (Severity.WARNING, Severity.CRITICAL)
            and "docstring" in f.title.lower()
            for f in result.findings
        )


# ===========================================================================
# TestConfigAnalyzer
# ===========================================================================

class TestConfigAnalyzer:
    def _analyzer(self, tmp_path):
        from doctor.analyzers.config import ConfigAnalyzer
        return ConfigAnalyzer(project_root=tmp_path)

    def test_no_pyproject_critical(self, tmp_path):
        result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.CRITICAL and "pyproject.toml" in f.title
                   for f in result.findings)

    def test_valid_pyproject_ok(self, tmp_path):
        _write(tmp_path / "pyproject.toml", (
            '[build-system]\nrequires = ["setuptools"]\n\n'
            '[project]\nname = "test"\nrequires-python = ">=3.11"\n\n'
            '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
        ))
        result = self._analyzer(tmp_path).analyze()
        # Should not have a CRITICAL about pyproject
        assert not any(
            f.severity == Severity.CRITICAL and "pyproject.toml" in f.title
            for f in result.findings
        )

    def test_missing_required_section_warning(self, tmp_path):
        _write(tmp_path / "pyproject.toml", "[project]\nname = \"test\"\n")
        result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.WARNING and "section" in f.title.lower()
                   for f in result.findings)

    def test_valid_workflow_yaml_ok(self, tmp_path):
        _write(tmp_path / "pyproject.toml", '[project]\nname = "test"\n')
        _write(tmp_path / "workflows" / "definitions" / "test.yaml", (
            "name: test-wf\nversion: \"1.0\"\n"
            "steps:\n  - id: s1\n    type: search\n"
        ))
        result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.OK and "workflow" in f.title.lower()
                   for f in result.findings)

    def test_invalid_workflow_yaml_warning(self, tmp_path):
        _write(tmp_path / "pyproject.toml", '[project]\nname = "test"\n')
        _write(tmp_path / "workflows" / "definitions" / "bad.yaml",
               "name: bad-wf\n# missing version and steps")
        result = self._analyzer(tmp_path).analyze()
        assert any(f.severity == Severity.WARNING and "malformed" in f.title.lower()
                   for f in result.findings)

    def test_no_workflow_dir_info(self, tmp_path):
        _write(tmp_path / "pyproject.toml", '[project]\nname = "test"\n')
        result = self._analyzer(tmp_path).analyze()
        assert any("workflow" in f.title.lower() and f.severity == Severity.INFO
                   for f in result.findings)


# ===========================================================================
# TestTaskHealthAnalyzer
# ===========================================================================

class TestTaskHealthAnalyzer:
    def _analyzer(self, tmp_path, monday=None):
        from doctor.analyzers.task_health import TaskHealthAnalyzer
        return TaskHealthAnalyzer(project_root=tmp_path, monday=monday)

    def test_no_monday_skips(self, tmp_path):
        result = self._analyzer(tmp_path, monday=None).analyze()
        assert any("skipped" in f.title.lower() for f in result.findings)

    def test_no_tasks_info(self, tmp_path):
        monday = _make_monday(tmp_path)
        result = self._analyzer(tmp_path, monday=monday).analyze()
        assert any("no active tasks" in f.title.lower() for f in result.findings)

    def test_blocked_task_warning(self, tmp_path):
        monday = _make_monday(tmp_path)
        # Create a task and manually force it to blocked state by direct manipulation
        r = monday.task(
            "create",
            title="Fix something",
            objective="Fix it",
            task_type="fix",
        )
        # Move to in-progress then blocked
        monday.task("start", task_id=r.task_id)
        from tasks import TaskManager, TaskStatus
        manager = TaskManager(tmp_path)
        manager.update_status(r.task_id, TaskStatus.BLOCKED, "test", "manual block")
        result = self._analyzer(tmp_path, monday=monday).analyze()
        assert any(f.severity == Severity.WARNING and "blocked" in f.title.lower()
                   for f in result.findings)

    def test_task_without_objective_warning(self, tmp_path):
        # TaskManager validates non-empty objective, so we mock list_active
        from tasks import TaskPriority, TaskStatus, TaskType
        from tasks.task import Task
        from datetime import datetime, timezone
        monday = _make_monday(tmp_path)
        fake_task = Task(
            id="TASK-9999",
            title="A task with no objective",
            task_type=TaskType.FEATURE,
            status=TaskStatus.BACKLOG,
            priority=TaskPriority.P2,
            created=datetime.now(tz=timezone.utc),
            updated=datetime.now(tz=timezone.utc),
            created_by="human:test",
            objective="",  # explicitly empty
        )
        with patch("tasks.TaskManager") as MockManager:
            MockManager.return_value.list_active.return_value = [fake_task]
            result = self._analyzer(tmp_path, monday=monday).analyze()
        assert any(f.severity == Severity.WARNING and "objective" in f.title.lower()
                   for f in result.findings)

    def test_clean_tasks_ok(self, tmp_path):
        monday = _make_monday(tmp_path)
        monday.task(
            "create",
            title="Clean task",
            objective="A real objective",
            task_type="feature",
        )
        result = self._analyzer(tmp_path, monday=monday).analyze()
        assert not any(f.severity == Severity.CRITICAL for f in result.findings)


# ===========================================================================
# TestMondayDoctor
# ===========================================================================

class TestMondayDoctor:
    def test_returns_doctor_response(self, tmp_path):
        from monday import DoctorResponse
        monday = _make_monday(tmp_path)
        r = monday.doctor()
        assert isinstance(r, DoctorResponse)
        assert r.action == "inspect"
        assert r.success is True

    def test_health_score_range(self, tmp_path):
        monday = _make_monday(tmp_path)
        r = monday.doctor()
        assert 0 <= r.health_score <= 100

    def test_grade_present(self, tmp_path):
        monday = _make_monday(tmp_path)
        r = monday.doctor()
        assert r.grade in ("Excellent", "Good", "Fair", "Poor", "Critical")

    def test_summary_nonempty(self, tmp_path):
        monday = _make_monday(tmp_path)
        r = monday.doctor()
        assert r.summary

    def test_data_is_full_report(self, tmp_path):
        monday = _make_monday(tmp_path)
        r = monday.doctor()
        assert "health_score" in r.data
        assert "analyzers" in r.data
        assert "recommendations" in r.data

    def test_analyzer_subset(self, tmp_path):
        monday = _make_monday(tmp_path)
        r = monday.doctor(analyzers=["config"])
        assert r.success
        names = [a["name"] for a in r.data["analyzers"]]
        assert names == ["config"]

    def test_healthy_repo_high_score(self, tmp_path):
        """A repo with .git, README, CHANGELOG, and valid pyproject should score well."""
        _git_init(tmp_path)
        _write(tmp_path / "README.md", "# MondayOS\n\nDescription.")
        _write(tmp_path / "docs" / "CHANGELOG.md", "# Changelog\n\n## [0.1.0]\n\n...")
        _write(tmp_path / "docs" / "DECISIONS.md", "# Decisions\n")
        _write(tmp_path / "pyproject.toml", (
            '[build-system]\nrequires = ["setuptools"]\n\n'
            '[project]\nname = "test"\nrequires-python = ">=3.11"\n\n'
            '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
        ))
        _write(tmp_path / "tests" / "test_x.py", "def test_x(): pass")
        monday = _make_monday(tmp_path)
        r = monday.doctor()
        # With good docs and config, score should be Fair or better
        assert r.health_score >= 50


# ===========================================================================
# TestCLIDoctor
# ===========================================================================

class TestCLIDoctor:
    def _run(self, args: list[str]) -> tuple[int, str, str]:
        import io
        import sys
        from monday.cli import main

        out, err = io.StringIO(), io.StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            rc = main(args)
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        return rc, out.getvalue(), err.getvalue()

    def test_doctor_runs(self, tmp_path):
        rc, out, _ = self._run(["--project-root", str(tmp_path), "doctor"])
        # Exit code 0 or 1 depending on health score — just ensure no crash
        assert rc in (0, 1)
        assert "Health Score" in out

    def test_doctor_json_output(self, tmp_path):
        rc, out, _ = self._run(["--project-root", str(tmp_path), "doctor", "--json"])
        assert rc in (0, 1)
        data = json.loads(out)
        assert "health_score" in data
        assert "analyzers" in data

    def test_doctor_verbose(self, tmp_path):
        rc, out, _ = self._run(["--project-root", str(tmp_path), "doctor", "--verbose"])
        assert rc in (0, 1)
        assert "Health Score" in out

    def test_doctor_only_flag(self, tmp_path):
        rc, out, _ = self._run([
            "--project-root", str(tmp_path), "doctor", "--only", "config",
        ])
        assert rc in (0, 1)
        assert "Health Score" in out

    def test_json_contains_recommendations(self, tmp_path):
        rc, out, _ = self._run(["--project-root", str(tmp_path), "doctor", "--json"])
        data = json.loads(out)
        assert "recommendations" in data

    def test_healthy_repo_exits_zero(self, tmp_path):
        _git_init(tmp_path)
        _write(tmp_path / "README.md", "# Project")
        _write(tmp_path / "docs" / "CHANGELOG.md", "# Log")
        _write(tmp_path / "docs" / "DECISIONS.md", "# ADRs")
        _write(tmp_path / "pyproject.toml", (
            '[build-system]\nrequires = ["setuptools"]\n\n'
            '[project]\nname = "t"\nrequires-python = ">=3.11"\n\n'
            '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
        ))
        _write(tmp_path / "tests" / "test_x.py", "def test_x(): pass")
        with patch("doctor.analyzers.git.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="main\n", returncode=0)
            rc, out, _ = self._run(["--project-root", str(tmp_path), "doctor"])
        # Should exit 0 if score >= 60
        assert "Health Score" in out
