"""
AgentRuntime — the role-based coordination layer over the Execution Orchestrator.

MondayOS stays the system of record. The runtime routes a task to a *role*,
resolves the role to a registered agent (and thus a provider), enforces the
review-required approval gate, then delegates the actual model execution to the
existing ExecutionOrchestrator via ``Monday.execute`` — it contains no
provider-specific code and performs no commit/push/secret/live-trade itself.
Every run is logged as a reviewable AgentRun under ``logs/agents/``.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.adapters import build_provider_for
from agents.gates import ApprovalGate, GateDecision
from agents.registry import AgentRegistry
from agents.roles import get_role, normalize_role
from agents.types import Agent, AgentRun
from brain.providers.base import ProviderAvailability
from orchestrator.report import ExecutionMode


class AgentRuntime:
    """
    Coordinates role-routed agent work on top of the Monday public API.

    Args:
        monday:                 A Monday instance (used for execute / task).
        project_root:           Project root; run logs land under logs/agents/.
        require_human_approval: Standing approval posture (default True →
                                autonomous completion still needs --approve).
    """

    def __init__(
        self,
        monday: Any,
        project_root: Path,
        require_human_approval: bool = True,
    ) -> None:
        self._monday = monday
        self._root = Path(project_root)
        self._registry = AgentRegistry(self._root)
        self._gate = ApprovalGate(require_human_approval=require_human_approval)
        self._runs_dir = self._root / "logs" / "agents"

    # ------------------------------------------------------------------
    # Registry
    # ------------------------------------------------------------------

    def list_agents(self, role: str | None = None) -> list[Agent]:
        """List registered agents (seeding the six defaults on first use)."""
        self._registry.ensure_seeded()
        return self._registry.list(role=role)

    def register(
        self,
        name: str,
        role: str,
        provider: str = "",
        capabilities: list[str] | None = None,
        is_default: bool = False,
        description: str = "",
    ) -> Agent:
        """Register a new agent for a role."""
        return self._registry.register(
            name=name,
            role=role,
            provider=provider,
            capabilities=capabilities,
            is_default=is_default,
            description=description,
        )

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def assign(self, task_id: str, role: str, assigned_by: str = "human:cli") -> Any:
        """
        Assign a task to a *role* (not a person/model).

        Sets ``assigned_to = "role:<slug>"`` via the Monday task API. Returns the
        TaskResponse from Monday.
        """
        role_slug = normalize_role(role)
        get_role(role_slug)  # validate — raises UnknownRoleError
        return self._monday.task(
            "assign",
            task_id=task_id,
            assignee=f"role:{role_slug}",
            assigned_by=assigned_by,
        )

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(
        self,
        task_id: str,
        role: str,
        provider: str = "",
        policy: str = "manual",
        mode: str = "review",
        autonomous_enabled: bool = False,
        approved: bool = False,
        requested_actions: list[str] | None = None,
        extra_context: str = "",
        update_task: bool = True,
        provider_instance: Any = None,
    ) -> AgentRun:
        """
        Route a task to a role and run it through the orchestrator.

        Pipeline: resolve role → agent → provider, evaluate the approval gate,
        and (if allowed) delegate to Monday.execute in the requested mode
        (REVIEW by default). Always returns a persisted AgentRun; never raises
        for expected failure paths.
        """
        role_slug = normalize_role(role)
        get_role(role_slug)  # validate the role up front

        self._registry.ensure_seeded()
        agent = self._registry.resolve_by_role(role_slug)
        if provider_instance is not None:
            provider_requested = provider_instance.name
        else:
            provider_requested = (provider or (agent.provider if agent else "")).strip()

        run = AgentRun(
            run_id=_new_run_id(),
            task_id=task_id,
            role=role_slug,
            agent_id=agent.id if agent else "",
            agent_name=agent.name if agent else "",
            provider_requested=provider_requested,
            mode=_normalize_mode(mode),
            created_at=_now_iso(),
        )

        # ── Approval gate (review-required by default) ───────────────────
        try:
            mode_enum = ExecutionMode.from_str(mode)
        except ValueError as exc:
            return self._finish_blocked(run, GateDecision(allowed=False, reason=str(exc)))

        decision = self._gate.evaluate(
            mode=mode_enum,
            autonomous_enabled=autonomous_enabled,
            approved=approved,
            requested_actions=requested_actions,
        )
        run.gate = decision.to_dict()
        if not decision.allowed:
            return self._finish_blocked(run, decision)

        # ── Build the provider for this agent/override ───────────────────
        if provider_instance is not None:
            prov = provider_instance
        else:
            prov = build_provider_for(agent if not provider else provider, role=role_slug)
        providers = [prov] if prov is not None else []
        manual_name = prov.name if prov is not None else provider_requested

        # ── Provider availability gate (graceful, key-aware) ─────────────
        # A run that will actually call a provider (not dry-run) must have a
        # ready provider. A missing SDK or API key stops here with clear
        # instructions instead of a raw failure mid-execution.
        if mode_enum is not ExecutionMode.DRY_RUN:
            avail = prov.availability() if prov is not None else ProviderAvailability(
                available=False, provider=provider_requested,
                reason=f"unknown or unconstructable provider {provider_requested!r}",
            )
            if not avail.available:
                run.status = "unavailable"
                run.success = False
                run.provider_model = avail.model
                run.approval = {"required": False, "decision": "not-required", "by": "", "at": "", "note": ""}
                run.message = f"Provider unavailable — {avail.instructions()}"
                self._persist(run)
                return run

        # ── Delegate execution to the orchestrator via Monday.execute ────
        resp = self._monday.execute(
            task_id,
            mode=mode,
            policy=policy or "manual",
            provider=manual_name,
            providers=providers,
            autonomous_enabled=autonomous_enabled,
            extra_context=extra_context,
            update_task=update_task,
        )

        run.provider_used = resp.provider_used
        run.provider_model = resp.data.get("model_used", "") or run.provider_model
        run.status = resp.status
        run.success = resp.success
        run.duration_ms = resp.duration_ms
        run.confidence = resp.confidence
        run.knowledge_captured = list(resp.knowledge_captured)
        run.execution_id = resp.execution_id
        run.execution = dict(resp.data)
        run.message = resp.message
        run.approval = _approval_for(mode_enum, decision, approved)
        self._persist(run)
        return run

    # ------------------------------------------------------------------
    # Review / history
    # ------------------------------------------------------------------

    def review(
        self,
        run_id: str,
        approve: bool,
        by: str = "human:cli",
        note: str = "",
    ) -> AgentRun:
        """
        Record a human review decision on a run.

        On approval, completes the task (if it is awaiting review). On rejection,
        the task is left at REVIEW for rework. Returns the updated AgentRun.
        Raises FileNotFoundError if the run does not exist.
        """
        run = self.get_run(run_id)
        run.approval = {
            **run.approval,
            "required": True,
            "decision": "approved" if approve else "rejected",
            "by": by,
            "at": _now_iso(),
            "note": note,
        }

        if approve and run.task_id:
            r = self._monday.task(
                "complete",
                task_id=run.task_id,
                reason=f"Approved agent run {run.run_id} ({run.role}) by {by}.",
                changed_by=by,
            )
            run.message = (
                f"Approved; task {run.task_id} completed."
                if getattr(r, "success", False)
                else f"Approved; task not completed: {getattr(r, 'message', '')}"
            )
        elif not approve:
            run.message = f"Rejected by {by}; task {run.task_id} left for rework."

        self._persist(run)
        return run

    def get_run(self, run_id: str) -> AgentRun:
        path = self._run_path(run_id)
        if not path.exists():
            raise FileNotFoundError(f"No agent run {run_id!r} in {self._runs_dir}.")
        return AgentRun.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def history(
        self,
        role: str | None = None,
        task_id: str | None = None,
        limit: int = 20,
    ) -> list[AgentRun]:
        """Return past runs (most recent first), optionally filtered."""
        if not self._runs_dir.exists():
            return []
        runs: list[AgentRun] = []
        for path in self._runs_dir.glob("run-*.json"):
            try:
                runs.append(AgentRun.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except Exception:
                continue
        if role is not None:
            role_slug = normalize_role(role)
            runs = [r for r in runs if r.role == role_slug]
        if task_id is not None:
            runs = [r for r in runs if r.task_id == task_id]
        runs.sort(key=lambda r: r.created_at, reverse=True)
        return runs[: max(0, limit)] if limit else runs

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _finish_blocked(self, run: AgentRun, decision: GateDecision) -> AgentRun:
        run.status = "blocked"
        run.success = False
        run.gate = decision.to_dict()
        run.approval = {
            "required": True,
            "decision": "pending",
            "by": "",
            "at": "",
            "note": decision.reason,
        }
        run.message = decision.reason
        self._persist(run)
        return run

    def _run_path(self, run_id: str) -> Path:
        return self._runs_dir / f"{run_id}.json"

    def _persist(self, run: AgentRun) -> None:
        try:
            self._runs_dir.mkdir(parents=True, exist_ok=True)
            self._run_path(run.run_id).write_text(
                json.dumps(run.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
            )
        except Exception:
            pass  # persistence failure must not mask the run result


def _new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_mode(mode: str) -> str:
    try:
        return ExecutionMode.from_str(mode).value
    except ValueError:
        return mode


def _approval_for(mode: ExecutionMode, decision: GateDecision, approved: bool) -> dict[str, Any]:
    if mode is ExecutionMode.DRY_RUN:
        return {"required": False, "decision": "not-required", "by": "", "at": "", "note": ""}
    if mode is ExecutionMode.AUTONOMOUS and approved:
        return {"required": True, "decision": "approved", "by": "", "at": _now_iso(), "note": ""}
    # REVIEW (and any executed run) awaits a human decision.
    return {"required": True, "decision": "pending", "by": "", "at": "", "note": decision.reason}
