"""
TeamWorkflow — run the registered agents as a collaborating team on one task.

The team executes a fixed sequence of roles end to end:

    CPO → Lead Engineer → QA → Security → Reviewer → (Human Approval)

Each stage is a normal role run (reused from AgentRuntime, logged as an AgentRun
under logs/agents/), and every stage receives the summaries of all prior stages
as extra context. QA, Security, and Reviewer are **blocking** — if any returns a
`block` (or `needs_changes`) verdict, the pipeline stops early. When all stages
pass, the task is moved to REVIEW awaiting the final human approval; nothing is
committed, pushed, or executed live. The whole run is recorded as one parent
TeamRun that references its child role-run IDs.

Verdicts are **structured** (MondayOS v2.4). Each stage run carries an
``AgentVerdict`` (see agents.verdicts) with an explicit ``verdict`` field; the
workflow inspects only that field and never scans the model's prose. This
replaced the old substring matching, which falsely vetoed on words like
"blocker" appearing in explanatory text.

A blocking role must produce a **valid** ``pass`` to advance. A missing,
malformed, or truncated verdict is ``invalid`` and stops the run — it is not
treated as approval. Earlier revisions defaulted an absent verdict to ``pass``,
so a QA, Security, or Reviewer stage that returned nothing readable advanced the
pipeline as though it had signed off.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents.roles import get_role
from agents.runtime import AgentRuntime
from agents.types import AgentRun
from agents.verdicts import ALL_VERDICTS, BLOCK, INVALID, NEEDS_CHANGES, PASS
from core.atomic import write_json_atomic
from core.errors import TeamCheckpointError

# The collaboration order. Human Approval is the terminal gate, not an agent run.
TEAM_SEQUENCE: tuple[str, ...] = ("cpo", "lead-engineer", "qa", "security", "reviewer")
_TEAM_RUN_ID = re.compile(r"^team-[A-Za-z0-9-]{1,80}$")

# Roles that must produce a valid `pass` for the pipeline to advance.
BLOCKING_ROLES: frozenset[str] = frozenset({"qa", "security", "reviewer"})

# Verdicts (from a blocking role) that stop the pipeline. `block` is a hard veto;
# `needs_changes` sends the work back for rework; `invalid` means the stage never
# produced a readable verdict at all and therefore cannot have approved anything.
STOPPING_VERDICTS: frozenset[str] = frozenset({BLOCK, NEEDS_CHANGES, INVALID})

# Team status recorded when a blocking role stops the run.
_STATUS_FOR_VERDICT: dict[str, str] = {
    BLOCK: "blocked",
    NEEDS_CHANGES: "changes-requested",
    INVALID: "invalid-verdict",
}


class TeamRunBusyError(ValueError):
    """Another live worker currently owns this task's team-run lease."""


# Appended to every stage's context so providers emit a structured verdict.
#
# The JSON is requested FIRST, before any prose. Providers are called with a
# finite output-token budget, and a thorough reviewer will happily spend all of
# it on narrative; a verdict requested last is the part that gets cut off. Asking
# for it first makes the verdict structurally survive truncation — the prose is
# what gets lost instead, and the prose is not what the workflow reads.
VERDICT_INSTRUCTION: str = (
    "IMPORTANT — begin your response with a single JSON object in a ```json code "
    "block, before any other text, of exactly this shape:\n"
    '{"verdict": "pass" | "needs_changes" | "block", '
    '"confidence": "high" | "medium" | "low", '
    '"summary": "one sentence", '
    '"findings": ["..."], "recommendations": ["..."]}\n'
    "After the closing fence, write whatever explanation you wish.\n"
    "Use \"block\" only for a genuine blocking defect. This JSON is the ONLY "
    "signal the workflow reads: wording elsewhere in your reply is ignored, and "
    "a reply without this object is treated as no verdict at all — which stops "
    "the pipeline rather than passing it."
)


@dataclass
class TeamStage:
    """One stage of a team run."""

    role: str
    run_id: str = ""
    agent_name: str = ""
    provider_used: str = ""
    provider_model: str = ""
    status: str = ""            # executed | dry-run | blocked | unavailable | skipped | …
    verdict: str = ""           # pass | needs_changes | block (structured)
    confidence: str = ""        # from the structured verdict, for humans
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TeamRun:
    """
    The parent record of one team workflow run.

    Persisted as logs/agents/{team_run_id}.json. References its child role-run
    IDs (each also persisted individually as logs/agents/run-*.json).
    """

    team_run_id: str
    task_id: str
    mode: str = ""
    # running | interrupted | awaiting-approval | completed | changes-requested
    # | blocked | failed | dry-run | rejected. A run reaching REVIEW is
    # 'awaiting-approval' until the human decision on approval_run_id lands (see
    # AgentRuntime.review), which transitions it to 'completed' (approved) or
    # 'changes-requested' (rejected).
    status: str = ""
    # False between identity reservation and task-lease acquisition, True once
    # this worker acquired it, and None for legacy records. This lets recovery
    # distinguish a crashed lease holder from a concurrent pre-lease contender.
    lease_acquired: bool | None = None
    success: bool = False
    created_at: str = ""
    sequence: list[str] = field(default_factory=list)
    stages: list[dict[str, Any]] = field(default_factory=list)
    child_run_ids: list[str] = field(default_factory=list)
    stopped_at: str = ""
    stopped_reason: str = ""
    approval_run_id: str = ""
    # {decision, by, at, note} once reviewed
    approval: dict[str, Any] = field(default_factory=dict)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TeamRun:
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


class TeamWorkflow:
    """
    Runs the agent team over a single task. Reuses AgentRuntime for each stage,
    so all the per-role safety (approval gate, review-required, logging) applies
    unchanged. This engine adds the sequencing, prior-stage context, early stop,
    and the parent TeamRun record.
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
        self._runtime = AgentRuntime(
            monday,
            project_root,
            require_human_approval,
            configured_providers=configured_providers,
        )
        self._runs_dir = self._root / "logs" / "agents"

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(
        self,
        task_id: str,
        provider: str = "",
        mode: str = "review",
        stage_providers: dict[str, Any] | None = None,
        team_run_id: str = "",
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        checkpoint_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> TeamRun:
        """
        Run the full team pipeline over ``task_id``.

        Args:
            task_id:         The task to work.
            provider:        Optional provider-name override for productive
                             stages. Reviewer remains OpenAI; "fake" is allowed
                             only for an offline run.
            mode:            "review" (default) or "dry-run". Autonomous is not
                             supported — team runs are review-required.
            stage_providers: Optional {role: AIProvider} injection (tests /
                             advanced use). A non-OpenAI Reviewer injection is
                             ignored; "fake" is allowed for offline tests.
            team_run_id:     Optional caller-reserved identity for durable remote
                             control. Must begin with ``team-``.
            progress_callback: Optional best-effort observer invoked at team and
                             stage boundaries. Observer failures never stop work.
            checkpoint_callback: Optional required observer invoked once with the
                             persisted ``team_started`` identity before any task
                             or model work. Failure aborts the run.

        Returns a persisted TeamRun for expected workflow failures. A durable
        startup checkpoint failure raises TeamCheckpointError before work begins.
        """
        requested_id = (team_run_id or "").strip()
        if requested_id:
            _validate_team_run_id(requested_id)
        team = TeamRun(
            team_run_id=requested_id or _new_team_id(),
            task_id=task_id,
            mode=_norm_mode(mode),
            created_at=_now_iso(),
            sequence=list(TEAM_SEQUENCE),
        )
        # Claim the identity before competing for the task lease. This makes a
        # caller-reserved ID a real compare-and-create token: a retry reconciles
        # the exact record, while a different ID that loses the lease is rejected
        # under its own exact identity. The flag prevents the lease winner from
        # mistaking a concurrent pre-lease contender for a stale crashed worker.
        team.status = "running"
        team.lease_acquired = False
        reconciled = self._claim_team_run(
            team,
            reconcile_existing=bool(requested_id),
        )
        if reconciled is not None:
            return reconciled

        # Hold one task-scoped kernel lease for the *entire* workflow. If a
        # process is killed, the OS releases the lease automatically; the next
        # worker can then identify and interrupt the stale `running` record.
        # A hash keeps arbitrary task IDs out of filesystem paths.
        lease_path = _task_lease_path(self._runs_dir, task_id)
        lease = _TeamRunLease(lease_path)
        try:
            lease.acquire()
        except TeamRunBusyError:
            return self._busy_result(
                team,
                progress_callback=progress_callback,
            )
        try:
            # A lease winner may have settled this contender just before it
            # released the lock. Re-read the durable claim so a late-scheduled
            # process cannot resurrect a rejected record and execute anyway.
            claimed = self.get_team_run(team.team_run_id)
            if claimed.status != "running" or claimed.lease_acquired is not False:
                return claimed
            team = claimed
            team.lease_acquired = True
            self._persist_required(team)
            return self._run_with_lease(
                team,
                provider=provider,
                mode=mode,
                stage_providers=stage_providers,
                progress_callback=progress_callback,
                checkpoint_callback=checkpoint_callback,
            )
        finally:
            try:
                self._settle_prelease_contenders(
                    task_id=task_id,
                    owner_team_run_id=team.team_run_id,
                )
            finally:
                lease.release()

    def _run_with_lease(
        self,
        team: TeamRun,
        *,
        provider: str,
        mode: str,
        stage_providers: dict[str, Any] | None,
        progress_callback: Callable[[dict[str, Any]], None] | None,
        checkpoint_callback: Callable[[dict[str, Any]], None] | None,
    ) -> TeamRun:
        """Execute a claimed team run while its task lease remains held."""
        task_id = team.task_id
        started_event = {
            "event": "team_started",
            "team_run_id": team.team_run_id,
            "task_id": task_id,
            "sequence": list(TEAM_SEQUENCE),
        }

        # Acquiring the task lease proves that no live worker from this version
        # still owns an older `running` record. Recover all such records before
        # doing work. An awaiting approval is intentionally different: it is a
        # live human gate and continues to block a new run.
        prior_runs = [
            existing
            for existing in self.history(task_id=task_id, limit=0)
            if existing.team_run_id != team.team_run_id
        ]
        for stale in (
            run
            for run in prior_runs
            if run.status == "running" and run.lease_acquired is not False
        ):
            stale.status = "interrupted"
            stale.success = False
            stale.stopped_reason = (
                "The previous worker ended while holding the task run lease."
            )
            stale.message = stale.stopped_reason
            self._persist_required(stale)

        awaiting = next(
            (run for run in prior_runs if run.status == "awaiting-approval"),
            None,
        )
        if awaiting is not None:
            team.status = "rejected"
            team.success = False
            team.message = (
                f"Task {task_id} already has an active team run "
                f"({awaiting.team_run_id}, awaiting-approval); approve or reject "
                "that run before starting another."
            )
            self._persist_required(team)
            _notify(progress_callback, _finished_event(team))
            return team

        try:
            _checkpoint(checkpoint_callback, started_event)
        except TeamCheckpointError:
            team.status = "interrupted"
            team.success = False
            team.stopped_reason = "The external startup checkpoint could not be recorded."
            team.message = team.stopped_reason
            self._persist_required(team)
            raise
        _notify(progress_callback, started_event)

        norm_mode = _norm_mode(mode)
        if norm_mode not in ("review", "dry-run"):
            team.status = "rejected"
            team.message = (
                f"Unsupported mode {mode!r}. Team runs are review-required: "
                "use 'review' (default) or 'dry-run'. Autonomous is not allowed."
            )
            self._persist(team)
            _notify(progress_callback, _finished_event(team))
            return team

        # Task must exist and be actionable.
        task_resp = self._monday.task("get", task_id=task_id)
        if not task_resp.success:
            team.status = "failed"
            team.message = task_resp.message
            self._persist(team)
            _notify(progress_callback, _finished_event(team))
            return team
        if task_resp.data.get("status") in ("completed", "cancelled"):
            team.status = "failed"
            team.message = f"Task {task_id} is {task_resp.data.get('status')}; nothing to run."
            self._persist(team)
            _notify(progress_callback, _finished_event(team))
            return team

        # Move the task into progress once (the stages themselves don't touch the
        # task lifecycle — update_task=False — so the team owns it).
        if norm_mode == "review":
            started = self._monday.task("start", task_id=task_id)
            if (
                not started.success
                or str(started.data.get("status") or "") != "in-progress"
            ):
                team.status = "failed"
                team.message = (
                    f"Could not place task {task_id} in progress: "
                    f"{started.message or started.data.get('status') or 'unknown state'}"
                )
                self._persist(team)
                _notify(progress_callback, _finished_event(team))
                return team

        prior: list[tuple[str, str]] = []  # (role, summary) accumulated across stages
        stopped = False

        for role in TEAM_SEQUENCE:
            _notify(progress_callback, {
                "event": "stage_started",
                "team_run_id": team.team_run_id,
                "task_id": task_id,
                "role": role,
            })
            stage_provider = provider
            if role == "reviewer" and provider.strip().lower() not in {
                "",
                "openai",
                "fake",
            }:
                # A whole-team pin may shape productive stages, but it may not
                # replace the mandatory independent ChatGPT/OpenAI gate.
                stage_provider = ""
            stage_provider_instance = (stage_providers or {}).get(role)
            if role == "reviewer" and stage_provider_instance is not None:
                injected_name = str(
                    getattr(stage_provider_instance, "name", "") or ""
                ).strip().lower()
                if injected_name not in {"openai", "fake"}:
                    # `stage_providers` is public test/advanced-use input, not a
                    # bypass around the mandatory independent OpenAI reviewer.
                    # The fake provider remains available for offline tests.
                    stage_provider_instance = None
            run = self._runtime.run(
                task_id=task_id,
                role=role,
                provider=stage_provider,
                mode=norm_mode,
                update_task=False,
                extra_context=_context_from(prior),
                provider_instance=stage_provider_instance,
            )
            summary = _summary(run)
            verdict = _stage_verdict(run)
            team.stages.append(TeamStage(
                role=role,
                run_id=run.run_id,
                agent_name=run.agent_name,
                provider_used=run.provider_used,
                provider_model=run.provider_model,
                status=run.status,
                verdict=verdict,
                confidence=str((run.verdict or {}).get("confidence", "")),
                summary=summary,
            ).to_dict())
            team.child_run_ids.append(run.run_id)
            prior.append((role, summary))
            self._persist(team)
            _notify(progress_callback, {
                "event": "stage_finished",
                "team_run_id": team.team_run_id,
                "task_id": task_id,
                "role": role,
                "status": run.status,
                "verdict": verdict,
                "provider": run.provider_used,
                "summary": summary,
            })

            if not run.success:
                team.status = "failed"
                team.stopped_at = role
                team.stopped_reason = run.message or "stage did not succeed"
                stopped = True
                break
            # Verdict gating applies to real runs only. A dry run plans the
            # stages without calling a provider, so there is no response to
            # carry a verdict — requiring one would make dry-run impossible.
            if norm_mode == "review" and _stops_pipeline(role, verdict):
                # `block` is a hard veto; `needs_changes` sends the work back for
                # rework; `invalid` means the stage never produced a readable
                # verdict. All stop the pipeline short of human approval.
                team.status = _STATUS_FOR_VERDICT.get(verdict, "blocked")
                team.stopped_at = role
                team.stopped_reason = _stop_reason(role, verdict, summary)
                stopped = True
                break

        if stopped:
            team.success = False
            team.message = (
                f"Pipeline stopped at {get_role(team.stopped_at).title}: {team.stopped_reason}"
            )
            self._persist(team)
            _notify(progress_callback, _finished_event(team))
            return team

        # All stages passed.
        if norm_mode == "dry-run":
            team.status = "dry-run"
            team.success = True
            team.message = "Dry run — all stages planned, no provider calls or changes."
        else:
            # Move the task to REVIEW for the terminal human approval.
            reviewed = self._monday.task(
                "review", task_id=task_id,
                reason=f"Team run {team.team_run_id} passed all stages; awaiting human approval.",
            )
            if not reviewed.success:
                current = self._monday.task("get", task_id=task_id)
                already_review = bool(
                    current.success and current.data.get("status") == "review"
                )
                if not already_review:
                    team.status = "failed"
                    team.success = False
                    team.message = (
                        f"All stages passed, but task {task_id} could not enter "
                        f"review: {reviewed.message}"
                    )
                    self._persist(team)
                    _notify(progress_callback, _finished_event(team))
                    return team
            team.status = "awaiting-approval"
            team.success = True
            team.approval_run_id = team.child_run_ids[-1] if team.child_run_ids else ""
            team.message = (
                "All stages passed. Task is at REVIEW awaiting human approval — "
                f"approve with: monday agent review {team.approval_run_id} --approve"
            )

        self._persist(team)
        _notify(progress_callback, _finished_event(team))
        return team

    def get_team_run(self, team_run_id: str) -> TeamRun:
        checked_id = _validate_team_run_id(team_run_id)
        path = self._runs_dir / f"{checked_id}.json"
        if not path.exists():
            if path.is_symlink():
                raise ValueError(f"Refusing symlinked team run record {checked_id!r}")
            raise FileNotFoundError(f"No team run {checked_id!r} in {self._runs_dir}.")
        return _load_team_run_record(path, expected_id=checked_id)

    def interrupt(self, team_run_id: str, reason: str = "") -> TeamRun:
        """Atomically mark an abandoned running team so recovery is auditable."""
        candidate = self.get_team_run(team_run_id)
        lease_path = _task_lease_path(self._runs_dir, candidate.task_id)
        try:
            with _TeamRunLease(lease_path):
                # Re-read under the lease. The workflow may have completed
                # between the initial identity lookup and lease acquisition.
                team = self.get_team_run(team_run_id)
                if team.status == "running":
                    team.status = "interrupted"
                    team.success = False
                    team.stopped_reason = (
                        reason or "Worker stopped before the team run finished."
                    )
                    team.message = team.stopped_reason
                    # Recovery must not begin unless this state change is durable.
                    write_json_atomic(
                        self._runs_dir / f"{team.team_run_id}.json",
                        team.to_dict(),
                    )
                return team
        except TeamRunBusyError as exc:
            raise TeamRunBusyError(
                f"Cannot interrupt {candidate.team_run_id}: its task still has "
                "a live team workflow."
            ) from exc

    def history(self, task_id: str | None = None, limit: int = 20) -> list[TeamRun]:
        if not self._runs_dir.exists():
            return []
        runs: list[TeamRun] = []
        for path in sorted(self._runs_dir.glob("team-*.json")):
            runs.append(_load_team_run_record(path, expected_id=path.stem))
        if task_id is not None:
            runs = [r for r in runs if r.task_id == task_id]
        runs.sort(key=lambda r: r.created_at, reverse=True)
        return runs[: max(0, limit)] if limit else runs

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _claim_team_run(
        self,
        team: TeamRun,
        *,
        reconcile_existing: bool,
    ) -> TeamRun | None:
        """Atomically claim a new identity or reconcile an exact retry.

        The task lease only serializes callers for one task. The short global
        identity lock also protects the same caller-provided ID from being
        claimed concurrently by *different* task IDs.
        """
        with _TeamIdentityLock(self._runs_dir / ".team-identity.lock"):
            path = self._runs_dir / f"{team.team_run_id}.json"
            if path.exists() or path.is_symlink():
                if reconcile_existing:
                    existing = _load_team_run_record(
                        path,
                        expected_id=team.team_run_id,
                    )
                    if existing.task_id != team.task_id:
                        raise ValueError(
                            f"team_run_id {team.team_run_id!r} already belongs "
                            f"to task {existing.task_id!r}, not {team.task_id!r}"
                        )
                    return existing

                # A generated ID collision is extraordinarily unlikely, but it
                # must not turn into an overwrite.
                while path.exists() or path.is_symlink():
                    team.team_run_id = _new_team_id()
                    path = self._runs_dir / f"{team.team_run_id}.json"

            self._persist_required(team)
        return None

    def _busy_result(
        self,
        requested: TeamRun,
        *,
        progress_callback: Callable[[dict[str, Any]], None] | None,
    ) -> TeamRun:
        """Reject a claimed contender under its exact durable identity."""
        requested.status = "rejected"
        requested.success = False
        requested.message = (
            f"Task {requested.task_id} already has a live team workflow; "
            "wait for it to finish before starting another."
        )
        self._persist_required(requested)
        _notify(progress_callback, _finished_event(requested))
        return requested

    def _settle_prelease_contenders(
        self,
        *,
        task_id: str,
        owner_team_run_id: str,
    ) -> None:
        """Close abandoned/concurrent identity claims before releasing a lease."""
        for contender in self.history(task_id=task_id, limit=0):
            if (
                contender.team_run_id == owner_team_run_id
                or contender.status != "running"
                or contender.lease_acquired is not False
            ):
                continue
            contender.status = "rejected"
            contender.success = False
            contender.stopped_reason = (
                f"Team run {owner_team_run_id} held the task run lease through "
                "completion; this pre-lease contender did not execute."
            )
            contender.message = contender.stopped_reason
            self._persist_required(contender)

    def _persist(self, team: TeamRun) -> None:
        checked_id = _validate_team_run_id(team.team_run_id)
        try:
            write_json_atomic(
                self._runs_dir / f"{checked_id}.json",
                team.to_dict(),
            )
        except Exception as exc:
            raise TeamCheckpointError(
                "Could not persist the team run checkpoint"
            ) from exc

    def _persist_required(self, team: TeamRun) -> None:
        """Persist the identity required for safe retry before any work starts."""
        self._persist(team)


class _TeamRunLease:
    """Non-blocking kernel lease held for one task's complete workflow."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: Any = None

    def acquire(self) -> _TeamRunLease:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self._path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._file.close()
            self._file = None
            raise TeamRunBusyError("A live team run already owns this task") from exc
        return self

    def release(self) -> None:
        if self._file is not None:
            try:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            finally:
                self._file.close()
                self._file = None

    def __enter__(self) -> _TeamRunLease:
        return self.acquire()

    def __exit__(self, *_args: Any) -> None:
        self.release()


class _TeamIdentityLock:
    """Serialize the short compare-and-create window for team identities."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: Any = None

    def __enter__(self) -> _TeamIdentityLock:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self._path.open("a+", encoding="utf-8")
        fcntl.flock(self._file.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *_args: Any) -> None:
        if self._file is not None:
            try:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            finally:
                self._file.close()
                self._file = None


def _notify(
    callback: Callable[[dict[str, Any]], None] | None,
    event: dict[str, Any],
) -> None:
    """Deliver optional progress without allowing an observer to break work."""
    if callback is None:
        return
    try:
        callback(event)
    except Exception:
        pass


def _checkpoint(
    callback: Callable[[dict[str, Any]], None] | None,
    event: dict[str, Any],
) -> None:
    """Deliver the required pre-work checkpoint, failing closed on error."""
    if callback is None:
        return
    try:
        callback(event)
    except Exception as exc:
        raise TeamCheckpointError(
            "Could not record the external team startup checkpoint"
        ) from exc


def _finished_event(team: TeamRun) -> dict[str, Any]:
    return {
        "event": "team_finished",
        "team_run_id": team.team_run_id,
        "task_id": team.task_id,
        "status": team.status,
        "success": team.success,
        "message": team.message,
    }


def _stage_verdict(run: AgentRun) -> str:
    """
    The stage's actual structured verdict, recorded verbatim for audit.

    Reads only the structured AgentVerdict — never prose. A run that did not
    succeed, or was gate-blocked, is a structural failure and reported as
    ``block``. A run with no readable verdict is ``invalid``; it is NOT assumed
    to be a pass, which is the defect this function previously carried.
    """
    if not run.success or run.status == "blocked":
        return BLOCK
    verdict = str((run.verdict or {}).get("verdict") or "")
    return verdict if verdict in ALL_VERDICTS else INVALID


def _stops_pipeline(role: str, verdict: str) -> bool:
    """
    Whether this stage's verdict halts the team run.

    A blocking role (QA / Security / Reviewer) must produce a valid ``pass`` to
    advance — anything else, including a missing or unreadable verdict, stops.

    Non-blocking roles (CPO, Lead Engineer) are productive rather than
    gatekeepers: their verdict never halts the team, unchanged from before. A
    structural failure still stops any role, but that is handled by the caller
    before this function is consulted, so it is deliberately not repeated here.
    """
    if role not in BLOCKING_ROLES:
        return False
    return verdict != PASS


def _stop_reason(role: str, verdict: str, summary: str) -> str:
    if verdict == INVALID:
        return (
            f"{role} produced no valid structured verdict "
            "(missing, malformed, or truncated) — the stage cannot be treated as a pass"
        )
    return summary or f"{role} returned verdict '{verdict}'"


def _summary(run: AgentRun) -> str:
    """Short, human-facing summary — the structured verdict's summary first."""
    structured = str((run.verdict or {}).get("summary") or "").strip()
    text = (
        structured
        or (run.execution.get("result_excerpt", "") or "").strip()
        or (run.message or "").strip()
    )
    return text[:240]


def _context_from(prior: list[tuple[str, str]]) -> str:
    lines: list[str] = []
    if prior:
        lines.append("Prior stage summaries (for your context):")
        for role, summary in prior:
            lines.append(f"- {get_role(role).title}: {summary}")
        lines.append("")
    lines.append(VERDICT_INSTRUCTION)
    return "\n".join(lines)


def _load_team_run_record(path: Path, *, expected_id: str) -> TeamRun:
    """Load one parent record only when its durable identity matches its path."""
    try:
        if path.is_symlink():
            raise ValueError(f"Refusing symlinked team run record {expected_id!r}")
        _validate_team_run_id(expected_id)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("team run record must be a JSON object")
        team = TeamRun.from_dict(payload)
        _validate_team_run_id(team.team_run_id)
        if team.team_run_id != expected_id:
            raise ValueError(
                f"team_run_id {team.team_run_id!r} does not match filename "
                f"{expected_id!r}"
            )
        return team
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError(
            f"Team run record {path.name!r} is unreadable or malformed: {exc}"
        ) from exc


def _new_team_id() -> str:
    return f"team-{uuid.uuid4().hex[:12]}"


def _task_lease_path(runs_dir: Path, task_id: str) -> Path:
    lease_key = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
    return runs_dir / "locks" / f"{lease_key}.lock"


def _validate_team_run_id(team_run_id: str) -> str:
    checked_id = (team_run_id or "").strip()
    if not _TEAM_RUN_ID.fullmatch(checked_id):
        raise ValueError(
            "team_run_id must begin with 'team-' and contain only letters, "
            "digits, or hyphens"
        )
    return checked_id


def _now_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_mode(mode: str) -> str:
    m = (mode or "review").strip().lower().replace("_", "-")
    return {"dry": "dry-run", "review-required": "review"}.get(m, m)
