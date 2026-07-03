"""Tests for the Multi-Agent Runtime (agents/ + Monday.agent + monday agent CLI)."""
from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agents.adapters import FAKE_PROVIDER, FakeAgentProvider, build_provider_for
from agents.gates import GATED_ACTIONS, ApprovalGate
from agents.registry import AgentExistsError, AgentRegistry
from agents.roles import (
    DEFAULT_ROLE_PROVIDERS,
    ROLES,
    UnknownRoleError,
    get_role,
    list_roles,
    normalize_role,
)
from monday import Monday, MondayConfig
from monday.cli import main
from orchestrator.report import ExecutionMode


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

class TestRoles(unittest.TestCase):
    def test_six_roles_defined(self):
        slugs = {r.slug for r in list_roles()}
        self.assertEqual(
            slugs, {"cpo", "lead-engineer", "qa", "security", "research", "reviewer"}
        )

    def test_pinned_provider_defaults(self):
        self.assertEqual(
            DEFAULT_ROLE_PROVIDERS,
            {
                "cpo": "openai",
                "lead-engineer": "anthropic",
                "qa": "anthropic",
                "security": "anthropic",
                "research": "openai",
                "reviewer": "anthropic",
            },
        )

    def test_normalize_role(self):
        self.assertEqual(normalize_role("Lead Engineer"), "lead-engineer")
        self.assertEqual(normalize_role("lead_engineer"), "lead-engineer")

    def test_get_role_unknown_raises(self):
        with self.assertRaises(UnknownRoleError):
            get_role("ceo")

    def test_role_gated_actions_are_within_universe(self):
        for role in ROLES.values():
            self.assertTrue(set(role.gated_actions).issubset(GATED_ACTIONS))


# ---------------------------------------------------------------------------
# ApprovalGate
# ---------------------------------------------------------------------------

class TestApprovalGate(unittest.TestCase):
    def setUp(self):
        self.gate = ApprovalGate(require_human_approval=True)

    def test_review_allowed(self):
        d = self.gate.evaluate(mode=ExecutionMode.REVIEW)
        self.assertTrue(d.allowed)

    def test_dry_run_allowed(self):
        self.assertTrue(self.gate.evaluate(mode=ExecutionMode.DRY_RUN).allowed)

    def test_autonomous_without_enable_blocked(self):
        d = self.gate.evaluate(mode=ExecutionMode.AUTONOMOUS, autonomous_enabled=False)
        self.assertFalse(d.allowed)
        self.assertIn("enablement", d.reason.lower())

    def test_autonomous_enabled_but_unapproved_blocked(self):
        d = self.gate.evaluate(
            mode=ExecutionMode.AUTONOMOUS, autonomous_enabled=True, approved=False
        )
        self.assertFalse(d.allowed)
        self.assertIn("approval", d.reason.lower())

    def test_autonomous_enabled_and_approved_allowed(self):
        d = self.gate.evaluate(
            mode=ExecutionMode.AUTONOMOUS, autonomous_enabled=True, approved=True
        )
        self.assertTrue(d.allowed)

    def test_gated_action_blocked_without_approval(self):
        for act in ("commit", "push", "secrets", "live_trade", "destructive"):
            d = self.gate.evaluate(mode=ExecutionMode.REVIEW, requested_actions=[act])
            self.assertFalse(d.allowed, act)
            self.assertIn(act, d.gated_actions)

    def test_gated_action_allowed_with_approval(self):
        d = self.gate.evaluate(
            mode=ExecutionMode.REVIEW, requested_actions=["commit"], approved=True
        )
        self.assertTrue(d.allowed)

    def test_non_gated_action_allowed(self):
        d = self.gate.evaluate(mode=ExecutionMode.REVIEW, requested_actions=["analyze"])
        self.assertTrue(d.allowed)


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------

class TestAdapters(unittest.TestCase):
    def test_build_fake_provider(self):
        prov = build_provider_for(FAKE_PROVIDER, role="qa")
        self.assertIsInstance(prov, FakeAgentProvider)
        self.assertTrue(prov.is_local)

    def test_build_unknown_provider_returns_none(self):
        self.assertIsNone(build_provider_for("nonesuch"))

    def test_empty_provider_returns_none(self):
        self.assertIsNone(build_provider_for(""))

    def test_fake_provider_output_is_substantial(self):
        prov = FakeAgentProvider(role="lead-engineer")
        resp = prov.ask("Implement the health endpoint.")
        self.assertGreater(len(resp.content), 20)
        self.assertNotIn("cannot", resp.content.lower())


# ---------------------------------------------------------------------------
# AgentRegistry
# ---------------------------------------------------------------------------

class TestAgentRegistry(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.reg = AgentRegistry(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_seed_defaults_creates_six(self):
        created = self.reg.seed_defaults()
        self.assertEqual(len(created), 6)
        self.assertEqual(len(self.reg.list()), 6)

    def test_seed_is_idempotent(self):
        self.reg.seed_defaults()
        self.reg.seed_defaults()
        self.assertEqual(len(self.reg.list()), 6)

    def test_default_agent_providers(self):
        self.reg.seed_defaults()
        self.assertEqual(self.reg.resolve_by_role("cpo").provider, "openai")
        self.assertEqual(self.reg.resolve_by_role("lead-engineer").provider, "anthropic")

    def test_id_allocation_sequential(self):
        a = self.reg.register("First", "qa")
        b = self.reg.register("Second", "qa")
        self.assertEqual(a.id, "AGENT-0001")
        self.assertEqual(b.id, "AGENT-0002")

    def test_register_defaults_provider_from_role(self):
        a = self.reg.register("Custom QA", "qa")
        self.assertEqual(a.provider, "anthropic")

    def test_register_provider_override(self):
        a = self.reg.register("Local QA", "qa", provider="ollama")
        self.assertEqual(a.provider, "ollama")

    def test_duplicate_name_raises(self):
        self.reg.register("Dup", "qa")
        with self.assertRaises(AgentExistsError):
            self.reg.register("Dup", "reviewer")

    def test_unknown_role_raises(self):
        with self.assertRaises(UnknownRoleError):
            self.reg.register("X", "ceo")

    def test_persistence_round_trip(self):
        self.reg.register("Persisted", "security", provider="ollama", description="note")
        reloaded = AgentRegistry(self.root).get("Persisted")
        self.assertEqual(reloaded.role, "security")
        self.assertEqual(reloaded.provider, "ollama")
        self.assertEqual(reloaded.description, "note")

    def test_resolve_prefers_default_over_custom(self):
        self.reg.seed_defaults()
        self.reg.register("Custom LE", "lead-engineer", provider="ollama")
        self.assertTrue(self.reg.resolve_by_role("lead-engineer").is_default)


# ---------------------------------------------------------------------------
# AgentRuntime / Monday.agent — end to end
# ---------------------------------------------------------------------------

class TestAgentRuntimeEndToEnd(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.monday = Monday(MondayConfig(project_root=self.root))
        created = self.monday.task(
            "create",
            title="Add health endpoint",
            objective="Add a /health endpoint that returns service status.",
            task_type="feature",
            priority="P1",
        )
        self.task_id = created.task_id

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, **kw):
        params = dict(task_id=self.task_id, role="lead-engineer", provider="fake")
        params.update(kw)
        return self.monday.agent("run", **params)

    def test_list_seeds_defaults(self):
        r = self.monday.agent("list")
        self.assertTrue(r.success)
        self.assertEqual(r.data["count"], 6)

    def test_list_filter_by_role(self):
        r = self.monday.agent("list", role="qa")
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["agents"][0]["role"], "qa")

    def test_assign_sets_assigned_to_role(self):
        r = self.monday.agent("assign", task_id=self.task_id, role="qa")
        self.assertTrue(r.success)
        got = self.monday.task("get", task_id=self.task_id)
        self.assertEqual(got.data["assigned_to"], "role:qa")

    def test_assign_unknown_role_fails(self):
        r = self.monday.agent("assign", task_id=self.task_id, role="ceo")
        self.assertFalse(r.success)

    def test_run_review_moves_task_to_review(self):
        r = self._run()
        self.assertTrue(r.success)
        self.assertEqual(r.status, "review")
        self.assertEqual(r.provider_used, "fake")
        got = self.monday.task("get", task_id=self.task_id)
        self.assertEqual(got.data["status"], "review")

    def test_run_captures_knowledge_and_persists_run(self):
        r = self._run()
        self.assertEqual(len(r.data["knowledge_captured"]), 1)
        run_log = self.root / "logs" / "agents" / f"{r.run_id}.json"
        self.assertTrue(run_log.exists())
        data = json.loads(run_log.read_text())
        self.assertEqual(data["task_id"], self.task_id)
        self.assertEqual(data["role"], "lead-engineer")

    def test_run_dry_run_makes_no_change(self):
        r = self._run(mode="dry-run")
        self.assertTrue(r.success)
        self.assertEqual(r.status, "dry-run")
        got = self.monday.task("get", task_id=self.task_id)
        self.assertEqual(got.data["status"], "backlog")

    def test_autonomous_without_enable_blocked(self):
        r = self._run(mode="autonomous")
        self.assertFalse(r.success)
        self.assertEqual(r.status, "blocked")
        got = self.monday.task("get", task_id=self.task_id)
        self.assertEqual(got.data["status"], "backlog")  # untouched

    def test_autonomous_enabled_needs_approval(self):
        r = self._run(mode="autonomous", autonomous_enabled=True)
        self.assertFalse(r.success)
        self.assertEqual(r.status, "blocked")

    def test_autonomous_enabled_and_approved_completes(self):
        r = self._run(mode="autonomous", autonomous_enabled=True, approved=True)
        self.assertTrue(r.success)
        self.assertEqual(r.status, "completed")
        got = self.monday.task("get", task_id=self.task_id)
        self.assertEqual(got.data["status"], "completed")

    def test_gated_action_blocked_without_approval(self):
        r = self._run(requested_actions=["commit"])
        self.assertFalse(r.success)
        self.assertEqual(r.status, "blocked")
        self.assertIn("commit", r.data["gate"]["gated_actions"])
        got = self.monday.task("get", task_id=self.task_id)
        self.assertEqual(got.data["status"], "backlog")  # no execution happened

    def test_gated_action_allowed_with_approval(self):
        r = self._run(requested_actions=["commit"], approved=True)
        self.assertTrue(r.success)
        self.assertEqual(r.status, "review")

    def test_review_approve_completes_task(self):
        run = self._run()
        r = self.monday.agent("review", run_id=run.run_id, approve=True)
        self.assertTrue(r.success)
        self.assertEqual(r.data["approval"]["decision"], "approved")
        got = self.monday.task("get", task_id=self.task_id)
        self.assertEqual(got.data["status"], "completed")

    def test_review_reject_records_decision(self):
        run = self._run()
        r = self.monday.agent("review", run_id=run.run_id, approve=False)
        self.assertEqual(r.data["approval"]["decision"], "rejected")
        got = self.monday.task("get", task_id=self.task_id)
        self.assertEqual(got.data["status"], "review")  # left for rework

    def test_review_unknown_run_fails(self):
        r = self.monday.agent("review", run_id="run-doesnotexist", approve=True)
        self.assertFalse(r.success)

    def test_history_lists_and_filters(self):
        self._run()
        self._run(role="qa")
        allr = self.monday.agent("history")
        self.assertEqual(allr.data["count"], 2)
        qa = self.monday.agent("history", role="qa")
        self.assertEqual(qa.data["count"], 1)
        byt = self.monday.agent("history", task_id=self.task_id)
        self.assertEqual(byt.data["count"], 2)

    def test_qa_and_security_run_independently(self):
        for role in ("qa", "security"):
            r = self._run(role=role)
            self.assertTrue(r.success, role)
            self.assertEqual(r.role, role)

    def test_register_new_agent_then_resolve(self):
        reg = self.monday.agent(
            "register", name="Extra Reviewer", role="reviewer", provider="fake", is_default=True
        )
        self.assertTrue(reg.success)
        r = self._run(role="reviewer")
        self.assertEqual(r.provider_used, "fake")

    def test_unknown_action(self):
        r = self.monday.agent("frobnicate")
        self.assertFalse(r.success)
        self.assertIn("Unknown action", r.message)

    def test_run_does_not_mutate_source_files(self):
        # The runtime must never write outside agents/ + logs/ (no code changes).
        r = self._run()
        self.assertTrue(r.success)
        # Only expected runtime dirs exist under root; no stray .py files created.
        stray = [p for p in self.root.rglob("*.py")]
        self.assertEqual(stray, [])


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------

class TestAgentCLI(unittest.TestCase):
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
        code, _ = self._cli("task", "create", "--title", "X", "--objective", "Do X.")
        self.assertEqual(code, 0)
        return "TASK-0001"

    def test_list(self):
        code, out = self._cli("agent", "list")
        self.assertEqual(code, 0)
        self.assertIn("Claude Code", out)
        self.assertIn("ChatGPT", out)

    def test_register(self):
        code, out = self._cli("agent", "register", "--name", "My QA", "--role", "qa", "--provider", "fake")
        self.assertEqual(code, 0)
        self.assertIn("AGENT-", out)

    def test_assign(self):
        tid = self._make_task()
        code, out = self._cli("agent", "assign", tid, "--role", "qa")
        self.assertEqual(code, 0)

    def test_run_review(self):
        tid = self._make_task()
        code, out = self._cli("agent", "run", tid, "--role", "lead-engineer", "--provider", "fake")
        self.assertEqual(code, 0)
        self.assertIn("AGENT RUN", out)
        self.assertIn("review", out)

    def test_run_autonomous_blocked(self):
        tid = self._make_task()
        code, out = self._cli("agent", "run", tid, "--role", "lead-engineer", "--provider", "fake", "--autonomous")
        self.assertEqual(code, 1)  # blocked → non-zero
        self.assertIn("BLOCKED", out)

    def test_run_gated_commit_blocked(self):
        tid = self._make_task()
        code, out = self._cli(
            "agent", "run", tid, "--role", "lead-engineer", "--provider", "fake", "--action", "commit"
        )
        self.assertEqual(code, 1)
        self.assertIn("BLOCKED", out)

    def test_history_empty(self):
        code, out = self._cli("agent", "history")
        self.assertEqual(code, 0)
        self.assertIn("No agent runs", out)

    def test_review_flow(self):
        tid = self._make_task()
        # run, then parse run id from JSON output
        code, out = self._cli("agent", "run", tid, "--role", "lead-engineer", "--provider", "fake", "--json")
        self.assertEqual(code, 0)
        run_id = json.loads(out)["run_id"]
        code, out = self._cli("agent", "review", run_id, "--approve")
        self.assertEqual(code, 0)
        self.assertIn("approved", out)


if __name__ == "__main__":
    unittest.main()
