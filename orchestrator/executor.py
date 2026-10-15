"""
ExecutionOrchestrator — coordinates engineering work across AI providers.

This is the heart of the Execution Orchestrator. It does NOT implement any AI
model; it composes existing MondayOS subsystems and the AIProvider abstraction
into a single pipeline:

    task → advisor prioritises → planner builds a plan → execution queue →
    provider selection → provider executes → result validation →
    knowledge capture → task update → execution report

No provider-specific code lives here: provider selection uses only the generic
metadata every AIProvider exposes (name, is_local, cost_tier, capability_tier),
and execution goes exclusively through the AIProvider interface.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, cast

from brain.providers.base import (
    ProviderResponse,
    provider_failure_code,
    safe_provider_availability,
    safe_provider_identifier,
    safe_provider_model,
    safe_provider_stop_reason,
    safe_provider_token_count,
)
from events.types import Event, EventType
from orchestrator.planner import ExecutionPlanner
from orchestrator.queue import ExecutionQueue, ExecutionUnit
from orchestrator.report import ExecutionMode, ExecutionReport
from orchestrator.validator import ResultValidator

if TYPE_CHECKING:
    from brain.providers.base import AIProvider
    from events.bus import EventBus


# ---------------------------------------------------------------------------
# Provider selection policy
# ---------------------------------------------------------------------------

class ProviderSelectionPolicy(Enum):
    """
    Policy governing which provider runs an execution.

    PREFER_LOCAL       — prefer a local provider (no cost / data stays local),
                         falling back to the most capable remote one.
    LOWEST_COST        — pick the cheapest provider (ties broken by capability).
    HIGHEST_CAPABILITY — pick the most capable provider (ties broken by cost).
    ORDERED_FAILOVER   — try providers in the caller-supplied order.
    MANUAL             — use the explicitly named provider only.
    """

    PREFER_LOCAL = "prefer-local"
    LOWEST_COST = "lowest-cost"
    HIGHEST_CAPABILITY = "highest-capability"
    ORDERED_FAILOVER = "ordered-failover"
    MANUAL = "manual"

    @classmethod
    def from_str(cls, value: str) -> "ProviderSelectionPolicy":
        normalized = (value or "").strip().lower().replace("_", "-")
        for policy in cls:
            if policy.value == normalized:
                return policy
        aliases = {
            "local": cls.PREFER_LOCAL,
            "cost": cls.LOWEST_COST,
            "cheapest": cls.LOWEST_COST,
            "capability": cls.HIGHEST_CAPABILITY,
            "best": cls.HIGHEST_CAPABILITY,
            "fallback": cls.ORDERED_FAILOVER,
            "failover": cls.ORDERED_FAILOVER,
        }
        if normalized in aliases:
            return aliases[normalized]
        valid = sorted({p.value for p in cls})
        raise ValueError(f"Unknown selection policy {value!r}. Valid policies: {valid}")


def select_provider(
    providers: "list[AIProvider]",
    policy: ProviderSelectionPolicy,
    manual_name: str = "",
) -> "AIProvider | None":
    """
    Choose a provider from `providers` according to `policy`.

    An explicit `manual_name` always wins (manual override): if given, the named
    provider is returned when available, else None. Selection relies solely on
    the generic metadata exposed by every AIProvider, so no provider-specific
    logic leaks into the orchestrator.
    """
    available = [p for p in providers if p is not None]
    if not available:
        return None

    # Manual override (explicit name) takes precedence over any policy.
    if manual_name:
        wanted = safe_provider_identifier(manual_name)
        if wanted == "unknown":
            return None
        for p in available:
            if safe_provider_identifier(p, fallback="custom") == wanted:
                return p
        return None

    if policy in (
        ProviderSelectionPolicy.MANUAL,
        ProviderSelectionPolicy.ORDERED_FAILOVER,
    ):
        return available[0]

    if policy is ProviderSelectionPolicy.PREFER_LOCAL:
        locals_ = [p for p in available if _safe_provider_bool(p, "is_local")]
        pool = locals_ or available
        return max(
            pool,
            key=lambda p: (
                _safe_provider_tier(p, "capability_tier", 0),
                -_safe_provider_tier(p, "cost_tier", 10),
            ),
        )

    if policy is ProviderSelectionPolicy.LOWEST_COST:
        return min(
            available,
            key=lambda p: (
                _safe_provider_tier(p, "cost_tier", 10),
                -_safe_provider_tier(p, "capability_tier", 0),
            ),
        )

    if policy is ProviderSelectionPolicy.HIGHEST_CAPABILITY:
        return max(
            available,
            key=lambda p: (
                _safe_provider_tier(p, "capability_tier", 0),
                -_safe_provider_tier(p, "cost_tier", 10),
            ),
        )

    return available[0]


def rank_providers(
    providers: "list[AIProvider]",
    policy: ProviderSelectionPolicy,
    manual_name: str = "",
) -> "list[AIProvider]":
    """Return providers in execution order so the orchestrator can fail over."""
    available = [p for p in providers if p is not None]
    if manual_name:
        wanted = safe_provider_identifier(manual_name)
        if wanted == "unknown":
            return []
        return [
            p
            for p in available
            if safe_provider_identifier(p, fallback="custom") == wanted
        ]
    if policy is ProviderSelectionPolicy.MANUAL:
        return available[:1]
    if policy is ProviderSelectionPolicy.ORDERED_FAILOVER:
        return available
    if policy is ProviderSelectionPolicy.PREFER_LOCAL:
        def key(p: AIProvider) -> tuple[int, int]:
            return (
                -_safe_provider_tier(p, "capability_tier", 0),
                _safe_provider_tier(p, "cost_tier", 10),
            )

        local = sorted(
            (p for p in available if _safe_provider_bool(p, "is_local")),
            key=key,
        )
        remote = sorted(
            (p for p in available if not _safe_provider_bool(p, "is_local")),
            key=key,
        )
        return local + remote
    if policy is ProviderSelectionPolicy.LOWEST_COST:
        return sorted(
            available,
            key=lambda p: (
                _safe_provider_tier(p, "cost_tier", 10),
                -_safe_provider_tier(p, "capability_tier", 0),
            ),
        )
    if policy is ProviderSelectionPolicy.HIGHEST_CAPABILITY:
        return sorted(
            available,
            key=lambda p: (
                -_safe_provider_tier(p, "capability_tier", 0),
                _safe_provider_tier(p, "cost_tier", 10),
            ),
        )
    return available


def _safe_provider_bool(provider: Any, attribute: str) -> bool:
    """Read provider selection metadata without trusting extension properties."""
    try:
        value = getattr(provider, attribute)
    except Exception:  # noqa: BLE001 - provider metadata is an extension boundary
        return False
    return value if type(value) is bool else False


def _safe_provider_tier(provider: Any, attribute: str, default: int) -> int:
    """Read a small bounded provider selection tier without leaking failures."""
    try:
        value = getattr(provider, attribute)
    except Exception:  # noqa: BLE001 - provider metadata is an extension boundary
        return default
    if type(value) is not int:
        return default
    return min(max(value, 0), 10)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

# Task priority string → urgency rank (lower = more urgent).
_PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}

_TERMINAL_STATUSES = {"completed", "cancelled"}


class ExecutionOrchestrator:
    """
    Coordinates a single task's execution across the provider abstraction.

    The orchestrator reuses existing subsystems through the Monday public API
    (advise, learn, task) and the shared EventBus — it duplicates none of their
    logic. Construction is cheap; one orchestrator runs one execute() call.

    Args:
        monday:              Monday instance — used for advise / learn / task.
        project_root:        Project root (logs land under logs/executions/).
        providers:           Candidate AIProviders (may be empty).
        policy:              Provider selection policy.
        mode:                Execution safety mode.
        bus:                 Shared EventBus for audit events (optional).
        autonomous_enabled:  Must be True for AUTONOMOUS mode to act.
        manual_provider:     Explicit provider name override (optional).
        max_tokens:          Max output tokens for the provider execution call.
                             Raised from 2048 to 4096: thorough review responses
                             were routinely hitting the old ceiling. The verdict
                             is now requested first (agents.team) so it survives
                             truncation regardless, but a budget that fits the
                             whole answer keeps the prose useful to humans too.
    """

    def __init__(
        self,
        monday: Any,
        project_root: Path,
        providers: "list[AIProvider]",
        policy: ProviderSelectionPolicy = ProviderSelectionPolicy.PREFER_LOCAL,
        mode: ExecutionMode = ExecutionMode.REVIEW,
        bus: "EventBus | None" = None,
        autonomous_enabled: bool = False,
        manual_provider: str = "",
        max_tokens: int = 4096,
        extra_context: str = "",
        update_task: bool = True,
    ) -> None:
        self._monday = monday
        self._root = Path(project_root)
        self._providers = list(providers or [])
        self._policy = policy
        self._mode = mode
        self._bus = bus
        self._autonomous_enabled = autonomous_enabled
        self._manual_provider = manual_provider
        self._max_tokens = max_tokens
        # extra_context: extra background injected into the plan (e.g. prior
        # team-stage summaries). update_task: when False, execute the provider
        # and capture knowledge but leave the task status untouched — used by the
        # team workflow so intermediate stages don't churn the task lifecycle.
        self._extra_context = extra_context
        self._do_task_update = update_task

        self._planner = ExecutionPlanner()
        self._validator = ResultValidator()
        self._logs_dir = self._root / "logs" / "executions"

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def execute(self, task_id: str) -> ExecutionReport:
        """
        Run the full execution pipeline for one task.

        Always returns an ExecutionReport (never raises for expected failure
        paths) and always persists it to logs/executions/. The report's
        `status` and `success` describe the outcome.
        """
        execution_id = _new_execution_id()
        t0 = perf_counter()
        report = ExecutionReport(
            execution_id=execution_id,
            task_id=task_id,
            mode=self._mode.value,
            policy=self._policy.value,
            started_at=_now_iso(),
        )

        # ── 1. Load the task ─────────────────────────────────────────────
        task_resp = self._monday.task("get", task_id=task_id)
        if not task_resp.success:
            return self._finish(report, t0, status="failed", error=task_resp.message)
        task = task_resp.data

        if task.get("status") in _TERMINAL_STATUSES:
            return self._finish(
                report, t0, status="skipped",
                error=f"Task {task_id} is {task.get('status')}; nothing to execute.",
            )

        # ── 2. Advisor prioritises (best-effort) ─────────────────────────
        advisory = self._load_advisory()

        # ── 3. Planner builds the execution plan ─────────────────────────
        plan = self._planner.plan(task, advisory)
        if self._extra_context:
            plan.context = (
                f"{plan.context}\n\n{self._extra_context}".strip()
                if plan.context else self._extra_context
            )
        report.plan = plan.to_dict()
        report.prompt_summary = _summarize(plan.prompt)

        # ── 4. Execution queue ───────────────────────────────────────────
        queue = ExecutionQueue()
        queue.enqueue(ExecutionUnit(
            task_id=task_id,
            priority=_priority_rank(task),
            plan=plan,
            objective=plan.objective,
        ))
        unit = queue.dequeue()
        assert unit is not None  # we just enqueued one

        # ── 5. Provider selection ────────────────────────────────────────
        candidates = rank_providers(self._providers, self._policy, self._manual_provider)
        provider = candidates[0] if candidates else None

        # ── Safety gate: autonomous requires explicit enablement ─────────
        if self._mode is ExecutionMode.AUTONOMOUS and not self._autonomous_enabled:
            return self._finish(
                report, t0, status="blocked",
                error=(
                    "Autonomous mode requires explicit enablement "
                    "(autonomous_enabled=True / --enable-autonomous). "
                    "No autonomous file modification is permitted otherwise."
                ),
            )

        # ── Dry run: plan only, no provider call, no mutations ───────────
        if self._mode is ExecutionMode.DRY_RUN:
            return self._finish(
                report, t0, status="dry-run", success=True,
                error="",
            )

        if provider is None:
            return self._finish(
                report, t0, status="skipped",
                error="No AI provider available. Configure a provider or run in dry-run mode.",
            )

        # ── 6. Provider executes (through the abstraction only) ──────────
        response = None
        for candidate in candidates:
            candidate_name = safe_provider_identifier(candidate, fallback="custom")
            try:
                readiness = safe_provider_availability(
                    candidate.availability(),
                    provider=candidate_name,
                )
            except Exception as exc:
                readiness = safe_provider_availability(
                    None,
                    provider=candidate_name,
                    error=exc,
                )
            if not readiness.available:
                report.provider_attempts.append({
                    "provider": readiness.provider,
                    "status": "unavailable",
                    "reason": readiness.reason,
                })
                continue

            self._publish(EventType.MODEL_CALL_STARTED, task_id, execution_id,
                          {"provider": candidate_name, "policy": self._policy.value})
            try:
                raw_response = candidate.ask(
                    unit.plan.prompt,
                    context=unit.plan.context,
                    max_tokens=self._max_tokens,
                )
            except Exception as exc:  # a provider failure triggers the next candidate
                reason = provider_failure_code(exc)
                report.provider_attempts.append({
                    "provider": candidate_name,
                    "status": "failed",
                    "reason": reason,
                })
                self._publish(EventType.MODEL_CALL_FAILED, task_id, execution_id,
                              {"provider": candidate_name, "error": reason})
                continue

            if type(raw_response) is not ProviderResponse:
                reason = "provider-error"
                report.provider_attempts.append({
                    "provider": candidate_name,
                    "status": "failed",
                    "reason": reason,
                })
                self._publish(
                    EventType.MODEL_CALL_FAILED,
                    task_id,
                    execution_id,
                    {"provider": candidate_name, "error": reason},
                )
                continue

            if type(raw_response.content) is not str:
                reason = "provider-error"
                report.provider_attempts.append({
                    "provider": candidate_name,
                    "status": "failed",
                    "reason": reason,
                })
                self._publish(
                    EventType.MODEL_CALL_FAILED,
                    task_id,
                    execution_id,
                    {"provider": candidate_name, "error": reason},
                )
                continue

            raw_meta: dict[str, Any] = {}
            if type(raw_response.metadata) is dict:
                # Looking up a normal string in a provider-owned dict can call
                # a hostile key's ``__eq__`` after a hash collision. Copy only
                # exact string keys while iterating, then use our own mapping.
                for key, value in raw_response.metadata.items():
                    if type(key) is str and key in {"stop_reason", "truncated"}:
                        raw_meta[key] = value
            tokens_used = safe_provider_token_count(raw_response.tokens_used)
            stop_reason = safe_provider_stop_reason(raw_meta.get("stop_reason", ""))
            response = ProviderResponse(
                content=(
                    raw_response.content
                    if type(raw_response.content) is str
                    else ""
                ),
                model=safe_provider_model(candidate_name),
                provider=candidate_name,
                tokens_used=tokens_used,
                cached=raw_response.cached is True,
                metadata={
                    "stop_reason": stop_reason,
                    "truncated": (
                        raw_meta.get("truncated") is True
                        or stop_reason in {"length", "max_tokens"}
                    ),
                },
            )

            report.provider_used = candidate_name
            report.provider_attempts.append({
                "provider": candidate_name,
                "status": "completed",
                "reason": "",
            })
            break

        if response is None:
            return self._finish(
                report, t0, status="failed",
                error=(
                    "All eligible AI providers failed. Check provider "
                    "configuration or retry later."
                ),
            )
        report.model_used = response.model
        self._publish(EventType.MODEL_CALL_COMPLETED, task_id, execution_id,
                      {"provider": report.provider_used, "model": report.model_used,
                       "tokens_used": response.tokens_used})

        report.result_excerpt = response.content[:500]
        report.result_full = response.content or ""
        meta = response.metadata
        report.stop_reason = safe_provider_stop_reason(meta.get("stop_reason", ""))
        report.truncated = meta.get("truncated") is True

        # ── 7. Result validation ─────────────────────────────────────────
        validation = self._validator.validate(plan, response, task)
        report.validation = validation.to_dict()
        report.confidence = validation.score
        if not validation.valid:
            return self._finish(
                report, t0, status="validation-failed",
                error="; ".join(validation.issues) or "Result failed validation.",
            )

        # ── 8. Knowledge capture ─────────────────────────────────────────
        report.knowledge_captured = self._capture_knowledge(
            task,
            response,
            report.provider_used,
        )

        # ── 9. Task update ───────────────────────────────────────────────
        if self._do_task_update:
            self._update_task(task_id, report)
        else:
            # Provider ran and knowledge was captured, but the task lifecycle is
            # left to the caller (e.g. the team workflow moves it once at the end).
            report.task_status = "executed"

        # ── 10. Execution report (persisted in _finish) ──────────────────
        return self._finish(report, t0, status=report.task_status or "completed", success=True)

    # ------------------------------------------------------------------
    # Pipeline helpers
    # ------------------------------------------------------------------

    def _load_advisory(self) -> dict[str, Any] | None:
        try:
            advise_resp = self._monday.advise()
            if advise_resp.success:
                return cast(dict[str, Any], advise_resp.data)
        except Exception:
            pass
        return None

    def _capture_knowledge(
        self,
        task: dict[str, Any],
        response: Any,
        provider_name: str,
    ) -> list[str]:
        """Persist the execution result as a knowledge entry. Best-effort."""
        content = (response.content or "").strip()
        if not content:
            return []
        title = f"Execution result: {task.get('title', task.get('id', ''))}"
        body = (
            f"{content}\n\n"
            f"---\n"
            f"_Captured by the Execution Orchestrator for task "
            f"{task.get('id', '')} via provider '{provider_name}'._"
        )
        try:
            learn_resp = self._monday.learn(
                content=body,
                title=title,
                entry_type="research",
                tags=["execution", provider_name],
                components=[task.get("task_type", "")] if task.get("task_type") else [],
                # This is model output, not curated knowledge. Saying so routes
                # it to the gitignored runtime store instead of the
                # source-controlled tree, so a run no longer dirties the working
                # tree it is about to be judged on.
                authored_by="agent",
            )
            if learn_resp.accepted:
                return [learn_resp.entry_id]
        except Exception:
            pass
        return []

    def _update_task(self, task_id: str, report: ExecutionReport) -> None:
        """
        Advance the task per the execution mode.

        REVIEW     — start the task and move it to REVIEW for a human.
        AUTONOMOUS — start the task and complete it.

        Task mutations flow through the Monday public API; failures are
        recorded on the report but never raise.
        """
        # Ensure the task is in progress (idempotent if already running).
        self._monday.task("start", task_id=task_id)

        if self._mode is ExecutionMode.AUTONOMOUS:
            r = self._monday.task(
                "complete",
                task_id=task_id,
                reason=(
                    f"Executed autonomously via {report.provider_used} "
                    f"(execution {report.execution_id[:8]})."
                ),
            )
            report.task_status = "completed" if r.success else "in-progress"
        else:  # REVIEW
            r = self._monday.task(
                "review",
                task_id=task_id,
                reason=(
                    f"Executed via {report.provider_used} "
                    f"(execution {report.execution_id[:8]}); awaiting human review."
                ),
            )
            report.task_status = "review" if r.success else "in-progress"

    def _publish(
        self,
        event_type: EventType,
        task_id: str,
        execution_id: str,
        payload: dict[str, Any],
    ) -> None:
        if self._bus is None:
            return
        try:
            self._bus.publish(Event(
                event_type=event_type,
                source="orchestrator",
                timestamp=datetime.now(tz=timezone.utc),
                payload=payload,
                task_id=task_id,
                correlation_id=execution_id,
            ))
        except Exception:
            pass  # audit publishing must never break execution

    def _finish(
        self,
        report: ExecutionReport,
        t0: float,
        *,
        status: str,
        success: bool = False,
        error: str = "",
    ) -> ExecutionReport:
        report.status = status
        report.success = success
        if error:
            report.error = error
        report.completed_at = _now_iso()
        report.duration_ms = (perf_counter() - t0) * 1000.0
        try:
            report.write_log(self._logs_dir)
        except Exception:
            pass  # persistence failure should not mask the result
        return report


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------

def _new_execution_id() -> str:
    return f"exec-{uuid.uuid4().hex[:12]}"


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _priority_rank(task: dict[str, Any]) -> int:
    return _PRIORITY_RANK.get(task.get("priority", "P2"), 2)


def _summarize(prompt: str, limit: int = 140) -> str:
    flat = " ".join(prompt.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
