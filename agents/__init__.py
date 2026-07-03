"""
agents — the MondayOS Multi-Agent Runtime.

A role-based coordination layer on top of the Execution Orchestrator. Work is
routed to a *role* (CPO, Lead Engineer, QA, Security, Research, Reviewer); the
registry resolves the role to a concrete agent and provider; an approval gate
enforces the review-required posture; and every run is logged as a reviewable
AgentRun. MondayOS remains the system of record — agents never commit, push,
touch secrets, or live-trade without explicit human approval.

Public surface:
    Role / ROLES / get_role / list_roles / GATED_ACTIONS   — role definitions
    Agent / AgentRun                                        — domain types
    AgentRegistry                                           — the agent registry
    ApprovalGate / GateDecision                             — approval gates
    AgentRuntime                                            — the coordinator
    FakeAgentProvider / build_provider_for                  — provider adapters
"""
from __future__ import annotations

from agents.adapters import FakeAgentProvider, build_provider_for
from agents.gates import ApprovalGate, GateDecision
from agents.registry import AgentNotFoundError, AgentExistsError, AgentRegistry
from agents.roles import (
    GATED_ACTIONS,
    ROLES,
    Role,
    UnknownRoleError,
    get_role,
    list_roles,
    normalize_role,
)
from agents.runtime import AgentRuntime
from agents.types import Agent, AgentRun

__all__ = [
    "Role",
    "ROLES",
    "GATED_ACTIONS",
    "get_role",
    "list_roles",
    "normalize_role",
    "UnknownRoleError",
    "Agent",
    "AgentRun",
    "AgentRegistry",
    "AgentExistsError",
    "AgentNotFoundError",
    "ApprovalGate",
    "GateDecision",
    "AgentRuntime",
    "FakeAgentProvider",
    "build_provider_for",
]
