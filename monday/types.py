"""
Typed response objects for all Monday public API methods.

These are the stable public contracts — their field names and types do not
change between internal implementation phases. When implementations are added,
they populate these same types; callers do not change.

Design rule: every field has a default so responses can always be constructed
even from a stub. Callers should check content, not presence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AskResponse:
    """
    Response from Monday.ask().

    Attributes:
        answer:                 Direct answer text synthesized from internal knowledge.
        sources:                IDs of all knowledge entries and tasks consulted.
        model_used:             Engine identifier. "monday-reasoning/1.0" for internal
                                reasoning; a model name when an LLM is wired in.
        confidence:             Estimated confidence, 0.0–1.0. 0.0 means no evidence
                                found; 0.95 is the practical maximum without LLM validation.
        task_id:                ID of a task created to fulfil the request, if any.
        supporting_entries:     Ranked knowledge entries (non-decision) that support
                                the answer. Each is a dict with id, title, entry_type,
                                summary, tags, components, confidence.
        related_tasks:          Active tasks related to the question topic.
        related_decisions:      Decision/ADR entries specifically matched.
        suggested_next_actions: Actionable follow-up calls the user can make immediately.
    """

    answer: str
    sources: list[str] = field(default_factory=list)
    model_used: str = ""
    confidence: float = 0.0
    task_id: str | None = None
    supporting_entries: list[dict[str, Any]] = field(default_factory=list)
    related_tasks: list[dict[str, Any]] = field(default_factory=list)
    related_decisions: list[dict[str, Any]] = field(default_factory=list)
    suggested_next_actions: list[str] = field(default_factory=list)


@dataclass
class LearnResponse:
    """
    Response from Monday.learn().

    Attributes:
        entry_id:   The ID of the created knowledge entry (e.g. PAT-0001).
        accepted:   True if the entry was successfully written.
        entry_type: The entry type that was used (bug, decision, pattern, runbook).
        message:    Human-readable status or error message.
    """

    entry_id: str
    accepted: bool
    entry_type: str
    message: str


@dataclass
class SearchResponse:
    """
    Response from Monday.search().

    Attributes:
        query:           The original query string.
        results:         Ranked list of search results. Each item is a dict
                         until SearchResult is surfaced in the public API.
        total_found:     Total number of matches (may exceed len(results) if
                         limited by the query's limit parameter).
        sources_queried: Which data sources were searched.
    """

    query: str
    results: list[dict[str, Any]] = field(default_factory=list)
    total_found: int = 0
    sources_queried: list[str] = field(default_factory=list)


@dataclass
class TaskResponse:
    """
    Response from Monday.task().

    Attributes:
        action:   The action that was requested (create, get, list, update, complete).
        success:  True if the action completed without error.
        task_id:  ID of the affected task, if applicable.
        data:     Action-specific payload (e.g. the Task object as a dict, or a list).
        message:  Human-readable status or error message.
    """

    action: str
    success: bool
    task_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""


@dataclass
class WorkflowResponse:
    """
    Response from Monday.workflow().

    Attributes:
        action:        The action requested (list, show, run).
        success:       True if the action completed without error.
        workflow_name: Name of the workflow acted on (empty for list).
        execution_id:  ID of the workflow execution (run action only).
        status:        Workflow execution status (run action only).
        data:          Action-specific payload.
        message:       Human-readable status or error message.
    """

    action: str
    success: bool
    workflow_name: str = ""
    execution_id: str = ""
    status: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""


@dataclass
class AdviseResponse:
    """
    Response from Monday.advise().

    Attributes:
        action:           Always "advise".
        success:          True if the advisory completed without internal errors.
        confidence:       0.0–1.0 confidence in the analysis (scales with data richness).
        sprint_goal:      Recommended sprint focus.
        risks:            Top engineering risks, ranked by severity.
        next_actions:     Highest-value next actions, ranked.
        repository_summary: One-paragraph state-of-the-project narrative.
        data:             Full Advisory as a dict (for --json output).
        message:          Status or error message.
    """

    action: str
    success: bool
    confidence: float = 0.0
    sprint_goal: str = ""
    risks: list[dict[str, Any]] = field(default_factory=list)
    next_actions: list[dict[str, Any]] = field(default_factory=list)
    repository_summary: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""


@dataclass
class DoctorResponse:
    """
    Response from Monday.doctor().

    Attributes:
        action:         Always "inspect".
        success:        True if the inspection completed without internal errors.
        health_score:   0–100 composite health score.
        grade:          Human label: Excellent / Good / Fair / Poor / Critical.
        summary:        One-line summary (e.g. "3 critical, 2 warnings").
        recommendations: Ranked list of actionable recommendations.
        data:           Full DoctorReport as a dict (for --json output).
        message:        Status or error message.
    """

    action: str
    success: bool
    health_score: int = 0
    grade: str = ""
    summary: str = ""
    recommendations: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""


@dataclass
class MigrateResponse:
    """
    Response from Monday.migrate().

    Attributes:
        action:             The action requested (list-sources, run, rollback).
        success:            True if the action completed without error.
        dry_run:            True if the run was a dry-run (no entries written).
        run_id:             UUID of this migration run (empty for list-sources).
        sources_processed:  Source names that were processed.
        candidates_found:   Total candidates found across all sources.
        imported_count:     Number of entries imported (or would-import on dry-run).
        skipped_count:      Number of candidates skipped.
        failed_count:       Number of candidates that failed.
        data:               Action-specific payload (source list, import report dict).
        message:            Human-readable status or error message.
    """

    action: str
    success: bool
    dry_run: bool = False
    run_id: str = ""
    sources_processed: list[str] = field(default_factory=list)
    candidates_found: int = 0
    imported_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""


@dataclass
class ProjectResponse:
    """
    Response from Monday.project().

    Attributes:
        action:      The action requested (register, list, get, remove).
        success:     True if the action completed without error.
        project_name: Name of the project acted on (empty for list).
        data:        Action-specific payload (entry dict or list of entries).
        message:     Human-readable status or error message.
    """

    action: str
    success: bool
    project_name: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""


@dataclass
class OnboardResponse:
    """
    Response from Monday.onboard().

    Attributes:
        action:           Always "onboard".
        success:          True if all onboarding steps completed without error.
        project_name:     Name of the project that was onboarded.
        migrate_summary:  Human-readable migration result.
        health_score:     Repository health score (0–100).
        grade:            Health grade (Excellent / Good / Fair / Poor / Critical).
        sprint_goal:      Recommended sprint goal from the advisor.
        confidence:       Advisory confidence score (0.0–1.0).
        report_path:      Absolute path to the generated onboarding report.
        data:             Full composite payload (migrate + doctor + advisory dicts).
        message:          Status or error message.
    """

    action: str
    success: bool
    project_name: str = ""
    migrate_summary: str = ""
    health_score: int = 0
    grade: str = ""
    sprint_goal: str = ""
    confidence: float = 0.0
    report_path: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""


@dataclass
class ModuleStatus:
    """Status of a single internal MondayOS module."""

    name: str
    available: bool
    initialized: bool


@dataclass
class StatusResponse:
    """
    Response from Monday.status().

    Attributes:
        healthy:        True if all core modules are initialized and responding.
        version:        MondayOS version string.
        session_id:     The current session ID.
        modules:        Per-module availability and initialization status.
        uptime_seconds: Seconds since this Monday instance was created.
    """

    healthy: bool
    version: str
    session_id: str
    modules: list[ModuleStatus] = field(default_factory=list)
    uptime_seconds: float = 0.0
