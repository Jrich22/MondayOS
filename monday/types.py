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
        answer:     The model's response to the prompt.
        sources:    IDs of knowledge entries or tasks used to form the answer.
        model_used: Identifier of the model that produced the answer.
        confidence: Estimated confidence, 0.0–1.0. 0.0 means unknown.
        task_id:    ID of the task created to fulfill the request, if any.
    """

    answer: str
    sources: list[str] = field(default_factory=list)
    model_used: str = ""
    confidence: float = 0.0
    task_id: str | None = None


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
