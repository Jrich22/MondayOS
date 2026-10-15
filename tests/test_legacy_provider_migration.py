"""Regression tests for upgrading persisted provider diagnostics safely."""

from __future__ import annotations

import json
import stat
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from agents.runtime import AgentRuntime
from agents.team import TeamRun, TeamWorkflow
from agents.types import AgentRun
from core.secure_io import write_json_atomic_at as secure_write_json_atomic_at
from core.secure_io import write_text_atomic_at as secure_write_text_atomic_at
from monday import Monday, MondayConfig
from orchestrator.report import ExecutionReport
from workspace.models import Message, MessageRole
from workspace.store import ConversationStore

LEGACY_SECRET = "legacy-sdk-error token=sk-do-not-return"
VISIBLE_OUTPUT = "The generated product recommendation remains available."
NON_FAILED_DIAGNOSTIC_STATUSES = (
    "",
    "planned",
    "dry-run",
    "skipped",
    "blocked",
    "validation-failed",
    "review",
    "completed",
    "unavailable",
)
NON_FAILED_TEAM_STATUSES = (
    "awaiting-approval",
    "blocked",
    "changes-requested",
    "completed",
    "dry-run",
    "interrupted",
    "invalid-verdict",
    "rejected",
    "running",
)


def _legacy_execution(*, status: str = "failed") -> dict[str, object]:
    return {
        "execution_id": "exec-legacy",
        "task_id": "TASK-0042",
        "provider_used": LEGACY_SECRET,
        "model_used": LEGACY_SECRET,
        "provider_attempts": [
            {
                "provider": LEGACY_SECRET,
                "status": "failed",
                "reason": LEGACY_SECRET,
            },
            {
                "provider": LEGACY_SECRET,
                "status": "unavailable",
                "reason": LEGACY_SECRET,
            },
        ],
        "status": status,
        "success": False,
        "error": LEGACY_SECRET,
        "result_excerpt": VISIBLE_OUTPUT,
        "result_full": VISIBLE_OUTPUT,
        "stop_reason": LEGACY_SECRET,
        "truncated": False,
    }


class TestLegacyAgentRunMigration(unittest.TestCase):
    def test_get_and_public_history_scrub_and_rewrite_legacy_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            monday = Monday(MondayConfig(project_root=root))
            runtime = AgentRuntime(monday, root)
            runs_dir = root / "logs" / "agents"
            runs_dir.mkdir(parents=True)
            path = runs_dir / "run-legacy-provider.json"
            legacy = {
                "run_id": "run-legacy-provider",
                "task_id": "TASK-0042",
                "role": "qa",
                "provider_requested": LEGACY_SECRET,
                "provider_used": LEGACY_SECRET,
                "provider_model": LEGACY_SECRET,
                "status": "failed",
                "success": False,
                "created_at": "2026-01-01T00:00:00Z",
                "execution": _legacy_execution(),
                "message": LEGACY_SECRET,
            }

            path.write_text(json.dumps(legacy), encoding="utf-8")
            loaded = runtime.get_run("run-legacy-provider")

            self.assertNotIn(LEGACY_SECRET, json.dumps(loaded.to_dict()))
            self.assertEqual(loaded.provider_requested, "custom")
            self.assertEqual(loaded.provider_used, "custom")
            self.assertEqual(loaded.execution["result_full"], VISIBLE_OUTPUT)
            self.assertNotIn(LEGACY_SECRET, path.read_text(encoding="utf-8"))

            # Exercise the independently reachable public history path too.
            path.write_text(json.dumps(legacy), encoding="utf-8")
            response = monday.agent("history", task_id="TASK-0042")

            self.assertTrue(response.success, response.message)
            self.assertEqual(response.data["count"], 1)
            public_record = response.data["runs"][0]
            self.assertEqual(public_record["execution"]["result_full"], VISIBLE_OUTPUT)
            self.assertNotIn(LEGACY_SECRET, json.dumps(response.data))
            self.assertNotIn(LEGACY_SECRET, path.read_text(encoding="utf-8"))

    def test_get_migration_cannot_overwrite_a_concurrent_approval(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            monday = Monday(MondayConfig(project_root=root))
            runtime = AgentRuntime(monday, root)
            runs_dir = root / "logs" / "agents"
            runs_dir.mkdir(parents=True)
            run_id = "run-legacy-approval-race"
            path = runs_dir / f"{run_id}.json"
            legacy = {
                "run_id": run_id,
                "task_id": "TASK-0042",
                "role": "reviewer",
                "provider_requested": LEGACY_SECRET,
                "provider_used": LEGACY_SECRET,
                "provider_model": LEGACY_SECRET,
                "status": "review",
                "success": True,
                "created_at": "2026-01-01T00:00:00Z",
                "approval": {
                    "required": True,
                    "decision": "pending",
                    "by": "",
                    "at": "",
                    "note": "",
                },
                "message": LEGACY_SECRET,
            }
            path.write_text(json.dumps(legacy), encoding="utf-8")
            approved = AgentRun.from_dict({
                **legacy,
                "approval": {
                    "required": True,
                    "decision": "approved",
                    "by": "human:test",
                    "at": "2026-01-01T00:01:00Z",
                    "note": "approved concurrently",
                },
            }).to_dict()
            raced = False

            def approve_before_migration(
                directory_fd: int,
                name: str,
                value: object,
                **kwargs: object,
            ) -> None:
                nonlocal raced
                if not raced and "expected_identity" in kwargs:
                    raced = True
                    secure_write_json_atomic_at(directory_fd, name, approved)
                secure_write_json_atomic_at(directory_fd, name, value, **kwargs)

            with patch(
                "agents.runtime.write_json_atomic_at",
                side_effect=approve_before_migration,
            ):
                loaded = runtime.get_run(run_id)

            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(raced)
            self.assertEqual(loaded.approval["decision"], "approved")
            self.assertEqual(persisted["approval"]["decision"], "approved")
            self.assertEqual(persisted, approved)

    def test_non_failed_statuses_scrub_diagnostics_and_rewrite_the_run(self) -> None:
        for index, status in enumerate(NON_FAILED_DIAGNOSTIC_STATUSES):
            with self.subTest(status=status), TemporaryDirectory() as tmp:
                root = Path(tmp)
                monday = Monday(MondayConfig(project_root=root))
                runtime = AgentRuntime(monday, root)
                runs_dir = root / "logs" / "agents"
                runs_dir.mkdir(parents=True)
                run_id = f"run-legacy-status-{index}"
                path = runs_dir / f"{run_id}.json"
                path.write_text(
                    json.dumps({
                        "run_id": run_id,
                        "task_id": "TASK-0042",
                        "role": "qa",
                        "provider_requested": LEGACY_SECRET,
                        "provider_used": LEGACY_SECRET,
                        "provider_model": LEGACY_SECRET,
                        "status": status,
                        "success": False,
                        "created_at": "2026-01-01T00:00:00Z",
                        "execution": _legacy_execution(status=status),
                        "message": LEGACY_SECRET,
                    }),
                    encoding="utf-8",
                )

                loaded = runtime.get_run(run_id)

                self.assertEqual(loaded.execution["result_full"], VISIBLE_OUTPUT)
                self.assertNotIn(LEGACY_SECRET, json.dumps(loaded.to_dict()))
                self.assertNotIn(LEGACY_SECRET, path.read_text(encoding="utf-8"))

    def test_history_rejects_a_linked_ancestor_without_migrating_the_victim(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "trusted"
            root.mkdir()
            monday = Monday(MondayConfig(project_root=root))
            runtime = AgentRuntime(monday, root)

            outside_runs = base / "outside" / "logs" / "agents"
            outside_runs.mkdir(parents=True)
            victim = outside_runs / "run-legacy-provider.json"
            victim.write_text(
                json.dumps({
                    "run_id": "run-legacy-provider",
                    "task_id": "TASK-0042",
                    "role": "qa",
                    "provider_requested": LEGACY_SECRET,
                    "provider_used": LEGACY_SECRET,
                    "provider_model": LEGACY_SECRET,
                    "status": "failed",
                    "success": False,
                    "created_at": "2026-01-01T00:00:00Z",
                    "execution": _legacy_execution(),
                    "message": LEGACY_SECRET,
                }),
                encoding="utf-8",
            )
            before = victim.read_bytes()
            (root / "logs").symlink_to(base / "outside" / "logs", target_is_directory=True)

            with self.assertRaises(OSError):
                runtime.history()

            self.assertEqual(victim.read_bytes(), before)
            self.assertIn(LEGACY_SECRET, victim.read_text(encoding="utf-8"))

    def test_approval_sync_sanitizes_legacy_parent_before_rewriting_it(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            monday = Monday(MondayConfig(project_root=root))
            runtime = AgentRuntime(monday, root)
            runs_dir = root / "logs" / "agents"
            runs_dir.mkdir(parents=True)
            parent_path = runs_dir / "team-legacy-approval.json"
            parent_path.write_text(
                json.dumps({
                    "team_run_id": "team-legacy-approval",
                    "task_id": "TASK-0042",
                    "status": "awaiting-approval",
                    "success": True,
                    "created_at": "2026-01-01T00:00:00Z",
                    "child_run_ids": ["run-legacy-approval"],
                    "approval_run_id": "run-legacy-approval",
                    "stages": [{
                        "role": "reviewer",
                        "run_id": "run-legacy-approval",
                        "provider_used": LEGACY_SECRET,
                        "provider_model": LEGACY_SECRET,
                        "status": "review",
                        "verdict": "pass",
                        "confidence": "high",
                        "summary": VISIBLE_OUTPUT,
                    }],
                }),
                encoding="utf-8",
            )
            approved = AgentRun(
                run_id="run-legacy-approval",
                task_id="TASK-0042",
                role="reviewer",
                approval={
                    "decision": "approved",
                    "by": "human:test",
                    "at": "2026-01-01T00:01:00Z",
                    "note": "",
                },
            )

            runtime._sync_parent_team_run(approved)

            migrated = json.loads(parent_path.read_text(encoding="utf-8"))
            self.assertEqual(migrated["status"], "completed")
            self.assertEqual(migrated["stages"][0]["provider_used"], "custom")
            self.assertEqual(migrated["stages"][0]["provider_model"], "")
            self.assertEqual(migrated["stages"][0]["summary"], VISIBLE_OUTPUT)
            self.assertNotIn(LEGACY_SECRET, json.dumps(migrated))


class TestLegacyTeamRunMigration(unittest.TestCase):
    def test_get_and_public_history_scrub_and_rewrite_legacy_team_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            monday = Monday(MondayConfig(project_root=root))
            runs_dir = root / "logs" / "agents"
            runs_dir.mkdir(parents=True)
            path = runs_dir / "team-legacy-provider.json"
            legacy = {
                "team_run_id": "team-legacy-provider",
                "task_id": "TASK-0042",
                "status": "failed",
                "success": False,
                "created_at": "2026-01-01T00:00:00Z",
                "stopped_at": "qa",
                "stopped_reason": LEGACY_SECRET,
                "message": LEGACY_SECRET,
                "stages": [
                    {
                        "role": "qa",
                        "run_id": "run-legacy-provider",
                        "provider_used": LEGACY_SECRET,
                        "provider_model": LEGACY_SECRET,
                        "status": "failed",
                        "verdict": "block",
                        "summary": LEGACY_SECRET,
                    },
                    {
                        "role": "cpo",
                        "run_id": "run-visible-output",
                        "provider_used": "openai",
                        "provider_model": LEGACY_SECRET,
                        "status": "executed",
                        "verdict": "pass",
                        "summary": VISIBLE_OUTPUT,
                    },
                ],
            }

            path.write_text(json.dumps(legacy), encoding="utf-8")
            response = monday.team("get", team_run_id="team-legacy-provider")

            self.assertTrue(response.success, response.message)
            self.assertNotIn(LEGACY_SECRET, json.dumps(response.data))
            self.assertEqual(response.data["stages"][0]["provider_used"], "custom")
            self.assertEqual(response.data["stages"][0]["summary"], "Stage did not complete.")
            self.assertEqual(response.data["stages"][1]["summary"], VISIBLE_OUTPUT)
            self.assertNotIn(LEGACY_SECRET, path.read_text(encoding="utf-8"))

            # History loads records independently and must perform the same rewrite.
            path.write_text(json.dumps(legacy), encoding="utf-8")
            history = monday.team("history", task_id="TASK-0042")

            self.assertTrue(history.success, history.message)
            self.assertEqual(history.data["count"], 1)
            self.assertEqual(
                history.data["runs"][0]["stages"][1]["summary"],
                VISIBLE_OUTPUT,
            )
            self.assertNotIn(LEGACY_SECRET, json.dumps(history.data))
            self.assertNotIn(LEGACY_SECRET, path.read_text(encoding="utf-8"))

    def test_get_migration_cannot_overwrite_a_concurrent_team_update(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            monday = Monday(MondayConfig(project_root=root))
            workflow = TeamWorkflow(monday, root)
            runs_dir = root / "logs" / "agents"
            runs_dir.mkdir(parents=True)
            team_run_id = "team-legacy-update-race"
            path = runs_dir / f"{team_run_id}.json"
            legacy = {
                "team_run_id": team_run_id,
                "task_id": "TASK-0042",
                "status": "awaiting-approval",
                "success": True,
                "created_at": "2026-01-01T00:00:00Z",
                "child_run_ids": ["run-legacy-approval"],
                "approval_run_id": "run-legacy-approval",
                "message": LEGACY_SECRET,
                "stopped_reason": LEGACY_SECRET,
                "stages": [{
                    "role": "reviewer",
                    "run_id": "run-legacy-approval",
                    "provider_used": LEGACY_SECRET,
                    "provider_model": LEGACY_SECRET,
                    "status": "review",
                    "verdict": "pass",
                    "confidence": "high",
                    "summary": VISIBLE_OUTPUT,
                }],
            }
            path.write_text(json.dumps(legacy), encoding="utf-8")
            current = TeamRun.from_dict({
                **legacy,
                "status": "completed",
                "success": True,
                "approval": {
                    "decision": "approved",
                    "by": "human:test",
                    "at": "2026-01-01T00:01:00Z",
                    "note": "approved concurrently",
                },
            }).to_dict()
            raced = False

            def complete_before_migration(
                directory_fd: int,
                name: str,
                value: object,
                **kwargs: object,
            ) -> None:
                nonlocal raced
                if not raced and "expected_identity" in kwargs:
                    raced = True
                    secure_write_json_atomic_at(directory_fd, name, current)
                secure_write_json_atomic_at(directory_fd, name, value, **kwargs)

            with patch(
                "agents.team.write_json_atomic_at",
                side_effect=complete_before_migration,
            ):
                loaded = workflow.get_team_run(team_run_id)

            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(raced)
            self.assertEqual(loaded.status, "completed")
            self.assertEqual(loaded.approval["decision"], "approved")
            self.assertEqual(persisted, current)

    def test_non_failed_statuses_scrub_top_level_diagnostics(self) -> None:
        for index, status in enumerate(NON_FAILED_TEAM_STATUSES):
            with self.subTest(status=status), TemporaryDirectory() as tmp:
                root = Path(tmp)
                monday = Monday(MondayConfig(project_root=root))
                runs_dir = root / "logs" / "agents"
                runs_dir.mkdir(parents=True)
                team_run_id = f"team-legacy-status-{index}"
                path = runs_dir / f"{team_run_id}.json"
                path.write_text(
                    json.dumps({
                        "team_run_id": team_run_id,
                        "task_id": "TASK-0042",
                        "status": status,
                        "success": status in {
                            "awaiting-approval",
                            "completed",
                            "dry-run",
                        },
                        "created_at": "2026-01-01T00:00:00Z",
                        "child_run_ids": ["run-legacy-approval"],
                        "approval_run_id": "run-legacy-approval",
                        "message": LEGACY_SECRET,
                        "stopped_reason": LEGACY_SECRET,
                        "stages": [],
                    }),
                    encoding="utf-8",
                )

                response = monday.team("get", team_run_id=team_run_id)

                self.assertTrue(response.success, response.message)
                self.assertNotIn(LEGACY_SECRET, json.dumps(response.data))
                self.assertNotIn(LEGACY_SECRET, path.read_text(encoding="utf-8"))

    def test_history_rejects_a_linked_ancestor_without_migrating_the_victim(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "trusted"
            root.mkdir()
            monday = Monday(MondayConfig(project_root=root))
            workflow = TeamWorkflow(monday, root)

            outside_runs = base / "outside" / "logs" / "agents"
            outside_runs.mkdir(parents=True)
            victim = outside_runs / "team-legacy-provider.json"
            victim.write_text(
                json.dumps({
                    "team_run_id": "team-legacy-provider",
                    "task_id": "TASK-0042",
                    "status": "failed",
                    "success": False,
                    "created_at": "2026-01-01T00:00:00Z",
                    "stopped_at": "qa",
                    "stopped_reason": LEGACY_SECRET,
                    "message": LEGACY_SECRET,
                    "stages": [{
                        "role": "qa",
                        "run_id": "run-legacy-provider",
                        "provider_used": LEGACY_SECRET,
                        "provider_model": LEGACY_SECRET,
                        "status": "failed",
                        "verdict": "block",
                        "summary": LEGACY_SECRET,
                    }],
                }),
                encoding="utf-8",
            )
            before = victim.read_bytes()
            (root / "logs").symlink_to(base / "outside" / "logs", target_is_directory=True)

            with self.assertRaises(OSError):
                workflow.history()

            self.assertEqual(victim.read_bytes(), before)
            self.assertIn(LEGACY_SECRET, victim.read_text(encoding="utf-8"))


class TestLegacyWorkspaceMigration(unittest.TestCase):
    def test_get_and_list_scrub_and_rewrite_legacy_conversation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ConversationStore(root)
            conversation = store.create(
                "acme",
                "Legacy provider record",
                now=datetime(2026, 1, 1, tzinfo=UTC),
            )
            conversation.messages.append(
                Message(
                    id="MSG-0001",
                    role=MessageRole.ASSISTANT,
                    content=VISIBLE_OUTPUT,
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    provider=LEGACY_SECRET,
                    model=LEGACY_SECRET,
                    tokens_used=10**12,
                    error=LEGACY_SECRET,
                    stop_reason=LEGACY_SECRET,
                )
            )
            path = store.save(conversation)

            loaded = store.get("acme", conversation.id)
            message = loaded.messages[0]

            self.assertEqual(message.content, VISIBLE_OUTPUT)
            self.assertEqual(message.provider, "custom")
            self.assertEqual(message.model, "")
            self.assertEqual(message.tokens_used, 0)
            self.assertEqual(message.error, "Provider request failed. Retry later.")
            self.assertNotIn(LEGACY_SECRET, json.dumps(loaded.to_dict()))
            self.assertNotIn(LEGACY_SECRET, path.read_text(encoding="utf-8"))

            # Sidebar/list loading is another migration entry point.
            store.save(conversation)
            listed = store.list("acme")

            self.assertEqual(len(listed), 1)
            self.assertNotIn(LEGACY_SECRET, json.dumps(listed[0].to_dict()))
            self.assertNotIn(LEGACY_SECRET, path.read_text(encoding="utf-8"))

    def test_migration_does_not_overwrite_a_concurrent_conversation_save(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ConversationStore(root)
            conversation = store.create(
                "acme",
                "Legacy provider record",
                now=datetime(2026, 1, 1, tzinfo=UTC),
            )
            conversation.messages.append(
                Message(
                    id="MSG-0001",
                    role=MessageRole.ASSISTANT,
                    content=VISIBLE_OUTPUT,
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    provider=LEGACY_SECRET,
                    error=LEGACY_SECRET,
                )
            )
            path = store.save(conversation)
            legacy_bytes = path.read_bytes()

            conversation.title = "Saved concurrently"
            conversation.messages[0].provider = "fake"
            conversation.messages[0].error = ""
            store.save(conversation)
            concurrent_text = path.read_text(encoding="utf-8")
            path.write_bytes(legacy_bytes)
            raced = False

            def save_before_migration(
                directory_fd: int,
                name: str,
                value: str,
                **kwargs: object,
            ) -> None:
                nonlocal raced
                if not raced and "expected_identity" in kwargs:
                    raced = True
                    secure_write_text_atomic_at(directory_fd, name, concurrent_text)
                secure_write_text_atomic_at(directory_fd, name, value, **kwargs)

            with patch(
                "workspace.store.write_text_atomic_at",
                side_effect=save_before_migration,
            ):
                loaded = store.get("acme", conversation.id)

            self.assertTrue(raced)
            self.assertEqual(loaded.title, "Saved concurrently")
            persisted = path.read_text(encoding="utf-8")
            self.assertIn("Saved concurrently", persisted)
            self.assertNotIn("Legacy provider record", persisted)

    def test_get_rejects_a_linked_ancestor_without_migrating_the_victim(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "trusted"
            outside_root = base / "outside"
            root.mkdir()

            outside_store = ConversationStore(outside_root)
            conversation = outside_store.create(
                "acme",
                "Legacy provider record",
                now=datetime(2026, 1, 1, tzinfo=UTC),
            )
            conversation.messages.append(
                Message(
                    id="MSG-0001",
                    role=MessageRole.ASSISTANT,
                    content=VISIBLE_OUTPUT,
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    provider=LEGACY_SECRET,
                    model=LEGACY_SECRET,
                    error=LEGACY_SECRET,
                )
            )
            victim = outside_store.save(conversation)
            before = victim.read_bytes()
            (root / "workspace").symlink_to(
                outside_root / "workspace",
                target_is_directory=True,
            )

            with self.assertRaises(ValueError):
                ConversationStore(root).get("acme", conversation.id)

            self.assertEqual(victim.read_bytes(), before)
            self.assertIn(LEGACY_SECRET, victim.read_text(encoding="utf-8"))

    def test_project_listing_rejects_a_linked_workspace_ancestor(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "trusted"
            outside = base / "outside"
            root.mkdir()
            victim = outside / "workspace" / "conversations" / "external-project"
            victim.mkdir(parents=True)
            marker = victim / "do-not-read.txt"
            marker.write_text(LEGACY_SECRET, encoding="utf-8")
            before = marker.read_bytes()
            (root / "workspace").symlink_to(
                outside / "workspace",
                target_is_directory=True,
            )

            projects = ConversationStore(root).projects_with_conversations()

            self.assertEqual(projects, [])
            self.assertEqual(marker.read_bytes(), before)

    def test_project_listing_ignores_a_linked_project_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "trusted"
            conversations = root / "workspace" / "conversations"
            conversations.mkdir(parents=True)
            victim = base / "outside-project"
            victim.mkdir()
            marker = victim / "do-not-read.txt"
            marker.write_text(LEGACY_SECRET, encoding="utf-8")
            before = marker.read_bytes()
            (conversations / "external-project").symlink_to(
                victim,
                target_is_directory=True,
            )

            projects = ConversationStore(root).projects_with_conversations()

            self.assertEqual(projects, [])
            self.assertEqual(marker.read_bytes(), before)

    def test_fixed_provider_errors_survive_save_and_get(self) -> None:
        fixed_errors = [
            "Provider authentication failed. Check the configured credential and retry.",
            "Provider credential is missing (openai); set OPENAI_API_KEY and retry.",
            "Provider request failed. Retry later.",
            "Provider request was rate-limited. Retry later.",
            "Provider returned an invalid response.",
            "Requested provider is unsupported or not configured.",
            "Provider service is unavailable. Retry later.",
            "The provider returned an empty response.",
            "Generation was stopped before any response arrived.",
            "The response stream ended unexpectedly.",
            (
                "No AI provider is configured for this MondayOS instance, so the "
                "workspace cannot answer. Configure a provider in MondayConfig."
            ),
            "unverified evidence in generated answer",
        ]
        with TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp))
            conversation = store.create(
                "acme",
                "Fixed provider errors",
                now=datetime(2026, 1, 1, tzinfo=UTC),
            )
            conversation.messages.extend(
                Message(
                    id=f"MSG-{index:04d}",
                    role=MessageRole.ASSISTANT,
                    content="",
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    provider="openai",
                    error=error,
                )
                for index, error in enumerate(fixed_errors, start=1)
            )

            store.save(conversation)
            loaded = store.get("acme", conversation.id)

            self.assertEqual(
                [message.error for message in loaded.messages],
                fixed_errors,
            )


class TestLegacyExecutionReportMigration(unittest.TestCase):
    def test_new_write_scrubs_old_diagnostics_but_preserves_generated_output(self) -> None:
        with TemporaryDirectory() as tmp:
            logs_dir = Path(tmp) / "executions"
            logs_dir.mkdir()
            legacy_path = logs_dir / "exec-legacy.json"
            legacy_path.write_text(json.dumps(_legacy_execution()), encoding="utf-8")

            ExecutionReport(
                execution_id="exec-new",
                task_id="TASK-0042",
                status="completed",
                success=True,
            ).write_log(logs_dir)

            migrated = json.loads(legacy_path.read_text(encoding="utf-8"))
            self.assertEqual(migrated["result_full"], VISIBLE_OUTPUT)
            self.assertEqual(migrated["result_excerpt"], VISIBLE_OUTPUT)
            self.assertEqual(migrated["provider_used"], "custom")
            self.assertEqual(migrated["model_used"], "")
            self.assertEqual(migrated["provider_attempts"][0]["reason"], "provider-error")
            self.assertEqual(migrated["provider_attempts"][1]["reason"], "unavailable")
            self.assertEqual(
                migrated["error"],
                "Execution failed. Check provider configuration or retry later.",
            )
            self.assertNotIn(LEGACY_SECRET, json.dumps(migrated))

    def test_new_write_scrubs_diagnostics_from_non_failed_legacy_statuses(self) -> None:
        with TemporaryDirectory() as tmp:
            logs_dir = Path(tmp) / "executions"
            logs_dir.mkdir()
            legacy_paths: list[Path] = []
            for index, status in enumerate(NON_FAILED_DIAGNOSTIC_STATUSES):
                path = logs_dir / f"exec-legacy-status-{index}.json"
                legacy = _legacy_execution(status=status)
                legacy["execution_id"] = path.stem
                path.write_text(
                    json.dumps(legacy),
                    encoding="utf-8",
                )
                legacy_paths.append(path)

            ExecutionReport(
                execution_id="exec-new",
                task_id="TASK-0042",
                status="completed",
                success=True,
            ).write_log(logs_dir)

            for status, path in zip(
                NON_FAILED_DIAGNOSTIC_STATUSES,
                legacy_paths,
                strict=True,
            ):
                with self.subTest(status=status):
                    migrated = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual(migrated["result_full"], VISIBLE_OUTPUT)
                    self.assertNotIn(LEGACY_SECRET, json.dumps(migrated))

    def test_migration_does_not_overwrite_a_concurrent_report_save(self) -> None:
        with TemporaryDirectory() as tmp:
            logs_dir = Path(tmp) / "executions"
            logs_dir.mkdir()
            legacy_path = logs_dir / "exec-legacy.json"
            legacy_path.write_text(json.dumps(_legacy_execution()), encoding="utf-8")
            concurrent = {
                **_legacy_execution(status="completed"),
                "provider_used": "fake",
                "model_used": "fake-1",
                "provider_attempts": [],
                "error": "",
                "result_full": "A newer completed report.",
                "result_excerpt": "A newer completed report.",
                "stop_reason": "end_turn",
                "success": True,
            }
            raced = False

            def save_before_migration(
                directory_fd: int,
                name: str,
                value: object,
                **kwargs: object,
            ) -> None:
                nonlocal raced
                if (
                    not raced
                    and name == "exec-legacy.json"
                    and "expected_identity" in kwargs
                ):
                    raced = True
                    secure_write_json_atomic_at(directory_fd, name, concurrent)
                secure_write_json_atomic_at(directory_fd, name, value, **kwargs)

            with patch(
                "orchestrator.report.write_json_atomic_at",
                side_effect=save_before_migration,
            ):
                ExecutionReport(
                    execution_id="exec-new",
                    task_id="TASK-0042",
                    status="completed",
                    success=True,
                ).write_log(logs_dir)

            self.assertTrue(raced)
            self.assertEqual(json.loads(legacy_path.read_text(encoding="utf-8")), concurrent)

    def test_malformed_report_cannot_block_a_new_audit_or_rewrite_other_json(self) -> None:
        with TemporaryDirectory() as tmp:
            logs_dir = Path(tmp) / "executions"
            logs_dir.mkdir()
            notes = logs_dir / "operator-notes.json"
            notes.write_text(
                json.dumps({"status": "failed", "error": LEGACY_SECRET}),
                encoding="utf-8",
            )
            notes.chmod(0o640)
            notes_before = notes.read_bytes()
            notes_mode = stat.S_IMODE(notes.stat().st_mode)

            malformed = logs_dir / "exec-malformed.json"
            malformed.write_text(
                json.dumps({
                    "execution_id": "exec-malformed",
                    "status": [],
                    "error": LEGACY_SECRET,
                }),
                encoding="utf-8",
            )

            new_path = ExecutionReport(
                execution_id="exec-new",
                task_id="TASK-0042",
                status="completed",
                success=True,
            ).write_log(logs_dir)

            self.assertTrue(new_path.exists())
            self.assertEqual(notes.read_bytes(), notes_before)
            self.assertEqual(stat.S_IMODE(notes.stat().st_mode), notes_mode)
            migrated = json.loads(malformed.read_text(encoding="utf-8"))
            self.assertEqual(migrated["status"], "failed")
            self.assertNotIn(LEGACY_SECRET, json.dumps(migrated))


if __name__ == "__main__":
    unittest.main()
