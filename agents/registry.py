"""
AgentRegistry — the system of record for registered agents.

Mirrors TaskManager's on-disk convention exactly:
    <root>/agents/active/{AGENT-ID}.md   — one Markdown+YAML-frontmatter file per agent
    <root>/agents/.sequences.json        — AGENT- id allocation, survives restarts

The registry seeds one default agent per role on first use (ChatGPT → CPO,
Claude Code → Lead Engineer, and the QA / Security / Research / Reviewer agents),
so a fresh project has a working roster with no manual setup. Registering more
agents — or new roles — never requires touching runtime code.
"""

from __future__ import annotations

import re
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from agents.roles import ROLES, get_role, normalize_role
from agents.types import Agent
from core.sequence import IdentityPolicy, Namespace, SequenceAllocator

_AGENT_PREFIX = "AGENT"
_SEQUENCES_FILENAME = ".sequences.json"
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)

# Human-facing names for the seeded default agents, keyed by role slug.
_DEFAULT_AGENT_NAMES: dict[str, str] = {
    "cpo": "ChatGPT",
    "lead-engineer": "Claude Code",
    "qa": "QA Agent",
    "security": "Security Agent",
    "research": "Research Agent",
    "reviewer": "Reviewer Agent",
}


class AgentExistsError(ValueError):
    """Raised when registering an agent whose name already exists."""


class AgentNotFoundError(LookupError):
    """Raised when an agent id or name cannot be resolved."""


class AgentRegistry:
    """CRUD + role resolution for agents, persisted under ``<root>/agents/``."""

    def __init__(self, project_root: Path = Path(".")) -> None:
        self._dir = Path(project_root) / "agents"
        self._active_dir = self._dir / "active"
        self._sequences_path = self._dir / _SEQUENCES_FILENAME

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        role: str,
        provider: str = "",
        capabilities: list[str] | None = None,
        is_default: bool = False,
        description: str = "",
    ) -> Agent:
        """
        Register a new agent for a role and persist it.

        ``provider`` defaults to the role's default provider. ``capabilities``
        default to the role's advertised capabilities. Raises UnknownRoleError
        for an unknown role and AgentExistsError if the name is already taken.
        """
        if not name.strip():
            raise ValueError("agent name cannot be empty")
        role_slug = normalize_role(role)
        role_def = get_role(role_slug)  # raises UnknownRoleError

        if self._find_by_name(name) is not None:
            raise AgentExistsError(f"An agent named {name!r} already exists.")

        now = datetime.now(tz=UTC)
        agent = Agent(
            id=self._next_id(),
            name=name,
            role=role_slug,
            provider=(provider or role_def.default_provider).strip(),
            capabilities=list(capabilities if capabilities is not None else role_def.capabilities),
            is_default=is_default,
            status="active",
            description=description,
            created=now,
            updated=now,
        )
        self._write(agent)
        return agent

    def get(self, agent_id_or_name: str) -> Agent:
        """Return an agent by id (AGENT-XXXX) or by exact name. Raises if absent."""
        path = self._active_dir / f"{agent_id_or_name}.md"
        if path.exists():
            return self._parse(path.read_text(encoding="utf-8"))
        found = self._find_by_name(agent_id_or_name)
        if found is None:
            raise AgentNotFoundError(f"No agent with id or name {agent_id_or_name!r}.")
        return found

    def list(self, role: str | None = None, include_disabled: bool = True) -> list[Agent]:
        """Return all agents, optionally filtered by role / active status."""
        agents = self._read_all()
        if role is not None:
            role_slug = normalize_role(role)
            agents = [a for a in agents if a.role == role_slug]
        if not include_disabled:
            agents = [a for a in agents if a.status == "active"]
        return sorted(agents, key=lambda a: a.id)

    def resolve_by_role(self, role: str) -> Agent | None:
        """
        Return the agent that should handle ``role``.

        Prefers the active default agent for the role; otherwise the first active
        agent registered for it; None if the role has no active agent.
        """
        role_slug = normalize_role(role)
        candidates = [a for a in self._read_all() if a.role == role_slug and a.status == "active"]
        if not candidates:
            return None
        candidates.sort(key=lambda a: a.id)
        for agent in candidates:
            if agent.is_default:
                return agent
        return candidates[0]

    def seed_defaults(self) -> list[Agent]:
        """
        Ensure every role has a default agent. Idempotent — creates a default
        only for roles that don't already have one. Returns the agents created.
        """
        existing_defaults = {
            a.role for a in self._read_all() if a.is_default and a.status == "active"
        }
        created: list[Agent] = []
        for slug in sorted(ROLES):
            if slug in existing_defaults:
                continue
            role_def = ROLES[slug]
            name = _DEFAULT_AGENT_NAMES.get(slug, f"{role_def.title} Agent")
            # Skip if the name is already taken (e.g. re-seed after a manual add).
            if self._find_by_name(name) is not None:
                continue
            created.append(
                self.register(
                    name=name,
                    role=slug,
                    provider=role_def.default_provider,
                    capabilities=list(role_def.capabilities),
                    is_default=True,
                    description=role_def.description,
                )
            )
        return created

    def ensure_seeded(self) -> None:
        """Seed defaults only if the registry is currently empty."""
        if not self._read_all():
            self.seed_defaults()

    # ------------------------------------------------------------------
    # Internal helpers (mirror tasks/manager.py + tasks/parser.py)
    # ------------------------------------------------------------------

    def _read_all(self) -> list[Agent]:
        if not self._active_dir.exists():
            return []
        agents: list[Agent] = []
        for path in sorted(self._active_dir.glob("*.md")):
            try:
                agents.append(self._parse(path.read_text(encoding="utf-8")))
            except Exception as exc:  # a malformed file must not break the roster
                warnings.warn(f"Skipping {path.name}: {exc}", stacklevel=2)
        return agents

    def _find_by_name(self, name: str) -> Agent | None:
        for agent in self._read_all():
            if agent.name == name:
                return agent
        return None

    def _write(self, agent: Agent) -> None:
        self._active_dir.mkdir(parents=True, exist_ok=True)
        path = self._active_dir / f"{agent.id}.md"
        path.write_text(self._serialize(agent), encoding="utf-8")

    def _serialize(self, agent: Agent) -> str:
        fm: dict[str, Any] = {
            "capabilities": list(agent.capabilities),
            "created": _fmt_dt(agent.created),
            "description": agent.description,
            "id": agent.id,
            "is_default": agent.is_default,
            "metadata": dict(agent.metadata),
            "name": agent.name,
            "provider": agent.provider,
            "role": agent.role,
            "status": agent.status,
            "updated": _fmt_dt(agent.updated),
        }
        fm_yaml = yaml.dump(
            fm, default_flow_style=False, allow_unicode=True, sort_keys=True, width=120
        )
        return f"---\n{fm_yaml}---\n"

    def _next_id(self) -> str:
        """
        Allocate the next agent id.

        RUNTIME_HYBRID: agent records live in the gitignored ``agents/active/``,
        so a duplicate would never reach a merge and would stay permanently
        invisible. The suffix supplies the uniqueness git cannot.

        This allocator previously trusted its counter outright, with no disk
        healing at all — the only one of the five that did not. Counter and
        records are both ignored so they cannot diverge through git, but losing
        or truncating the counter while the records survived would have reissued
        AGENT-0001 straight over a live record.
        """
        return self._allocator().allocate()

    def _allocator(self) -> SequenceAllocator:
        return SequenceAllocator(
            Namespace(
                prefix=_AGENT_PREFIX,
                records=self._dir,
                counter=self._sequences_path,
                policy=IdentityPolicy.RUNTIME_HYBRID,
            )
        )

    def _parse(self, raw: str) -> Agent:
        match = _FRONTMATTER_RE.match(raw)
        if not match:
            raise ValueError("No YAML frontmatter block found (expected --- ... ---)")
        fm: dict[str, Any] = yaml.safe_load(match.group(1)) or {}
        for required in ("id", "name", "role", "provider"):
            if required not in fm:
                raise ValueError(f"Missing required field: {required!r}")
        return Agent(
            id=str(fm["id"]),
            name=str(fm["name"]),
            role=str(fm["role"]),
            provider=str(fm["provider"]),
            capabilities=list(fm.get("capabilities") or []),
            is_default=bool(fm.get("is_default", False)),
            status=str(fm.get("status", "active")),
            description=str(fm.get("description", "")),
            created=_parse_dt(fm.get("created")),
            updated=_parse_dt(fm.get("updated")),
            metadata=dict(fm.get("metadata") or {}),
        )


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.rstrip("Z")).replace(tzinfo=UTC)
    return datetime.now(tz=UTC)


def _fmt_dt(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
