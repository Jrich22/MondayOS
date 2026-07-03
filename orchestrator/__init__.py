"""
orchestrator — the MondayOS Execution Orchestrator.

The orchestrator receives engineering work and delegates it to one or more AI
providers. It does not implement AI models; it coordinates them. Execution flows
through a fixed pipeline:

    task → advisor prioritises → planner → execution queue → provider selection
    → provider executes → validation → knowledge capture → task update → report

It reuses existing subsystems (AIProvider, AdvisorEngine, TaskManager,
KnowledgeStore, EventBus) via the Monday public API and the provider
abstraction — no provider-specific code exists here.

Public surface:
    ExecutionOrchestrator   — the coordinating engine
    ExecutionMode           — DRY_RUN | REVIEW | AUTONOMOUS (safety modes)
    ProviderSelectionPolicy — PREFER_LOCAL | LOWEST_COST | HIGHEST_CAPABILITY | MANUAL
    select_provider         — policy-based provider chooser
    ExecutionPlan / ExecutionPlanner
    ExecutionQueue / ExecutionUnit
    ResultValidator / ValidationResult
    ExecutionReport
"""
from __future__ import annotations

from orchestrator.executor import (
    ExecutionOrchestrator,
    ProviderSelectionPolicy,
    select_provider,
)
from orchestrator.planner import ExecutionPlan, ExecutionPlanner
from orchestrator.queue import ExecutionQueue, ExecutionUnit
from orchestrator.report import ExecutionMode, ExecutionReport
from orchestrator.validator import ResultValidator, ValidationResult

__all__ = [
    "ExecutionOrchestrator",
    "ExecutionMode",
    "ProviderSelectionPolicy",
    "select_provider",
    "ExecutionPlan",
    "ExecutionPlanner",
    "ExecutionQueue",
    "ExecutionUnit",
    "ResultValidator",
    "ValidationResult",
    "ExecutionReport",
]
