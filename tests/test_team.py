"""Tests for the Agent Team Workflow (agents/team.py + Monday.team + monday team CLI)."""
from __future__ import annotations

import contextlib
import io
import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from agents.adapters import FakeAgentProvider
from agents.team import (
    BLOCKING_ROLES,
    STOPPING_VERDICTS,
    TEAM_SEQUENCE,
    TeamCheckpointError,
    TeamRun,
    TeamWorkflow,
    _stage_verdict,
    _stops_pipeline,
)
from agents.types import AgentRun
from agents.verdicts import INVALID
from brain.providers.base import ProviderAvailability
from monday import Monday, MondayConfig
from monday.cli import main
from tasks import TaskStatus


class _UnavailableFake(FakeAgentProvider):
    def availability(self) -> ProviderAvailability:
        return ProviderAvailability(
            available=False,
            provider=self.name,
            model=f"{self.name}-offline",
            reason="test primary unavailable",
        )


class _HoldingFake(FakeAgentProvider):
    """Pause one provider call so lease/concurrency behavior is observable."""

    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        super().__init__(name="fake", role="cpo")
        self._started = started
        self._release = release

    def ask(self, *args, **kwargs):
        self._started.set()
        if not self._release.wait(timeout=5):
            raise TimeoutError("test did not release the holding provider")
        return super().ask(*args, **kwargs)


# ---------------------------------------------------------------------------
# Constants + verdict logic
# ---------------------------------------------------------------------------

class TestTeamConstants(unittest.TestCase):
    def test_sequence(self):
        self.assertEqual(
            TEAM_SEQUENCE, ("cpo", "lead-engineer", "qa", "security", "reviewer")
        )

    def test_blocking_roles(self):
        self.assertEqual(BLOCKING_ROLES, frozenset({"qa", "security", "reviewer"}))


class TestDeriveVerdict(unittest.TestCase):
    """
    The team stage verdict comes from the structured AgentVerdict only (v2.4).

    Split in TASK-0055 into two functions: ``_stage_verdict`` records what the
    stage actually returned (for audit), and ``_stops_pipeline`` decides whether
    that halts the run. The split is what lets a non-gating role's real verdict
    be logged honestly without it affecting flow.
    """

    def _run(self, role, *, success=True, status="executed", verdict=None, excerpt="", message=""):
        return AgentRun(
            run_id="r", task_id="t", role=role, success=success, status=status,
            verdict=(verdict if verdict is not None else {}),
            execution={"result_excerpt": excerpt}, message=message,
        )

    def test_pass_normal(self):
        run = self._run("qa", verdict={"verdict": "pass"})
        self.assertEqual(_stage_verdict(run), "pass")
        self.assertFalse(_stops_pipeline("qa", "pass"))

    def test_structured_block_blocks(self):
        run = self._run("security", verdict={"verdict": "block"})
        self.assertEqual(_stage_verdict(run), "block")
        self.assertTrue(_stops_pipeline("security", "block"))

    def test_structured_needs_changes_stops(self):
        run = self._run("qa", verdict={"verdict": "needs_changes"})
        self.assertEqual(_stage_verdict(run), "needs_changes")
        self.assertTrue(_stops_pipeline("qa", "needs_changes"))
        self.assertIn("needs_changes", STOPPING_VERDICTS)

    def test_prose_blocker_does_not_block(self):
        # The word "blocker"/"blocking" in prose must NOT veto — only structure does.
        run = self._run(
            "security",
            verdict={"verdict": "pass"},
            excerpt="I found a potential blocker earlier but it is a blocking issue no longer.",
        )
        self.assertEqual(_stage_verdict(run), "pass")
        self.assertFalse(_stops_pipeline("security", "pass"))

    def test_no_structured_verdict_is_invalid_and_stops(self):
        # TASK-0055: this previously returned "pass" — a blocking role that
        # produced no verdict at all advanced the pipeline as though it had
        # approved the work. It is now `invalid`, and it stops the run.
        self.assertEqual(_stage_verdict(self._run("security")), INVALID)
        self.assertTrue(_stops_pipeline("security", INVALID))
        self.assertIn(INVALID, STOPPING_VERDICTS)

    def test_unrecognised_verdict_value_is_invalid(self):
        run = self._run("qa", verdict={"verdict": "probably fine"})
        self.assertEqual(_stage_verdict(run), INVALID)
        self.assertTrue(_stops_pipeline("qa", INVALID))

    def test_non_blocking_role_ignores_block_verdict(self):
        # cpo / lead-engineer are productive, not gatekeepers — even a structured
        # block from them does not halt the pipeline. Preserved unchanged.
        run = self._run("cpo", verdict={"verdict": "block"})
        self.assertEqual(_stage_verdict(run), "block")   # recorded honestly...
        self.assertFalse(_stops_pipeline("cpo", "block"))  # ...but does not halt

    def test_non_blocking_role_invalid_verdict_does_not_halt(self):
        self.assertEqual(_stage_verdict(self._run("lead-engineer")), INVALID)
        self.assertFalse(_stops_pipeline("lead-engineer", INVALID))

    def test_failed_run_blocks(self):
        self.assertEqual(_stage_verdict(self._run("qa", success=False)), "block")

    def test_gate_blocked_status_blocks(self):
        self.assertEqual(_stage_verdict(self._run("reviewer", status="blocked")), "block")


# ---------------------------------------------------------------------------
# Full pipeline — via Monday.team with the fake provider
# ---------------------------------------------------------------------------

class TestTeamPipeline(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.monday = Monday(MondayConfig(project_root=self.root))
        self.task_id = self.monday.task(
            "create",
            title="Add retry/backoff to the API client",
            objective="Add retry with exponential backoff to the API client.",
            task_type="feature",
            priority="P1",
        ).task_id

    def tearDown(self):
        self._tmp.cleanup()

    def _team(self, **kw):
        params = dict(task_id=self.task_id, provider="fake")
        params.update(kw)
        return self.monday.team("run", **params)

    def test_full_pass_runs_all_stages(self):
        r = self._team()
        self.assertTrue(r.success)
        self.assertEqual(r.status, "awaiting-approval")
        roles = [s["role"] for s in r.stages]
        self.assertEqual(roles, list(TEAM_SEQUENCE))
        self.assertTrue(all(s["verdict"] == "pass" for s in r.stages))

    def test_blank_provider_team_falls_back_when_role_primary_is_unavailable(self):
        openai = FakeAgentProvider(name="openai")
        anthropic = _UnavailableFake(name="anthropic")
        workflow = TeamWorkflow(
            self.monday,
            self.root,
            configured_providers=[openai, anthropic],
        )

        run = workflow.run(task_id=self.task_id, provider="")

        self.assertTrue(run.success)
        self.assertEqual(run.status, "awaiting-approval")
        lead = next(stage for stage in run.stages if stage["role"] == "lead-engineer")
        self.assertEqual(lead["provider_used"], "openai")
        self.assertEqual([stage["role"] for stage in run.stages], list(TEAM_SEQUENCE))

    def test_whole_team_pin_cannot_replace_mandatory_openai_reviewer(self):
        deepseek = FakeAgentProvider(name="deepseek")
        openai = FakeAgentProvider(name="openai")
        workflow = TeamWorkflow(
            self.monday,
            self.root,
            configured_providers=[deepseek, openai],
        )

        run = workflow.run(
            task_id=self.task_id,
            provider="deepseek",
            stage_providers={"reviewer": deepseek},
        )

        self.assertTrue(run.success)
        self.assertTrue(all(
            stage["provider_used"] == "deepseek"
            for stage in run.stages[:-1]
        ))
        self.assertEqual(run.stages[-1]["role"], "reviewer")
        self.assertEqual(run.stages[-1]["provider_used"], "openai")

    def test_progress_callback_reports_ordered_stage_boundaries(self):
        events = []
        r = self._team(progress_callback=events.append)
        self.assertTrue(r.success)
        self.assertEqual(events[0]["event"], "team_started")
        self.assertEqual(events[-1]["event"], "team_finished")
        self.assertEqual(events[-1]["status"], "awaiting-approval")
        self.assertEqual(
            [e["role"] for e in events if e["event"] == "stage_started"],
            list(TEAM_SEQUENCE),
        )
        self.assertEqual(
            [e["role"] for e in events if e["event"] == "stage_finished"],
            list(TEAM_SEQUENCE),
        )

    def test_progress_callback_failure_never_stops_team(self):
        def broken(_event):
            raise RuntimeError("notification service is down")

        r = self._team(progress_callback=broken)
        self.assertTrue(r.success)
        self.assertEqual(r.status, "awaiting-approval")

    def test_required_checkpoint_failure_stops_before_task_or_provider_work(self):
        provider = FakeAgentProvider(role="cpo")

        def broken(_event):
            raise OSError("state storage unavailable")

        with self.assertRaises(TeamCheckpointError):
            self._team(
                checkpoint_callback=broken,
                stage_providers={role: provider for role in TEAM_SEQUENCE},
            )

        self.assertEqual(provider.calls, [])
        task = self.monday.task("get", task_id=self.task_id)
        self.assertEqual(task.data["status"], "backlog")
        parent = self.monday.team(
            "history", task_id=self.task_id,
        ).data["runs"][0]
        self.assertEqual(parent["status"], "interrupted")

    def test_stale_running_team_is_interrupted_after_lease_recovery(self):
        workflow = TeamWorkflow(self.monday, self.root)
        stale = TeamRun(
            team_run_id="team-stale-worker",
            task_id=self.task_id,
            mode="review",
            status="running",
            created_at="2026-01-01T00:00:00Z",
        )
        workflow._persist(stale)

        recovered = self._team()

        self.assertTrue(recovered.success)
        self.assertEqual(recovered.status, "awaiting-approval")
        stale_after = workflow.get_team_run(stale.team_run_id)
        self.assertEqual(stale_after.status, "interrupted")
        self.assertIn("run lease", stale_after.stopped_reason)

    def test_prelease_orphan_is_settled_and_does_not_block_approval(self):
        workflow = TeamWorkflow(self.monday, self.root)
        orphan = TeamRun(
            team_run_id="team-prelease-orphan",
            task_id=self.task_id,
            mode="review",
            status="running",
            lease_acquired=False,
            created_at="2026-01-01T00:00:00Z",
        )
        workflow._persist(orphan)

        completed_team = self._team()

        orphan_after = workflow.get_team_run(orphan.team_run_id)
        self.assertEqual(orphan_after.status, "rejected")
        self.assertIn("did not execute", orphan_after.stopped_reason)
        approved = self.monday.agent(
            "review",
            run_id=completed_team.approval_run_id,
            approve=True,
            by="human:test",
        )
        self.assertTrue(approved.success)
        self.assertEqual(
            self.monday.task("get", task_id=self.task_id).data["status"],
            "completed",
        )

    def test_concurrent_team_start_has_one_live_winner_and_one_rejection(self):
        started = threading.Event()
        release = threading.Event()
        holding_provider = _HoldingFake(started, release)
        first_results = []
        first_errors = []

        def run_first():
            try:
                monday = Monday(MondayConfig(project_root=self.root))
                first_results.append(TeamWorkflow(monday, self.root).run(
                    task_id=self.task_id,
                    provider="fake",
                    team_run_id="team-live-owner",
                    stage_providers={"cpo": holding_provider},
                ))
            except Exception as exc:  # pragma: no cover - surfaced below
                first_errors.append(exc)

        thread = threading.Thread(target=run_first)
        thread.start()
        try:
            self.assertTrue(started.wait(timeout=3))

            other = Monday(MondayConfig(project_root=self.root))
            interrupted = other.team(
                "interrupt", team_run_id="team-live-owner", reason="unsafe",
            )
            self.assertFalse(interrupted.success)
            self.assertIn("live team workflow", interrupted.message)
            self.assertEqual(
                other.team("get", team_run_id="team-live-owner").status,
                "running",
            )

            reconciled = TeamWorkflow(other, self.root).run(
                task_id=self.task_id,
                provider="fake",
                team_run_id="team-live-owner",
            )
            self.assertEqual(reconciled.team_run_id, "team-live-owner")
            self.assertEqual(reconciled.status, "running")

            rejected = TeamWorkflow(other, self.root).run(
                task_id=self.task_id,
                provider="fake",
                team_run_id="team-busy-contender",
            )
            self.assertFalse(rejected.success)
            self.assertEqual(rejected.status, "rejected")
            self.assertEqual(rejected.team_run_id, "team-busy-contender")
            self.assertIn("live team workflow", rejected.message)
        finally:
            release.set()
            thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(first_errors, [])
        self.assertEqual(len(first_results), 1)
        self.assertEqual(first_results[0].status, "awaiting-approval")
        statuses = {
            run.status for run in TeamWorkflow(self.monday, self.root).history(
                task_id=self.task_id, limit=0,
            )
        }
        self.assertEqual(statuses, {"awaiting-approval", "rejected"})

    def test_reserved_team_id_retry_reconciles_without_overwrite(self):
        reserved = "team-caller-reserved"
        first = self._team(team_run_id=reserved)

        retried = self._team(team_run_id=reserved)

        self.assertEqual(retried.team_run_id, reserved)
        self.assertEqual(retried.data, first.data)
        history = self.monday.team("history", task_id=self.task_id).data["runs"]
        self.assertEqual(len(history), 1)

    def test_reserved_team_id_cannot_be_reused_for_another_task(self):
        reserved = "team-owned-by-first-task"
        original = self._team(team_run_id=reserved)
        other_task = self.monday.task(
            "create", title="Other", objective="Do something else.",
        ).task_id

        collision = self.monday.team(
            "run", task_id=other_task, provider="fake", team_run_id=reserved,
        )

        self.assertFalse(collision.success)
        self.assertIn("already belongs", collision.message)
        persisted = self.monday.team("get", team_run_id=reserved)
        self.assertEqual(persisted.task_id, self.task_id)
        self.assertEqual(persisted.data, original.data)

    def test_public_team_lookup_rejects_path_traversal_ids(self):
        for action in ("get", "interrupt"):
            with self.subTest(action=action):
                response = self.monday.team(action, team_run_id="../private-data")
                self.assertFalse(response.success)
                self.assertIn("team_run_id must begin", response.message)

    def test_public_team_lookup_rejects_symlinked_run_record(self):
        runs_dir = self.root / "logs" / "agents"
        runs_dir.mkdir(parents=True, exist_ok=True)
        target = self.root / "outside-team-record.json"
        target.write_text(json.dumps(TeamRun(
            team_run_id="team-symlinked",
            task_id=self.task_id,
            status="running",
        ).to_dict()), encoding="utf-8")
        (runs_dir / "team-symlinked.json").symlink_to(target)

        for action in ("get", "interrupt"):
            with self.subTest(action=action):
                response = self.monday.team(action, team_run_id="team-symlinked")
                self.assertFalse(response.success)
                self.assertIn("Refusing symlinked", response.message)

    def test_mismatched_persisted_identity_fails_closed(self):
        runs_dir = self.root / "logs" / "agents"
        runs_dir.mkdir(parents=True, exist_ok=True)
        corrupt_path = runs_dir / "team-corrupt-identity.json"
        corrupt_path.write_text(json.dumps({
            "team_run_id": "../../victim",
            "task_id": self.task_id,
            "status": "running",
            "lease_acquired": True,
        }), encoding="utf-8")
        victim = self.root / "victim.json"
        victim.write_text("unchanged", encoding="utf-8")
        workflow = TeamWorkflow(self.monday, self.root)

        with self.assertRaisesRegex(ValueError, "does not match|must begin"):
            workflow.history(task_id=self.task_id)
        response = self._team()

        self.assertFalse(response.success)
        self.assertIn("unreadable or malformed", response.message)
        self.assertEqual(victim.read_text(encoding="utf-8"), "unchanged")

    def test_exact_running_team_can_be_retrieved_and_interrupted(self):
        workflow = TeamWorkflow(self.monday, self.root)
        pending = TeamRun(
            team_run_id="team-interrupted-test",
            task_id=self.task_id,
            mode="review",
            status="running",
            created_at="2026-01-01T00:00:00Z",
        )
        workflow._persist(pending)

        found = self.monday.team("get", team_run_id=pending.team_run_id)
        self.assertTrue(found.success)
        self.assertEqual(found.status, "running")

        interrupted = self.monday.team(
            "interrupt",
            team_run_id=pending.team_run_id,
            reason="worker restarted",
        )
        self.assertTrue(interrupted.success)
        self.assertEqual(interrupted.status, "interrupted")
        self.assertEqual(
            self.monday.team("get", team_run_id=pending.team_run_id).status,
            "interrupted",
        )

    def test_full_pass_moves_task_to_review(self):
        self._team()
        got = self.monday.task("get", task_id=self.task_id)
        self.assertEqual(got.data["status"], "review")

    def test_second_team_run_is_rejected_while_first_awaits_approval(self):
        first = self._team()
        second = self._team()

        self.assertEqual(first.status, "awaiting-approval")
        self.assertFalse(second.success)
        self.assertEqual(second.status, "rejected")
        self.assertIn(first.team_run_id, second.message)

        approved = self.monday.agent(
            "review",
            run_id=first.approval_run_id,
            approve=True,
            by="human:test",
        )
        self.assertTrue(approved.success)
        self.assertEqual(
            self.monday.task("get", task_id=self.task_id).data["status"],
            "completed",
        )

    def test_blocked_task_is_explicitly_restarted_before_team_work(self):
        self.monday.task("start", task_id=self.task_id)
        manager = self.monday._Monday__tasks
        manager.update_status(
            task_id=self.task_id,
            new_status=TaskStatus.BLOCKED,
            changed_by="human:test",
            reason="waiting on a dependency",
        )

        run = self._team()

        self.assertTrue(run.success)
        self.assertEqual(
            self.monday.task("get", task_id=self.task_id).data["status"],
            "review",
        )

    def test_failed_final_task_transition_cannot_claim_awaiting_approval(self):
        real_task = self.monday.task

        def fail_review(action, *args, **kwargs):
            if action == "review":
                return SimpleNamespace(
                    success=False,
                    data={},
                    message="simulated transition failure",
                )
            return real_task(action, *args, **kwargs)

        with mock.patch.object(self.monday, "task", side_effect=fail_review):
            run = self._team()

        self.assertFalse(run.success)
        self.assertEqual(run.status, "failed")
        self.assertIn("could not enter review", run.message)
        self.assertEqual(
            self.monday.task("get", task_id=self.task_id).data["status"],
            "in-progress",
        )

    def test_parent_and_child_logs(self):
        r = self._team()
        parent = self.root / "logs" / "agents" / f"{r.team_run_id}.json"
        self.assertTrue(parent.exists())
        self.assertEqual(len(r.data["child_run_ids"]), 5)
        for run_id in r.data["child_run_ids"]:
            self.assertTrue((self.root / "logs" / "agents" / f"{run_id}.json").exists())

    def test_stages_do_not_churn_task_each_time(self):
        # update_task=False on stages → each stage status is "executed", task
        # is only moved to REVIEW once at the end.
        r = self._team()
        self.assertTrue(all(s["status"] == "executed" for s in r.stages))

    def test_prior_summaries_reach_later_stages(self):
        self._team()
        hist = self.monday.agent("history", task_id=self.task_id)
        reviewer = [x for x in hist.data["runs"] if x["role"] == "reviewer"][0]
        ctx = reviewer["execution"]["plan"]["context"]
        self.assertIn("Role: Reviewer (reviewer)", ctx)
        self.assertIn("Code review, PR review, and risk assessment", ctx)
        self.assertIn("Prior stage summaries", ctx)
        self.assertIn("CPO:", ctx)
        self.assertIn("QA:", ctx)

    def test_approval_completes_task(self):
        r = self._team()
        self.assertTrue(r.approval_run_id)
        ar = self.monday.agent("review", run_id=r.approval_run_id, approve=True)
        self.assertTrue(ar.success)
        got = self.monday.task("get", task_id=self.task_id)
        self.assertEqual(got.data["status"], "completed")

    def test_approval_updates_parent_team_run(self):
        # Approving the gate run must move the parent team run off
        # awaiting-approval to completed, mirroring the task lifecycle.
        r = self._team()
        self.assertEqual(r.status, "awaiting-approval")
        self.monday.agent("review", run_id=r.approval_run_id, approve=True, by="human:test")
        hist = self.monday.team("history", task_id=self.task_id)
        parent = hist.data["runs"][0]
        self.assertEqual(parent["status"], "completed")
        self.assertTrue(parent["success"])
        self.assertEqual(parent["approval"]["decision"], "approved")
        self.assertEqual(parent["approval"]["by"], "human:test")

    def test_failed_task_completion_cannot_record_or_propagate_approval(self):
        run = self._team()
        real_task = self.monday.task

        def fail_complete(action, *args, **kwargs):
            if action == "complete":
                return SimpleNamespace(
                    success=False,
                    data={},
                    message="simulated completion failure",
                )
            return real_task(action, *args, **kwargs)

        with mock.patch.object(self.monday, "task", side_effect=fail_complete):
            response = self.monday.agent(
                "review",
                run_id=run.approval_run_id,
                approve=True,
                by="human:test",
            )

        self.assertFalse(response.success)
        child = self.monday.agent(
            "history", task_id=self.task_id,
        ).data["runs"]
        child = next(item for item in child if item["run_id"] == run.approval_run_id)
        self.assertEqual(child["approval"]["decision"], "pending")
        parent = self.monday.team(
            "history", task_id=self.task_id,
        ).data["runs"][0]
        self.assertEqual(parent["status"], "awaiting-approval")
        self.assertEqual(
            self.monday.task("get", task_id=self.task_id).data["status"],
            "review",
        )

    def test_non_gate_children_cannot_be_reviewed(self):
        r = self._team()
        child_run_id = r.data["child_run_ids"][0]

        for approve in (True, False):
            with self.subTest(approve=approve):
                response = self.monday.agent(
                    "review",
                    run_id=child_run_id,
                    approve=approve,
                    by="human:attacker",
                )
                self.assertFalse(response.success)
                self.assertIn("cannot be reviewed directly", response.message)

        child = self.monday.agent("history", task_id=self.task_id).data["runs"]
        child = next(item for item in child if item["run_id"] == child_run_id)
        self.assertEqual(child["approval"]["decision"], "pending")
        self.assertEqual(
            self.monday.task("get", task_id=self.task_id).data["status"],
            "review",
        )
        parent = self.monday.team("history", task_id=self.task_id).data["runs"][0]
        self.assertEqual(parent["status"], "awaiting-approval")
        self.assertEqual(parent["approval"], {})

    def test_standalone_run_cannot_bypass_an_awaiting_team_gate(self):
        team = self._team()
        gate_path = self.root / "logs" / "agents" / f"{team.approval_run_id}.json"
        rogue = json.loads(gate_path.read_text(encoding="utf-8"))
        rogue["run_id"] = "run-standalone-bypass"
        rogue_path = self.root / "logs" / "agents" / f"{rogue['run_id']}.json"
        rogue_path.write_text(json.dumps(rogue), encoding="utf-8")

        response = self.monday.agent(
            "review",
            run_id=rogue["run_id"],
            approve=True,
            by="human:attacker",
        )

        self.assertFalse(response.success)
        self.assertIn("only be completed through its approval run", response.message)
        self.assertEqual(
            self.monday.task("get", task_id=self.task_id).data["status"],
            "review",
        )

    def test_gate_child_requires_parent_to_be_awaiting_approval(self):
        r = self._team()
        parent_path = self.root / "logs" / "agents" / f"{r.team_run_id}.json"
        parent = json.loads(parent_path.read_text(encoding="utf-8"))
        parent["status"] = "running"
        parent_path.write_text(json.dumps(parent), encoding="utf-8")

        response = self.monday.agent(
            "review",
            run_id=r.approval_run_id,
            approve=True,
            by="human:test",
        )

        self.assertFalse(response.success)
        self.assertIn("expected 'awaiting-approval'", response.message)
        child = json.loads(
            (self.root / "logs" / "agents" / f"{r.approval_run_id}.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(child["approval"]["decision"], "pending")

    def test_repeated_same_approval_reconciles_original_audit_fields(self):
        r = self._team()
        first = self.monday.agent(
            "review",
            run_id=r.approval_run_id,
            approve=True,
            by="human:original",
            note="Reviewed the final evidence.",
        )
        original_approval = dict(first.data["approval"])
        parent_path = self.root / "logs" / "agents" / f"{r.team_run_id}.json"
        parent = json.loads(parent_path.read_text(encoding="utf-8"))
        parent.update({"status": "awaiting-approval", "success": True, "approval": {}})
        parent_path.write_text(json.dumps(parent), encoding="utf-8")

        self.monday.agent(
            "review",
            run_id=r.approval_run_id,
            approve=True,
            by="human:retrying-caller",
            note="This retry must not replace the original note.",
        )

        repaired = json.loads(parent_path.read_text(encoding="utf-8"))
        self.assertEqual(repaired["status"], "completed")
        self.assertEqual(
            repaired["approval"],
            {
                "decision": original_approval["decision"],
                "by": original_approval["by"],
                "at": original_approval["at"],
                "note": original_approval["note"],
            },
        )
        self.assertIn("human:original", repaired["message"])
        self.assertNotIn("human:retrying-caller", repaired["message"])

    def test_opposite_decision_cannot_replace_first_decision(self):
        r = self._team()
        first = self.monday.agent(
            "review",
            run_id=r.approval_run_id,
            approve=True,
            by="human:first",
            note="Ship it.",
        )
        original_approval = dict(first.data["approval"])
        parent_path = self.root / "logs" / "agents" / f"{r.team_run_id}.json"
        original_parent = json.loads(parent_path.read_text(encoding="utf-8"))

        retry = self.monday.agent(
            "review",
            run_id=r.approval_run_id,
            approve=False,
            by="human:second",
            note="Changed my mind.",
        )

        self.assertTrue(retry.success)
        self.assertEqual(retry.data["approval"], original_approval)
        self.assertEqual(
            json.loads(parent_path.read_text(encoding="utf-8")),
            original_parent,
        )
        self.assertEqual(
            self.monday.task("get", task_id=self.task_id).data["status"],
            "completed",
        )

    def test_concurrent_opposite_decisions_leave_one_consistent_audit(self):
        run = self._team()
        barrier = threading.Barrier(2)
        responses = []

        def decide(approve, actor):
            monday = Monday(MondayConfig(project_root=self.root))
            barrier.wait()
            responses.append(monday.agent(
                "review",
                run_id=run.approval_run_id,
                approve=approve,
                by=actor,
            ))

        threads = [
            threading.Thread(target=decide, args=(True, "human:approve")),
            threading.Thread(target=decide, args=(False, "human:reject")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual(len(responses), 2)
        child = self.monday.agent(
            "history", task_id=self.task_id,
        ).data["runs"]
        child = next(item for item in child if item["run_id"] == run.approval_run_id)
        parent = self.monday.team(
            "history", task_id=self.task_id,
        ).data["runs"][0]
        decision = child["approval"]["decision"]
        self.assertEqual(parent["approval"]["decision"], decision)
        self.assertEqual(
            parent["status"],
            "completed" if decision == "approved" else "changes-requested",
        )
        self.assertEqual(
            self.monday.task("get", task_id=self.task_id).data["status"],
            "completed" if decision == "approved" else "review",
        )

    def test_rejection_marks_parent_changes_requested(self):
        # Rejecting the gate run leaves the task for rework and the parent
        # team run reflects changes-requested (not stuck at awaiting-approval).
        r = self._team()
        self.monday.agent("review", run_id=r.approval_run_id, approve=False, by="human:test")
        hist = self.monday.team("history", task_id=self.task_id)
        parent = hist.data["runs"][0]
        self.assertEqual(parent["status"], "changes-requested")
        self.assertFalse(parent["success"])
        self.assertEqual(parent["approval"]["decision"], "rejected")
        got = self.monday.task("get", task_id=self.task_id)
        self.assertEqual(got.data["status"], "review")  # left for rework

    def test_no_source_files_written(self):
        self._team()
        self.assertEqual(list(self.root.rglob("*.py")), [])

    # ── Early stop ──────────────────────────────────────────────────────

    def _block_at(self, role):
        return self._team(stage_providers={role: FakeAgentProvider(role=role, verdict="block")})

    def test_qa_block_stops_early(self):
        r = self._block_at("qa")
        self.assertFalse(r.success)
        self.assertEqual(r.status, "blocked")
        self.assertEqual(r.stopped_at, "qa")
        roles = [s["role"] for s in r.stages]
        self.assertEqual(roles, ["cpo", "lead-engineer", "qa"])  # security/reviewer skipped

    def test_security_block_stops_early(self):
        r = self._block_at("security")
        self.assertEqual(r.stopped_at, "security")
        self.assertNotIn("reviewer", [s["role"] for s in r.stages])

    def test_reviewer_block_stops_early(self):
        r = self._block_at("reviewer")
        self.assertEqual(r.stopped_at, "reviewer")
        self.assertEqual(len(r.stages), 5)

    def test_block_leaves_task_not_at_review(self):
        self._block_at("qa")
        got = self.monday.task("get", task_id=self.task_id)
        self.assertNotEqual(got.data["status"], "review")

    def test_nonblocking_stage_failure_stops(self):
        # Unknown provider → build fails → provider None → stage skipped/failed.
        r = self._team(provider="nonesuch")
        self.assertFalse(r.success)
        self.assertEqual(r.status, "failed")
        self.assertEqual(r.stopped_at, "cpo")

    # ── Modes / guards ──────────────────────────────────────────────────

    def test_autonomous_rejected(self):
        r = self._team(mode="autonomous")
        self.assertFalse(r.success)
        self.assertEqual(r.status, "rejected")
        got = self.monday.task("get", task_id=self.task_id)
        self.assertEqual(got.data["status"], "backlog")  # untouched

    def test_dry_run_no_changes(self):
        r = self._team(mode="dry-run")
        self.assertTrue(r.success)
        self.assertEqual(r.status, "dry-run")
        got = self.monday.task("get", task_id=self.task_id)
        self.assertEqual(got.data["status"], "backlog")

    def test_unknown_task_fails(self):
        r = self.monday.team("run", task_id="TASK-9999", provider="fake")
        self.assertFalse(r.success)
        self.assertEqual(r.status, "failed")

    def test_completed_task_not_runnable(self):
        self.monday.task("start", task_id=self.task_id)
        self.monday.task("complete", task_id=self.task_id)
        r = self._team()
        self.assertFalse(r.success)
        self.assertEqual(r.status, "failed")

    def test_history(self):
        self._team()
        self._team()
        r = self.monday.team("history", task_id=self.task_id)
        self.assertEqual(r.data["count"], 2)

    def test_unknown_action(self):
        r = self.monday.team("frobnicate")
        self.assertFalse(r.success)
        self.assertIn("Unknown action", r.message)


# ---------------------------------------------------------------------------
# Direct engine construction
# ---------------------------------------------------------------------------

class TestTeamWorkflowDirect(unittest.TestCase):
    def test_construct_and_run(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            monday = Monday(MondayConfig(project_root=root))
            tid = monday.task("create", title="X", objective="Do X.").task_id
            wf = TeamWorkflow(monday, root)
            tr = wf.run(tid, provider="fake")
            self.assertEqual(tr.status, "awaiting-approval")
            self.assertEqual(wf.get_team_run(tr.team_run_id).team_run_id, tr.team_run_id)


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------

class TestTeamCLI(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = str(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _cli(self, *argv) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = main(["--project-root", self.root, *argv])
        return code, buf.getvalue()

    def _make_task(self) -> str:
        self._cli("task", "create", "--title", "X", "--objective", "Do X.")
        return "TASK-0001"

    def test_team_run(self):
        tid = self._make_task()
        code, out = self._cli("team", "run", tid, "--provider", "fake")
        self.assertEqual(code, 0)
        self.assertIn("TEAM RUN", out)
        self.assertIn("awaiting-approval", out)
        self.assertIn("reviewer", out)

    def test_team_run_dry(self):
        tid = self._make_task()
        code, out = self._cli("team", "run", tid, "--provider", "fake", "--mode", "dry-run")
        self.assertEqual(code, 0)
        self.assertIn("dry-run", out)
        self.assertIn("planned", out)
        self.assertNotIn("✗", out)

    def test_team_run_json(self):
        tid = self._make_task()
        code, out = self._cli("team", "run", tid, "--provider", "fake", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(len(data["child_run_ids"]), 5)

    def test_team_history_empty(self):
        code, out = self._cli("team", "history")
        self.assertEqual(code, 0)
        self.assertIn("No team runs", out)

    def test_team_history_reports_malformed_record_as_failure(self):
        runs = Path(self.root) / "logs" / "agents"
        runs.mkdir(parents=True)
        (runs / "team-corrupt.json").write_text("not-json", encoding="utf-8")

        code, out = self._cli("team", "history")

        self.assertEqual(code, 1)
        self.assertIn("Error:", out)


if __name__ == "__main__":
    unittest.main()
