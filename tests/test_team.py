"""Tests for the Agent Team Workflow (agents/team.py + Monday.team + monday team CLI)."""
from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agents.adapters import FakeAgentProvider
from agents.team import BLOCKING_ROLES, STOPPING_VERDICTS, TEAM_SEQUENCE, TeamWorkflow, _derive_verdict
from agents.types import AgentRun
from monday import Monday, MondayConfig
from monday.cli import main


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
    """The team stage verdict comes from the structured AgentVerdict only (v2.4)."""

    def _run(self, role, *, success=True, status="executed", verdict=None, excerpt="", message=""):
        return AgentRun(
            run_id="r", task_id="t", role=role, success=success, status=status,
            verdict=(verdict if verdict is not None else {}),
            execution={"result_excerpt": excerpt}, message=message,
        )

    def test_pass_normal(self):
        self.assertEqual(
            _derive_verdict("qa", self._run("qa", verdict={"verdict": "pass"})), "pass"
        )

    def test_structured_block_blocks(self):
        self.assertEqual(
            _derive_verdict("security", self._run("security", verdict={"verdict": "block"})),
            "block",
        )

    def test_structured_needs_changes_stops(self):
        self.assertEqual(
            _derive_verdict("qa", self._run("qa", verdict={"verdict": "needs_changes"})),
            "needs_changes",
        )
        self.assertIn("needs_changes", STOPPING_VERDICTS)

    def test_prose_blocker_does_not_block(self):
        # The word "blocker"/"blocking" in prose must NOT veto — only structure does.
        run = self._run(
            "security",
            verdict={"verdict": "pass"},
            excerpt="I found a potential blocker earlier but it is a blocking issue no longer.",
        )
        self.assertEqual(_derive_verdict("security", run), "pass")

    def test_no_structured_verdict_defaults_pass(self):
        # A blocking role with no structured verdict at all → pass (never block).
        self.assertEqual(_derive_verdict("security", self._run("security")), "pass")

    def test_non_blocking_role_ignores_block_verdict(self):
        # cpo / lead-engineer are productive, not gatekeepers — even a structured
        # block from them does not halt the pipeline.
        self.assertEqual(
            _derive_verdict("cpo", self._run("cpo", verdict={"verdict": "block"})), "pass"
        )

    def test_failed_run_blocks(self):
        self.assertEqual(_derive_verdict("qa", self._run("qa", success=False)), "block")

    def test_gate_blocked_status_blocks(self):
        self.assertEqual(
            _derive_verdict("reviewer", self._run("reviewer", status="blocked")), "block"
        )


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

    def test_full_pass_moves_task_to_review(self):
        self._team()
        got = self.monday.task("get", task_id=self.task_id)
        self.assertEqual(got.data["status"], "review")

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


if __name__ == "__main__":
    unittest.main()
