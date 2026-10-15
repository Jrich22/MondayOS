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

import fcntl
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents.adapters import build_provider_for, build_provider_pool
from agents.gates import ApprovalGate, GateDecision
from agents.registry import AgentRegistry
from agents.roles import get_role, normalize_role
from agents.types import Agent, AgentRun, is_agent_run_id, sanitize_team_record
from agents.verdicts import parse_verdict
from brain.providers.base import (
    ProviderAvailability,
    provider_availability_message,
    safe_provider_availability,
    safe_provider_identifier,
    safe_provider_model,
)
from core.secure_io import (
    ConcurrentRecordChangeError,
    open_directory_beneath,
    open_private_lock_file,
    read_json_with_identity_at,
    validate_private_lock_file,
    write_json_atomic_at,
)
from orchestrator.report import ExecutionMode
from tasks.manager import TaskManager
from tasks.task import TaskStatus

_APPROVAL_COMPLETION_PREFIX = "monday-agent-approval-v1:"
_ALLOWED_REVIEWER_PROVIDERS = frozenset({"openai", "fake"})
_LEGACY_MIGRATION_ATTEMPTS = 3


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
        configured_providers: list[Any] | None = None,
    ) -> None:
        self._monday = monday
        self._root = Path(project_root)
        self._registry = AgentRegistry(self._root)
        self._gate = ApprovalGate(require_human_approval=require_human_approval)
        self._runs_dir = self._root / "logs" / "agents"
        self._configured_providers = configured_providers

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
        policy: str = "ordered-failover",
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
        role_definition = get_role(role_slug)  # validate the role up front

        self._registry.ensure_seeded()
        agent = self._registry.resolve_by_role(role_slug)
        provider_override = (provider or "").strip()
        if provider_instance is not None:
            provider_requested = safe_provider_identifier(
                provider_instance,
                fallback="custom",
            )
        else:
            provider_requested = safe_provider_identifier(
                provider_override or (agent.provider if agent else ""),
                fallback="custom",
            )

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

        # The Reviewer is MondayOS's independent ChatGPT/OpenAI gate. Enforce
        # that invariant in the runtime itself, not only in TeamWorkflow, so a
        # direct agent call or advanced provider injection cannot bypass it.
        # ``fake`` remains an explicit, offline-only test harness.
        reviewer_override = ""
        if role_slug == "reviewer" and (
            provider_instance is not None or provider_override
        ):
            reviewer_override = safe_provider_identifier(
                provider_instance if provider_instance is not None else provider_override,
                fallback="custom",
            )
        if reviewer_override and reviewer_override not in _ALLOWED_REVIEWER_PROVIDERS:
            run.status = "blocked"
            run.success = False
            run.approval = {
                "required": False,
                "decision": "not-required",
                "by": "",
                "at": "",
                "note": "",
            }
            run.message = (
                f"Reviewer provider {reviewer_override!r} is not allowed; "
                "the independent Reviewer requires OpenAI/ChatGPT "
                "('fake' is permitted only for offline tests)."
            )
            self._persist(run)
            return run

        # ── Build providers for this agent/override ──
        # Explicit provider instances/names stay pinned. With no override, the
        # role's configured provider is primary and the orchestrator may fail
        # over through the deterministic pool built by agents.adapters.
        if provider_instance is not None:
            prov = provider_instance
            providers = [prov]
            manual_name = safe_provider_identifier(prov, fallback="custom")
            effective_policy = policy or "manual"
        elif provider_override:
            prov = build_provider_for(
                provider_override,
                role=role_slug,
                configured_providers=self._configured_providers,
            )
            providers = [prov] if prov is not None else []
            manual_name = (
                safe_provider_identifier(prov, fallback="custom")
                if prov is not None
                else provider_requested
            )
            effective_policy = policy or "manual"
        else:
            providers = build_provider_pool(
                agent,
                role=role_slug,
                configured_providers=self._configured_providers,
            )
            prov = providers[0] if providers else None
            manual_name = ""
            effective_policy = policy or "ordered-failover"

        # ── Provider availability gate (graceful, key-aware) ─────────────
        # A run that will actually call a provider (not dry-run) must have a
        # ready provider. A missing SDK or API key stops here with bounded
        # configuration guidance instead of raw provider or SDK prose.
        if mode_enum is not ExecutionMode.DRY_RUN:
            availability: list[ProviderAvailability] = []
            for candidate in providers:
                candidate_name = safe_provider_identifier(candidate, fallback="custom")
                try:
                    status = safe_provider_availability(
                        candidate.availability(),
                        provider=candidate_name,
                    )
                except Exception as exc:
                    status = safe_provider_availability(
                        None,
                        provider=candidate_name,
                        error=exc,
                    )
                availability.append(status)
            if not availability:
                availability = [
                    safe_provider_availability(
                        ProviderAvailability(
                            available=False,
                            provider=provider_requested,
                            code="provider-not-configured",
                        ),
                        provider=provider_requested,
                    )
                ]
            if not any(item.available for item in availability):
                primary_availability = availability[0]
                run.status = "unavailable"
                run.success = False
                run.provider_model = primary_availability.model
                run.approval = {
                    "required": False,
                    "decision": "not-required",
                    "by": "",
                    "at": "",
                    "note": "",
                }
                guidance = list(dict.fromkeys(
                    provider_availability_message(item) for item in availability
                ))
                run.message = " ".join(guidance)
                self._persist(run)
                return run

        # ── Delegate execution to the orchestrator via Monday.execute ────
        role_context = _role_context(role_definition)
        effective_context = (
            f"{role_context}\n\n{extra_context}".strip()
            if extra_context
            else role_context
        )
        resp = self._monday.execute(
            task_id,
            mode=mode,
            policy=effective_policy,
            provider=manual_name,
            providers=providers,
            autonomous_enabled=autonomous_enabled,
            extra_context=effective_context,
            update_task=update_task,
        )

        raw_provider_used = resp.provider_used
        run.provider_used = (
            safe_provider_identifier(raw_provider_used, fallback="custom")
            if type(raw_provider_used) is str and raw_provider_used
            else ""
        )
        run.provider_model = safe_provider_model(run.provider_used)
        run.status = resp.status
        run.success = resp.success
        run.duration_ms = resp.duration_ms
        run.confidence = resp.confidence
        run.knowledge_captured = list(resp.knowledge_captured)
        run.execution_id = resp.execution_id
        run.execution = dict(resp.data)
        run.message = resp.message
        run.approval = _approval_for(mode_enum, decision, approved)
        # Reduce the provider response to one structured verdict, once, here —
        # before any workflow consumes it. Prefer the full response; the excerpt
        # is a safe fallback for older/partial reports.
        verdict_text = resp.data.get("result_full") or resp.data.get("result_excerpt") or ""
        run.verdict = parse_verdict(
            verdict_text,
            role=role_slug,
            # A response cut off at the token limit may be missing its verdict.
            # Passing this through lets the parser say so instead of the run
            # silently reading as approved.
            truncated=bool(resp.data.get("truncated", False)),
        ).to_dict()
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
        with _ApprovalLock(self._root):
            return self._review_locked(run_id, approve=approve, by=by, note=note)

    def _review_locked(
        self,
        run_id: str,
        approve: bool,
        by: str,
        note: str,
    ) -> AgentRun:
        run = self.get_run(run_id)
        wanted = "approved" if approve else "rejected"
        prior = str((run.approval or {}).get("decision") or "")
        if prior in {"approved", "rejected"}:
            # The first durable child decision is immutable. Always reconcile
            # its parent after a crash between those two records, even when the
            # retry asks for the opposite decision.
            self._sync_parent_team_run(run)
            return run

        if (
            prior != "pending"
            or not bool((run.approval or {}).get("required"))
            or not run.success
        ):
            raise ValueError(
                f"Agent run {run.run_id!r} is not a successful pending review "
                "and cannot receive an approval decision."
            )

        # Team stage runs are individually persisted, but they are not five
        # independent approval gates.  Only the final run named by the parent
        # may receive a decision, and only while that parent is actually
        # waiting for one.  Standalone runs have no parent and remain directly
        # reviewable.
        self._require_reviewable_team_child(run)

        approval = {
            "required": True,
            "decision": wanted,
            "by": by,
            "at": _now_iso(),
            "note": note,
        }
        if run.task_id:
            current = self._monday.task("get", task_id=run.task_id)
            current_status = str(getattr(current, "data", {}).get("status") or "")
            if getattr(current, "success", False) and current_status == "completed":
                recovered = self._recover_completed_approval(run)
                if recovered is not None:
                    # The terminal task record atomically includes its exact
                    # approval attribution. If the process died before the
                    # child checkpoint, restore the original actor/time/note.
                    run.approval = recovered
                    run.message = f"Approved; task {run.task_id} completed."
                    self._persist_required(run)
                    self._sync_parent_team_run(run)
                    return run
                else:
                    raise ValueError(
                        f"Approval was not recorded because task {run.task_id} is "
                        "'completed', but its durable completion history does not "
                        f"attribute that transition to agent run {run.run_id!r}."
                    )
            elif not getattr(current, "success", False) or current_status != "review":
                shown_status = repr(current_status) if current_status else "unknown"
                raise ValueError(
                    f"Approval was not recorded because task {run.task_id} is "
                    f"{shown_status}; expected 'review'. The task may have been "
                    "restarted or otherwise changed since this run was created."
                )
            elif approve:
                completed = self._monday.task(
                    "complete",
                    task_id=run.task_id,
                    reason=_approval_completion_reason(run, approval),
                    changed_by=by,
                )
                if not getattr(completed, "success", False):
                    detail = str(getattr(completed, "message", "") or "unknown failure")
                    raise ValueError(
                        f"Approval was not recorded because task {run.task_id} "
                        f"could not be completed: {detail}"
                    )

        run.approval = approval

        if approve and run.task_id:
            run.message = f"Approved; task {run.task_id} completed."
        elif not approve:
            run.message = f"Rejected by {by}; task {run.task_id} left for rework."

        self._persist_required(run)
        self._sync_parent_team_run(run)
        return run

    def get_run(self, run_id: str) -> AgentRun:
        _validate_run_id(run_id)
        name = f"{run_id}.json"
        try:
            directory_fd = open_directory_beneath(
                self._root,
                Path("logs") / "agents",
            )
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"No agent run {run_id!r} in {self._runs_dir}."
            ) from exc
        try:
            return _load_agent_run_record_at(
                directory_fd,
                name,
                expected_run_id=run_id,
            )
        finally:
            os.close(directory_fd)

    def history(
        self,
        role: str | None = None,
        task_id: str | None = None,
        limit: int = 20,
    ) -> list[AgentRun]:
        """Return past runs (most recent first), optionally filtered."""
        try:
            directory_fd = open_directory_beneath(
                self._root,
                Path("logs") / "agents",
            )
        except FileNotFoundError:
            return []
        runs: list[AgentRun] = []
        try:
            names = sorted(
                name
                for name in os.listdir(directory_fd)
                if name.startswith("run-") and name.endswith(".json")
            )
            for name in names:
                try:
                    runs.append(_load_agent_run_record_at(directory_fd, name))
                except Exception:
                    continue
        finally:
            os.close(directory_fd)
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

    def _recover_completed_approval(self, run: AgentRun) -> dict[str, Any] | None:
        """Recover only a completion durably attributed to this exact run.

        The terminal task record and its status-history transition are written
        together atomically. Removing the prior active copy is a separate cleanup
        step, but TaskManager treats the completed record as authoritative. The
        transition reason contains the original approval payload. This closes
        the crash window after task completion but before the child AgentRun
        checkpoint without treating an unrelated completion as approval.
        """
        try:
            task = TaskManager(self._root).get(run.task_id)
        except Exception:
            return None
        if task.status is not TaskStatus.COMPLETED or not task.status_history:
            return None

        transition = task.status_history[-1]
        if (
            transition.from_status is not TaskStatus.REVIEW
            or transition.to_status is not TaskStatus.COMPLETED
        ):
            return None
        approval = _approval_from_completion_reason(transition.reason, run)
        if approval is None or transition.changed_by != approval["by"]:
            return None
        return approval

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

    def _require_reviewable_team_child(self, run: AgentRun) -> None:
        """Reject a decision on a team child unless it is the live final gate."""
        parent: dict[str, Any] | None = None
        active_for_task: list[dict[str, Any]] = []
        for data in self._load_team_runs_for_approval():
            if data["task_id"] == run.task_id and data["status"] in {
                "running",
                "awaiting-approval",
            }:
                active_for_task.append(data)
            if run.run_id not in data["child_run_ids"]:
                continue
            if parent is not None:
                raise ValueError(
                    f"Agent run {run.run_id!r} belongs to more than one team run; "
                    "its approval parent is ambiguous."
                )
            parent = data

        if parent is None:
            if active_for_task:
                gates = ", ".join(
                    f"{item['team_run_id']} "
                    f"({item['status']}, gate "
                    f"{item['approval_run_id'] or '(unassigned)'})"
                    for item in active_for_task
                )
                raise ValueError(
                    f"Task {run.task_id!r} has an active team run and can only "
                    "be completed through its approval run; a standalone run "
                    "cannot bypass the final team gate: "
                    f"{gates}."
                )
            return

        team_run_id = str(parent.get("team_run_id") or "")
        if parent["task_id"] != run.task_id:
            raise ValueError(
                f"Agent run {run.run_id!r} belongs to team run {team_run_id!r}, "
                f"but their task IDs do not match ({run.task_id!r} versus "
                f"{parent['task_id']!r})."
            )
        approval_run_id = str(parent.get("approval_run_id") or "")
        if approval_run_id != run.run_id:
            gate = repr(approval_run_id) if approval_run_id else "not yet assigned"
            raise ValueError(
                f"Agent run {run.run_id!r} is a child of team run "
                f"{team_run_id!r} and cannot be reviewed directly; that team's "
                f"approval run is {gate}."
            )
        status = str(parent.get("status") or "")
        if status != "awaiting-approval":
            raise ValueError(
                f"Agent run {run.run_id!r} cannot be reviewed while team run "
                f"{team_run_id!r} is {status!r}; expected 'awaiting-approval'."
            )
        other_active = [
            str(item["team_run_id"])
            for item in active_for_task
            if item.get("team_run_id") != parent.get("team_run_id")
        ]
        if other_active:
            raise ValueError(
                f"Task {run.task_id!r} has multiple active team runs; "
                "resolve or supersede them before approval."
            )

    def _load_team_runs_for_approval(self) -> list[dict[str, Any]]:
        """Load and minimally validate every team record, failing closed.

        An unreadable record cannot safely be assumed unrelated to the run under
        review: it may be the only durable evidence that the run is a team child.
        Approval therefore stops until the record is repaired instead of silently
        treating the child as a standalone run.
        """
        try:
            directory_fd = open_directory_beneath(
                self._root,
                Path("logs") / "agents",
            )
        except FileNotFoundError:
            return []

        records: list[dict[str, Any]] = []
        try:
            names = sorted(
                name
                for name in os.listdir(directory_fd)
                if name.startswith("team-") and name.endswith(".json")
            )
            for name in names:
                try:
                    sanitized = _load_team_approval_record_at(
                        directory_fd,
                        name,
                    )
                except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                    raise ValueError(
                        "Approval cannot safely determine team-run ancestry because "
                        f"{name!r} is unreadable or malformed: {exc}"
                    ) from exc
                records.append(sanitized)
        finally:
            os.close(directory_fd)
        return records

    def _sync_parent_team_run(self, run: AgentRun) -> None:
        """
        Propagate a human review decision to the parent team run, if any.

        A team run (``logs/agents/team-*.json``) reaches ``awaiting-approval``
        with its final reviewer stage recorded as ``approval_run_id``. Reviewing
        that run here is the terminal human decision, so leave the team run's
        status in sync: ``completed`` on approve, ``changes-requested`` on
        reject. A no-op for standalone runs (nothing references them) and for
        team runs already past the gate. Written directly as JSON to avoid a
        circular import with agents.team; TeamRun.from_dict ignores extra keys.
        """
        child_approval = dict(run.approval or {})
        decision = str(child_approval.get("decision") or "")
        if decision not in {"approved", "rejected"}:
            return
        if not self._runs_dir.exists():
            return
        records = self._load_team_runs_for_approval()
        matches = [data for data in records if data["approval_run_id"] == run.run_id]
        if len(matches) > 1:
            raise ValueError(
                f"Agent run {run.run_id!r} is the approval gate for more than "
                "one team run; its approval parent is ambiguous."
            )
        for data in matches:
            if data["task_id"] != run.task_id:
                raise ValueError(
                    f"Agent run {run.run_id!r} and team run "
                    f"{data['team_run_id']!r} have different task IDs."
                )
            if data.get("approval_run_id") != run.run_id:
                continue
            if data.get("status") != "awaiting-approval":
                return  # decision already recorded; don't clobber
            actor = child_approval.get("by", "")
            if decision == "approved":
                data["status"] = "completed"
                data["success"] = True
                data["message"] = f"Approved by {actor}; task {run.task_id} completed."
            else:
                data["status"] = "changes-requested"
                data["success"] = False
                data["message"] = (
                    f"Rejected by {actor}; task {run.task_id} left for rework."
                )
            data["approval"] = {
                "decision": decision,
                "by": actor,
                "at": child_approval.get("at", ""),
                "note": child_approval.get("note", ""),
            }
            try:
                self._write_record(f"{data['team_run_id']}.json", data)
            except Exception as exc:
                raise RuntimeError(
                    "Could not persist the parent team approval checkpoint"
                ) from exc
            return  # a run is the approval gate of at most one team run

    def _run_path(self, run_id: str) -> Path:
        return self._runs_dir / f"{run_id}.json"

    def _persist(self, run: AgentRun) -> None:
        try:
            self._write_record(f"{run.run_id}.json", run.to_dict())
        except Exception as exc:
            raise RuntimeError("Could not persist the agent run checkpoint") from exc

    def _write_record(self, name: str, data: dict[str, Any]) -> None:
        directory_fd = open_directory_beneath(
            self._root,
            Path("logs") / "agents",
            create=True,
        )
        try:
            write_json_atomic_at(directory_fd, name, data)
        finally:
            os.close(directory_fd)

    def _persist_required(self, run: AgentRun) -> None:
        """Persist an approval decision before propagating it to its parent."""
        self._persist(run)


def _load_agent_run_record_at(
    directory_fd: int,
    name: str,
    *,
    expected_run_id: str | None = None,
) -> AgentRun:
    """Load and opportunistically migrate a run without replacing newer state."""
    for _attempt in range(_LEGACY_MIGRATION_ATTEMPTS):
        payload, identity = read_json_with_identity_at(directory_fd, name)
        run = _agent_run_from_payload(payload, name, expected_run_id)
        migrated = run.to_dict()
        if migrated == payload:
            return run
        try:
            write_json_atomic_at(
                directory_fd,
                name,
                migrated,
                expected_identity=identity,
            )
        except ConcurrentRecordChangeError:
            continue
        return run

    # Persistent contention should not turn a read into an unbounded retry.
    # Return one final, current snapshot without attempting another migration.
    payload, _identity = read_json_with_identity_at(directory_fd, name)
    return _agent_run_from_payload(payload, name, expected_run_id)


def _agent_run_from_payload(
    payload: Any,
    name: str,
    expected_run_id: str | None,
) -> AgentRun:
    if not isinstance(payload, dict):
        raise TypeError("agent run record must be a JSON object")
    run = AgentRun.from_dict(payload)
    if expected_run_id is not None and run.run_id != expected_run_id:
        raise ValueError(
            f"Agent run record {name!r} identifies itself as "
            f"{run.run_id!r}; expected {expected_run_id!r}."
        )
    return run


def _load_team_approval_record_at(
    directory_fd: int,
    name: str,
) -> dict[str, Any]:
    """Load and sanitize approval ancestry without clobbering team updates."""
    for _attempt in range(_LEGACY_MIGRATION_ATTEMPTS):
        payload, identity = read_json_with_identity_at(directory_fd, name)
        data = _validated_team_approval_payload(payload, name)
        migrated = sanitize_team_record(data)
        if migrated == data:
            return migrated
        try:
            write_json_atomic_at(
                directory_fd,
                name,
                migrated,
                expected_identity=identity,
            )
        except ConcurrentRecordChangeError:
            continue
        return migrated

    payload, _identity = read_json_with_identity_at(directory_fd, name)
    return sanitize_team_record(_validated_team_approval_payload(payload, name))


def _validated_team_approval_payload(
    payload: Any,
    name: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TypeError("team record must be a JSON object")

    team_run_id = payload.get("team_run_id")
    task_id = payload.get("task_id")
    status = payload.get("status")
    child_run_ids = payload.get("child_run_ids")
    approval_run_id = payload.get("approval_run_id")
    if not isinstance(team_run_id, str) or not team_run_id:
        raise TypeError("team_run_id must be a non-empty string")
    if team_run_id != Path(name).stem:
        raise ValueError("team_run_id does not match its filename")
    if not isinstance(task_id, str):
        raise TypeError("task_id must be a string")
    if not isinstance(status, str) or not status:
        raise TypeError("status must be a non-empty string")
    if not isinstance(child_run_ids, list) or not all(
        isinstance(item, str) and item for item in child_run_ids
    ):
        raise TypeError("child_run_ids must be a list of run IDs")
    if not isinstance(approval_run_id, str):
        raise TypeError("approval_run_id must be a string")
    if approval_run_id and approval_run_id not in child_run_ids:
        raise ValueError("approval_run_id is not a child run")
    if status == "awaiting-approval" and not approval_run_id:
        raise ValueError("awaiting team run has no approval_run_id")
    return payload


class _ApprovalLock:
    """Serialize approval compare-and-set across CLI and Telegram processes."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._file_fd: int | None = None
        self._directory_fd: int | None = None

    def __enter__(self) -> _ApprovalLock:
        file_fd, directory_fd = open_private_lock_file(
            self._root,
            Path("logs") / "agents",
            ".approval.lock",
        )
        try:
            fcntl.flock(file_fd, fcntl.LOCK_EX)
            validate_private_lock_file(directory_fd, file_fd, ".approval.lock")
        except BaseException:
            try:
                fcntl.flock(file_fd, fcntl.LOCK_UN)
            finally:
                os.close(file_fd)
                os.close(directory_fd)
            raise
        self._file_fd = file_fd
        self._directory_fd = directory_fd
        return self

    def __exit__(self, *_args: Any) -> None:
        if self._file_fd is not None:
            try:
                fcntl.flock(self._file_fd, fcntl.LOCK_UN)
            finally:
                os.close(self._file_fd)
                self._file_fd = None
                if self._directory_fd is not None:
                    os.close(self._directory_fd)
                    self._directory_fd = None


def _validate_run_id(run_id: str) -> None:
    if not is_agent_run_id(run_id):
        raise ValueError(
            "run_id must begin with 'run-' and contain only letters, digits, "
            "or hyphens (maximum 84 characters)."
        )


def _approval_completion_reason(run: AgentRun, approval: dict[str, Any]) -> str:
    record = {
        "schema": 1,
        "run_id": run.run_id,
        "task_id": run.task_id,
        "role": run.role,
        "approval": {
            "required": True,
            "decision": "approved",
            "by": approval["by"],
            "at": approval["at"],
            "note": approval["note"],
        },
    }
    return _APPROVAL_COMPLETION_PREFIX + json.dumps(
        record,
        separators=(",", ":"),
        sort_keys=True,
    )


def _approval_from_completion_reason(
    reason: str,
    run: AgentRun,
) -> dict[str, Any] | None:
    if not isinstance(reason, str) or not reason.startswith(_APPROVAL_COMPLETION_PREFIX):
        return None
    try:
        record = json.loads(reason.removeprefix(_APPROVAL_COMPLETION_PREFIX))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(record, dict) or record.get("schema") != 1:
        return None
    if (
        record.get("run_id") != run.run_id
        or record.get("task_id") != run.task_id
        or record.get("role") != run.role
    ):
        return None

    approval = record.get("approval")
    if not isinstance(approval, dict):
        return None
    if approval.get("required") is not True or approval.get("decision") != "approved":
        return None
    if not all(isinstance(approval.get(key), str) for key in ("by", "at", "note")):
        return None
    if not approval["at"]:
        return None
    return {
        "required": True,
        "decision": "approved",
        "by": approval["by"],
        "at": approval["at"],
        "note": approval["note"],
    }


def _new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"


def _now_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def _role_context(role: Any) -> str:
    capabilities = ", ".join(role.capabilities) or "general"
    return (
        "MondayOS role assignment (authoritative):\n"
        f"Role: {role.title} ({role.slug})\n"
        f"Responsibilities: {role.description}\n"
        f"Capabilities: {capabilities}\n"
        "Perform the task specifically from this role's perspective. Stay within "
        "these responsibilities and produce concrete evidence for later stages."
    )
