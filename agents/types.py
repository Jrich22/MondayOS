"""
Domain types for the Multi-Agent Runtime: Agent (a registry entry) and
AgentRun (the logged, reviewable record of one role-routed execution).

These are plain dataclasses with dict round-tripping. Persistence lives in
agents.registry (Agents → Markdown+frontmatter, mirroring tasks/) and
agents.runtime (AgentRuns → JSON under logs/agents/, mirroring ExecutionReport).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from brain.providers.base import safe_provider_identifier, safe_provider_model
from core.types import EntityId, Timestamp
from orchestrator.report import sanitize_execution_record

_AGENT_RUN_ID = re.compile(r"^run-[A-Za-z0-9][A-Za-z0-9-]{0,79}$")
_TASK_ID = re.compile(r"^TASK-\d{1,64}(?:-[A-Z0-9]{1,12})?$")
_APPROVAL_ACTOR = re.compile(
    r"^(?:agent|human|reconciliation|workflow):[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
)
_AGENT_RUN_STATUSES = frozenset({
    "blocked",
    "completed",
    "dry-run",
    "executed",
    "failed",
    "planned",
    "review",
    "skipped",
    "unavailable",
    "validation-failed",
})
_TEAM_ROLES = frozenset({"cpo", "lead-engineer", "qa", "security", "reviewer"})
_TEAM_STAGE_STATUSES = frozenset({
    "blocked",
    "completed",
    "dry-run",
    "executed",
    "failed",
    "review",
    "skipped",
    "unavailable",
    "validation-failed",
})
_TEAM_STATUSES = frozenset({
    "awaiting-approval",
    "blocked",
    "changes-requested",
    "completed",
    "dry-run",
    "failed",
    "interrupted",
    "invalid-verdict",
    "rejected",
    "running",
})
_VERDICTS = frozenset({"", "block", "invalid", "needs_changes", "pass"})
_CONFIDENCE_LEVELS = frozenset({"", "high", "medium", "low"})
_TEAM_STATUS_MESSAGES = {
    "awaiting-approval": "Team run is awaiting human approval.",
    "blocked": "Team run was blocked by a required stage.",
    "changes-requested": "Team run requires changes before it can continue.",
    "completed": "Team run completed.",
    "dry-run": "Team dry run completed.",
    "failed": "Team run failed. Check provider configuration or retry later.",
    "interrupted": "Team run was interrupted after its run lease ended.",
    "invalid-verdict": "Team run stopped because a required stage returned no valid verdict.",
    "rejected": "Team run was rejected and did not execute.",
    "running": "",
}
_TEAM_STOPPED_REASONS = {
    status: message
    for status, message in _TEAM_STATUS_MESSAGES.items()
    if status in {
        "blocked",
        "changes-requested",
        "failed",
        "interrupted",
        "invalid-verdict",
        "rejected",
    }
}


def _agent_run_message(status: str, approval: Any) -> str:
    """Render a fixed persisted message without retaining legacy diagnostics."""
    decision = approval.get("decision", "") if type(approval) is dict else ""
    if decision == "approved":
        return "Agent run was approved."
    if decision == "rejected":
        return "Agent run was rejected and left for rework."
    return {
        "blocked": "Agent run was blocked.",
        "completed": "Agent run completed.",
        "dry-run": "Agent dry run completed.",
        "executed": "Agent run completed.",
        "failed": "Agent run failed. Check provider configuration or retry later.",
        "planned": "Agent run is planned.",
        "review": "Agent run completed and is awaiting review.",
        "skipped": "Agent run was skipped.",
        "unavailable": "Provider is unavailable. Check its configuration and retry.",
        "validation-failed": "Agent result failed validation.",
    }.get(status, "Agent run failed.")


def _team_run_message(value: dict[str, Any], status: str) -> str:
    """Rebuild safe dynamic messages from structured TeamRun state."""
    task_id = value.get("task_id", "")
    approval_run_id = value.get("approval_run_id", "")
    approval = value.get("approval", {})
    decision = approval.get("decision", "") if type(approval) is dict else ""
    actor = approval.get("by", "") if type(approval) is dict else ""

    if (
        status == "awaiting-approval"
        and type(approval_run_id) is str
        and _AGENT_RUN_ID.fullmatch(approval_run_id) is not None
    ):
        return (
            "All stages passed. Task is at REVIEW awaiting human approval — "
            f"approve with: monday agent review {approval_run_id} --approve"
        )
    if (
        status == "completed"
        and decision == "approved"
        and type(actor) is str
        and type(task_id) is str
        and _APPROVAL_ACTOR.fullmatch(actor) is not None
        and _TASK_ID.fullmatch(task_id) is not None
    ):
        return f"Approved by {actor}; task {task_id} completed."
    if (
        status == "changes-requested"
        and decision == "rejected"
        and type(actor) is str
        and type(task_id) is str
        and _APPROVAL_ACTOR.fullmatch(actor) is not None
        and _TASK_ID.fullmatch(task_id) is not None
    ):
        return f"Rejected by {actor}; task {task_id} left for rework."
    return _TEAM_STATUS_MESSAGES[status]


def is_agent_run_id(value: Any) -> bool:
    """Return whether *value* is a canonical persisted agent-run identity."""
    return type(value) is str and _AGENT_RUN_ID.fullmatch(value) is not None


def sanitize_team_record(value: Any) -> dict[str, Any]:
    """Upgrade legacy TeamRun provenance without altering visible model output."""
    if type(value) is not dict:
        return {}
    clean = dict(value)
    top_status = value.get("status", "")
    if type(top_status) is not str or top_status not in _TEAM_STATUSES:
        top_status = "failed"
    clean["status"] = top_status
    clean["message"] = _team_run_message(value, top_status)
    clean["stopped_reason"] = _TEAM_STOPPED_REASONS.get(top_status, "")

    stages: list[dict[str, Any]] = []
    raw_stages = value.get("stages", [])
    if type(raw_stages) is list:
        for raw in raw_stages:
            if type(raw) is not dict:
                continue
            stage = dict(raw)
            provider_value = raw.get("provider_used", "")
            provider = (
                ""
                if type(provider_value) is str and not provider_value
                else safe_provider_identifier(provider_value, fallback="custom")
            )
            stage["provider_used"] = provider
            stage["provider_model"] = safe_provider_model(provider)
            role = raw.get("role", "")
            stage["role"] = role if type(role) is str and role in _TEAM_ROLES else ""
            status = raw.get("status", "")
            status = (
                status
                if type(status) is str and status in _TEAM_STAGE_STATUSES
                else "failed"
            )
            stage["status"] = status
            verdict = raw.get("verdict", "")
            stage["verdict"] = (
                verdict if type(verdict) is str and verdict in _VERDICTS else "invalid"
            )
            confidence = raw.get("confidence", "")
            stage["confidence"] = (
                confidence
                if type(confidence) is str and confidence in _CONFIDENCE_LEVELS
                else ""
            )
            if status == "unavailable":
                stage["summary"] = "Provider was unavailable for this stage."
            elif status in {"failed", "blocked", "skipped"}:
                stage["summary"] = "Stage did not complete."
            elif status == "validation-failed":
                stage["summary"] = "Stage result failed validation."
            stages.append(stage)
    clean["stages"] = stages
    return clean


@dataclass
class Agent:
    """
    A registered agent: a named binding of a role to a provider.

    Attributes:
        id:           Stable identifier, e.g. "AGENT-0007-9p3d6xkw".
        name:         Human-readable name (e.g. "Claude Code").
        role:         Role slug this agent fulfils (see agents.roles).
        provider:     AI provider name ("anthropic" | "openai" | "ollama" |
                      "fake"). "fake" is the offline test/demo adapter.
        capabilities: What this agent advertises (defaults to the role's).
        is_default:   True if this is the seeded default agent for its role.
        status:       "active" or "disabled".
        description:  Optional free-text note.
        created/updated: UTC timestamps.
        metadata:     Free-form extension bag.
    """

    id: EntityId
    name: str
    role: str
    provider: str
    capabilities: list[str] = field(default_factory=list)
    is_default: bool = False
    status: str = "active"
    description: str = ""
    created: Timestamp = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated: Timestamp = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON/CLI-friendly dict (timestamps as ISO strings)."""
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "provider": self.provider,
            "capabilities": list(self.capabilities),
            "is_default": self.is_default,
            "status": self.status,
            "description": self.description,
            "created": _iso(self.created),
            "updated": _iso(self.updated),
            "metadata": dict(self.metadata),
        }


@dataclass
class AgentRun:
    """
    The logged, reviewable record of one `monday agent run`.

    Wraps the orchestrator's ExecutionReport with the role/agent/gate/approval
    context that the agent layer adds. Persisted as logs/agents/{run_id}.json.
    """

    run_id: str
    task_id: str
    role: str
    agent_id: str = ""
    agent_name: str = ""
    provider_requested: str = ""
    provider_used: str = ""
    provider_model: str = ""
    mode: str = ""
    status: str = (
        ""  # blocked | skipped | unavailable | failed | validation-failed |
        # review | completed | dry-run
    )
    success: bool = False
    created_at: str = ""
    duration_ms: float = 0.0
    confidence: float = 0.0
    gate: dict[str, Any] = field(default_factory=dict)
    approval: dict[str, Any] = field(default_factory=dict)
    knowledge_captured: list[str] = field(default_factory=list)
    execution_id: str = ""
    execution: dict[str, Any] = field(default_factory=dict)
    # Structured reduction of the provider response (agents.verdicts.AgentVerdict
    # as a dict: verdict/confidence/summary/findings/recommendations/source). The
    # team workflow inspects only this — never the raw prose. Empty for runs that
    # never called a provider (blocked / unavailable / dry-run without output).
    verdict: dict[str, Any] = field(default_factory=dict)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentRun":
        """Build an AgentRun while upgrading legacy provider diagnostics."""
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in data.items() if k in known}

        def provider_field(name: str) -> str:
            value = data.get(name, "")
            if type(value) is str and not value:
                return ""
            return safe_provider_identifier(value, fallback="custom")

        clean["provider_requested"] = provider_field("provider_requested")
        clean["provider_used"] = provider_field("provider_used")
        clean["provider_model"] = safe_provider_model(clean["provider_used"])
        clean["execution"] = sanitize_execution_record(data.get("execution", {}))
        status = data.get("status", "")
        if type(status) is not str or status not in _AGENT_RUN_STATUSES:
            status = "failed"
        clean["status"] = status
        clean["message"] = _agent_run_message(status, data.get("approval", {}))
        return cls(**clean)


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)
