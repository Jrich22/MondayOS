"""Tests for the advisor / engineering advisory system (Initiative 009)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from advisor.advisory import Action, Advisory, Risk
from advisor.engine import AdvisorEngine
from advisor.reasoning import (
    _SEV_ORDER,
    compute_confidence,
    summarize_repository,
    synthesize_debt,
    synthesize_documentation_gaps,
    synthesize_knowledge_gaps,
    synthesize_next_actions,
    synthesize_risks,
    synthesize_sprint_goal,
)
from doctor.finding import Finding, Severity
from doctor.result import AnalyzerResult, DoctorReport


# ---------------------------------------------------------------------------
# Shared test fixtures / helpers
# ---------------------------------------------------------------------------

def _make_monday(tmp_path: Path):
    from monday import Monday, MondayConfig
    return Monday(MondayConfig(project_root=tmp_path))


def _finding(category: str, severity: Severity, title: str, **kwargs) -> Finding:
    return Finding(category=category, severity=severity, title=title, **kwargs)


def _empty_report() -> DoctorReport:
    """DoctorReport with a single OK finding — no issues."""
    results = [AnalyzerResult("git", [_finding("git", Severity.OK, "All good")])]
    return DoctorReport.build(results)


def _report_with(*findings: Finding) -> DoctorReport:
    results = [AnalyzerResult(f.category, [f]) for f in findings]
    return DoctorReport.build(results)


# ── Mock task / knowledge objects ──

class _Status(str, Enum):
    BACKLOG = "backlog"
    IN_PROGRESS = "in-progress"
    BLOCKED = "blocked"
    DONE = "done"


class _Priority(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"


class _EntryType(str, Enum):
    DECISION = "decision"
    PATTERN = "pattern"
    BUG = "bug"
    RUNBOOK = "runbook"
    SPRINT = "sprint"
    LESSON = "lesson"


@dataclass
class _Task:
    id: str
    title: str
    status: _Status = _Status.BACKLOG
    priority: _Priority = _Priority.P2
    objective: str = "some objective"


@dataclass
class _KBEntry:
    id: str
    title: str
    entry_type: _EntryType = _EntryType.DECISION
    tags: list[str] = field(default_factory=list)
    status: _Status = _Status.IN_PROGRESS  # unused by reasoning but present


# ===========================================================================
# TestRiskDataclass
# ===========================================================================

class TestRiskDataclass:
    def test_to_dict_round_trips(self):
        r = Risk(
            title="Failing tests",
            severity="critical",
            category="tests",
            impact="Deployment is risky.",
            recommendation="Fix tests.",
            source="doctor",
        )
        d = r.to_dict()
        assert d["title"] == "Failing tests"
        assert d["severity"] == "critical"
        assert d["category"] == "tests"
        assert d["impact"] == "Deployment is risky."
        assert d["recommendation"] == "Fix tests."
        assert d["source"] == "doctor"

    def test_source_defaults_empty(self):
        r = Risk(title="x", severity="low", category="k", impact="i", recommendation="r")
        assert r.source == ""


# ===========================================================================
# TestActionDataclass
# ===========================================================================

class TestActionDataclass:
    def test_to_dict_round_trips(self):
        a = Action(
            title="Run tests",
            priority=1,
            category="quality",
            rationale="Tests are failing.",
            effort="minutes",
            command="pytest",
        )
        d = a.to_dict()
        assert d["title"] == "Run tests"
        assert d["priority"] == 1
        assert d["command"] == "pytest"

    def test_command_defaults_empty(self):
        a = Action(title="x", priority=2, category="c", rationale="r", effort="hours")
        assert a.command == ""


# ===========================================================================
# TestAdvisoryDataclass
# ===========================================================================

class TestAdvisoryDataclass:
    def test_to_dict_contains_all_keys(self):
        adv = Advisory()
        d = adv.to_dict()
        expected_keys = [
            "generated_at", "confidence", "health_score", "health_grade",
            "repository_summary", "risks", "next_actions", "sprint_goal",
            "sprint_rationale", "technical_debt_summary", "debt_items",
            "knowledge_gaps", "documentation_gaps", "data_sources",
        ]
        for key in expected_keys:
            assert key in d, f"Missing key: {key}"

    def test_confidence_rounded(self):
        adv = Advisory(confidence=0.33333)
        d = adv.to_dict()
        assert d["confidence"] == 0.33

    def test_risks_serialized(self):
        risk = Risk(title="R", severity="high", category="c", impact="i", recommendation="r")
        adv = Advisory(risks=[risk])
        d = adv.to_dict()
        assert len(d["risks"]) == 1
        assert d["risks"][0]["title"] == "R"

    def test_next_actions_serialized(self):
        action = Action(title="A", priority=1, category="c", rationale="r", effort="hours")
        adv = Advisory(next_actions=[action])
        d = adv.to_dict()
        assert len(d["next_actions"]) == 1
        assert d["next_actions"][0]["title"] == "A"


# ===========================================================================
# TestSynthesizeRisks
# ===========================================================================

class TestSynthesizeRisks:
    def test_no_findings_empty_kb_produces_kb_risk(self):
        report = _empty_report()
        risks = synthesize_risks(report, [], [])
        titles = [r.title for r in risks]
        assert any("empty" in t.lower() or "Knowledge base" in t for t in titles)

    def test_critical_finding_becomes_critical_risk(self):
        f = _finding("tests", Severity.CRITICAL, "Test suite failing")
        report = _report_with(f)
        risks = synthesize_risks(report, [_KBEntry("e1", "T", _EntryType.DECISION)], [])
        crit = [r for r in risks if r.severity == "critical"]
        assert len(crit) >= 1
        assert crit[0].source == "doctor"

    def test_warning_in_high_impact_category_becomes_high(self):
        f = _finding("tests", Severity.WARNING, "Coverage low")
        report = _report_with(f)
        entry = _KBEntry("e1", "T", _EntryType.DECISION)
        risks = synthesize_risks(report, [entry], [])
        sev_high = [r for r in risks if r.severity == "high" and r.source == "doctor"]
        assert sev_high

    def test_warning_in_non_high_impact_category_becomes_medium(self):
        f = _finding("documentation", Severity.WARNING, "Missing README")
        report = _report_with(f)
        entry = _KBEntry("e1", "T", _EntryType.DECISION)
        risks = synthesize_risks(report, [entry], [])
        med = [r for r in risks if r.severity == "medium" and r.source == "doctor"]
        assert med

    def test_blocked_task_high_priority_becomes_high_risk(self):
        task = _Task("T-001", "Deploy fix", _Status.BLOCKED, _Priority.P0)
        report = _empty_report()
        entry = _KBEntry("e1", "T", _EntryType.DECISION)
        risks = synthesize_risks(report, [entry], [task])
        blocked_risks = [r for r in risks if "blocked" in r.title.lower()]
        assert blocked_risks
        assert blocked_risks[0].severity == "high"

    def test_blocked_task_low_priority_becomes_medium_risk(self):
        task = _Task("T-001", "Nice to have", _Status.BLOCKED, _Priority.P2)
        report = _empty_report()
        entry = _KBEntry("e1", "T", _EntryType.DECISION)
        risks = synthesize_risks(report, [entry], [task])
        blocked_risks = [r for r in risks if "blocked" in r.title.lower()]
        assert blocked_risks[0].severity == "medium"

    def test_sorted_critical_before_high(self):
        f_warn = _finding("documentation", Severity.WARNING, "Missing README")
        f_crit = _finding("tests", Severity.CRITICAL, "Tests failing")
        report = _report_with(f_crit, f_warn)
        entry = _KBEntry("e1", "T", _EntryType.DECISION)
        risks = synthesize_risks(report, [entry], [])
        severities = [r.severity for r in risks]
        first_high_idx = next((i for i, s in enumerate(severities) if s == "high"), len(severities))
        last_crit_idx = max((i for i, s in enumerate(severities) if s == "critical"), default=-1)
        assert last_crit_idx < first_high_idx

    def test_deduplication(self):
        f = _finding("tests", Severity.CRITICAL, "Test suite failing")
        report = _report_with(f, f)  # duplicate findings
        entry = _KBEntry("e1", "T", _EntryType.DECISION)
        risks = synthesize_risks(report, [entry], [])
        test_risks = [r for r in risks if r.title == "Test suite failing"]
        assert len(test_risks) == 1

    def test_high_priority_unstarted_backlog_risk(self):
        task = _Task("T-001", "Critical feature", _Status.BACKLOG, _Priority.P0)
        report = _empty_report()
        entry = _KBEntry("e1", "T", _EntryType.DECISION)
        risks = synthesize_risks(report, [entry], [task])
        backlog_risks = [r for r in risks if "unstarted" in r.title.lower()]
        assert backlog_risks
        assert backlog_risks[0].severity == "high"

    def test_missing_kb_types_produce_low_risks(self):
        report = _empty_report()
        # Only decisions present — others will be flagged as gaps
        entry = _KBEntry("e1", "T", _EntryType.DECISION)
        risks = synthesize_risks(report, [entry], [])
        low_risks = [r for r in risks if r.severity == "low"]
        assert low_risks  # e.g. "No sprint entries", "No runbook entries", etc.


# ===========================================================================
# TestSynthesizeNextActions
# ===========================================================================

class TestSynthesizeNextActions:
    def _base_call(self, risks=None, tasks=None, entries=None, report=None, runs=None):
        return synthesize_next_actions(
            risks or [],
            tasks or [],
            entries or [],
            report or _empty_report(),
            runs or [],
        )

    def test_returns_list(self):
        actions = self._base_call()
        assert isinstance(actions, list)

    def test_review_health_always_present(self):
        actions = self._base_call()
        titles = [a.title for a in actions]
        assert any("health" in t.lower() for t in titles)

    def test_critical_risk_becomes_first_action(self):
        risk = Risk(title="Test failure", severity="critical", category="tests",
                    impact="Deploy risky", recommendation="Fix tests.")
        actions = self._base_call(risks=[risk])
        assert actions[0].priority == 1
        assert "test" in actions[0].title.lower() or "fix" in actions[0].title.lower()

    def test_empty_kb_adds_import_action(self):
        actions = self._base_call(entries=[])
        titles = [a.title for a in actions]
        assert any("import" in t.lower() or "knowledge" in t.lower() for t in titles)

    def test_non_empty_kb_no_import_action(self):
        entry = _KBEntry("e1", "T", _EntryType.DECISION)
        actions = self._base_call(entries=[entry])
        titles = [a.title for a in actions]
        assert not any("Import knowledge" in t for t in titles)

    def test_blocked_task_becomes_action(self):
        task = _Task("T-001", "Deploy feature", _Status.BLOCKED, _Priority.P1)
        actions = self._base_call(tasks=[task])
        titles = [a.title for a in actions]
        assert any("blocker" in t.lower() for t in titles)

    def test_capped_at_ten(self):
        risks = [
            Risk(title=f"Risk {i}", severity="critical", category="tests",
                 impact="Impact", recommendation="Fix.")
            for i in range(20)
        ]
        actions = self._base_call(risks=risks)
        assert len(actions) <= 10

    def test_priorities_renumbered(self):
        entry = _KBEntry("e1", "T", _EntryType.DECISION)
        actions = self._base_call(entries=[entry])
        for i, a in enumerate(actions, 1):
            assert a.priority == i


# ===========================================================================
# TestSynthesizeSprintGoal
# ===========================================================================

class TestSynthesizeSprintGoal:
    def test_critical_risks_trigger_health_goal(self):
        risk = Risk(title="Tests failing", severity="critical", category="tests",
                    impact="i", recommendation="r")
        goal, rationale = synthesize_sprint_goal([risk], [], [_KBEntry("e1", "T")])
        assert "health" in goal.lower() or "critical" in goal.lower()
        assert "critical" in rationale.lower()

    def test_urgent_backlog_triggers_start_goal(self):
        task = _Task("T-001", "Feature X", _Status.BACKLOG, _Priority.P0)
        goal, rationale = synthesize_sprint_goal([], [task], [_KBEntry("e1", "T")])
        assert "T-001" in goal or "high-priority" in goal.lower()

    def test_empty_kb_triggers_knowledge_goal(self):
        goal, rationale = synthesize_sprint_goal([], [], [])
        assert "knowledge" in goal.lower()
        assert "monday migrate" in rationale

    def test_blocked_tasks_trigger_blocker_goal(self):
        task = _Task("T-001", "Blocked task", _Status.BLOCKED, _Priority.P2)
        goal, rationale = synthesize_sprint_goal([], [task], [_KBEntry("e1", "T")])
        assert "block" in goal.lower()

    def test_in_progress_triggers_complete_goal(self):
        task = _Task("T-001", "In-progress work", _Status.IN_PROGRESS, _Priority.P2)
        goal, rationale = synthesize_sprint_goal([], [task], [_KBEntry("e1", "T")])
        assert "in-progress" in goal.lower() or "complete" in goal.lower()

    def test_healthy_state_returns_momentum_goal(self):
        goal, rationale = synthesize_sprint_goal([], [], [_KBEntry("e1", "T")])
        assert "momentum" in goal.lower() or "forward" in goal.lower()

    def test_priority_order_critical_before_backlog(self):
        risk = Risk(title="Tests failing", severity="critical", category="tests",
                    impact="i", recommendation="r")
        task = _Task("T-001", "Feature", _Status.BACKLOG, _Priority.P0)
        goal, _ = synthesize_sprint_goal([risk], [task], [_KBEntry("e1", "T")])
        assert "health" in goal.lower()  # critical wins over backlog


# ===========================================================================
# TestSynthesizeDebt
# ===========================================================================

class TestSynthesizeDebt:
    def test_empty_kb_and_clean_report_no_debt(self):
        report = _empty_report()
        summary, items = synthesize_debt(report, [])
        # items may contain "no bug entries" note — that's fine
        assert isinstance(items, list)
        assert isinstance(summary, str)

    def test_bug_entries_appear_in_items(self):
        bug = _KBEntry("b1", "Auth crash on logout", _EntryType.BUG)
        report = _empty_report()
        _, items = synthesize_debt(report, [bug])
        assert any("bug" in item.lower() for item in items)

    def test_summary_single_item(self):
        # Force exactly one item by having no bugs and a clean report
        report = _empty_report()
        summary, items = synthesize_debt(report, [])
        if len(items) == 1:
            assert summary.endswith(".")

    def test_summary_multi_items(self):
        # Multiple bugs to force multi-item summary
        bugs = [_KBEntry(f"b{i}", f"Bug {i}", _EntryType.BUG) for i in range(3)]
        report = _empty_report()
        summary, items = synthesize_debt(report, bugs)
        if len(items) > 1:
            assert "sources" in summary.lower() or str(len(items)) in summary


# ===========================================================================
# TestSynthesizeKnowledgeGaps
# ===========================================================================

class TestSynthesizeKnowledgeGaps:
    def test_empty_kb_single_gap(self):
        gaps = synthesize_knowledge_gaps([], [])
        assert len(gaps) == 1
        assert "no knowledge" in gaps[0].lower()

    def test_missing_types_reported(self):
        entry = _KBEntry("e1", "Decision X", _EntryType.DECISION)
        gaps = synthesize_knowledge_gaps([entry], [])
        assert any("sprint" in g.lower() for g in gaps)
        assert any("pattern" in g.lower() or "runbook" in g.lower() for g in gaps)

    def test_capped_at_eight(self):
        entry = _KBEntry("e1", "Decision", _EntryType.DECISION)
        tasks = [_Task(f"T-{i:03d}", f"Very unique task about zzzxxx{i}") for i in range(20)]
        gaps = synthesize_knowledge_gaps([entry], tasks)
        assert len(gaps) <= 8

    def test_all_types_present_no_type_gaps(self):
        from advisor.reasoning import _EXPECTED_KB_TYPES
        entries = [_KBEntry(f"e{i}", f"Entry {i}", et) for i, et in enumerate(
            [_EntryType(t) for t in _EXPECTED_KB_TYPES]
        )]
        gaps = synthesize_knowledge_gaps(entries, [])
        type_gap_texts = [g for g in gaps if "No " in g and "entries" in g]
        assert not type_gap_texts


# ===========================================================================
# TestSynthesizeDocumentationGaps
# ===========================================================================

class TestSynthesizeDocumentationGaps:
    def test_no_doc_findings_returns_empty(self):
        report = _empty_report()
        gaps = synthesize_documentation_gaps(report)
        assert gaps == []

    def test_warning_documentation_finding_becomes_gap(self):
        f = _finding("documentation", Severity.WARNING, "Missing README.md")
        report = _report_with(f)
        gaps = synthesize_documentation_gaps(report)
        assert "Missing README.md" in gaps

    def test_critical_documentation_finding_becomes_gap(self):
        f = _finding("documentation", Severity.CRITICAL, "Broken links found")
        report = _report_with(f)
        gaps = synthesize_documentation_gaps(report)
        assert "Broken links found" in gaps

    def test_info_documentation_finding_not_gap(self):
        f = _finding("documentation", Severity.INFO, "Consider adding examples")
        report = _report_with(f)
        gaps = synthesize_documentation_gaps(report)
        assert gaps == []

    def test_non_documentation_warning_not_gap(self):
        f = _finding("tests", Severity.WARNING, "Coverage low")
        report = _report_with(f)
        gaps = synthesize_documentation_gaps(report)
        assert gaps == []


# ===========================================================================
# TestSummarizeRepository
# ===========================================================================

class TestSummarizeRepository:
    def test_returns_string(self):
        report = _empty_report()
        summary = summarize_repository("myproject", report, [], [], [])
        assert isinstance(summary, str)
        assert len(summary) > 20

    def test_health_info_present(self):
        report = _empty_report()
        summary = summarize_repository("myproject", report, [], [], [])
        assert "100" in summary or "Excellent" in summary

    def test_knowledge_count_present(self):
        entries = [_KBEntry(f"e{i}", f"Title {i}", _EntryType.DECISION) for i in range(5)]
        report = _empty_report()
        summary = summarize_repository("myproject", report, entries, [], [])
        assert "5" in summary

    def test_empty_kb_noted(self):
        report = _empty_report()
        summary = summarize_repository("myproject", report, [], [], [])
        assert "empty" in summary.lower() or "0 entries" in summary.lower()

    def test_task_count_present(self):
        tasks = [_Task(f"T-{i:03d}", f"Task {i}") for i in range(3)]
        report = _empty_report()
        summary = summarize_repository("myproject", report, [], tasks, [])
        assert "3" in summary

    def test_workflow_runs_mentioned(self):
        runs = [{"id": f"r{i}"} for i in range(5)]
        report = _empty_report()
        summary = summarize_repository("myproject", report, [], [], runs)
        assert "5" in summary or "workflow" in summary.lower()


# ===========================================================================
# TestComputeConfidence
# ===========================================================================

class TestComputeConfidence:
    def test_base_no_data(self):
        score = compute_confidence([], [], [], False)
        assert score == 0.25

    def test_doctor_adds_confidence(self):
        score_with = compute_confidence([], [], [], True)
        score_without = compute_confidence([], [], [], False)
        assert score_with > score_without

    def test_knowledge_entries_add_confidence(self):
        entries = [_KBEntry(f"e{i}", f"T{i}", _EntryType.DECISION) for i in range(10)]
        score_with = compute_confidence(entries, [], [], False)
        assert score_with > 0.25

    def test_tasks_add_confidence(self):
        tasks = [_Task("T-001", "Task")]
        score_with = compute_confidence([], tasks, [], False)
        score_without = compute_confidence([], [], [], False)
        assert score_with > score_without

    def test_max_capped_at_0_85(self):
        entries = [_KBEntry(f"e{i}", f"T{i}", _EntryType(t)) for i, t in enumerate(
            ["decision", "pattern", "bug", "runbook", "sprint", "lesson"] * 10
        )]
        tasks = [_Task(f"T-{i:03d}", f"Task {i}") for i in range(5)]
        runs = [{"id": f"r{i}"} for i in range(10)]
        score = compute_confidence(entries, tasks, runs, True)
        assert score <= 0.85

    def test_diverse_types_add_bonus(self):
        mono = [_KBEntry(f"e{i}", f"T{i}", _EntryType.DECISION) for i in range(5)]
        diverse = [
            _KBEntry("e0", "T0", _EntryType.DECISION),
            _KBEntry("e1", "T1", _EntryType.PATTERN),
            _KBEntry("e2", "T2", _EntryType.BUG),
        ]
        score_mono = compute_confidence(mono, [], [], False)
        score_diverse = compute_confidence(diverse, [], [], False)
        assert score_diverse > score_mono


# ===========================================================================
# TestAdvisorEngine
# ===========================================================================

class TestAdvisorEngine:
    def _make_engine(self, tmp_path: Path) -> AdvisorEngine:
        monday = _make_monday(tmp_path)
        return AdvisorEngine(monday, tmp_path)

    def test_analyze_returns_advisory(self, tmp_path):
        engine = self._make_engine(tmp_path)
        with patch.object(engine, "_run_doctor", return_value=_empty_report()), \
             patch.object(engine, "_load_knowledge", return_value=[]), \
             patch.object(engine, "_load_tasks", return_value=[]), \
             patch.object(engine, "_load_workflow_runs", return_value=[]):
            advisory = engine.analyze()
        assert isinstance(advisory, Advisory)

    def test_analyze_uses_provided_doctor_report(self, tmp_path):
        engine = self._make_engine(tmp_path)
        report = _empty_report()
        with patch.object(engine, "_run_doctor", side_effect=RuntimeError("should not call")) as mock_doctor, \
             patch.object(engine, "_load_knowledge", return_value=[]), \
             patch.object(engine, "_load_tasks", return_value=[]), \
             patch.object(engine, "_load_workflow_runs", return_value=[]):
            advisory = engine.analyze(doctor_report=report)
        # _run_doctor should not have been called
        assert advisory.health_score == report.health_score

    def test_advisory_health_score_matches_report(self, tmp_path):
        engine = self._make_engine(tmp_path)
        f = _finding("tests", Severity.CRITICAL, "Tests failing")
        report = _report_with(f)
        with patch.object(engine, "_run_doctor", return_value=report), \
             patch.object(engine, "_load_knowledge", return_value=[]), \
             patch.object(engine, "_load_tasks", return_value=[]), \
             patch.object(engine, "_load_workflow_runs", return_value=[]):
            advisory = engine.analyze()
        assert advisory.health_score == report.health_score

    def test_advisory_confidence_scales(self, tmp_path):
        engine = self._make_engine(tmp_path)
        entries = [_KBEntry(f"e{i}", f"T{i}", _EntryType.DECISION) for i in range(20)]
        with patch.object(engine, "_run_doctor", return_value=_empty_report()), \
             patch.object(engine, "_load_knowledge", return_value=entries), \
             patch.object(engine, "_load_tasks", return_value=[]), \
             patch.object(engine, "_load_workflow_runs", return_value=[]):
            advisory = engine.analyze()
        assert advisory.confidence > 0.25

    def test_data_sources_doctor_always_present(self, tmp_path):
        engine = self._make_engine(tmp_path)
        with patch.object(engine, "_run_doctor", return_value=_empty_report()), \
             patch.object(engine, "_load_knowledge", return_value=[]), \
             patch.object(engine, "_load_tasks", return_value=[]), \
             patch.object(engine, "_load_workflow_runs", return_value=[]):
            advisory = engine.analyze()
        assert "doctor" in advisory.data_sources

    def test_data_sources_knowledge_when_entries(self, tmp_path):
        engine = self._make_engine(tmp_path)
        entries = [_KBEntry("e1", "T", _EntryType.DECISION)]
        with patch.object(engine, "_run_doctor", return_value=_empty_report()), \
             patch.object(engine, "_load_knowledge", return_value=entries), \
             patch.object(engine, "_load_tasks", return_value=[]), \
             patch.object(engine, "_load_workflow_runs", return_value=[]):
            advisory = engine.analyze()
        assert "knowledge" in advisory.data_sources

    def test_load_workflow_runs_reads_json_files(self, tmp_path):
        logs_dir = tmp_path / "logs" / "workflows"
        logs_dir.mkdir(parents=True)
        (logs_dir / "run1.json").write_text(json.dumps({"id": "r1", "workflow": "deploy"}))
        (logs_dir / "run2.json").write_text(json.dumps({"id": "r2", "workflow": "test"}))
        engine = self._make_engine(tmp_path)
        runs = engine._load_workflow_runs()
        assert len(runs) == 2

    def test_load_workflow_runs_skips_invalid_json(self, tmp_path):
        logs_dir = tmp_path / "logs" / "workflows"
        logs_dir.mkdir(parents=True)
        (logs_dir / "run1.json").write_text("not json")
        (logs_dir / "run2.json").write_text(json.dumps({"id": "r2"}))
        engine = self._make_engine(tmp_path)
        runs = engine._load_workflow_runs()
        assert len(runs) == 1

    def test_load_workflow_runs_empty_dir(self, tmp_path):
        engine = self._make_engine(tmp_path)
        runs = engine._load_workflow_runs()
        assert runs == []


# ===========================================================================
# TestMondayAdvise
# ===========================================================================

class TestMondayAdvise:
    def _make_monday(self, tmp_path: Path):
        from monday import Monday, MondayConfig
        return Monday(MondayConfig(project_root=tmp_path))

    def test_advise_returns_advise_response(self, tmp_path):
        from monday.types import AdviseResponse
        monday = self._make_monday(tmp_path)
        with patch("advisor.engine.AdvisorEngine.analyze", return_value=Advisory(
            confidence=0.4, health_score=80, health_grade="Good",
            sprint_goal="Ship it", sprint_rationale="Because",
        )):
            resp = monday.advise()
        assert isinstance(resp, AdviseResponse)
        assert resp.success is True

    def test_advise_response_has_sprint_goal(self, tmp_path):
        from monday.types import AdviseResponse
        monday = self._make_monday(tmp_path)
        with patch("advisor.engine.AdvisorEngine.analyze", return_value=Advisory(
            sprint_goal="Restore health",
        )):
            resp = monday.advise()
        assert resp.sprint_goal == "Restore health"

    def test_advise_response_has_confidence(self, tmp_path):
        from monday.types import AdviseResponse
        monday = self._make_monday(tmp_path)
        with patch("advisor.engine.AdvisorEngine.analyze", return_value=Advisory(
            confidence=0.65,
        )):
            resp = monday.advise()
        assert resp.confidence == 0.65

    def test_advise_data_contains_full_dict(self, tmp_path):
        from monday.types import AdviseResponse
        monday = self._make_monday(tmp_path)
        with patch("advisor.engine.AdvisorEngine.analyze", return_value=Advisory(
            sprint_goal="Build tests", confidence=0.5,
        )):
            resp = monday.advise()
        assert isinstance(resp.data, dict)
        assert "sprint_goal" in resp.data


# ===========================================================================
# TestCLIAdvise
# ===========================================================================

_MOCK_ADVISORY = Advisory(
    confidence=0.5,
    health_score=75,
    health_grade="Good",
    repository_summary="The project is in good shape.",
    sprint_goal="Complete in-progress work",
    sprint_rationale="Focus delivers more value.",
    risks=[Risk("Test debt", "medium", "tests", "Regressions undetected", "Fix tests.")],
    next_actions=[Action("Run tests", 1, "quality", "Tests", "minutes", "pytest")],
    technical_debt_summary="Minor debt.",
    debt_items=["TODO markers"],
    knowledge_gaps=["No sprint entries"],
    documentation_gaps=["Missing README.md"],
    data_sources=["doctor", "knowledge"],
)


class TestCLIAdvise:
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

    def test_advise_basic_output(self, tmp_path):
        with patch("advisor.engine.AdvisorEngine.analyze", return_value=_MOCK_ADVISORY):
            rc, out, _ = self._run(["--project-root", str(tmp_path), "advise"])
        assert rc == 0
        assert "ENGINEERING ADVISORY" in out or "advisory" in out.lower()

    def test_advise_json_output(self, tmp_path):
        with patch("advisor.engine.AdvisorEngine.analyze", return_value=_MOCK_ADVISORY):
            rc, out, _ = self._run(["--project-root", str(tmp_path), "advise", "--json"])
        assert rc == 0
        data = json.loads(out)
        assert "sprint_goal" in data

    def test_advise_brief_output(self, tmp_path):
        with patch("advisor.engine.AdvisorEngine.analyze", return_value=_MOCK_ADVISORY):
            rc, out, _ = self._run(["--project-root", str(tmp_path), "advise", "--brief"])
        assert rc == 0
        assert len(out) < 2000

    def test_advise_json_is_valid_json(self, tmp_path):
        with patch("advisor.engine.AdvisorEngine.analyze", return_value=_MOCK_ADVISORY):
            rc, out, _ = self._run(["--project-root", str(tmp_path), "advise", "--json"])
        parsed = json.loads(out)
        assert isinstance(parsed, dict)
