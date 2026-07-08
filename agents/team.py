"""
TeamWorkflow — run the registered agents as a collaborating team on one task.

The team executes a fixed sequence of roles end to end:

    CPO → Lead Engineer → QA → Security → Reviewer → (Human Approval)

Each stage is a normal role run (reused from AgentRuntime, logged as an AgentRun
under logs/agents/), and every stage receives the summaries of all prior stages
as extra context. QA, Security, and Reviewer are **blocking** — if any returns a
block verdict, the pipeline stops early. When all stages pass, the task is moved
to REVIEW awaiting the final human approval; nothing is committed, pushed, or
executed live. The whole run is recorded as one parent TeamRun that references
its child role-run IDs.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.roles import get_role
from agents.runtime import AgentRuntime
from agents.types import AgentRun

# The collaboration order. Human Approval is the terminal gate, not an agent run.
TEAM_SEQUENCE: tuple[str, ...] = ("cpo", "lead-engineer", "qa", "security", "reviewer")

# Roles whose block verdict stops the pipeline early.
BLOCKING_ROLES: frozenset[str] = frozenset({"qa", "security", "reviewer"})

# Markers a blocking stage may emit to veto the pipeline. Deliberately strong
# tokens to avoid false positives on ordinary prose. A production provider would
# be prompted to emit an explicit verdict; this is the documented convention.
BLOCK_MARKERS: tuple[str, ...] = ("BLOCK", "REJECT", "VETO", "NOT APPROVED", "DO NOT MERGE")


@dataclass
class TeamStage:
    """One stage of a team run."""

    role: str
    run_id: str = ""
    agent_name: str = ""
    provider_used: str = ""
    provider_model: str = ""
    status: str = ""            # executed | dry-run | blocked | unavailable | skipped | …
    verdict: str = ""           # pass | block
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
    # awaiting-approval | completed | changes-requested | blocked | failed |
    # dry-run | rejected. A run reaching REVIEW is 'awaiting-approval' until the
    # human decision on approval_run_id lands (see AgentRuntime.review), which
    # transitions it to 'completed' (approved) or 'changes-requested' (rejected).
    status: str = ""
    success: bool = False
    created_at: str = ""
    sequence: list[str] = field(default_factory=list)
    stages: list[dict[str, Any]] = field(default_factory=list)
    child_run_ids: list[str] = field(default_factory=list)
    stopped_at: str = ""
    stopped_reason: str = ""
    approval_run_id: str = ""
    approval: dict[str, Any] = field(default_factory=dict)  # {decision, by, at} once reviewed
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TeamRun":
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
    ) -> None:
        self._monday = monday
        self._root = Path(project_root)
        self._runtime = AgentRuntime(monday, project_root, require_human_approval)
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
    ) -> TeamRun:
        """
        Run the full team pipeline over ``task_id``.

        Args:
            task_id:         The task to work.
            provider:        Optional provider-name override applied to every
                             stage (e.g. "fake" for an offline run).
            mode:            "review" (default) or "dry-run". Autonomous is not
                             supported — team runs are review-required.
            stage_providers: Optional {role: AIProvider} injection (tests /
                             advanced use); overrides registry resolution per role.

        Always returns a persisted TeamRun; never raises for expected failures.
        """
        team = TeamRun(
            team_run_id=_new_team_id(),
            task_id=task_id,
            mode=_norm_mode(mode),
            created_at=_now_iso(),
            sequence=list(TEAM_SEQUENCE),
        )

        norm_mode = _norm_mode(mode)
        if norm_mode not in ("review", "dry-run"):
            team.status = "rejected"
            team.message = (
                f"Unsupported mode {mode!r}. Team runs are review-required: "
                "use 'review' (default) or 'dry-run'. Autonomous is not allowed."
            )
            self._persist(team)
            return team

        # Task must exist and be actionable.
        task_resp = self._monday.task("get", task_id=task_id)
        if not task_resp.success:
            team.status = "failed"
            team.message = task_resp.message
            self._persist(team)
            return team
        if task_resp.data.get("status") in ("completed", "cancelled"):
            team.status = "failed"
            team.message = f"Task {task_id} is {task_resp.data.get('status')}; nothing to run."
            self._persist(team)
            return team

        # Move the task into progress once (the stages themselves don't touch the
        # task lifecycle — update_task=False — so the team owns it).
        if norm_mode == "review":
            self._monday.task("start", task_id=task_id)

        prior: list[tuple[str, str]] = []  # (role, summary) accumulated across stages
        stopped = False

        for role in TEAM_SEQUENCE:
            run = self._runtime.run(
                task_id=task_id,
                role=role,
                provider=provider,
                mode=norm_mode,
                update_task=False,
                extra_context=_context_from(prior),
                provider_instance=(stage_providers or {}).get(role),
            )
            summary = _summary(run)
            verdict = _derive_verdict(role, run)
            team.stages.append(TeamStage(
                role=role,
                run_id=run.run_id,
                agent_name=run.agent_name,
                provider_used=run.provider_used,
                provider_model=run.provider_model,
                status=run.status,
                verdict=verdict,
                summary=summary,
            ).to_dict())
            team.child_run_ids.append(run.run_id)
            prior.append((role, summary))

            if not run.success:
                team.status = "failed"
                team.stopped_at = role
                team.stopped_reason = run.message or "stage did not succeed"
                stopped = True
                break
            if verdict == "block":
                team.status = "blocked"
                team.stopped_at = role
                team.stopped_reason = summary or f"{role} blocked the pipeline"
                stopped = True
                break

        if stopped:
            team.success = False
            team.message = (
                f"Pipeline stopped at {get_role(team.stopped_at).title}: {team.stopped_reason}"
            )
            self._persist(team)
            return team

        # All stages passed.
        if norm_mode == "dry-run":
            team.status = "dry-run"
            team.success = True
            team.message = "Dry run — all stages planned, no provider calls or changes."
        else:
            # Move the task to REVIEW for the terminal human approval.
            self._monday.task(
                "review", task_id=task_id,
                reason=f"Team run {team.team_run_id} passed all stages; awaiting human approval.",
            )
            team.status = "awaiting-approval"
            team.success = True
            team.approval_run_id = team.child_run_ids[-1] if team.child_run_ids else ""
            team.message = (
                "All stages passed. Task is at REVIEW awaiting human approval — "
                f"approve with: monday agent review {team.approval_run_id} --approve"
            )

        self._persist(team)
        return team

    def get_team_run(self, team_run_id: str) -> TeamRun:
        path = self._runs_dir / f"{team_run_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"No team run {team_run_id!r} in {self._runs_dir}.")
        return TeamRun.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def history(self, task_id: str | None = None, limit: int = 20) -> list[TeamRun]:
        if not self._runs_dir.exists():
            return []
        runs: list[TeamRun] = []
        for path in self._runs_dir.glob("team-*.json"):
            try:
                runs.append(TeamRun.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except Exception:
                continue
        if task_id is not None:
            runs = [r for r in runs if r.task_id == task_id]
        runs.sort(key=lambda r: r.created_at, reverse=True)
        return runs[: max(0, limit)] if limit else runs

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _persist(self, team: TeamRun) -> None:
        try:
            self._runs_dir.mkdir(parents=True, exist_ok=True)
            (self._runs_dir / f"{team.team_run_id}.json").write_text(
                json.dumps(team.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
            )
        except Exception:
            pass  # persistence failure must not mask the run result


def _derive_verdict(role: str, run: AgentRun) -> str:
    """Return "pass" or "block" for a completed stage run."""
    if not run.success or run.status == "blocked":
        return "block"
    if role in BLOCKING_ROLES:
        text = f"{run.execution.get('result_excerpt', '')} {run.message}".upper()
        if any(marker in text for marker in BLOCK_MARKERS):
            return "block"
    return "pass"


def _summary(run: AgentRun) -> str:
    excerpt = (run.execution.get("result_excerpt", "") or "").strip()
    text = excerpt or (run.message or "").strip()
    return text[:240]


def _context_from(prior: list[tuple[str, str]]) -> str:
    if not prior:
        return ""
    lines = ["Prior stage summaries (for your context):"]
    for role, summary in prior:
        lines.append(f"- {get_role(role).title}: {summary}")
    return "\n".join(lines)


def _new_team_id() -> str:
    return f"team-{uuid.uuid4().hex[:12]}"


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_mode(mode: str) -> str:
    m = (mode or "review").strip().lower().replace("_", "-")
    return {"dry": "dry-run", "review-required": "review"}.get(m, m)
