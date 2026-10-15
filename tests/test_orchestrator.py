"""Tests for the Execution Orchestrator (orchestrator/ + Monday.execute)."""
from __future__ import annotations

import json
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock

from brain.providers.base import (
    AIProvider,
    ProviderAvailability,
    ProviderResponse,
    ProviderUnavailableError,
)
from monday import Monday, MondayConfig
from orchestrator import (
    ExecutionMode,
    ExecutionPlan,
    ExecutionPlanner,
    ExecutionQueue,
    ExecutionReport,
    ExecutionUnit,
    ProviderSelectionPolicy,
    ResultValidator,
    rank_providers,
    select_provider,
)
from orchestrator.executor import ExecutionOrchestrator

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeProvider(AIProvider):
    """A test double implementing the AIProvider interface — no network."""

    def __init__(
        self,
        name: str = "fake",
        *,
        content: str = "This is a complete and correct result addressing the objective.",
        is_local: bool = False,
        cost_tier: int = 2,
        capability_tier: int = 2,
        raise_on_call: bool = False,
    ) -> None:
        self._name = name
        self._content = content
        self._is_local = is_local
        self._cost_tier = cost_tier
        self._capability_tier = capability_tier
        self._raise = raise_on_call
        self.calls: list[tuple[str, str]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_local(self) -> bool:
        return self._is_local

    @property
    def cost_tier(self) -> int:
        return self._cost_tier

    @property
    def capability_tier(self) -> int:
        return self._capability_tier

    def ask(self, prompt: str, context: str = "", max_tokens: int = 1024, **kwargs: Any) -> ProviderResponse:
        if self._raise:
            raise ProviderUnavailableError(f"{self._name} unavailable")
        self.calls.append(("ask", prompt))
        return ProviderResponse(content=self._content, model=f"{self._name}-1", provider=self._name, tokens_used=42)

    def plan(self, objective: str, context: str = "", max_tokens: int = 2048, **kwargs: Any) -> ProviderResponse:
        self.calls.append(("plan", objective))
        return ProviderResponse(content="1. step", provider=self._name)

    def summarize(self, content: str, max_words: int = 150, **kwargs: Any) -> ProviderResponse:
        self.calls.append(("summarize", content))
        return ProviderResponse(content="summary", provider=self._name)

    def review(self, content: str, criteria: list[str] | None = None, **kwargs: Any) -> ProviderResponse:
        self.calls.append(("review", content))
        return ProviderResponse(content="looks good", provider=self._name)


def _objective_text() -> str:
    return "Implement the authentication module with token refresh support."


# ---------------------------------------------------------------------------
# ExecutionMode / ProviderSelectionPolicy parsing
# ---------------------------------------------------------------------------

class TestEnumsFromStr(unittest.TestCase):
    def test_mode_review_default_alias(self):
        self.assertIs(ExecutionMode.from_str("review-required"), ExecutionMode.REVIEW)

    def test_mode_dry_run(self):
        self.assertIs(ExecutionMode.from_str("dry-run"), ExecutionMode.DRY_RUN)
        self.assertIs(ExecutionMode.from_str("dry_run"), ExecutionMode.DRY_RUN)

    def test_mode_autonomous(self):
        self.assertIs(ExecutionMode.from_str("autonomous"), ExecutionMode.AUTONOMOUS)
        self.assertIs(ExecutionMode.from_str("auto"), ExecutionMode.AUTONOMOUS)

    def test_mode_invalid_raises(self):
        with self.assertRaises(ValueError):
            ExecutionMode.from_str("yolo")

    def test_policy_aliases(self):
        self.assertIs(ProviderSelectionPolicy.from_str("local"), ProviderSelectionPolicy.PREFER_LOCAL)
        self.assertIs(ProviderSelectionPolicy.from_str("cheapest"), ProviderSelectionPolicy.LOWEST_COST)
        self.assertIs(ProviderSelectionPolicy.from_str("best"), ProviderSelectionPolicy.HIGHEST_CAPABILITY)
        self.assertIs(ProviderSelectionPolicy.from_str("manual"), ProviderSelectionPolicy.MANUAL)
        self.assertIs(
            ProviderSelectionPolicy.from_str("ordered-failover"),
            ProviderSelectionPolicy.ORDERED_FAILOVER,
        )
        self.assertIs(
            ProviderSelectionPolicy.from_str("fallback"),
            ProviderSelectionPolicy.ORDERED_FAILOVER,
        )
        self.assertIs(
            ProviderSelectionPolicy.from_str("failover"),
            ProviderSelectionPolicy.ORDERED_FAILOVER,
        )

    def test_policy_invalid_raises(self):
        with self.assertRaises(ValueError):
            ProviderSelectionPolicy.from_str("random")


# ---------------------------------------------------------------------------
# select_provider
# ---------------------------------------------------------------------------

class TestSelectProvider(unittest.TestCase):
    def setUp(self):
        self.anthropic = FakeProvider("anthropic", is_local=False, cost_tier=3, capability_tier=3)
        self.openai = FakeProvider("openai", is_local=False, cost_tier=2, capability_tier=2)
        self.ollama = FakeProvider("ollama", is_local=True, cost_tier=0, capability_tier=1)
        self.all = [self.anthropic, self.openai, self.ollama]

    def test_empty_returns_none(self):
        self.assertIsNone(select_provider([], ProviderSelectionPolicy.PREFER_LOCAL))

    def test_none_entries_filtered(self):
        self.assertIsNone(select_provider([None, None], ProviderSelectionPolicy.LOWEST_COST))

    def test_prefer_local_picks_local(self):
        chosen = select_provider(self.all, ProviderSelectionPolicy.PREFER_LOCAL)
        self.assertIs(chosen, self.ollama)

    def test_prefer_local_falls_back_to_capability(self):
        chosen = select_provider([self.anthropic, self.openai], ProviderSelectionPolicy.PREFER_LOCAL)
        self.assertIs(chosen, self.anthropic)

    def test_lowest_cost(self):
        chosen = select_provider(self.all, ProviderSelectionPolicy.LOWEST_COST)
        self.assertIs(chosen, self.ollama)  # cost_tier 0

    def test_lowest_cost_without_local(self):
        chosen = select_provider([self.anthropic, self.openai], ProviderSelectionPolicy.LOWEST_COST)
        self.assertIs(chosen, self.openai)  # cost_tier 2 < 3

    def test_highest_capability(self):
        chosen = select_provider(self.all, ProviderSelectionPolicy.HIGHEST_CAPABILITY)
        self.assertIs(chosen, self.anthropic)

    def test_manual_policy_returns_first(self):
        chosen = select_provider(self.all, ProviderSelectionPolicy.MANUAL)
        self.assertIs(chosen, self.anthropic)

    def test_ordered_failover_selects_first_candidate(self):
        chosen = select_provider(self.all, ProviderSelectionPolicy.ORDERED_FAILOVER)
        self.assertIs(chosen, self.anthropic)

    def test_manual_name_override(self):
        chosen = select_provider(self.all, ProviderSelectionPolicy.PREFER_LOCAL, manual_name="openai")
        self.assertIs(chosen, self.openai)

    def test_manual_name_not_available_returns_none(self):
        chosen = select_provider(self.all, ProviderSelectionPolicy.PREFER_LOCAL, manual_name="cohere")
        self.assertIsNone(chosen)

    def test_explicit_custom_selector_matches_noncanonical_provider_object(self):
        custom = FakeProvider("cohere")
        self.assertIs(
            select_provider(
                [custom],
                ProviderSelectionPolicy.MANUAL,
                manual_name="custom",
            ),
            custom,
        )
        self.assertEqual(
            rank_providers(
                [custom],
                ProviderSelectionPolicy.MANUAL,
                manual_name="custom",
            ),
            [custom],
        )
        self.assertIsNone(
            select_provider(
                [custom],
                ProviderSelectionPolicy.MANUAL,
                manual_name="cohere",
            )
        )

    def test_rank_providers_preserves_fallback_order(self):
        ranked = rank_providers(self.all, ProviderSelectionPolicy.HIGHEST_CAPABILITY)
        self.assertEqual([p.name for p in ranked], ["anthropic", "openai", "ollama"])

    def test_rank_ordered_failover_preserves_caller_order(self):
        ranked = rank_providers(
            [self.openai, self.ollama, self.anthropic],
            ProviderSelectionPolicy.ORDERED_FAILOVER,
        )
        self.assertEqual([p.name for p in ranked], ["openai", "ollama", "anthropic"])


# ---------------------------------------------------------------------------
# ExecutionQueue
# ---------------------------------------------------------------------------

def _unit(task_id: str, priority: int) -> ExecutionUnit:
    plan = ExecutionPlan(task_id=task_id, objective="obj", prompt="p")
    return ExecutionUnit(task_id=task_id, priority=priority, plan=plan, objective="obj")


class TestExecutionQueue(unittest.TestCase):
    def test_empty(self):
        q = ExecutionQueue()
        self.assertTrue(q.is_empty())
        self.assertEqual(len(q), 0)
        self.assertIsNone(q.dequeue())
        self.assertIsNone(q.peek())
        self.assertFalse(bool(q))

    def test_priority_order(self):
        q = ExecutionQueue()
        q.enqueue(_unit("C", 2))
        q.enqueue(_unit("A", 0))
        q.enqueue(_unit("B", 1))
        self.assertEqual([q.dequeue().task_id for _ in range(3)], ["A", "B", "C"])

    def test_fifo_within_same_priority(self):
        q = ExecutionQueue()
        q.enqueue(_unit("first", 1))
        q.enqueue(_unit("second", 1))
        q.enqueue(_unit("third", 1))
        self.assertEqual([q.dequeue().task_id for _ in range(3)], ["first", "second", "third"])

    def test_peek_does_not_remove(self):
        q = ExecutionQueue()
        q.enqueue(_unit("X", 0))
        self.assertEqual(q.peek().task_id, "X")
        self.assertEqual(len(q), 1)

    def test_len_and_bool(self):
        q = ExecutionQueue()
        q.enqueue(_unit("X", 0))
        self.assertEqual(len(q), 1)
        self.assertTrue(bool(q))


# ---------------------------------------------------------------------------
# ExecutionPlanner
# ---------------------------------------------------------------------------

class TestExecutionPlanner(unittest.TestCase):
    def setUp(self):
        self.planner = ExecutionPlanner()
        self.task = {
            "id": "TASK-0001",
            "title": "Auth module",
            "objective": _objective_text(),
            "context": "Use JWT.",
            "task_type": "feature",
            "acceptance_criteria": ["Tokens refresh", "Sessions expire"],
        }

    def test_plan_basic_fields(self):
        plan = self.planner.plan(self.task)
        self.assertEqual(plan.task_id, "TASK-0001")
        self.assertEqual(plan.objective, _objective_text())
        self.assertEqual(plan.source, "deterministic")

    def test_prompt_includes_objective_and_criteria(self):
        plan = self.planner.plan(self.task)
        self.assertIn("authentication module", plan.prompt.lower())
        self.assertIn("Tokens refresh", plan.prompt)

    def test_steps_non_empty(self):
        plan = self.planner.plan(self.task)
        self.assertTrue(plan.steps)

    def test_context_includes_task_context(self):
        plan = self.planner.plan(self.task)
        self.assertIn("Use JWT.", plan.context)

    def test_context_includes_advisory(self):
        advisory = {
            "repository_summary": "A small project with 5 entries.",
            "sprint_goal": "Ship auth.",
            "risks": [{"severity": "high", "title": "No tests"}],
        }
        plan = self.planner.plan(self.task, advisory)
        self.assertIn("A small project", plan.context)
        self.assertIn("Ship auth.", plan.context)
        self.assertIn("No tests", plan.context)

    def test_objective_falls_back_to_title(self):
        task = {"id": "T", "title": "Just a title", "objective": ""}
        plan = self.planner.plan(task)
        self.assertEqual(plan.objective, "Just a title")

    def test_to_dict_roundtrip(self):
        plan = self.planner.plan(self.task)
        d = plan.to_dict()
        self.assertEqual(d["task_id"], "TASK-0001")
        self.assertIn("prompt", d)


# ---------------------------------------------------------------------------
# ResultValidator
# ---------------------------------------------------------------------------

class TestResultValidator(unittest.TestCase):
    def setUp(self):
        self.validator = ResultValidator()
        self.plan = ExecutionPlan(task_id="T", objective=_objective_text(), prompt="p")
        self.task = {"id": "T"}

    def test_valid_response(self):
        resp = ProviderResponse(content="A thorough authentication module implementation with refresh.")
        result = self.validator.validate(self.plan, resp, self.task)
        self.assertTrue(result.valid)
        self.assertGreater(result.score, 0.5)

    def test_empty_response_invalid(self):
        resp = ProviderResponse(content="")
        result = self.validator.validate(self.plan, resp, self.task)
        self.assertFalse(result.valid)
        self.assertIn("empty", " ".join(result.issues).lower())

    def test_none_response_invalid(self):
        result = self.validator.validate(self.plan, None, self.task)
        self.assertFalse(result.valid)

    def test_short_response_invalid(self):
        resp = ProviderResponse(content="ok")
        result = self.validator.validate(self.plan, resp, self.task)
        self.assertFalse(result.valid)

    def test_refusal_invalid(self):
        resp = ProviderResponse(content="I cannot help with that.")
        result = self.validator.validate(self.plan, resp, self.task)
        self.assertFalse(result.valid)
        self.assertFalse(result.checks["not_refusal"])

    def test_addresses_goal_false_still_valid_lower_score(self):
        resp = ProviderResponse(content="Completely unrelated lorem ipsum dolor sit amet text here.")
        result = self.validator.validate(self.plan, resp, self.task)
        self.assertTrue(result.valid)  # length+non-empty+not-refusal pass
        self.assertFalse(result.checks["addresses_goal"])

    def test_score_capped(self):
        resp = ProviderResponse(content="A thorough authentication module with token refresh support.")
        result = self.validator.validate(self.plan, resp, self.task)
        self.assertLessEqual(result.score, 0.9)

    def test_to_dict(self):
        resp = ProviderResponse(content="authentication module result with refresh")
        d = self.validator.validate(self.plan, resp, self.task).to_dict()
        self.assertIn("valid", d)
        self.assertIn("checks", d)


# ---------------------------------------------------------------------------
# ExecutionReport
# ---------------------------------------------------------------------------

class TestExecutionReport(unittest.TestCase):
    def test_write_log_creates_file(self):
        with TemporaryDirectory() as tmp:
            report = ExecutionReport(execution_id="exec-abc", task_id="TASK-0001", status="completed", success=True)
            path = report.write_log(Path(tmp))
            self.assertTrue(path.exists())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            data = json.loads(path.read_text())
            self.assertEqual(data["execution_id"], "exec-abc")
            self.assertEqual(data["task_id"], "TASK-0001")

    def test_write_log_migrates_existing_reports_to_private_mode(self):
        with TemporaryDirectory() as tmp:
            logs_dir = Path(tmp)
            legacy = logs_dir / "legacy.json"
            legacy.write_text(
                '{"execution_id": "legacy", "status": "completed"}\n',
                encoding="utf-8",
            )
            legacy.chmod(0o644)

            report = ExecutionReport(execution_id="exec-new", task_id="TASK-0001")
            new_path = report.write_log(logs_dir)

            self.assertEqual(stat.S_IMODE(legacy.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(new_path.stat().st_mode), 0o600)

    def test_write_log_migration_skips_symlinks_and_nonregular_entries(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs_dir = root / "executions"
            logs_dir.mkdir()
            target = root / "outside.json"
            target.write_text("do not touch\n", encoding="utf-8")
            target.chmod(0o644)
            link = logs_dir / "linked.json"
            link.symlink_to(target)
            nonregular = logs_dir / "directory.json"
            nonregular.mkdir()

            ExecutionReport(execution_id="exec-new", task_id="TASK-0001").write_log(logs_dir)

            self.assertTrue(link.is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "do not touch\n")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
            self.assertTrue(nonregular.is_dir())

    def test_write_log_migration_skips_hard_links(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs_dir = root / "executions"
            logs_dir.mkdir()
            target = root / "outside.json"
            target.write_text("do not touch\n", encoding="utf-8")
            target.chmod(0o644)
            (logs_dir / "linked.json").hardlink_to(target)

            ExecutionReport(execution_id="exec-new", task_id="TASK-0001").write_log(logs_dir)

            self.assertEqual(target.read_text(encoding="utf-8"), "do not touch\n")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)

    def test_write_log_rejects_a_linked_report_directory(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            outside.mkdir()
            logs_dir = root / "executions"
            logs_dir.symlink_to(outside, target_is_directory=True)

            with self.assertRaises(OSError):
                ExecutionReport(
                    execution_id="exec-new",
                    task_id="TASK-0001",
                ).write_log(logs_dir)

            self.assertEqual(list(outside.iterdir()), [])

    def test_write_log_rejects_a_linked_report_parent(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            executions = outside / "executions"
            executions.mkdir(parents=True)
            victim = executions / "legacy.json"
            victim.write_text('{"secret": "unchanged"}\n', encoding="utf-8")
            victim.chmod(0o644)
            (root / "logs").symlink_to(outside, target_is_directory=True)

            with self.assertRaises(OSError):
                ExecutionReport(
                    execution_id="exec-new",
                    task_id="TASK-0001",
                ).write_log(root / "logs" / "executions")

            self.assertEqual(
                victim.read_text(encoding="utf-8"),
                '{"secret": "unchanged"}\n',
            )
            self.assertEqual(stat.S_IMODE(victim.stat().st_mode), 0o644)
            self.assertFalse((executions / "exec-new.json").exists())

    def test_write_log_failure_preserves_the_prior_complete_report(self):
        with TemporaryDirectory() as tmp:
            logs_dir = Path(tmp)
            report = ExecutionReport(
                execution_id="exec-stable",
                task_id="TASK-0001",
                status="completed",
            )
            path = report.write_log(logs_dir)
            before = path.read_bytes()

            with mock.patch(
                "orchestrator.report.os.replace",
                side_effect=OSError("simulated write failure"),
            ):
                with self.assertRaises(OSError):
                    report.write_log(logs_dir)

            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(logs_dir.glob("*.tmp")), [])

    def test_to_dict_has_all_fields(self):
        report = ExecutionReport(execution_id="x", task_id="t")
        d = report.to_dict()
        for key in ("provider_used", "knowledge_captured", "follow_up_tasks", "confidence", "files_changed"):
            self.assertIn(key, d)


# ---------------------------------------------------------------------------
# Monday.execute — end-to-end on a temp project
# ---------------------------------------------------------------------------

class TestMondayExecute(unittest.TestCase):
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

    def _provider(self, **kw) -> FakeProvider:
        return FakeProvider("ollama", is_local=True, cost_tier=0, capability_tier=1, **kw)

    def test_review_mode_success(self):
        prov = self._provider()
        r = self.monday.execute(self.task_id, mode="review", providers=[prov])
        self.assertTrue(r.success)
        self.assertEqual(r.status, "review")
        self.assertEqual(r.provider_used, "ollama")
        self.assertEqual(len(prov.calls), 1)  # provider was actually called
        # task moved to review
        got = self.monday.task("get", task_id=self.task_id)
        self.assertEqual(got.data["status"], "review")
        # knowledge captured
        self.assertEqual(len(r.knowledge_captured), 1)
        # report persisted
        self.assertTrue(Path(r.report_path).exists())

    def test_dry_run_makes_no_call_and_no_changes(self):
        prov = self._provider()
        r = self.monday.execute(self.task_id, mode="dry-run", providers=[prov])
        self.assertTrue(r.success)
        self.assertEqual(r.status, "dry-run")
        self.assertEqual(len(prov.calls), 0)  # provider NOT called
        # task untouched
        got = self.monday.task("get", task_id=self.task_id)
        self.assertEqual(got.data["status"], "backlog")
        self.assertEqual(r.knowledge_captured, [])
        # plan still produced + report persisted
        self.assertIn("plan", r.data)
        self.assertTrue(Path(r.report_path).exists())

    def test_dry_run_flag_via_mode(self):
        r = self.monday.execute(self.task_id, mode="dry-run", providers=[self._provider()])
        self.assertEqual(r.mode, "dry-run")

    def test_autonomous_without_enable_blocked(self):
        r = self.monday.execute(self.task_id, mode="autonomous", providers=[self._provider()])
        self.assertFalse(r.success)
        self.assertEqual(r.status, "blocked")
        got = self.monday.task("get", task_id=self.task_id)
        self.assertEqual(got.data["status"], "backlog")  # untouched

    def test_autonomous_with_enable_completes(self):
        prov = self._provider()
        r = self.monday.execute(
            self.task_id, mode="autonomous", autonomous_enabled=True, providers=[prov],
        )
        self.assertTrue(r.success)
        self.assertEqual(r.status, "completed")
        got = self.monday.task("get", task_id=self.task_id)
        self.assertEqual(got.data["status"], "completed")

    def test_no_provider_skips(self):
        r = self.monday.execute(self.task_id, mode="review", providers=[])
        self.assertFalse(r.success)
        self.assertEqual(r.status, "skipped")
        self.assertEqual(r.provider_used, "")

    def test_provider_failure_reported(self):
        prov = self._provider(raise_on_call=True)
        r = self.monday.execute(self.task_id, mode="review", providers=[prov])
        self.assertFalse(r.success)
        self.assertEqual(r.status, "failed")
        self.assertEqual(r.provider_used, "")
        self.assertIn("failed", r.message.lower())

    def test_provider_exception_text_never_crosses_the_execution_boundary(self):
        secret = "provider echoed credential and private prompt LEAK-ME-123456789"

        class LeakingProvider(FakeProvider):
            def ask(self, *args, **kwargs):
                raise RuntimeError(secret)

        r = self.monday.execute(
            self.task_id,
            mode="review",
            providers=[LeakingProvider("openai")],
        )

        report_text = Path(r.report_path).read_text(encoding="utf-8")
        response_text = json.dumps({
            "message": r.message,
            "attempts": r.provider_attempts,
            "data": r.data,
        })
        bus = self.monday._Monday__bus
        from events.types import EventType
        failure_events = bus.history(EventType.MODEL_CALL_FAILED)
        event_text = json.dumps([event.payload for event in failure_events])

        self.assertFalse(r.success)
        self.assertEqual(r.provider_attempts[0]["reason"], "provider-error")
        self.assertEqual(failure_events[0].payload["error"], "provider-error")
        self.assertNotIn(secret, report_text)
        self.assertNotIn(secret, response_text)
        self.assertNotIn(secret, event_text)

    def test_hostile_provider_metadata_accessors_are_contained_and_fail_over(self):
        secret = "SELECTION-METADATA-SECRET-92841"

        class HostileProvider(FakeProvider):
            @property
            def name(self) -> str:
                raise RuntimeError(secret)

            @property
            def is_local(self) -> bool:
                raise RuntimeError(secret)

            @property
            def cost_tier(self) -> int:
                raise RuntimeError(secret)

            @property
            def capability_tier(self) -> int:
                raise RuntimeError(secret)

            def availability(self) -> ProviderAvailability:
                return ProviderAvailability(
                    available=True,
                    provider="PRIVATEPROMPT123456",
                    model="sk-proj-LEAKME123456",
                    code="ready",
                )

            def ask(self, *args: Any, **kwargs: Any) -> ProviderResponse:
                raise RuntimeError(secret)

        hostile = HostileProvider("openai")
        healthy = FakeProvider("openai")
        for policy in ProviderSelectionPolicy:
            with self.subTest(policy=policy.value):
                ranked = rank_providers([hostile, healthy], policy)
                expected = 2 if policy is not ProviderSelectionPolicy.MANUAL else 1
                self.assertEqual(len(ranked), expected)

        result = self.monday.execute(
            self.task_id,
            mode="review",
            policy="ordered-failover",
            providers=[hostile, healthy],
        )

        report_text = Path(result.report_path).read_text(encoding="utf-8")
        bus = self.monday._Monday__bus
        rendered = json.dumps({
            "response": result.data,
            "attempts": result.provider_attempts,
            "events": [event.payload for event in bus.history()],
        })
        self.assertTrue(result.success)
        self.assertEqual(result.provider_used, "openai")
        self.assertEqual(result.provider_attempts[0]["provider"], "custom")
        self.assertNotIn(secret, rendered)
        self.assertNotIn(secret, report_text)
        self.assertNotIn("LEAKME", rendered)
        self.assertNotIn("PRIVATEPROMPT", rendered)

    def test_provider_response_metadata_is_reduced_to_fixed_values(self):
        secret = "PRIVATEPROMPT123456"

        class HostileResponseProvider(FakeProvider):
            def ask(self, *args: Any, **kwargs: Any) -> ProviderResponse:
                return ProviderResponse(
                    content=(
                        "A complete authentication result with enough detail "
                        "to pass deterministic validation."
                    ),
                    provider="sk-proj-LEAKME123456",
                    model=secret,
                    tokens_used=secret,  # type: ignore[arg-type]
                    metadata={"stop_reason": secret, "truncated": secret},
                )

        result = self.monday.execute(
            self.task_id,
            mode="review",
            providers=[HostileResponseProvider("openai")],
        )

        report_text = Path(result.report_path).read_text(encoding="utf-8")
        bus = self.monday._Monday__bus
        rendered = json.dumps({
            "response": result.data,
            "events": [event.payload for event in bus.history()],
        })
        self.assertTrue(result.success)
        self.assertEqual(result.data["model_used"], "openai-model")
        self.assertEqual(result.data["stop_reason"], "")
        self.assertNotIn(secret, rendered)
        self.assertNotIn(secret, report_text)
        self.assertNotIn("LEAKME", rendered)

    def test_malformed_provider_content_fails_over_without_leaking(self):
        secret = "MALFORMED-CONTENT-SECRET-18642"

        class HostileContent(str):
            def __str__(self):
                raise RuntimeError(secret)

            def __getitem__(self, key):
                raise RuntimeError(secret)

        class MalformedContentProvider(FakeProvider):
            def __init__(self, name: str, content: object):
                super().__init__(name)
                self._malformed_content = content

            def ask(self, *args: Any, **kwargs: Any) -> ProviderResponse:
                return ProviderResponse(
                    content=self._malformed_content,  # type: ignore[arg-type]
                    provider=self.name,
                )

        result = self.monday.execute(
            self.task_id,
            mode="review",
            policy="ordered-failover",
            providers=[
                MalformedContentProvider("openai", secret.encode()),
                MalformedContentProvider("anthropic", HostileContent(secret)),
                FakeProvider("deepseek"),
            ],
        )

        report_text = Path(result.report_path).read_text(encoding="utf-8")
        rendered = json.dumps({
            "response": result.data,
            "attempts": result.provider_attempts,
        })
        self.assertTrue(result.success)
        self.assertEqual(result.provider_used, "deepseek")
        self.assertEqual(
            [
                (attempt["provider"], attempt["status"], attempt["reason"])
                for attempt in result.provider_attempts
            ],
            [
                ("openai", "failed", "provider-error"),
                ("anthropic", "failed", "provider-error"),
                ("deepseek", "completed", ""),
            ],
        )
        self.assertNotIn(secret, rendered)
        self.assertNotIn(secret, report_text)

    def test_length_stop_reasons_force_truncated_reports(self):
        class TruncatingProvider(FakeProvider):
            def __init__(self, stop_reason: str):
                super().__init__("openai")
                self._stop_reason = stop_reason

            def ask(self, *args: Any, **kwargs: Any) -> ProviderResponse:
                return ProviderResponse(
                    content=(
                        "This complete authentication result contains enough "
                        "detail for deterministic validation."
                    ),
                    provider="openai",
                    metadata={
                        "stop_reason": self._stop_reason,
                        "truncated": False,
                    },
                )

        for stop_reason in ("length", "max_tokens"):
            with self.subTest(stop_reason=stop_reason):
                created = self.monday.task(
                    "create",
                    title=f"Exercise {stop_reason} handling",
                    objective="Implement authentication result handling.",
                    task_type="feature",
                    priority="P1",
                )
                result = self.monday.execute(
                    created.task_id,
                    mode="review",
                    providers=[TruncatingProvider(stop_reason)],
                )
                persisted = json.loads(
                    Path(result.report_path).read_text(encoding="utf-8")
                )

                self.assertTrue(result.success)
                self.assertEqual(result.data["stop_reason"], stop_reason)
                self.assertIs(result.data["truncated"], True)
                self.assertEqual(persisted["stop_reason"], stop_reason)
                self.assertIs(persisted["truncated"], True)

    def test_metadata_dict_subclass_is_discarded_without_access(self):
        secret = "HOSTILE-METADATA-DICT-95371"

        class HostileMetadata(dict):
            def get(self, *args: Any, **kwargs: Any):
                raise RuntimeError(secret)

            def items(self):
                raise RuntimeError(secret)

            def __iter__(self):
                raise RuntimeError(secret)

        class HostileMetadataProvider(FakeProvider):
            def ask(self, *args: Any, **kwargs: Any) -> ProviderResponse:
                return ProviderResponse(
                    content=(
                        "This complete authentication result contains enough "
                        "detail for deterministic validation."
                    ),
                    provider="openai",
                    metadata=HostileMetadata({"stop_reason": "length"}),
                )

        result = self.monday.execute(
            self.task_id,
            mode="review",
            providers=[HostileMetadataProvider("openai")],
        )

        report_text = Path(result.report_path).read_text(encoding="utf-8")
        self.assertTrue(result.success)
        self.assertEqual(result.provider_used, "openai")
        self.assertEqual(result.data["stop_reason"], "")
        self.assertIs(result.data["truncated"], False)
        self.assertNotIn(secret, report_text)

    def test_hostile_metadata_key_is_ignored_without_comparison_or_leak(self):
        secret = "HOSTILE-METADATA-KEY-68423"

        class HostileKey:
            def __hash__(self):
                return hash("stop_reason")

            def __eq__(self, other):
                raise RuntimeError(secret)

        class HostileMetadataProvider(FakeProvider):
            def ask(self, *args: Any, **kwargs: Any) -> ProviderResponse:
                return ProviderResponse(
                    content=(
                        "This complete authentication result contains enough "
                        "detail for deterministic validation."
                    ),
                    provider="openai",
                    metadata={HostileKey(): "length"},  # type: ignore[dict-item]
                )

        result = self.monday.execute(
            self.task_id,
            mode="review",
            providers=[HostileMetadataProvider("openai")],
        )

        report_text = Path(result.report_path).read_text(encoding="utf-8")
        self.assertTrue(result.success)
        self.assertEqual(result.provider_used, "openai")
        self.assertEqual(result.data["stop_reason"], "")
        self.assertIs(result.data["truncated"], False)
        self.assertNotIn(secret, report_text)

    def test_provider_failure_falls_back_to_next_candidate(self):
        primary = FakeProvider(
            "anthropic", capability_tier=3, cost_tier=3, raise_on_call=True,
        )
        fallback = FakeProvider("deepseek", capability_tier=2, cost_tier=1)
        r = self.monday.execute(
            self.task_id,
            mode="review",
            policy="highest-capability",
            providers=[fallback, primary],
        )
        self.assertTrue(r.success)
        self.assertEqual(r.provider_used, "deepseek")
        self.assertEqual(
            [(a["provider"], a["status"]) for a in r.provider_attempts],
            [("anthropic", "failed"), ("deepseek", "completed")],
        )

    def test_validation_failure_blocks_capture(self):
        prov = self._provider(content="")  # empty → fails validation
        r = self.monday.execute(self.task_id, mode="review", providers=[prov])
        self.assertFalse(r.success)
        self.assertEqual(r.status, "validation-failed")
        self.assertEqual(r.knowledge_captured, [])
        got = self.monday.task("get", task_id=self.task_id)
        self.assertNotEqual(got.data["status"], "review")

    def test_unknown_task_fails(self):
        r = self.monday.execute("TASK-9999", mode="review", providers=[self._provider()])
        self.assertFalse(r.success)
        self.assertEqual(r.status, "failed")

    def test_terminal_task_skipped(self):
        # Move task to completed first
        self.monday.task("start", task_id=self.task_id)
        self.monday.task("complete", task_id=self.task_id)
        r = self.monday.execute(self.task_id, mode="review", providers=[self._provider()])
        self.assertFalse(r.success)
        self.assertEqual(r.status, "skipped")

    def test_invalid_mode_message(self):
        r = self.monday.execute(self.task_id, mode="nonsense", providers=[self._provider()])
        self.assertFalse(r.success)
        self.assertIn("mode", r.message.lower())

    def test_invalid_policy_message(self):
        r = self.monday.execute(self.task_id, policy="nonsense", providers=[self._provider()])
        self.assertFalse(r.success)
        self.assertIn("policy", r.message.lower())

    def test_manual_provider_override(self):
        a = FakeProvider("anthropic", cost_tier=3, capability_tier=3)
        o = FakeProvider("ollama", is_local=True, cost_tier=0, capability_tier=1)
        r = self.monday.execute(self.task_id, provider="anthropic", providers=[a, o])
        self.assertEqual(r.provider_used, "anthropic")

    def test_policy_prefers_local(self):
        a = FakeProvider("anthropic", cost_tier=3, capability_tier=3)
        o = FakeProvider("ollama", is_local=True, cost_tier=0, capability_tier=1)
        r = self.monday.execute(self.task_id, policy="prefer-local", providers=[a, o])
        self.assertEqual(r.provider_used, "ollama")

    def test_emits_model_call_events(self):
        # The orchestrator shares Monday's bus; check audit history via a fresh run.
        prov = self._provider()
        self.monday.execute(self.task_id, mode="review", providers=[prov])
        # Access the shared bus through name-mangled attribute for verification.
        bus = self.monday._Monday__bus
        from events.types import EventType
        started = bus.history(EventType.MODEL_CALL_STARTED)
        completed = bus.history(EventType.MODEL_CALL_COMPLETED)
        self.assertTrue(started)
        self.assertTrue(completed)


# ---------------------------------------------------------------------------
# Monday.task("review") transition
# ---------------------------------------------------------------------------

class TestTaskReviewAction(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.monday = Monday(MondayConfig(project_root=Path(self._tmp.name)))
        created = self.monday.task("create", title="X", objective="Do X.")
        self.task_id = created.task_id

    def tearDown(self):
        self._tmp.cleanup()

    def test_review_requires_task_id(self):
        r = self.monday.task("review")
        self.assertFalse(r.success)

    def test_review_from_in_progress(self):
        self.monday.task("start", task_id=self.task_id)
        r = self.monday.task("review", task_id=self.task_id)
        self.assertTrue(r.success)
        self.assertEqual(r.data["status"], "review")

    def test_review_from_backlog_invalid(self):
        r = self.monday.task("review", task_id=self.task_id)
        self.assertFalse(r.success)  # BACKLOG → REVIEW is illegal


# ---------------------------------------------------------------------------
# Orchestrator constructed directly (unit-level wiring)
# ---------------------------------------------------------------------------

class TestExecutionOrchestratorDirect(unittest.TestCase):
    def test_default_construction(self):
        with TemporaryDirectory() as tmp:
            monday = Monday(MondayConfig(project_root=Path(tmp)))
            orch = ExecutionOrchestrator(
                monday=monday,
                project_root=Path(tmp),
                providers=[FakeProvider()],
            )
            self.assertIs(orch._policy, ProviderSelectionPolicy.PREFER_LOCAL)
            self.assertIs(orch._mode, ExecutionMode.REVIEW)


if __name__ == "__main__":
    unittest.main()
