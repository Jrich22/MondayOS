"""
Domain types for the Multi-Agent Runtime: Agent (a registry entry) and
AgentRun (the logged, reviewable record of one role-routed execution).

These are plain dataclasses with dict round-tripping. Persistence lives in
agents.registry (Agents → Markdown+frontmatter, mirroring tasks/) and
agents.runtime (AgentRuns → JSON under logs/agents/, mirroring ExecutionReport).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.types import EntityId, Timestamp


@dataclass
class Agent:
    """
    A registered agent: a named binding of a role to a provider.

    Attributes:
        id:           Stable identifier, e.g. "AGENT-0001".
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
    status: str = ""            # blocked | skipped | failed | validation-failed | review | completed | dry-run
    success: bool = False
    created_at: str = ""
    duration_ms: float = 0.0
    confidence: float = 0.0
    gate: dict[str, Any] = field(default_factory=dict)
    approval: dict[str, Any] = field(default_factory=dict)
    knowledge_captured: list[str] = field(default_factory=list)
    execution_id: str = ""
    execution: dict[str, Any] = field(default_factory=dict)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentRun":
        """Build an AgentRun from a persisted dict, ignoring unknown keys."""
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)
