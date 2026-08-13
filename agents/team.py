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

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.roles import get_role
from agents.runtime import AgentRuntime
from agents.types import AgentRun
from agents.verdicts import ALL_VERDICTS, BLOCK, INVALID, NEEDS_CHANGES, PASS

# The collaboration order. Human Approval is the terminal gate, not an agent run.
TEAM_SEQUENCE: tuple[str, ...] = ("cpo", "lead-engineer", "qa", "security", "reviewer")

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
    text = structured or (run.execution.get("result_excerpt", "") or "").strip() or (run.message or "").strip()
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


def _new_team_id() -> str:
    return f"team-{uuid.uuid4().hex[:12]}"


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_mode(mode: str) -> str:
    m = (mode or "review").strip().lower().replace("_", "-")
    return {"dry": "dry-run", "review-required": "review"}.get(m, m)
