"""Telegram control-plane tests: no sockets, no real bot, no provider calls."""
from __future__ import annotations

import hashlib
import json
import os
import plistlib
import threading
import time
import unittest
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from monday import Monday, MondayConfig
from monday.cli import _cmd_telegram
from telegram_bot.client import (
    HttpTelegramClient,
    TelegramAPIError,
    TelegramRateLimitError,
    split_message,
)
from telegram_bot.config import TelegramConfig
from telegram_bot.runner import (
    TelegramJobWorker,
    TelegramRunner,
    TelegramUpdateRetryError,
)
from telegram_bot.service import (
    TelegramBotService,
    TelegramUpdatePendingError,
    _lifecycle_snapshot,
    _parse,
    _title,
)
from telegram_bot.state import (
    InstanceLock,
    TelegramState,
    TelegramStateError,
    instance_lock_is_held,
    job_id,
    parse_job_id,
)


def _fake_token() -> str:
    return "secret-" + "token"


_ORIGIN_KEYS = frozenset({
    "origin_user_id",
    "origin_chat_id",
    "origin_chat_type",
})


def _update(update_id=10, user_id=7, chat_id=7, text="Build a health endpoint"):
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "from": {"id": user_id, "is_bot": False},
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


def test_launchd_example_has_explicit_tool_path_without_credentials() -> None:
    plist_path = (
        Path(__file__).resolve().parent.parent
        / "deploy"
        / "launchd"
        / "com.mondayos.telegram.plist.example"
    )
    raw = plist_path.read_bytes()
    payload = plistlib.loads(raw)
    environment = payload["EnvironmentVariables"]

    assert set(environment) == {"PATH"}
    paths = environment["PATH"].split(":")
    assert paths == [
        "/Applications/ChatGPT.app/Contents/Resources/codex-cli/"
        "CodexCLI.app/Contents/MacOS",
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    assert raw.count(b"REPLACE_WITH_MONDAYOS_ROOT") == 5
    assert all(marker not in raw.upper() for marker in (b"TOKEN", b"PASSWORD", b"API_KEY"))


class FakeTelegramClient:
    def __init__(self, updates=None, identity=None):
        self.updates = list(updates or [])
        self.sent = []
        self.offsets = []
        self.webhook_deleted = False
        self.identity = dict(identity or {"id": 99, "username": "monday_test_bot"})

    def get_me(self):
        return dict(self.identity)

    def delete_webhook(self, *, drop_pending_updates=False):
        self.webhook_deleted = not drop_pending_updates
        return True

    def get_updates(self, *, offset, timeout):
        self.offsets.append(offset)
        rows, self.updates = self.updates, []
        return rows

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        return [{"message_id": len(self.sent)}]


class FakeMonday:
    def __init__(self):
        self.tasks = {}
        self.task_calls = []
        self.team_calls = 0
        self.team_requests = []
        self.team_runs = []
        self.agent_runs = []
        self.agent_calls = []
        self.build_calls = []
        self.build_executions = 0
        self.delivery_runs = {}
        self.observe_results = {}

    def task(self, action, **kwargs):
        self.task_calls.append((action, kwargs))
        if action == "create":
            task_id = f"TASK-{len(self.tasks) + 1:04d}"
            task = {
                "id": task_id,
                "title": kwargs["title"],
                "objective": kwargs["objective"],
                "context": kwargs.get("context", ""),
                "project": kwargs.get("project", ""),
                "status": "backlog",
                "priority": kwargs.get("priority", "P2"),
            }
            self.tasks[task_id] = task
            return SimpleNamespace(success=True, task_id=task_id, data=task, message="created")
        if action == "list_active":
            return SimpleNamespace(
                success=True,
                data={"tasks": list(self.tasks.values()), "count": len(self.tasks)},
            )
        if action == "get":
            task = self.tasks.get(kwargs.get("task_id"))
            return SimpleNamespace(
                success=task is not None,
                data=task or {},
                message="not found" if task is None else "found",
            )
        raise AssertionError(action)

    def team(self, action, **kwargs):
        if action == "get":
            run = next(
                (
                    row for row in self.team_runs
                    if row["team_run_id"] == kwargs.get("team_run_id")
                ),
                None,
            )
            return SimpleNamespace(
                success=run is not None,
                data=dict(run or {}),
                message="not found" if run is None else "found",
            )
        if action == "interrupt":
            run = next(
                (
                    row for row in self.team_runs
                    if row["team_run_id"] == kwargs.get("team_run_id")
                ),
                None,
            )
            if run is None:
                return SimpleNamespace(success=False, data={}, message="not found")
            if run["status"] == "running":
                run["status"] = "interrupted"
                run["message"] = kwargs.get("reason", "interrupted")
            return SimpleNamespace(success=True, data=dict(run), message=run["message"])
        if action == "history":
            rows = [r for r in self.team_runs if r["task_id"] == kwargs.get("task_id")]
            return SimpleNamespace(success=True, data={"runs": rows, "count": len(rows)})
        if action != "run":
            raise AssertionError(action)
        self.team_calls += 1
        self.team_requests.append(dict(kwargs))
        callback = kwargs.get("progress_callback")
        checkpoint = kwargs.get("checkpoint_callback")
        team_id = kwargs.get("team_run_id") or f"team-{self.team_calls}"
        if checkpoint:
            checkpoint({"event": "team_started", "team_run_id": team_id})
        if callback:
            callback({"event": "team_started", "team_run_id": team_id})
            callback({
                "event": "stage_finished",
                "role": "cpo",
                "verdict": "pass",
                "provider": kwargs.get("provider") or "fake",
            })
        run = {
            "team_run_id": team_id,
            "task_id": kwargs["task_id"],
            "status": "awaiting-approval",
            "success": True,
            "approval_run_id": "run-reviewer-1",
            "message": "All stages passed.",
        }
        self.team_runs.insert(0, run)
        return SimpleNamespace(
            success=True,
            team_run_id=team_id,
            status="awaiting-approval",
            data=run,
        )

    def agent(self, action, **kwargs):
        self.agent_calls.append((action, dict(kwargs)))
        if action == "history":
            limit = int(kwargs.get("limit", 20))
            rows = list(self.agent_runs)
            if limit:
                rows = rows[:max(0, limit)]
            return SimpleNamespace(success=True, data={"runs": rows})
        if action == "review":
            run = next(
                (
                    item for item in self.agent_runs
                    if item.get("run_id") == kwargs.get("run_id")
                ),
                None,
            )
            if run is None:
                return SimpleNamespace(
                    success=False,
                    data={},
                    message=f"No agent run {kwargs.get('run_id')} exists.",
                )
            decision = "approved" if kwargs["approve"] else "rejected"
            approval = run.setdefault("approval", {})
            prior = str(approval.get("decision") or "")
            if prior not in {"approved", "rejected"}:
                approval.update({
                    "required": True,
                    "decision": decision,
                    "by": kwargs.get("by", ""),
                    "note": kwargs.get("note", ""),
                })
            return SimpleNamespace(
                success=True,
                data=dict(run),
                message=f"Run {approval.get('decision', decision)}.",
            )
        raise AssertionError(action)

    def build(self, action, **kwargs):
        self.build_calls.append((action, dict(kwargs)))
        if action == "observe":
            delivery_id = kwargs["delivery_id"]
            queued = self.observe_results.get(delivery_id)
            if isinstance(queued, list) and queued:
                result = dict(queued.pop(0))
            elif isinstance(queued, dict):
                result = dict(queued)
            else:
                run = self.delivery_runs.get(delivery_id, {})
                result = {
                    "delivery_id": delivery_id,
                    "task_id": run.get("task_id", ""),
                    "project_name": kwargs.get("project", ""),
                    "pr_url": run.get("pr_url", ""),
                    "pr_state": "open",
                    "checks_status": "pending",
                    "merge_commit_sha": "",
                }
            success = bool(result.pop("success", True))
            return SimpleNamespace(
                action="observe",
                success=success,
                delivery_id=delivery_id,
                task_id=str(result.get("task_id") or ""),
                project_name=str(result.get("project_name") or ""),
                pr_url=str(result.get("pr_url") or ""),
                message="observed" if success else "unavailable",
                data=result,
            )
        if action != "run":
            raise AssertionError(action)
        delivery_id = kwargs["delivery_id"]
        existing = self.delivery_runs.get(delivery_id)
        if existing is None:
            self.build_executions += 1
            callback = kwargs.get("progress_callback")
            if callback:
                callback({
                    "delivery_id": delivery_id,
                    "status": "running",
                    "phase": "preparing",
                    "attempts": [],
                })
                callback({
                    "delivery_id": delivery_id,
                    "status": "running",
                    "phase": "implementing",
                    "attempts": [],
                })
                callback({
                    "delivery_id": delivery_id,
                    "status": "running",
                    "phase": "validating",
                    "attempts": [],
                })
                callback({
                    "delivery_id": delivery_id,
                    "status": "running",
                    "phase": "reviewing",
                    "attempts": [],
                })
                callback({
                    "delivery_id": delivery_id,
                    "status": "running",
                    "phase": "pushing",
                    "attempts": [{"number": 1, "status": "passed"}],
                })
            existing = {
                "delivery_id": delivery_id,
                "task_id": kwargs["task_id"],
                "status": "pr-open",
                "phase": "completed",
                "success": True,
                "branch": "codex/task-0001-12345678",
                "commit_sha": "a" * 40,
                "pr_url": "https://github.com/example/mondayos/pull/55",
                "changed_files": ["telegram_bot/service.py", "tests/test_telegram.py"],
                "attempts": [{"number": 1, "status": "passed"}],
                "message": "Pull request opened.",
            }
            self.delivery_runs[delivery_id] = existing
        return SimpleNamespace(
            action="run",
            success=existing["success"],
            message=existing["message"],
            delivery_id=delivery_id,
            task_id=existing["task_id"],
            status=existing["status"],
            phase=existing["phase"],
            branch=existing["branch"],
            commit_sha=existing["commit_sha"],
            pr_url=existing["pr_url"],
            changed_files=list(existing["changed_files"]),
            attempts=list(existing["attempts"]),
            data=dict(existing),
        )

    def status(self):
        return SimpleNamespace(healthy=True, version="1.0.0")


class TelegramFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            provider="fake",
        )
        self.client = FakeTelegramClient()
        self.state = TelegramState(self.config.state_path)
        self.monday = FakeMonday()
        self.service = TelegramBotService(
            self.monday, self.client, self.state, self.config,
        )

    def tearDown(self):
        self.tmp.cleanup()


class TestTelegramConfig(unittest.TestCase):
    def test_requires_token(self):
        with TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, "TOKEN"):
            TelegramConfig.from_env(Path(tmp), {
                "MONDAYOS_TELEGRAM_ALLOWED_USER_IDS": "7",
            })

    def test_requires_nonempty_allowlist(self):
        with TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, "ALLOWED_USER"):
            TelegramConfig.from_env(Path(tmp), {"TELEGRAM_BOT_TOKEN": "token"})

    def test_parses_signed_ids_and_hides_token(self):
        with TemporaryDirectory() as tmp:
            cfg = TelegramConfig.from_env(Path(tmp), {
                "TELEGRAM_BOT_TOKEN": "super-secret",
                "MONDAYOS_TELEGRAM_ALLOWED_USER_IDS": "7, 8",
                "MONDAYOS_TELEGRAM_ALLOWED_CHAT_IDS": "-100123",
            })
        self.assertEqual(cfg.allowed_user_ids, frozenset({7, 8}))
        self.assertEqual(cfg.allowed_chat_ids, frozenset({-100123}))
        self.assertNotIn("super-secret", repr(cfg))
        self.assertNotIn("super-secret", str(cfg.safe_summary()))
        self.assertFalse(cfg.live_build)

    def test_live_build_is_explicit_and_strict(self):
        with TemporaryDirectory() as tmp:
            cfg = TelegramConfig.from_env(Path(tmp), {
                "TELEGRAM_BOT_TOKEN": "token",
                "MONDAYOS_TELEGRAM_ALLOWED_USER_IDS": "7",
                "MONDAYOS_TELEGRAM_LIVE_BUILD": "true",
            })
            self.assertTrue(cfg.live_build)
            self.assertTrue(cfg.safe_summary()["live_build"])

            with self.assertRaisesRegex(ValueError, "must be true or false"):
                TelegramConfig.from_env(Path(tmp), {
                    "TELEGRAM_BOT_TOKEN": "token",
                    "MONDAYOS_TELEGRAM_ALLOWED_USER_IDS": "7",
                    "MONDAYOS_TELEGRAM_LIVE_BUILD": "tru",
                })

    def test_rejects_non_numeric_allowlist_and_invalid_timeout(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "non-numeric"):
                TelegramConfig.from_env(Path(tmp), {
                    "TELEGRAM_BOT_TOKEN": "token",
                    "MONDAYOS_TELEGRAM_ALLOWED_USER_IDS": "not-a-number",
                })
            with self.assertRaisesRegex(ValueError, "between 1 and 50"):
                TelegramConfig.from_env(Path(tmp), {
                    "TELEGRAM_BOT_TOKEN": "token",
                    "MONDAYOS_TELEGRAM_ALLOWED_USER_IDS": "7",
                    "MONDAYOS_TELEGRAM_POLL_TIMEOUT": "0",
                })

    def test_rejects_nonpositive_user_and_zero_chat_ids(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "positive"):
                TelegramConfig.from_env(Path(tmp), {
                    "TELEGRAM_BOT_TOKEN": "token",
                    "MONDAYOS_TELEGRAM_ALLOWED_USER_IDS": "0",
                })
            with self.assertRaisesRegex(ValueError, "cannot contain zero"):
                TelegramConfig.from_env(Path(tmp), {
                    "TELEGRAM_BOT_TOKEN": "token",
                    "MONDAYOS_TELEGRAM_ALLOWED_USER_IDS": "7",
                    "MONDAYOS_TELEGRAM_ALLOWED_CHAT_IDS": "0",
                })


class TestTelegramParsing(unittest.TestCase):
    def test_command_for_named_bot(self):
        self.assertEqual(
            _parse("/task@MondayBot Build this", "mondaybot"),
            ("build", "Build this"),
        )

    def test_command_for_other_named_bot_is_ignored(self):
        self.assertIsNone(_parse("/task@OtherBot Build this", "mondaybot"))

    def test_title_is_bounded(self):
        self.assertLessEqual(len(_title("x" * 200)), 80)

    def test_long_messages_split_without_loss(self):
        original = "a" * 4100 + "\n" + "b" * 4100
        chunks = split_message(original)
        self.assertTrue(all(0 < len(chunk) <= 4096 for chunk in chunks))
        self.assertEqual("".join(chunks), original)

    def test_lifecycle_snapshot_requires_exact_complete_public_identity(self):
        delivery_id = "delivery-telegram-abc123"
        task_id = "TASK-0001"
        project_name = "weatherbot"
        pr_url = "https://github.com/example/mondayos/pull/55"

        def snapshot(**overrides):
            fields = {
                "success": True,
                "delivery_id": delivery_id,
                "task_id": task_id,
                "project_name": project_name,
                "pr_url": pr_url,
                "pr_state": "open",
                "checks_status": "pending",
                "merge_commit_sha": "",
                "data": {},
            }
            fields.update(overrides)
            return _lifecycle_snapshot(
                SimpleNamespace(**fields),
                delivery_id=delivery_id,
                task_id=task_id,
                project_name=project_name,
                pr_url=pr_url,
            )

        self.assertEqual(snapshot()["pr_state"], "open")
        for malformed in (
            {"delivery_id": ""},
            {"task_id": ""},
            {"project_name": ""},
            {"pr_url": ""},
            {"checks_status": "unknown"},
            {"pr_state": "merged", "merge_commit_sha": ""},
            {"pr_state": "open", "merge_commit_sha": "b" * 40},
            {"pr_state": "merged", "merge_commit_sha": "b" * 7},
        ):
            with self.subTest(malformed=malformed):
                self.assertEqual(snapshot(**malformed), {})

        merged = snapshot(pr_state="merged", merge_commit_sha="B" * 40)
        self.assertEqual(merged["merge_commit_sha"], "b" * 40)


class TestTelegramService(TelegramFixture):
    def test_command_for_other_bot_is_ignored(self):
        runner = TelegramRunner(self.client, self.service, self.state)
        runner.start()

        self.service.handle_update(_update(text="/build@OtherBot Do not build this"))

        self.assertFalse(self.monday.tasks)
        self.assertFalse(self.client.sent)

    def test_command_for_other_bot_is_ignored_in_allowlisted_group(self):
        group_config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            allowed_chat_ids=frozenset({-100}),
            project_root=self.root,
            provider="fake",
        )
        service = TelegramBotService(
            self.monday, self.client, self.state, group_config,
        )
        runner = TelegramRunner(self.client, service, self.state)
        runner.start()
        update = _update(
            chat_id=-100,
            text="/build@OtherBot Do not build this",
        )
        update["message"]["chat"]["type"] = "group"

        service.handle_update(update)

        self.assertFalse(self.monday.tasks)
        self.assertFalse(self.client.sent)

    def test_command_for_own_bot_is_accepted_case_insensitively(self):
        runner = TelegramRunner(self.client, self.service, self.state)
        runner.start()

        self.service.handle_update(
            _update(text="/build@MONDAY_TEST_BOT Build the addressed request")
        )

        self.assertEqual(len(self.monday.tasks), 1)
        self.assertEqual(self.monday.team_calls, 1)

    def test_unauthorized_user_creates_nothing_and_gets_no_details(self):
        self.service.handle_update(_update(user_id=999, chat_id=999))
        self.assertEqual(self.monday.tasks, {})
        self.assertEqual(self.client.sent, [])

    def test_missing_boolean_and_zero_user_ids_are_rejected(self):
        for user_id in (None, True, 0):
            update = _update(user_id=7)
            if user_id is None:
                update["message"]["from"].pop("id")
            else:
                update["message"]["from"]["id"] = user_id
            self.service.handle_update(update)
        self.assertFalse(self.monday.tasks)
        self.assertFalse(self.client.sent)

    def test_plain_text_creates_task_runs_team_and_reports_progress(self):
        self.service.handle_update(_update())
        self.assertEqual(len(self.monday.tasks), 1)
        self.assertEqual(self.monday.team_calls, 1)
        create = next(call for call in self.monday.task_calls if call[0] == "create")
        self.assertEqual(create[1]["created_by"], "human:telegram:7")
        self.assertIn("telegram:update:10", create[1]["context"])
        combined = "\n".join(text for _, text in self.client.sent)
        self.assertIn("Created TASK-0001", combined)
        self.assertIn("Product strategy: pass via fake", combined)
        self.assertIn("/approve run-reviewer-1", combined)

    def test_disabled_live_flag_keeps_build_on_team_workflow(self):
        self.service.handle_update(_update(text="/build Keep the safe workflow"))

        self.assertEqual(self.monday.team_calls, 1)
        self.assertEqual(self.monday.build_executions, 0)
        self.assertEqual(self.state.job(10)["workflow"], "team-review")

    def test_private_build_runs_live_delivery_and_persists_progress(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            provider="fake",
            live_build=True,
        )
        service = TelegramBotService(self.monday, self.client, self.state, config)
        runner = TelegramRunner(self.client, service, self.state)
        runner.start()

        self.client.updates = [_update(text="/build Ship a health endpoint")]
        self.assertEqual(runner.run_once(), 1)

        self.assertEqual(len(self.monday.tasks), 1)
        self.assertEqual(self.monday.team_calls, 0)
        self.assertEqual(self.monday.build_executions, 1)
        action, request = self.monday.build_calls[-1]
        self.assertEqual(action, "run")
        self.assertEqual(request["task_id"], "TASK-0001")
        self.assertRegex(request["delivery_id"], r"^delivery-telegram-[0-9a-f]{20}$")
        job = self.state.completed_job(10)
        self.assertEqual(job["workflow"], "live-build")
        self.assertEqual(job["delivery_id"], request["delivery_id"])
        self.assertEqual(job["delivery_status"], "pr-open")
        self.assertEqual(job["delivery_progress"]["phase"], "pushing")
        self.assertTrue(job["delivery_terminal"])
        self.assertTrue(job["delivery_final_sent"])
        combined = "\n".join(text for _, text in self.client.sent)
        self.assertIn("isolated build workspace", combined)
        self.assertIn("Monday is validating the build", combined)
        self.assertIn("ChatGPT is reviewing the build", combined)
        self.assertIn("passed ChatGPT review", combined)
        self.assertIn("https://github.com/example/mondayos/pull/55", combined)

    def test_live_build_progress_renews_fixed_runtime_operations(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            provider="fake",
            live_build=True,
        )
        operations = []
        service = TelegramBotService(self.monday, self.client, self.state, config)
        service.bind_bot_identity(self.client.get_me())
        service.bind_runtime_progress(operations.append)

        service.handle_update(
            _update(update_id=41, text="/build Exercise every live phase")
        )

        self.assertEqual(
            operations,
            ["preparing", "implementing", "validating", "reviewing", "pushing"],
        )

    def test_team_runtime_progress_precedes_a_failed_telegram_update(self):
        operations = []

        class FailingProgressClient(FakeTelegramClient):
            def send_message(self, chat_id, text):
                raise TelegramAPIError("temporary outage", retryable=True)

        service = TelegramBotService(
            self.monday,
            FailingProgressClient(),
            self.state,
            self.config,
        )
        service.bind_runtime_progress(operations.append)

        with self.assertRaises(TelegramAPIError):
            service._progress(7, {"event": "team_started"})

        self.assertEqual(operations, ["team-stage"])

    def test_team_progress_reduces_hostile_provider_metadata(self):
        secret = "PRIVATEPROMPT123456"

        self.service._progress(7, {
            "event": "stage_finished",
            "role": secret,
            "verdict": secret,
            "provider": "sk-proj-LEAKME123456",
        })

        message = self.client.sent[-1][1]
        self.assertEqual(message, "Agent: finished.")
        self.assertNotIn(secret, message)
        self.assertNotIn("LEAKME", message)

    def test_private_build_can_explicitly_target_a_registered_project(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            provider="fake",
            live_build=True,
        )
        service = TelegramBotService(self.monday, self.client, self.state, config)

        service.handle_update(
            _update(text="/build weatherbot :: Ship a forecast endpoint")
        )

        create = next(call for call in self.monday.task_calls if call[0] == "create")
        self.assertEqual(create[1]["project"], "weatherbot")
        self.assertEqual(create[1]["objective"], "Ship a forecast endpoint")
        self.assertEqual(create[1]["title"], "Ship a forecast endpoint")
        self.assertEqual(self.monday.build_calls[-1][1]["project"], "weatherbot")
        self.assertEqual(self.state.job(10)["project_name"], "weatherbot")

    def test_project_build_delimiter_allows_optional_surrounding_spaces(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            provider="fake",
            live_build=True,
        )
        service = TelegramBotService(self.monday, self.client, self.state, config)
        cases = [
            (20, "/build weatherbot:: Ship it", "weatherbot"),
            (21, "/build weatherbot ::Ship it", "weatherbot"),
            (22, "/build Weather Portal::Ship it", "Weather Portal"),
        ]

        for update_id, text, expected_project in cases:
            with self.subTest(text=text):
                service.handle_update(_update(update_id=update_id, text=text))
                create = [call for call in self.monday.task_calls if call[0] == "create"][-1]
                self.assertEqual(create[1]["project"], expected_project)
                self.assertEqual(create[1]["objective"], "Ship it")
                self.assertEqual(
                    self.monday.build_calls[-1][1]["project"],
                    expected_project,
                )

        self.assertNotIn("", [task["project"] for task in self.monday.tasks.values()])

    def test_project_build_requires_both_selector_and_request(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )
        service = TelegramBotService(self.monday, self.client, self.state, config)

        service.handle_update(_update(text="/build weatherbot ::"))

        self.assertFalse(self.monday.tasks)
        self.assertEqual(self.monday.build_executions, 0)
        self.assertIn("/build PROJECT :: REQUEST", self.client.sent[-1][1])

    def test_project_build_with_empty_selector_never_falls_back_to_controller(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )
        service = TelegramBotService(self.monday, self.client, self.state, config)

        service.handle_update(_update(text="/build :: Ship it"))

        self.assertFalse(self.monday.tasks)
        self.assertEqual(self.monday.build_executions, 0)
        self.assertIn("/build PROJECT :: REQUEST", self.client.sent[-1][1])

    def test_nonterminal_live_delivery_stays_retryable_until_reconciled(self):
        created = self.monday.task(
            "create",
            title="Existing",
            objective="Ship it",
            priority="P2",
        )
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )
        service = TelegramBotService(self.monday, self.client, self.state, config)
        service.bind_bot_identity(self.client.get_me())
        delivery_id = service._delivery_id(20)
        self.monday.delivery_runs[delivery_id] = {
            "delivery_id": delivery_id,
            "task_id": created.task_id,
            "status": "running",
            "phase": "validating",
            "success": False,
            "branch": "codex/task-0001-12345678",
            "commit_sha": "",
            "pr_url": "",
            "changed_files": ["app.py"],
            "attempts": [{"number": 1, "status": "validating"}],
            "message": "Validation is still running.",
        }
        update = _update(update_id=20, text=f"/deliver {created.task_id}")

        normalized = service.normalize_update(update)
        self.assertIsNotNone(normalized)
        self.state.enqueue(20, normalized)
        self.state.complete_acceptance_receipt(20, delivered=True)
        worker = TelegramJobWorker(
            service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )
        with self.assertRaises(TelegramUpdatePendingError):
            worker.run_once()

        pending = self.state.job(20)
        self.assertFalse(pending["delivery_terminal"])
        self.assertFalse(pending["delivery_final_sent"])
        self.assertIn("validating", self.client.sent[-1][1].lower())

        durable = self.monday.delivery_runs[delivery_id]
        durable.update({
            "status": "interrupted",
            "phase": "interrupted",
            "message": "Start a new build request with a new delivery identity.",
        })
        self.assertTrue(worker.run_once())
        self.assertTrue(worker.run_delivery_notice_once())

        settled = self.state.completed_job(20)
        self.assertTrue(settled["delivery_terminal"])
        self.assertTrue(settled["delivery_final_sent"])
        self.assertEqual(len(self.monday.build_calls), 2)
        self.assertIn("live build: interrupted", self.client.sent[-1][1])

    def test_private_deliver_runs_existing_task_without_creating_another(self):
        created = self.monday.task(
            "create", title="Existing", objective="Ship it", priority="P2",
        )
        initial_task_calls = len(self.monday.task_calls)
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )
        service = TelegramBotService(self.monday, self.client, self.state, config)

        service.handle_update(
            _update(update_id=18, text=f"/deliver {created.task_id}")
        )

        self.assertEqual(len(self.monday.tasks), 1)
        self.assertEqual(self.monday.build_executions, 1)
        self.assertEqual(self.monday.build_calls[-1][1]["task_id"], created.task_id)
        self.assertFalse(any(
            action == "create"
            for action, _kwargs in self.monday.task_calls[initial_task_calls:]
        ))
        self.assertNotIn("project", self.monday.build_calls[-1][1])

    def test_private_deliver_routes_a_project_bound_task_to_its_target(self):
        created = self.monday.task(
            "create",
            title="Existing",
            objective="Ship it",
            priority="P2",
            project="weatherbot",
        )
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )
        service = TelegramBotService(self.monday, self.client, self.state, config)

        service.handle_update(
            _update(update_id=21, text=f"/deliver {created.task_id}")
        )

        self.assertEqual(self.monday.build_calls[-1][1]["project"], "weatherbot")
        self.assertEqual(self.state.job(21)["project_name"], "weatherbot")

    def test_private_deliver_keeps_explicit_mondayos_task_on_self_target(self):
        created = self.monday.task(
            "create",
            title="Existing",
            objective="Ship it",
            priority="P2",
            project="mondayos",
        )
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )
        service = TelegramBotService(self.monday, self.client, self.state, config)

        service.handle_update(
            _update(update_id=23, text=f"/deliver {created.task_id}")
        )

        self.assertNotIn("project", self.monday.build_calls[-1][1])
        self.assertEqual(self.state.job(23)["project_name"], "")

    def test_deliver_refuses_to_rebind_a_saved_update_to_another_project(self):
        created = self.monday.task(
            "create",
            title="Existing",
            objective="Ship it",
            priority="P2",
            project="weatherbot",
        )
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )
        service = TelegramBotService(self.monday, self.client, self.state, config)
        self.state.set_job(
            22,
            task_id=created.task_id,
            workflow="live-build",
            project_name="another-project",
        )

        with self.assertRaisesRegex(RuntimeError, "another project"):
            service.handle_update(
                _update(update_id=22, text=f"/deliver {created.task_id}")
            )

        self.assertEqual(self.monday.build_executions, 0)

    def test_live_build_duplicate_update_reuses_task_and_delivery(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )
        update = _update(update_id=19, text="/build Ship this exactly once")
        service = TelegramBotService(self.monday, self.client, self.state, config)
        identity = self.client.get_me()
        service.bind_bot_identity(identity)

        service.handle_update(update)
        first_id = self.state.job(19)["delivery_id"]
        restarted = TelegramBotService(
            self.monday,
            self.client,
            TelegramState(config.state_path),
            config,
        )
        restarted.bind_bot_identity(identity)
        restarted.handle_update(update)

        self.assertEqual(len(self.monday.tasks), 1)
        self.assertEqual(self.monday.build_executions, 1)
        self.assertEqual(len(self.monday.build_calls), 1)
        self.assertEqual(TelegramState(config.state_path).job(19)["delivery_id"], first_id)

    def test_live_mode_never_runs_live_builds_from_groups(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            allowed_chat_ids=frozenset({-100}),
            project_root=self.root,
            provider="fake",
            live_build=True,
        )
        service = TelegramBotService(self.monday, self.client, self.state, config)
        update = _update(chat_id=-100, text="/build Keep groups review-only")
        update["message"]["chat"]["type"] = "group"

        service.handle_update(update)

        self.assertEqual(self.monday.team_calls, 1)
        self.assertEqual(self.monday.build_executions, 0)

    def test_group_deliver_is_silently_ignored_even_when_live_is_enabled(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            allowed_chat_ids=frozenset({-100}),
            project_root=self.root,
            live_build=True,
        )
        service = TelegramBotService(self.monday, self.client, self.state, config)
        update = _update(chat_id=-100, text="/deliver TASK-0001")
        update["message"]["chat"]["type"] = "group"

        service.handle_update(update)

        self.assertEqual(self.monday.build_executions, 0)
        self.assertFalse(self.client.sent)

    def test_group_queue_full_notice_points_to_private_controls(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            allowed_chat_ids=frozenset({-100}),
            project_root=self.root,
            provider="fake",
        )
        service = TelegramBotService(
            self.monday, self.client, self.state, config,
        )
        service.announce_queue_full_notice({
            "user_id": 7,
            "chat_id": -100,
            "chat_type": "group",
        })

        self.assertEqual(self.client.sent[-1][0], -100)
        self.assertIn("request was not saved", self.client.sent[-1][1])
        self.assertIn("Open Monday's private chat", self.client.sent[-1][1])

    def test_delivery_notice_rejects_unknown_semantic_display_phases(self):
        base = {
            "notice_kind": "terminal-result",
            "user_id": 7,
            "chat_id": 7,
            "chat_type": "private",
            "task_id": "TASK-0001",
            "delivery_id": "delivery-test-notice",
            "follow_up_phase": "",
            "retry_unavailable_phase": "",
            "result": {
                "delivery_id": "delivery-test-notice",
                "success": False,
                "status": "failed",
                "phase": "failed",
                "message": "must not be rendered",
            },
        }

        for field in ("follow_up_phase", "retry_unavailable_phase"):
            for malformed in ("unknown-phase", []):
                with self.subTest(field=field, malformed=malformed):
                    notice = dict(base)
                    notice[field] = malformed
                    with self.assertRaisesRegex(ValueError, "Invalid Telegram"):
                        self.service.deliver_delivery_notice(notice)

        self.assertEqual(self.client.sent, [])

    def test_acceptance_receipt_is_fixed_request_free_and_reauthorized(self):
        group_config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            allowed_chat_ids=frozenset({-100}),
            project_root=self.root,
            provider="fake",
        )
        service = TelegramBotService(
            self.monday,
            self.client,
            self.state,
            group_config,
        )
        notice = {
            "job_id": "JOB-123",
            "user_id": 7,
            "chat_id": -100,
            "chat_type": "group",
        }

        self.assertTrue(service.announce_acceptance_receipt(notice))
        text = self.client.sent[-1][1]
        self.assertEqual(self.client.sent[-1][0], -100)
        self.assertEqual(
            text,
            "Request saved as JOB-123. Open Monday's private chat to view or "
            "cancel your queued requests.",
        )
        self.assertNotIn("position", text.lower())

        revoked_group = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            provider="fake",
        )
        revoked_service = TelegramBotService(
            self.monday,
            self.client,
            self.state,
            revoked_group,
        )
        sent_before = len(self.client.sent)
        self.assertFalse(revoked_service.announce_acceptance_receipt(notice))
        self.assertEqual(len(self.client.sent), sent_before)

    def test_deliver_reports_disabled_without_touching_task_or_build(self):
        self.service.handle_update(_update(text="/deliver TASK-0001"))

        self.assertIn("Live builds are disabled", self.client.sent[-1][1])
        self.assertFalse(self.monday.task_calls)
        self.assertEqual(self.monday.build_executions, 0)

    def test_live_flag_makes_private_plain_text_a_live_build_request(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            provider="fake",
            live_build=True,
        )
        service = TelegramBotService(self.monday, self.client, self.state, config)

        service.handle_update(_update(text="Build this from an ordinary message"))

        self.assertEqual(self.monday.team_calls, 0)
        self.assertEqual(self.monday.build_executions, 1)
        self.assertEqual(self.state.job(10)["workflow"], "live-build")

    def test_retry_same_update_does_not_duplicate_task_or_team_run(self):
        update = _update()
        self.service.handle_update(update)
        restarted = TelegramBotService(
            self.monday,
            self.client,
            TelegramState(self.config.state_path),
            self.config,
        )
        restarted.handle_update(update)
        self.assertEqual(len(self.monday.tasks), 1)
        self.assertEqual(self.monday.team_calls, 1)

    def test_new_run_update_starts_new_team_but_its_retry_does_not(self):
        self.service.handle_update(_update())
        command = _update(update_id=11, text="/run TASK-0001")

        self.service.handle_update(command)
        restarted = TelegramBotService(
            self.monday,
            self.client,
            TelegramState(self.config.state_path),
            self.config,
        )
        restarted.handle_update(command)

        self.assertEqual(self.monday.team_calls, 2)

    def test_interrupted_team_is_marked_and_replaced_once(self):
        created = self.monday.task(
            "create", title="X", objective="Do X", priority="P2",
        )
        old = {
            "team_run_id": "team-old",
            "task_id": created.task_id,
            "status": "running",
            "success": False,
            "approval_run_id": "",
            "message": "",
        }
        self.monday.team_runs.append(old)
        self.state.set_job(
            22,
            task_id=created.task_id,
            chat_id=7,
            team_run_id="team-old",
        )

        self.service.handle_update(_update(update_id=22, text=f"/run {created.task_id}"))

        self.assertEqual(old["status"], "interrupted")
        self.assertEqual(self.monday.team_calls, 1)
        job = self.state.job(22)
        self.assertEqual(job["interrupted_team_run_id"], "team-old")
        self.assertTrue(job["team_run_id"].startswith("team-telegram-"))
        self.assertFalse(job["team_reserved"])

    def test_absent_reserved_team_retries_with_exact_id_and_clears_flags(self):
        created = self.monday.task(
            "create", title="X", objective="Do X", priority="P2",
        )
        reserved = "team-telegram-reserved123"
        self.state.set_job(
            23,
            task_id=created.task_id,
            chat_id=7,
            team_run_id=reserved,
            team_reserved=True,
            recovery_pending=True,
        )

        self.service.handle_update(_update(update_id=23, text=f"/run {created.task_id}"))

        self.assertEqual(self.monday.team_calls, 1)
        self.assertEqual(self.monday.team_requests[-1]["team_run_id"], reserved)
        self.assertEqual(self.monday.team_runs[0]["team_run_id"], reserved)
        job = self.state.job(23)
        self.assertEqual(job["team_run_id"], reserved)
        self.assertFalse(job["team_reserved"])
        self.assertFalse(job["recovery_pending"])

    def test_recovery_flag_reuses_existing_awaiting_team(self):
        created = self.monday.task(
            "create", title="X", objective="Do X", priority="P2",
        )
        existing = {
            "team_run_id": "team-telegram-awaiting123",
            "task_id": created.task_id,
            "status": "awaiting-approval",
            "success": True,
            "approval_run_id": "run-reviewer-existing",
            "message": "All stages passed.",
        }
        self.monday.team_runs.append(existing)
        self.state.set_job(
            24,
            task_id=created.task_id,
            chat_id=7,
            team_run_id=existing["team_run_id"],
            team_reserved=True,
            recovery_pending=True,
        )

        self.service.handle_update(_update(update_id=24, text=f"/run {created.task_id}"))

        self.assertEqual(self.monday.team_calls, 0)
        self.assertEqual(len(self.monday.team_runs), 1)
        job = self.state.job(24)
        self.assertEqual(job["team_run_id"], existing["team_run_id"])
        self.assertFalse(job["team_reserved"])
        self.assertFalse(job["recovery_pending"])
        self.assertIn("awaiting-approval", self.client.sent[-1][1])

    def test_existing_interrupted_team_starts_one_replacement(self):
        created = self.monday.task(
            "create", title="X", objective="Do X", priority="P2",
        )
        interrupted = {
            "team_run_id": "team-telegram-interrupted123",
            "task_id": created.task_id,
            "status": "interrupted",
            "success": False,
            "approval_run_id": "",
            "message": "The startup checkpoint failed.",
        }
        self.monday.team_runs.append(interrupted)
        self.state.set_job(
            26,
            task_id=created.task_id,
            chat_id=7,
            team_run_id=interrupted["team_run_id"],
            team_reserved=True,
            recovery_pending=True,
        )

        self.service.handle_update(_update(update_id=26, text=f"/run {created.task_id}"))

        self.assertEqual(self.monday.team_calls, 1)
        self.assertEqual(len(self.monday.team_runs), 2)
        job = self.state.job(26)
        self.assertEqual(
            job["interrupted_team_run_id"], interrupted["team_run_id"],
        )
        self.assertNotEqual(job["team_run_id"], interrupted["team_run_id"])
        self.assertFalse(job["team_reserved"])
        self.assertFalse(job["recovery_pending"])

    def test_approval_lookup_finds_pending_run_older_than_500(self):
        newer = [
            {
                "run_id": f"run-new-{index}",
                "approval": {"required": True, "decision": "pending"},
            }
            for index in range(501)
        ]
        target = {
            "run_id": "run-old-pending",
            "approval": {"required": True, "decision": "pending"},
        }
        self.monday.agent_runs = [*newer, target]

        self.service.handle_update(
            _update(update_id=25, text="/approve run-old-pending")
        )

        self.assertEqual(target["approval"]["decision"], "approved")
        history_calls = [
            kwargs for action, kwargs in self.monday.agent_calls
            if action == "history"
        ]
        self.assertEqual(history_calls[-1]["limit"], 0)

    def test_voice_is_acknowledged_without_creating_task(self):
        update = _update(text="")
        update["message"].pop("text")
        update["message"]["voice"] = {"file_id": "v1", "duration": 3}
        self.service.handle_update(update)
        self.assertFalse(self.monday.tasks)
        self.assertIn("voice note", self.client.sent[0][1])

    def test_group_chatter_is_ignored_even_from_allowed_user(self):
        update = _update(chat_id=-100)
        update["message"]["chat"]["type"] = "group"
        self.service.handle_update(update)
        self.assertFalse(self.monday.tasks)

    def test_help_tasks_status_and_unknown_command(self):
        self.service.handle_update(_update(text="/help"))
        self.service.handle_update(_update(update_id=11, text="/tasks"))
        self.service.handle_update(_update(update_id=12, text="/status"))
        self.service.handle_update(_update(update_id=13, text="/does-not-exist"))
        combined = "\n".join(text for _, text in self.client.sent)
        self.assertIn("/build REQUEST", combined)
        self.assertIn("no active", combined.lower())
        self.assertIn("is healthy", combined)
        self.assertIn("Unknown command", combined)

    def test_group_help_only_advertises_group_commands(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            allowed_chat_ids=frozenset({-100}),
            project_root=self.root,
            provider="fake",
        )
        service = TelegramBotService(
            self.monday,
            self.client,
            self.state,
            config,
        )
        update = _update(update_id=11, chat_id=-100, text="/help")
        update["message"]["chat"]["type"] = "group"

        service.handle_update(update)

        help_text = self.client.sent[-1][1]
        self.assertIn("/build REQUEST", help_text)
        self.assertIn("/run TASK-ID", help_text)
        self.assertIn("/status [TASK-ID]", help_text)
        self.assertNotIn("/queue", help_text)
        self.assertNotIn("/cancel", help_text)
        self.assertNotIn("/tasks", help_text)
        self.assertNotIn("/approve", help_text)
        self.assertNotIn("/reject", help_text)

    def test_run_requires_task_id(self):
        self.service.handle_update(_update(text="/run no"))
        self.assertIn("Use /run TASK-0001", self.client.sent[-1][1])

    def test_status_rejects_path_traversal_before_task_lookup(self):
        self.service.handle_update(_update(text="/status ../active/TASK-0001"))

        self.assertIn("Use /status TASK-0001", self.client.sent[-1][1])
        self.assertFalse(any(action == "get" for action, _ in self.monday.task_calls))

    def test_run_rejects_unbounded_task_id_before_task_lookup(self):
        self.service.handle_update(_update(text="/run TASK-" + "1" * 1000))

        self.assertIn("Use /run TASK-0001", self.client.sent[-1][1])
        self.assertEqual(self.monday.team_calls, 0)


class TestTelegramStateAndRunner(TelegramFixture):
    def _complete_acceptance_receipt(self, update_id: int) -> None:
        notice = self.state.acceptance_receipt_notice(update_id)
        self.assertEqual(notice.get("job_id"), f"JOB-{update_id}")
        self.state.complete_acceptance_receipt(update_id, delivered=True)

    def _seed_terminal_live_build_checkpoint(
        self,
        update_id: int,
        *,
        failure_class: str = "transient",
        pushed: bool = False,
        commit_sha: str = "",
        publication_uncertain: bool = False,
        retry_expires_at: float = 86_500.0,
    ) -> tuple[TelegramConfig, str]:
        """Simulate a crash after saving a terminal result but before recovery."""
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )
        identity = self.client.get_me()
        self.state.bind_bot_identity(identity["id"], identity["username"])
        service = TelegramBotService(self.monday, self.client, self.state, config)
        service.bind_bot_identity(identity)
        update = _update(
            update_id=update_id,
            text="/build Recover the saved transient result",
        )
        normalized = service.normalize_update(update)
        self.assertIsNotNone(normalized)
        self.state.enqueue(update_id, normalized)
        self._complete_acceptance_receipt(update_id)
        claimed = self.state.claim_next()
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed[0], update_id)

        delivery_id = service._delivery_id(update_id)
        self.state.set_job(
            update_id,
            task_id="TASK-0001",
            chat_id=7,
            workflow="live-build",
            project_name="",
            live_started_announced=True,
            delivery_id=delivery_id,
            delivery_reserved=False,
            delivery_terminal=True,
            delivery_final_sent=False,
            delivery_status="failed",
            delivery_phase="failed",
            delivery_result={
                "delivery_id": delivery_id,
                "success": False,
                "status": "failed",
                "phase": "failed",
                "branch": "codex/reconcile-existing-publication" if pushed else "",
                "commit_sha": commit_sha,
                "pr_url": "",
                "publication_uncertain": publication_uncertain,
                "pushed": pushed,
                "changed_files": [],
                "attempts": 1,
                "failure_code": "provider-timeout",
                "failure_class": failure_class,
                "retry_after_seconds": 0,
                "message": "",
            },
            delivery_retry_expires_at=retry_expires_at,
        )
        return config, delivery_id

    def _seed_delivery_retry(
        self,
        update_id: int,
        *,
        now: float = 100.0,
        delay_seconds: float = 60.0,
    ) -> str:
        root_delivery_id = f"delivery-test-{update_id}"
        self.state.enqueue(update_id, _update(update_id=update_id, text="/help"))
        self.state.set_job(
            update_id,
            task_id="TASK-0001",
            chat_id=7,
            workflow="live-build",
            project_name="",
            delivery_id=root_delivery_id,
            delivery_terminal=True,
            delivery_final_sent=False,
            delivery_result={
                "delivery_id": root_delivery_id,
                "success": False,
                "status": "failed",
                "phase": "failed",
                "branch": "",
                "commit_sha": "",
                "pr_url": "",
                "pushed": False,
                "changed_files": [],
                "attempts": 1,
                "failure_code": "provider-timeout",
                "failure_class": "transient",
                "retry_after_seconds": 0,
                "message": "",
            },
        )
        self.state.schedule_delivery_retry(
            update_id,
            {
                "kind": "delivery-retry",
                "root_delivery_id": root_delivery_id,
                "delivery_id": f"{root_delivery_id}-r2",
                "task_id": "TASK-0001",
                "project_name": "",
                "chat_id": 7,
                "attempt": 2,
                "max_attempts": 6,
                "failure_code": "provider-timeout",
            },
            delay_seconds=delay_seconds,
            now=now,
        )
        return root_delivery_id

    def test_retry_build_progress_renews_without_recreating_acknowledged_job(self):
        operations = []
        root_delivery_id = self._seed_delivery_retry(42, now=100.0)
        self.state.acknowledge(42)
        service = TelegramBotService(
            self.monday,
            self.client,
            self.state,
            self.config,
        )
        service.bind_runtime_progress(operations.append)
        worker = TelegramJobWorker(
            service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )

        with (
            patch("telegram_bot.state.time.time", return_value=161.0),
            patch("telegram_bot.service.time.time", return_value=161.0),
        ):
            self.assertTrue(worker.run_delivery_retry_once())

        self.assertEqual(
            operations,
            ["preparing", "implementing", "validating", "reviewing", "pushing"],
        )
        self.assertEqual(self.state.job(42), {})
        self.assertEqual(
            self.monday.build_calls[-1][1]["delivery_id"],
            f"{root_delivery_id}-r2",
        )

    def _stage_terminal_notice(
        self,
        update_id: int,
        *,
        retry_unavailable_phase: str = "",
        follow_up_phase: str = "",
    ) -> tuple[TelegramConfig, str]:
        config, delivery_id = self._seed_terminal_live_build_checkpoint(
            update_id,
            failure_class="permanent",
        )
        fields: dict[str, object] = {}
        if retry_unavailable_phase:
            fields["delivery_retry"] = {
                "phase": retry_unavailable_phase,
                "root_delivery_id": delivery_id,
                "delivery_id": delivery_id,
                "attempt": 1,
                "max_attempts": 6,
                "failure_code": "provider-timeout",
            }
        if follow_up_phase:
            fields["follow_up"] = {
                "kind": "pr-lifecycle",
                "phase": follow_up_phase,
                "delivery_id": delivery_id,
                "task_id": "TASK-0001",
                "project_name": "",
                "chat_id": 7,
                "pr_url": "https://github.com/example/mondayos/pull/55",
                "failure_count": 0,
                "next_poll_at": 0.0,
                "created_at": 1.0,
                "expires_at": 86_401.0,
            }
        if fields:
            self.state.set_job(update_id, **fields)
        self.state.acknowledge(update_id)
        return config, delivery_id

    def test_state_survives_restart(self):
        self.state.set_job(10, task_id="TASK-0001")
        reloaded = TelegramState(self.config.state_path)
        self.assertEqual(reloaded.job(10)["task_id"], "TASK-0001")
        reloaded.acknowledge(10)
        final = TelegramState(self.config.state_path)
        self.assertEqual(final.next_offset, 11)
        self.assertEqual(final.job(10), {})
        self.assertEqual(final.completed_job(10)["task_id"], "TASK-0001")

    def test_worker_heartbeat_survives_restart_and_clean_stop_is_not_fresh(self):
        self.state.mark_worker_started(now=100.0)
        self.state.mark_worker_heartbeat(now=120.0)

        reloaded = TelegramState(self.config.state_path)
        active = reloaded.worker_status(now=125.0, stale_after_seconds=10.0)
        self.assertTrue(active["heartbeat_fresh"])
        self.assertEqual(active["heartbeat_age_seconds"], 5.0)

        reloaded.mark_worker_stopped(now=126.0)
        stopped = TelegramState(self.config.state_path).worker_status(
            now=127.0,
            stale_after_seconds=10.0,
        )
        self.assertFalse(stopped["heartbeat_fresh"])
        self.assertEqual(stopped["stopped_at"], 126.0)

    def test_runtime_heartbeat_requires_both_independent_lanes(self):
        self.state.mark_worker_started(now=100.0)
        starting = self.state.worker_status(now=100.0, stale_after_seconds=10.0)
        self.assertTrue(starting["heartbeat_fresh"])
        self.assertFalse(starting["runtime_ready"])

        self.state.mark_runtime_lane(
            "intake",
            "active",
            "poll",
            timeout_seconds=40,
            now=101.0,
        )
        self.state.mark_runtime_lane(
            "worker",
            "idle",
            "idle",
            now=102.0,
        )
        self.state.mark_worker_heartbeat(now=103.0)
        ready = TelegramState(self.config.state_path).worker_status(
            now=104.0,
            stale_after_seconds=10.0,
        )
        self.assertTrue(ready["runtime_ready"])
        self.assertTrue(ready["intake"]["ready"])
        self.assertTrue(ready["worker"]["ready"])

        before = json.loads(
            self.state.heartbeat_path.read_text(encoding="utf-8")
        )["lanes"]
        self.state.mark_worker_heartbeat(now=105.0)
        after = json.loads(
            self.state.heartbeat_path.read_text(encoding="utf-8")
        )
        self.assertEqual(after["lanes"], before)
        self.assertEqual(after["supervisor"], {"pulse_at": 105.0})

    def test_overdue_or_backing_off_lane_fails_runtime_health(self):
        self.state.mark_worker_started(now=100.0)
        self.state.mark_runtime_lane(
            "intake",
            "active",
            "poll",
            timeout_seconds=40,
            now=100.0,
        )
        self.state.mark_runtime_lane("worker", "idle", "idle", now=100.0)
        self.state.mark_worker_heartbeat(now=150.0)

        overdue_intake = self.state.worker_status(
            now=150.0,
            stale_after_seconds=10.0,
        )
        self.assertTrue(overdue_intake["heartbeat_fresh"])
        self.assertFalse(overdue_intake["intake"]["ready"])
        self.assertTrue(overdue_intake["worker"]["ready"])
        self.assertFalse(overdue_intake["runtime_ready"])

        self.state.mark_runtime_lane(
            "intake",
            "active",
            "dispatch",
            timeout_seconds=300,
            now=150.0,
        )
        self.state.mark_runtime_lane(
            "worker",
            "active",
            "job",
            timeout_seconds=10,
            now=150.0,
        )
        self.state.mark_worker_heartbeat(now=161.0)
        overdue_worker = self.state.worker_status(
            now=161.0,
            stale_after_seconds=10.0,
        )
        self.assertTrue(overdue_worker["intake"]["ready"])
        self.assertFalse(overdue_worker["worker"]["ready"])
        self.assertFalse(overdue_worker["runtime_ready"])

        self.state.mark_runtime_lane(
            "worker",
            "backoff",
            "retry",
            resume_after_seconds=5,
            now=162.0,
        )
        self.state.mark_worker_heartbeat(now=170.0)
        after_resume = self.state.worker_status(
            now=170.0,
            stale_after_seconds=10.0,
        )
        self.assertGreater(170.0, after_resume["worker"]["resume_at"])
        self.assertFalse(after_resume["worker"]["ready"])

        self.state.mark_runtime_lane("worker", "idle", "idle", now=171.0)
        recovered = self.state.worker_status(
            now=171.0,
            stale_after_seconds=10.0,
        )
        self.assertTrue(recovered["runtime_ready"])

    def test_long_worker_phase_lease_boundaries_are_exact(self):
        leases = {
            "implementing": 2_100.0,
            "validating": 2_100.0,
            "reviewing": 2_700.0,
            "team-stage": 3_900.0,
        }
        for operation, lease in leases.items():
            with self.subTest(operation=operation):
                self.state.mark_worker_started(now=100.0)
                self.state.mark_runtime_lane(
                    "worker",
                    "active",
                    operation,
                    timeout_seconds=lease,
                    now=100.0,
                )
                boundary = 100.0 + lease
                self.state.mark_worker_heartbeat(now=boundary)
                at_boundary = self.state.worker_status(
                    now=boundary,
                    stale_after_seconds=10.0,
                )
                self.assertTrue(at_boundary["worker"]["ready"])
                after_boundary = self.state.worker_status(
                    now=boundary + 0.01,
                    stale_after_seconds=10.0,
                )
                self.assertFalse(after_boundary["worker"]["ready"])

    def test_runtime_heartbeat_rejects_legacy_corrupt_and_future_state(self):
        legacy = {"started_at": 1.0, "heartbeat_at": 2.0}
        self.state.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        self.state.heartbeat_path.write_text(
            json.dumps(legacy),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(TelegramStateError, "heartbeat"):
            self.state.worker_status(now=3.0)

        self.state.mark_worker_started(now=100.0)
        baseline = json.loads(
            self.state.heartbeat_path.read_text(encoding="utf-8")
        )
        self.assertEqual(baseline["version"], 2)
        self.assertNotIn("heartbeat_at", baseline)

        def extra_field(candidate):
            candidate["private_diagnostic"] = "must not be accepted"

        def unknown_operation(candidate):
            candidate["lanes"]["worker"]["operation"] = "private-task-name"

        def unknown_active_operation(candidate):
            lane = candidate["lanes"]["worker"]
            lane.update({
                "state": "active",
                "operation": "secret-bearing-private-operation",
                "deadline_at": lane["progress_at"] + 1.0,
            })

        def unknown_version(candidate):
            candidate["version"] = 99

        def future_started(candidate):
            candidate["started_at"] = 201.0

        def future_supervisor(candidate):
            candidate["supervisor"]["pulse_at"] = 201.0

        def future_stopped(candidate):
            candidate["stopped_at"] = 201.0
            for lane in candidate["lanes"].values():
                lane.update({
                    "state": "stopped",
                    "operation": "stopped",
                    "progress_at": 201.0,
                    "deadline_at": None,
                    "resume_at": None,
                })

        def future_progress(candidate):
            candidate["lanes"]["worker"]["progress_at"] = 201.0

        def oversized_deadline(candidate):
            lane = candidate["lanes"]["worker"]
            lane.update({
                "state": "active",
                "operation": "job",
                "deadline_at": lane["progress_at"] + 301.0,
            })

        huge_timestamp = 10**1_000

        def overflow_started(candidate):
            candidate["started_at"] = huge_timestamp

        def overflow_stopped(candidate):
            candidate["stopped_at"] = huge_timestamp

        def overflow_supervisor(candidate):
            candidate["supervisor"]["pulse_at"] = huge_timestamp

        def overflow_progress(candidate):
            candidate["lanes"]["worker"]["progress_at"] = huge_timestamp

        def overflow_deadline(candidate):
            candidate["lanes"]["worker"]["deadline_at"] = huge_timestamp

        def overflow_resume(candidate):
            candidate["lanes"]["worker"]["resume_at"] = huge_timestamp

        for label, mutate in (
            ("extra-field", extra_field),
            ("unknown-operation", unknown_operation),
            ("unknown-active-operation", unknown_active_operation),
            ("unknown-version", unknown_version),
            ("future-started", future_started),
            ("future-supervisor", future_supervisor),
            ("future-stopped", future_stopped),
            ("future-progress", future_progress),
            ("oversized-deadline", oversized_deadline),
            ("overflow-started", overflow_started),
            ("overflow-stopped", overflow_stopped),
            ("overflow-supervisor", overflow_supervisor),
            ("overflow-progress", overflow_progress),
            ("overflow-deadline", overflow_deadline),
            ("overflow-resume", overflow_resume),
        ):
            with self.subTest(label=label):
                candidate = json.loads(json.dumps(baseline))
                mutate(candidate)
                self.state.heartbeat_path.write_text(
                    json.dumps(candidate),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    TelegramStateError,
                    "heartbeat|runtime",
                ):
                    self.state.worker_status(now=200.0)

        self.state.heartbeat_path.write_text(
            json.dumps(legacy),
            encoding="utf-8",
        )
        self.state.mark_worker_started(now=300.0)
        restarted = self.state.worker_status(now=300.0)
        self.assertFalse(restarted["runtime_ready"])
        self.assertEqual(restarted["intake"]["state"], "starting")
        self.assertEqual(restarted["worker"]["state"], "starting")

    def test_runtime_heartbeat_rejects_oversized_nonregular_and_deep_files(self):
        heartbeat = self.state.heartbeat_path
        heartbeat.parent.mkdir(parents=True, exist_ok=True)

        heartbeat.write_bytes(b"{" + (b" " * 16_384) + b"}")
        with self.assertRaisesRegex(TelegramStateError, "heartbeat"):
            self.state.worker_status()

        heartbeat.write_text(("[" * 2_000) + ("]" * 2_000), encoding="utf-8")
        with self.assertRaisesRegex(TelegramStateError, "heartbeat"):
            self.state.worker_status()

        target = self.root / "heartbeat-target.json"
        target.write_text("{}", encoding="utf-8")
        heartbeat.unlink()
        heartbeat.symlink_to(target)
        with self.assertRaisesRegex(TelegramStateError, "heartbeat"):
            self.state.worker_status()

        heartbeat.unlink()
        os.mkfifo(heartbeat)
        with self.assertRaisesRegex(TelegramStateError, "heartbeat"):
            self.state.worker_status()

    def test_known_worker_crash_immediately_fails_its_runtime_lane(self):
        now = time.time()
        self.state.mark_worker_started(now=now)
        self.state.mark_runtime_lane(
            "intake",
            "active",
            "poll",
            timeout_seconds=60,
            now=now,
        )
        self.state.mark_runtime_lane("worker", "idle", "idle", now=now)
        runner = TelegramRunner(self.client, self.service, self.state)
        runner._runtime_leases_enabled = True
        runner._worker.enable_runtime_leases(True)

        with patch.object(
            runner._worker,
            "run_forever",
            side_effect=RuntimeError("simulated worker crash"),
        ):
            runner._worker_main()

        status = self.state.worker_status()
        self.assertFalse(status["runtime_ready"])
        self.assertEqual(status["worker"]["state"], "stopped")
        self.assertFalse(status["worker"]["ready"])
        self.assertIsInstance(runner._worker_error, RuntimeError)

    def test_daemon_poll_lease_matches_the_configured_long_poll(self):
        stop = threading.Event()
        observed = {}
        state = self.state

        class InspectingClient(FakeTelegramClient):
            def get_updates(self, *, offset, timeout):
                self.offsets.append(offset)
                observed.update(state.worker_status())
                stop.set()
                return []

        client = InspectingClient()
        service = TelegramBotService(self.monday, client, state, self.config)
        runner = TelegramRunner(
            client,
            service,
            state,
            poll_timeout=17,
            stop_event=stop,
        )

        runner.run_forever()

        self.assertEqual(observed["intake"]["state"], "active")
        self.assertEqual(observed["intake"]["operation"], "poll")
        self.assertTrue(observed["intake"]["ready"])
        self.assertEqual(
            observed["intake"]["deadline_at"]
            - observed["intake"]["progress_at"],
            47.0,
        )
        stopped = state.worker_status()
        self.assertEqual(stopped["intake"]["state"], "stopped")
        self.assertEqual(stopped["worker"]["state"], "stopped")

    def test_daemon_binds_service_progress_to_the_persisted_worker_lease(self):
        stop = threading.Event()
        observed = {}
        state = self.state

        class ProgressService:
            reporter = None

            def bind_runtime_progress(self, reporter):
                self.reporter = reporter

        service = ProgressService()

        class ReportingClient(FakeTelegramClient):
            def get_updates(self, *, offset, timeout):
                self.offsets.append(offset)
                self_service_reporter = service.reporter
                if not callable(self_service_reporter):
                    raise AssertionError("daemon did not bind runtime progress")
                self_service_reporter("reviewing")
                observed.update(state.worker_status())
                stop.set()
                return []

        runner = TelegramRunner(
            ReportingClient(),
            service,
            state,
            poll_timeout=1,
            stop_event=stop,
        )

        with patch.object(
            runner._worker,
            "run_forever",
            side_effect=lambda: stop.wait(5),
        ):
            runner.run_forever()

        self.assertEqual(observed["worker"]["state"], "active")
        self.assertEqual(observed["worker"]["operation"], "reviewing")
        self.assertEqual(
            observed["worker"]["deadline_at"]
            - observed["worker"]["progress_at"],
            2_700.0,
        )
        self.assertIsNone(service.reporter)

    def test_worker_progress_uses_exact_phase_leases_and_safe_fallback(self):
        now = time.time()
        self.state.mark_worker_started(now=now)
        runner = TelegramRunner(self.client, self.service, self.state)
        runner._runtime_leases_enabled = True

        expected = {
            "background": 300.0,
            "job": 300.0,
            "preflight": 300.0,
            "preparing": 300.0,
            "implementing": 2_100.0,
            "validating": 2_100.0,
            "reviewing": 2_700.0,
            "team-stage": 3_900.0,
            "committing": 300.0,
            "publication-ready": 300.0,
            "pushing": 300.0,
            "pull-request": 300.0,
        }
        for operation, lease in expected.items():
            with self.subTest(operation=operation):
                runner._renew_worker_runtime(operation)
                checkpoint = self.state.worker_status()["worker"]
                self.assertEqual(checkpoint["operation"], operation)
                self.assertEqual(
                    checkpoint["deadline_at"] - checkpoint["progress_at"],
                    lease,
                )

        runner._renew_worker_runtime("untrusted-private-phase")
        fallback = self.state.worker_status()["worker"]
        self.assertEqual(fallback["operation"], "job")
        self.assertEqual(
            fallback["deadline_at"] - fallback["progress_at"],
            300.0,
        )

    def test_one_shot_runner_never_advertises_daemon_runtime_health(self):
        runner = TelegramRunner(
            self.client,
            self.service,
            self.state,
            poll_timeout=1,
        )

        self.assertEqual(runner.run_once(), 0)

        self.assertFalse(self.state.heartbeat_path.exists())

    def test_instance_lock_probe_does_not_create_a_missing_lock(self):
        lock_path = self.root / "logs" / "telegram" / "health.lock"
        self.assertFalse(instance_lock_is_held(lock_path))
        self.assertFalse(lock_path.exists())

        with InstanceLock(lock_path):
            self.assertTrue(instance_lock_is_held(lock_path))

        self.assertFalse(instance_lock_is_held(lock_path))

    def test_legacy_state_load_is_read_only_until_the_first_mutation(self):
        legacy = {
            "version": 2,
            "bot_id": 99,
            "bot_username": "monday_test_bot",
            "last_update_id": -1,
            "jobs": {},
            "completed_jobs": {},
            "failed_jobs": {},
        }
        self.config.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.state_path.write_text(json.dumps(legacy), encoding="utf-8")

        loaded = TelegramState(self.config.state_path)

        self.assertEqual(
            json.loads(self.config.state_path.read_text(encoding="utf-8"))["version"],
            2,
        )
        loaded.bind_bot_identity(99, "monday_test_bot")
        self.assertEqual(
            json.loads(self.config.state_path.read_text(encoding="utf-8"))["version"],
            8,
        )

    def test_transient_live_build_retries_after_restart_without_request_text(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )
        update = _update(update_id=84, text="/build Recover this automatically")
        service = TelegramBotService(self.monday, self.client, self.state, config)
        service.bind_bot_identity(self.client.get_me())
        delivery_id = service._delivery_id(84)
        self.monday.delivery_runs[delivery_id] = {
            "delivery_id": delivery_id,
            "task_id": "TASK-0001",
            "status": "failed",
            "phase": "failed",
            "success": False,
            "branch": "",
            "commit_sha": "",
            "pr_url": "",
            "pushed": False,
            "changed_files": [],
            "attempts": [],
            "failure_code": "provider-timeout",
            "failure_class": "transient",
            "retry_after_seconds": 1,
            "message": "private provider diagnostic must not be sent",
        }
        self.client.updates = [update]
        runner = TelegramRunner(self.client, service, self.state, poll_timeout=1)
        started = time.time()

        with patch("telegram_bot.state.time.time", return_value=started):
            self.assertEqual(runner.run_once(), 1)

        tombstone = self.state.completed_job(84)
        retry = self.state.delivery_retry(84)
        self.assertNotIn("request", tombstone)
        self.assertEqual(retry["delivery_id"], f"{delivery_id}-r2")
        self.assertEqual(retry["attempt"], 2)
        self.assertEqual(
            retry["expires_at"],
            tombstone["delivery_retry_expires_at"],
        )
        self.assertNotIn("private provider diagnostic", "\n".join(
            text for _chat, text in self.client.sent
        ))
        self.assertNotIn(
            "private provider diagnostic",
            config.state_path.read_text(encoding="utf-8"),
        )

        restarted_state = TelegramState(config.state_path)
        restarted_service = TelegramBotService(
            self.monday,
            self.client,
            restarted_state,
            config,
        )
        worker = TelegramJobWorker(
            restarted_service,
            restarted_state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )
        with patch("telegram_bot.state.time.time", return_value=started + 61):
            self.assertTrue(worker.run_delivery_retry_once())

        final = restarted_state.completed_job(84)
        self.assertEqual(final["delivery_status"], "pr-open")
        self.assertEqual(final["delivery_retry"]["phase"], "completed")
        self.assertEqual(final["follow_up"]["kind"], "pr-lifecycle")
        self.assertEqual(restarted_state.delivery_retry(84), {})
        self.assertEqual(self.monday.build_executions, 1)
        self.assertEqual(
            self.monday.build_calls[-1][1]["delivery_id"],
            f"{delivery_id}-r2",
        )
        self.assertIn("recovered automatically", self.client.sent[-1][1])

    def test_restart_recovers_missing_transient_retry_once_without_rerunning_root(self):
        config, delivery_id = self._seed_terminal_live_build_checkpoint(
            83,
        )

        class FailingRetryNoticeClient(FakeTelegramClient):
            fail_notice = True

            def send_message(self, chat_id, text):
                if self.fail_notice and "temporary model or tool outage" in text:
                    raise TelegramAPIError("temporary outage", retryable=True)
                return super().send_message(chat_id, text)

        client = FailingRetryNoticeClient(identity=self.client.get_me())
        restarted_state = TelegramState(config.state_path)
        restarted_service = TelegramBotService(
            self.monday,
            client,
            restarted_state,
            config,
        )
        restarted_service.bind_bot_identity(client.get_me())
        worker = TelegramJobWorker(
            restarted_service,
            restarted_state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )

        with patch("telegram_bot.state.time.time", return_value=100.0):
            self.assertTrue(worker.run_once())
            self.assertTrue(worker.run_delivery_notice_once())

        retry = restarted_state.delivery_retry(83)
        self.assertEqual(retry["root_delivery_id"], delivery_id)
        self.assertEqual(retry["delivery_id"], f"{delivery_id}-r2")
        self.assertEqual(retry["attempt"], 2)
        self.assertEqual(self.monday.build_calls, [])
        self.assertEqual(restarted_state.job(83), {})
        self.assertTrue(restarted_state.is_terminal(83))
        self.assertEqual(restarted_state.pending_delivery_notice_count, 1)
        self.assertEqual(restarted_state.due_delivery_notice_ids(now=129.0), [])

        client.fail_notice = False
        resumed_state = TelegramState(config.state_path)
        resumed_service = TelegramBotService(
            self.monday,
            client,
            resumed_state,
            config,
        )
        resumed_service.bind_bot_identity(client.get_me())
        resumed_worker = TelegramJobWorker(
            resumed_service,
            resumed_state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )

        with (
            patch("telegram_bot.state.time.time", return_value=131.0),
            patch("telegram_bot.service.time.time", return_value=131.0),
        ):
            self.assertTrue(resumed_worker.run_delivery_notice_once())
        self.assertFalse(resumed_worker.run_once())
        self.assertEqual(resumed_state.delivery_retry(83), retry)
        self.assertEqual(self.monday.build_calls, [])
        state_payload = json.loads(config.state_path.read_text(encoding="utf-8"))
        self.assertEqual(set(state_payload["delivery_retries"]), {"83"})
        self.assertEqual(state_payload["delivery_notices"], {})
        self.assertTrue(resumed_state.is_terminal(83))

    def test_restart_does_not_recover_unsafe_or_nontransient_terminal_results(self):
        cases = (
            (91, "permanent", False, "", False),
            (92, "policy", False, "", False),
            (93, "transient", True, "b" * 40, False),
            (94, "transient", False, "", True),
        )
        for update_id, failure_class, pushed, commit_sha, uncertain in cases:
            with self.subTest(
                update_id=update_id,
                failure_class=failure_class,
                pushed=pushed,
                publication_uncertain=uncertain,
            ):
                self.state = TelegramState(self.config.state_path)
                config, _delivery_id = (
                    self._seed_terminal_live_build_checkpoint(
                        update_id,
                        failure_class=failure_class,
                        pushed=pushed,
                        commit_sha=commit_sha,
                        publication_uncertain=uncertain,
                    )
                )
                restarted_state = TelegramState(config.state_path)
                restarted_service = TelegramBotService(
                    self.monday,
                    self.client,
                    restarted_state,
                    config,
                )
                restarted_service.bind_bot_identity(self.client.get_me())
                worker = TelegramJobWorker(
                    restarted_service,
                    restarted_state,
                    stop_event=threading.Event(),
                    wake_event=threading.Event(),
                )

                self.assertTrue(worker.run_once())
                self.assertEqual(restarted_state.delivery_retry(update_id), {})
                self.assertTrue(restarted_state.is_terminal(update_id))
                self.assertEqual(self.monday.build_calls, [])

    def test_restart_after_original_retry_deadline_does_not_start_recovery(self):
        config, delivery_id = self._seed_terminal_live_build_checkpoint(
            95,
            retry_expires_at=99.0,
        )
        restarted_state = TelegramState(config.state_path)
        restarted_service = TelegramBotService(
            self.monday,
            self.client,
            restarted_state,
            config,
        )
        restarted_service.bind_bot_identity(self.client.get_me())
        worker = TelegramJobWorker(
            restarted_service,
            restarted_state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )

        with patch("telegram_bot.state.time.time", return_value=100.0):
            self.assertTrue(worker.run_once())
            self.assertTrue(worker.run_delivery_notice_once())

        terminal = restarted_state.completed_job(95)
        self.assertEqual(restarted_state.delivery_retry(95), {})
        self.assertEqual(terminal["delivery_retry"]["phase"], "not-started-deadline")
        self.assertEqual(terminal["delivery_retry"]["delivery_id"], delivery_id)
        self.assertEqual(self.monday.build_calls, [])
        self.assertIn("no automatic retry was started", self.client.sent[-1][1])

    def test_delayed_delivery_retry_does_not_block_new_fifo_work(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )
        service = TelegramBotService(self.monday, self.client, self.state, config)
        service.bind_bot_identity(self.client.get_me())
        first = _update(update_id=85, text="/build Retry later")
        delivery_id = service._delivery_id(85)
        self.monday.delivery_runs[delivery_id] = {
            "delivery_id": delivery_id,
            "task_id": "TASK-0001",
            "status": "failed",
            "phase": "failed",
            "success": False,
            "branch": "",
            "commit_sha": "",
            "pr_url": "",
            "pushed": False,
            "changed_files": [],
            "attempts": [],
            "failure_code": "provider-timeout",
            "failure_class": "transient",
            "retry_after_seconds": 3_600,
            "message": "temporary outage",
        }
        self.client.updates = [first]
        runner = TelegramRunner(self.client, service, self.state, poll_timeout=1)
        self.assertEqual(runner.run_once(), 1)
        self.assertTrue(self.state.delivery_retry(85))

        second = _update(update_id=86, text="/help")
        self.state.enqueue(86, service.normalize_update(second))
        self._complete_acceptance_receipt(86)
        worker = TelegramJobWorker(
            service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )

        self.assertTrue(worker.run_once())
        self.assertTrue(self.state.is_terminal(86))
        self.assertTrue(self.state.delivery_retry(85))

    def test_policy_failure_and_publication_started_never_schedule_fresh_delivery(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )
        service = TelegramBotService(self.monday, self.client, self.state, config)
        service.bind_bot_identity(self.client.get_me())

        for update_id, failure_class, commit_sha, pr_url in (
            (87, "policy", "", ""),
            (88, "transient", "b" * 40, ""),
            (89, "transient", "", "https://evil.example/pull/1"),
        ):
            with self.subTest(update_id=update_id):
                delivery_id = service._delivery_id(update_id)
                self.monday.delivery_runs[delivery_id] = {
                    "delivery_id": delivery_id,
                    "task_id": f"TASK-{update_id - 86:04d}",
                    "status": "failed",
                    "phase": "failed",
                    "success": False,
                    "branch": "codex/reconcile-existing-publication",
                    "commit_sha": commit_sha,
                    "pr_url": pr_url,
                    "pushed": bool(commit_sha),
                    "changed_files": [],
                    "attempts": [],
                    "failure_code": "artifact-security",
                    "failure_class": failure_class,
                    "retry_after_seconds": 60 if failure_class == "transient" else 0,
                    "message": "stopped",
                }
                self.client.updates = [
                    _update(update_id=update_id, text="/build Do not retry this"),
                ]
                runner = TelegramRunner(
                    self.client,
                    service,
                    self.state,
                    poll_timeout=1,
                )
                self.assertEqual(runner.run_once(), 1)
                self.assertEqual(self.state.delivery_retry(update_id), {})

    def test_delivery_retry_waits_for_request_free_tombstone_before_claim(self):
        root_delivery_id = self._seed_delivery_retry(89, now=100.0)

        self.assertIsNone(self.state.claim_due_delivery_retry(now=200.0))
        self.state.acknowledge(89)
        claimed = self.state.claim_due_delivery_retry(now=200.0)

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed[0], 89)
        self.assertEqual(claimed[1]["delivery_id"], f"{root_delivery_id}-r2")

    def test_due_retry_supersedes_equally_due_scheduled_notice(self):
        root_delivery_id = self._seed_delivery_retry(
            189,
            now=100.0,
            delay_seconds=60.0,
        )
        self.state.acknowledge(189)
        notice = self.state.delivery_notice(189)
        self.assertEqual(notice["notice_kind"], "retry-scheduled")
        self.state.defer_delivery_notice(
            189,
            source_binding=str(notice["source_binding"]),
            delay_seconds=60.0,
            now=100.0,
        )
        self.assertEqual(self.state.due_delivery_notice_ids(now=160.0), [])

        worker = TelegramJobWorker(
            self.service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )
        with (
            patch("telegram_bot.state.time.time", return_value=160.0),
            patch("telegram_bot.service.time.time", return_value=160.0),
        ):
            self.assertTrue(worker.run_delivery_retry_once())
            self.assertFalse(worker.run_delivery_notice_once())

        self.assertEqual(self.state.pending_delivery_notice_count, 0)
        self.assertEqual(self.state.delivery_retry(189), {})
        completed = self.state.completed_job(189)
        self.assertEqual(
            completed["delivery_notice"],
            {"phase": "superseded", "notice_kind": "retry-scheduled"},
        )
        self.assertEqual(completed["delivery_id"], f"{root_delivery_id}-r2")
        self.assertFalse(any(
            "will try automatically" in text
            for _chat_id, text in self.client.sent
        ))

    def test_one_shot_does_not_send_an_overdue_retry_schedule_notice(self):
        self._seed_delivery_retry(187, now=100.0, delay_seconds=60.0)
        self.state.acknowledge(187)
        runner = TelegramRunner(
            self.client,
            self.service,
            self.state,
            poll_timeout=1,
        )

        with patch("telegram_bot.state.time.time", return_value=160.0):
            self.assertEqual(runner.run_once(), 0)

        self.assertEqual(self.state.pending_delivery_notice_count, 1)
        self.assertTrue(self.state.delivery_retry(187))
        self.assertFalse(any(
            "will try automatically" in text
            for _chat_id, text in self.client.sent
        ))

    def test_reschedule_retry_atomically_stages_next_semantic_notice(self):
        root_delivery_id = self._seed_delivery_retry(188, now=100.0)
        self.state.acknowledge(188)
        worker = TelegramJobWorker(
            self.service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )
        with patch("telegram_bot.state.time.time", return_value=100.0):
            self.assertTrue(worker.run_delivery_notice_once())
        self.assertTrue(self.state.completed_job(188)["delivery_final_sent"])
        claimed = self.state.claim_due_delivery_retry(now=161.0)
        self.assertIsNotNone(claimed)

        with patch.object(
            self.state,
            "_write",
            wraps=self.state._write,
        ) as writer:
            retry = self.state.reschedule_delivery_retry(
                188,
                delivery_id=f"{root_delivery_id}-r3",
                attempt=3,
                delay_seconds=300,
                failure_code="provider-timeout",
                now=161.0,
            )
            self.assertEqual(writer.call_count, 1)

        self.assertEqual(retry["next_attempt_at"], 461.0)
        self.assertFalse(self.state.completed_job(188)["delivery_final_sent"])
        notice = self.state.delivery_notice(188)
        self.assertEqual(notice["notice_kind"], "retry-scheduled")
        self.assertEqual(notice["attempt"], 3)
        self.assertEqual(notice["next_attempt_at"], 461.0)
        self.assertNotIn(
            "request",
            json.loads(
                self.config.state_path.read_text(encoding="utf-8")
            )["completed_jobs"]["188"],
        )
        restarted = TelegramState(self.config.state_path)
        self.assertEqual(restarted.delivery_notice(188)["attempt"], 3)

    def test_terminal_notice_is_request_free_and_tampering_fails_closed(self):
        config, _delivery_id = self._stage_terminal_notice(
            190,
            retry_unavailable_phase="not-started-deadline",
        )
        payload = json.loads(config.state_path.read_text(encoding="utf-8"))
        source = payload["completed_jobs"]["190"]
        notice = payload["delivery_notices"]["190"]
        self.assertNotIn("request", source)
        self.assertEqual(notice["retry_unavailable_phase"], "not-started-deadline")
        self.assertNotIn(
            "Recover the saved transient result",
            config.state_path.read_text(encoding="utf-8"),
        )
        baseline = json.loads(json.dumps(payload))

        def changed_binding(candidate):
            candidate["delivery_notices"]["190"]["source_binding"] = "0" * 64

        def changed_route(candidate):
            current = candidate["delivery_notices"]["190"]
            current["origin_user_id"] = 8
            current["origin_chat_id"] = 8

        def changed_source(candidate):
            candidate["completed_jobs"]["190"]["project_name"] = "other-project"

        def changed_semantics(candidate):
            candidate["delivery_notices"]["190"][
                "retry_unavailable_phase"
            ] = "not-started-capacity"

        def changed_result_shape(candidate):
            candidate["completed_jobs"]["190"]["delivery_result"][
                "provider_diagnostic"
            ] = "must not survive"

        for label, mutate in (
            ("binding", changed_binding),
            ("route", changed_route),
            ("source", changed_source),
            ("semantic-display", changed_semantics),
            ("non-canonical-result", changed_result_shape),
        ):
            with self.subTest(label=label):
                candidate = json.loads(json.dumps(baseline))
                mutate(candidate)
                config.state_path.write_text(json.dumps(candidate), encoding="utf-8")
                with self.assertRaisesRegex(
                    TelegramStateError,
                    "Invalid Telegram state",
                ):
                    TelegramState(config.state_path)

    def test_permanent_root_diagnostic_never_enters_state_notice_or_send(self):
        secret = "provider stack trace with private customer context"
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )

        class RetryableFinalClient(FakeTelegramClient):
            fail_final = True

            def send_message(self, chat_id, text):
                if self.fail_final and "live build: failed" in text:
                    raise TelegramAPIError("temporary outage", retryable=True)
                return super().send_message(chat_id, text)

        client = RetryableFinalClient([
            _update(update_id=205, text="/build Keep diagnostics private"),
        ])
        self.state.bind_bot_identity(99, "monday_test_bot")
        service = TelegramBotService(self.monday, client, self.state, config)
        service.bind_bot_identity(client.get_me())
        delivery_id = service._delivery_id(205)
        self.monday.delivery_runs[delivery_id] = {
            "delivery_id": delivery_id,
            "task_id": "TASK-0001",
            "status": "failed",
            "phase": "failed",
            "success": False,
            "branch": "",
            "commit_sha": "",
            "pr_url": "",
            "pushed": False,
            "changed_files": [],
            "attempts": [],
            "failure_code": "provider-invalid-response",
            "failure_class": "permanent",
            "retry_after_seconds": 0,
            "message": secret,
        }
        runner = TelegramRunner(client, service, self.state, poll_timeout=1)

        self.assertEqual(runner.run_once(), 1)

        serialized = config.state_path.read_text(encoding="utf-8")
        self.assertNotIn(secret, serialized)
        self.assertEqual(self.state.pending_delivery_notice_count, 1)
        notice = self.state.delivery_notice(205)
        result = notice["result"]
        self.assertEqual(result["message"], "")
        self.assertEqual(set(result), {
            "delivery_id", "success", "status", "phase", "branch",
            "commit_sha", "pr_url", "publication_uncertain", "pushed",
            "changed_files", "attempts", "failure_code", "failure_class",
            "retry_after_seconds", "message",
        })

        baseline = json.loads(serialized)
        tampered = json.loads(serialized)
        tampered["completed_jobs"]["205"]["delivery_result"]["message"] = secret
        config.state_path.write_text(json.dumps(tampered), encoding="utf-8")
        with self.assertRaisesRegex(TelegramStateError, "Invalid Telegram state"):
            TelegramState(config.state_path)
        config.state_path.write_text(json.dumps(baseline), encoding="utf-8")

        client.fail_final = False
        restarted = TelegramState(config.state_path)
        restarted_service = TelegramBotService(
            self.monday,
            client,
            restarted,
            config,
        )
        restarted_worker = TelegramJobWorker(
            restarted_service,
            restarted,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )
        due_at = baseline["delivery_notices"]["205"]["next_attempt_at"]
        with patch("telegram_bot.state.time.time", return_value=due_at):
            self.assertTrue(restarted_worker.run_delivery_notice_once())

        final_messages = [
            text for _chat_id, text in client.sent if "live build: failed" in text
        ]
        self.assertEqual(len(final_messages), 1)
        self.assertNotIn(secret, final_messages[0])
        self.assertEqual(restarted.pending_delivery_notice_count, 0)
        self.assertNotIn(secret, config.state_path.read_text(encoding="utf-8"))

    def test_v7_migration_does_not_synthesize_historical_delivery_notice(self):
        config, _delivery_id = self._stage_terminal_notice(191)
        payload = json.loads(config.state_path.read_text(encoding="utf-8"))
        payload["version"] = 7
        payload.pop("delivery_notices")
        payload.pop("delivery_notice_pause_until")
        config.state_path.write_text(json.dumps(payload), encoding="utf-8")

        migrated = TelegramState(config.state_path)

        self.assertEqual(migrated.pending_delivery_notice_count, 0)
        self.assertFalse(migrated.completed_job(191)["delivery_final_sent"])
        self.assertEqual(
            json.loads(config.state_path.read_text(encoding="utf-8"))["version"],
            7,
        )
        migrated.bind_bot_identity(99, "monday_test_bot")
        persisted = json.loads(config.state_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["version"], 8)
        self.assertEqual(persisted["delivery_notices"], {})
        self.assertEqual(persisted["delivery_notice_pause_until"], 0.0)

    def test_legacy_migration_scrubs_delivery_results_in_every_job_bucket(self):
        config, _delivery_id = self._seed_terminal_live_build_checkpoint(
            209,
            failure_class="permanent",
        )
        payload = json.loads(config.state_path.read_text(encoding="utf-8"))
        template = payload["jobs"]["209"]["delivery_result"]
        bucket_keys = {
            "jobs": "209",
            "completed_jobs": "207",
            "failed_jobs": "208",
        }
        for index, (bucket_name, key) in enumerate(bucket_keys.items(), start=1):
            if bucket_name != "jobs":
                payload[bucket_name][key] = {
                    "phase": "completed" if bucket_name == "completed_jobs" else "dead-letter",
                    "delivery_terminal": True,
                    "delivery_result": json.loads(json.dumps(template)),
                }
            result = payload[bucket_name][key]["delivery_result"]
            result["message"] = f"legacy private diagnostic {index}"
            result[f"provider_extra_{index}"] = f"private-extra-{index}"
        expected_fields = {
            "delivery_id", "success", "status", "phase", "branch",
            "commit_sha", "pr_url", "publication_uncertain", "pushed",
            "changed_files", "attempts", "failure_code", "failure_class",
            "retry_after_seconds", "message",
        }
        for legacy_version in (0, 7):
            with self.subTest(legacy_version=legacy_version):
                candidate = json.loads(json.dumps(payload))
                candidate["version"] = legacy_version
                candidate.pop("delivery_notices", None)
                candidate.pop("delivery_notice_pause_until", None)
                config.state_path.write_text(json.dumps(candidate), encoding="utf-8")

                migrated = TelegramState(config.state_path)
                self.assertEqual(
                    migrated.job(209)["delivery_result"]["message"],
                    "",
                )
                untouched = config.state_path.read_text(encoding="utf-8")
                self.assertIn("legacy private diagnostic", untouched)
                migrated.bind_bot_identity(99, "monday_test_bot")

                persisted = json.loads(
                    config.state_path.read_text(encoding="utf-8")
                )
                for bucket_name, key in bucket_keys.items():
                    result = persisted[bucket_name][key]["delivery_result"]
                    self.assertEqual(set(result), expected_fields)
                    self.assertEqual(result["message"], "")
                serialized = config.state_path.read_text(encoding="utf-8")
                self.assertNotIn("legacy private diagnostic", serialized)
                self.assertNotIn("private-extra", serialized)

    def test_rate_limited_terminal_notice_defers_due_cohort_atomically(self):
        config, _first_delivery = self._stage_terminal_notice(192)
        self._stage_terminal_notice(193)
        worker = TelegramJobWorker(
            self.service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )

        with (
            patch.object(
                self.service,
                "deliver_delivery_notice",
                side_effect=TelegramRateLimitError(9),
            ),
            patch("telegram_bot.state.time.time", return_value=100.0),
            patch.object(self.state, "_write", wraps=self.state._write) as writer,
        ):
            self.assertTrue(worker.run_delivery_notice_once())
            self.assertEqual(writer.call_count, 1)

        self.assertEqual(self.state.pending_delivery_notice_count, 2)
        self.assertEqual(self.state.due_delivery_notice_ids(now=108.0), [])
        restarted = TelegramState(config.state_path)
        self.assertEqual(restarted.due_delivery_notice_ids(now=109.0), [192, 193])
        persisted = json.loads(config.state_path.read_text(encoding="utf-8"))
        for update_id in ("192", "193"):
            notice = persisted["delivery_notices"][update_id]
            self.assertEqual(notice["phase"], "retrying")
            self.assertEqual(notice["failure_count"], 1)
            self.assertEqual(notice["next_attempt_at"], 109.0)

    def test_delivery_notice_pause_covers_future_and_new_notice_after_restart(self):
        config, _first_delivery = self._stage_terminal_notice(201)
        self._stage_terminal_notice(202)
        future = self.state.delivery_notice(202)
        self.state.defer_delivery_notice(
            202,
            source_binding=str(future["source_binding"]),
            delay_seconds=5,
            now=100.0,
        )
        worker = TelegramJobWorker(
            self.service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )
        with (
            patch.object(
                self.service,
                "deliver_delivery_notice",
                side_effect=TelegramRateLimitError(9),
            ),
            patch("telegram_bot.state.time.time", return_value=100.0),
        ):
            self.assertTrue(worker.run_delivery_notice_once())

        with patch("telegram_bot.state.time.time", return_value=101.0):
            self._stage_terminal_notice(203)
            restarted = TelegramState(config.state_path)

        payload = json.loads(config.state_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["delivery_notice_pause_until"], 109.0)
        self.assertEqual(restarted.due_delivery_notice_ids(now=108.0), [])
        self.assertEqual(restarted.next_follow_up_delay(now=101.0), 8.0)
        self.assertEqual(
            restarted.due_delivery_notice_ids(now=109.0),
            [201, 202, 203],
        )

        restarted.bind_bot_identity(100, "replacement_bot")
        reset = json.loads(config.state_path.read_text(encoding="utf-8"))
        self.assertEqual(reset["delivery_notice_pause_until"], 0.0)
        self.assertEqual(reset["delivery_notices"], {})

    def test_current_delivery_notice_pause_is_bounded_and_validated(self):
        config, _delivery_id = self._stage_terminal_notice(204)
        payload = json.loads(config.state_path.read_text(encoding="utf-8"))

        for malformed in (True, 86_502.0):
            with self.subTest(malformed=malformed):
                candidate = json.loads(json.dumps(payload))
                candidate["delivery_notice_pause_until"] = malformed
                config.state_path.write_text(json.dumps(candidate), encoding="utf-8")
                with (
                    patch("telegram_bot.state.time.time", return_value=100.0),
                    self.assertRaisesRegex(
                        TelegramStateError,
                        "Invalid Telegram state",
                    ),
                ):
                    TelegramState(config.state_path)

        config.state_path.write_text(json.dumps(payload), encoding="utf-8")

    def test_revoked_and_permanent_terminal_notice_routes_do_not_hot_loop(self):
        config, _delivery_id = self._stage_terminal_notice(194)
        revoked = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({999}),
            project_root=self.root,
        )
        revoked_service = TelegramBotService(
            self.monday,
            self.client,
            self.state,
            revoked,
        )
        revoked_worker = TelegramJobWorker(
            revoked_service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )
        self.assertTrue(revoked_worker.run_delivery_notice_once())
        self.assertEqual(self.state.pending_delivery_notice_count, 0)
        self.assertEqual(
            self.state.completed_job(194)["delivery_notice"]["phase"],
            "notification-failed",
        )

        self._stage_terminal_notice(195)
        worker = TelegramJobWorker(
            self.service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )
        with patch.object(
            self.service,
            "deliver_delivery_notice",
            side_effect=TelegramAPIError(
                "chat unavailable",
                retryable=False,
                status_code=403,
            ),
        ):
            self.assertTrue(worker.run_delivery_notice_once())
        self.assertEqual(self.state.pending_delivery_notice_count, 0)
        self.assertEqual(
            self.state.completed_job(195)["delivery_notice"]["phase"],
            "notification-failed",
        )
        self.assertEqual(TelegramState(config.state_path).due_delivery_notice_ids(), [])

    def test_deferred_notice_keeps_bound_follow_up_snapshot_as_source_advances(self):
        config, _delivery_id = self._stage_terminal_notice(
            196,
            follow_up_phase="pending",
        )
        notice = self.state.delivery_notice(196)
        self.assertEqual(notice["follow_up_phase"], "pending")
        self.state.defer_delivery_notice(
            196,
            source_binding=str(notice["source_binding"]),
            delay_seconds=60,
            now=100.0,
        )
        self.state.update_follow_up(196, phase="polling")

        restarted = TelegramState(config.state_path)
        durable = restarted.delivery_notice(196)
        self.assertEqual(durable["follow_up_phase"], "pending")
        self.assertEqual(
            restarted.completed_job(196)["follow_up"]["phase"],
            "polling",
        )

    def test_invalid_terminal_notice_ack_does_not_partially_mutate_memory(self):
        config, _delivery_id = self._seed_terminal_live_build_checkpoint(
            197,
            failure_class="permanent",
        )
        self.state.set_job(197, chat_id=999)
        persisted_before = config.state_path.read_text(encoding="utf-8")

        with self.assertRaisesRegex(TelegramStateError, "notice route"):
            self.state.acknowledge(197)

        self.assertTrue(self.state.job(197).get("request"))
        self.assertEqual(self.state.completed_job(197), {})
        self.assertEqual(config.state_path.read_text(encoding="utf-8"), persisted_before)

    def test_full_delivery_notice_outbox_still_scrubs_and_advances_fifo(self):
        with patch("telegram_bot.state._MAX_ACTIVE_DELIVERY_NOTICES", 1):
            config, _first_delivery = self._stage_terminal_notice(198)
            _config, _overflow_delivery = self._seed_terminal_live_build_checkpoint(
                199,
                failure_class="permanent",
            )
            unsafe_result = self.state.job(199)["delivery_result"]
            unsafe_result["message"] = "overflow private diagnostic 199"
            unsafe_result["provider_context"] = "overflow-extra-199"
            self.state.set_job(199, delivery_result=unsafe_result)
            self.state.enqueue(200, _update(update_id=200, text="/help"))
            self._complete_acceptance_receipt(200)

            self.state.acknowledge(199)

        overflow = self.state.completed_job(199)
        self.assertNotIn("request", overflow)
        self.assertTrue(_ORIGIN_KEYS.isdisjoint(overflow))
        self.assertEqual(
            overflow["delivery_notice"],
            {"phase": "not-started-capacity", "notice_kind": "terminal-result"},
        )
        self.assertEqual(self.state.next_offset, 201)
        self.assertEqual(self.state.pending_delivery_notice_count, 1)
        self.assertEqual(overflow["delivery_result"]["message"], "")
        self.assertNotIn("provider_context", overflow["delivery_result"])
        serialized = config.state_path.read_text(encoding="utf-8")
        self.assertNotIn("overflow private diagnostic 199", serialized)
        self.assertNotIn("overflow-extra-199", serialized)
        before_eviction = TelegramState(config.state_path)
        self.assertEqual(
            before_eviction.completed_job(199)["delivery_result"]["message"],
            "",
        )

        claimed = self.state.claim_next()
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed[0], 200)
        with patch("telegram_bot.state._MAX_COMPLETED_JOBS", 1):
            self.state.acknowledge(200)

        self.assertTrue(self.state.completed_job(198))
        self.assertTrue(self.state.delivery_notice(198))
        self.assertEqual(self.state.completed_job(199), {})
        restarted = TelegramState(config.state_path)
        self.assertTrue(restarted.completed_job(198))
        self.assertEqual(restarted.pending_delivery_notice_count, 1)

    def test_full_outbox_reschedule_scrubs_legacy_completed_source(self):
        self.state.bind_bot_identity(99, "monday_test_bot")
        root_delivery_id = self._seed_delivery_retry(207, now=100.0)
        self.state.acknowledge(207)
        scheduled = self.state.delivery_notice(207)
        self.state.complete_delivery_notice(
            207,
            source_binding=str(scheduled["source_binding"]),
            delivered=True,
        )
        self.assertTrue(self.state.completed_job(207)["delivery_final_sent"])
        with patch("telegram_bot.state._MAX_ACTIVE_DELIVERY_NOTICES", 1):
            config, _blocker_delivery = self._stage_terminal_notice(208)

        payload = json.loads(config.state_path.read_text(encoding="utf-8"))
        unsafe_result = payload["completed_jobs"]["207"]["delivery_result"]
        unsafe_result["message"] = "reschedule private diagnostic 207"
        unsafe_result["provider_context"] = "reschedule-extra-207"
        config.state_path.write_text(json.dumps(payload), encoding="utf-8")
        legacy = TelegramState(config.state_path)
        claimed = legacy.claim_due_delivery_retry(now=161.0)
        self.assertIsNotNone(claimed)

        with patch("telegram_bot.state._MAX_ACTIVE_DELIVERY_NOTICES", 1):
            retry = legacy.reschedule_delivery_retry(
                207,
                delivery_id=f"{root_delivery_id}-r3",
                attempt=3,
                delay_seconds=300,
                failure_code="provider-timeout",
                now=161.0,
            )

        self.assertEqual(retry["attempt"], 3)
        self.assertEqual(legacy.pending_delivery_notice_count, 1)
        self.assertEqual(legacy.delivery_notice(207), {})
        source = legacy.completed_job(207)
        self.assertEqual(source["delivery_result"]["message"], "")
        self.assertNotIn("provider_context", source["delivery_result"])
        self.assertEqual(
            source["delivery_notice"],
            {"phase": "not-started-capacity", "notice_kind": "retry-scheduled"},
        )
        serialized = config.state_path.read_text(encoding="utf-8")
        self.assertNotIn("reschedule private diagnostic 207", serialized)
        self.assertNotIn("reschedule-extra-207", serialized)
        restarted = TelegramState(config.state_path)
        self.assertEqual(restarted.delivery_retry(207)["attempt"], 3)

    def test_retry_notice_failure_replays_checkpoint_without_rerunning_root(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )
        update = _update(update_id=90, text="/build Recover after restart")

        class FailingNoticeClient(FakeTelegramClient):
            fail_notice = True

            def send_message(self, chat_id, text):
                if self.fail_notice and "temporary model or tool outage" in text:
                    raise TelegramAPIError("temporary outage", retryable=True)
                return super().send_message(chat_id, text)

        client = FailingNoticeClient([update])
        self.state.bind_bot_identity(99, "monday_test_bot")
        service = TelegramBotService(self.monday, client, self.state, config)
        service.bind_bot_identity(client.get_me())
        delivery_id = service._delivery_id(90)
        self.monday.delivery_runs[delivery_id] = {
            "delivery_id": delivery_id,
            "task_id": "TASK-0001",
            "status": "failed",
            "phase": "failed",
            "success": False,
            "branch": "",
            "commit_sha": "",
            "pr_url": "",
            "pushed": False,
            "changed_files": [],
            "attempts": [],
            "failure_code": "provider-timeout",
            "failure_class": "transient",
            "retry_after_seconds": 0,
            "message": "private diagnostic",
        }
        runner = TelegramRunner(client, service, self.state, poll_timeout=1)

        self.assertEqual(runner.run_once(), 1)

        self.assertEqual(self.state.job(90), {})
        self.assertTrue(self.state.is_terminal(90))
        self.assertEqual(self.state.pending_delivery_notice_count, 1)
        self.assertTrue(self.state.delivery_retry(90))
        self.assertEqual(len(self.monday.build_calls), 1)

        later = service.normalize_update(_update(update_id=91, text="/help"))
        self.assertIsNotNone(later)
        self.state.enqueue(91, later)
        self._complete_acceptance_receipt(91)
        worker = TelegramJobWorker(
            service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )
        self.assertTrue(worker.run_once())
        self.assertTrue(self.state.is_terminal(91))

        client.fail_notice = False
        restarted_state = TelegramState(config.state_path)
        restarted_service = TelegramBotService(
            self.monday,
            client,
            restarted_state,
            config,
        )
        restarted_service.bind_bot_identity(client.get_me())
        restarted_runner = TelegramRunner(
            client,
            restarted_service,
            restarted_state,
            poll_timeout=1,
        )
        persisted = json.loads(config.state_path.read_text(encoding="utf-8"))
        notice_due = persisted["delivery_notices"]["90"]["next_attempt_at"]
        retry_due = persisted["delivery_retries"]["90"]["next_attempt_at"]
        self.assertLess(notice_due, retry_due)
        resume_at = notice_due + ((retry_due - notice_due) / 2)

        with (
            patch("telegram_bot.state.time.time", return_value=resume_at),
            patch("telegram_bot.service.time.time", return_value=resume_at),
        ):
            self.assertEqual(restarted_runner.run_once(), 0)
        self.assertTrue(restarted_state.is_terminal(90))
        self.assertTrue(restarted_state.delivery_retry(90))
        self.assertEqual(restarted_state.pending_delivery_notice_count, 0)
        self.assertEqual(len(self.monday.build_calls), 1)
        self.assertIn("will try automatically", client.sent[-1][1])

    def test_permanent_final_notice_failure_keeps_recovered_pr_outcome(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )

        class FailingFinalClient(FakeTelegramClient):
            fail_final = False

            def send_message(self, chat_id, text):
                if self.fail_final and "recovered automatically" in text:
                    raise TelegramAPIError(
                        "chat unavailable",
                        retryable=False,
                        status_code=403,
                    )
                return super().send_message(chat_id, text)

        client = FailingFinalClient([
            _update(update_id=91, text="/build Recover and preserve the PR"),
        ])
        self.state.bind_bot_identity(99, "monday_test_bot")
        service = TelegramBotService(self.monday, client, self.state, config)
        service.bind_bot_identity(client.get_me())
        delivery_id = service._delivery_id(91)
        self.monday.delivery_runs[delivery_id] = {
            "delivery_id": delivery_id,
            "task_id": "TASK-0001",
            "status": "failed",
            "phase": "failed",
            "success": False,
            "branch": "",
            "commit_sha": "",
            "pr_url": "",
            "pushed": False,
            "changed_files": [],
            "attempts": [],
            "failure_code": "provider-timeout",
            "failure_class": "transient",
            "retry_after_seconds": 0,
            "message": "temporary outage",
        }
        started = time.time()
        runner = TelegramRunner(client, service, self.state, poll_timeout=1)
        with patch("telegram_bot.state.time.time", return_value=started):
            self.assertEqual(runner.run_once(), 1)

        client.fail_final = True
        worker = TelegramJobWorker(
            service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )
        with patch("telegram_bot.state.time.time", return_value=started + 61):
            self.assertTrue(worker.run_delivery_retry_once())

        completed = self.state.completed_job(91)
        self.assertEqual(completed["delivery_status"], "pr-open")
        self.assertTrue(completed["delivery_result"]["success"])
        self.assertFalse(completed["delivery_final_sent"])
        self.assertEqual(
            completed["delivery_retry"]["phase"],
            "notification-failed",
        )
        self.assertEqual(completed["follow_up"]["phase"], "notification-failed")
        self.assertEqual(self.state.delivery_retry(91), {})
        self.assertEqual(self.monday.build_executions, 1)

    def test_retryable_later_schedule_notice_survives_restart_without_rerun(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )

        class FailingThirdRunNoticeClient(FakeTelegramClient):
            fail_notice = True

            def send_message(self, chat_id, text):
                if self.fail_notice and "delivery run 3/6" in text:
                    raise TelegramAPIError("temporary outage", retryable=True)
                return super().send_message(chat_id, text)

        client = FailingThirdRunNoticeClient([
            _update(update_id=92, text="/build Preserve retry backoff"),
        ])
        self.state.bind_bot_identity(99, "monday_test_bot")
        service = TelegramBotService(self.monday, client, self.state, config)
        service.bind_bot_identity(client.get_me())
        delivery_id = service._delivery_id(92)
        transient = {
            "delivery_id": delivery_id,
            "task_id": "TASK-0001",
            "status": "failed",
            "phase": "failed",
            "success": False,
            "branch": "",
            "commit_sha": "",
            "pr_url": "",
            "pushed": False,
            "changed_files": [],
            "attempts": [],
            "failure_code": "provider-timeout",
            "failure_class": "transient",
            "retry_after_seconds": 0,
            "message": "temporary outage",
        }
        self.monday.delivery_runs[delivery_id] = dict(transient)
        started = time.time()
        runner = TelegramRunner(client, service, self.state, poll_timeout=1)
        with patch("telegram_bot.state.time.time", return_value=started):
            self.assertEqual(runner.run_once(), 1)
        self.assertTrue(self.state.completed_job(92)["delivery_final_sent"])
        self.assertEqual(self.state.pending_delivery_notice_count, 0)

        retry_two = f"{delivery_id}-r2"
        self.monday.delivery_runs[retry_two] = {
            **transient,
            "delivery_id": retry_two,
        }
        worker = TelegramJobWorker(
            service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )
        with patch("telegram_bot.state.time.time", return_value=started + 61):
            self.assertTrue(worker.run_delivery_retry_once())

        retry = self.state.delivery_retry(92)
        self.assertEqual(retry["delivery_id"], f"{delivery_id}-r3")
        self.assertEqual(retry["failure_count"], 0)
        self.assertEqual(retry["next_attempt_at"], started + 361)
        self.assertFalse(self.state.completed_job(92)["delivery_final_sent"])
        self.assertEqual(self.state.pending_delivery_notice_count, 1)
        self.assertFalse(any(
            "delivery run 3/6" in text for _chat_id, text in client.sent
        ))

        with patch("telegram_bot.state.time.time", return_value=started + 62):
            self.assertTrue(worker.run_delivery_notice_once())

        durable_retry = self.state.delivery_retry(92)
        self.assertEqual(durable_retry["failure_count"], 0)
        self.assertEqual(durable_retry["next_attempt_at"], started + 361)
        payload = json.loads(config.state_path.read_text(encoding="utf-8"))
        notice = payload["delivery_notices"]["92"]
        self.assertEqual(notice["phase"], "retrying")
        self.assertEqual(notice["failure_count"], 1)
        self.assertEqual(notice["next_attempt_at"], started + 92)

        restarted_state = TelegramState(config.state_path)
        restarted_service = TelegramBotService(
            self.monday,
            client,
            restarted_state,
            config,
        )
        restarted_worker = TelegramJobWorker(
            restarted_service,
            restarted_state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )
        client.fail_notice = False
        with patch("telegram_bot.state.time.time", return_value=started + 92):
            self.assertTrue(restarted_worker.run_delivery_notice_once())
        self.assertEqual(restarted_state.pending_delivery_notice_count, 0)
        self.assertEqual(
            sum("delivery run 3/6" in text for _chat_id, text in client.sent),
            1,
        )
        with patch("telegram_bot.state.time.time", return_value=started + 122):
            self.assertFalse(restarted_worker.run_delivery_retry_once())
        self.assertEqual(
            [call[1]["delivery_id"] for call in self.monday.build_calls],
            [delivery_id, retry_two],
        )

    def test_noncanonical_retry_pr_signal_never_allocates_fresh_identity(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )
        self.state.bind_bot_identity(99, "monday_test_bot")
        service = TelegramBotService(self.monday, self.client, self.state, config)
        service.bind_bot_identity(self.client.get_me())
        update = _update(update_id=100, text="/build Reconcile uncertain publication")
        delivery_id = service._delivery_id(100)
        transient = {
            "delivery_id": delivery_id,
            "task_id": "TASK-0001",
            "status": "failed",
            "phase": "failed",
            "success": False,
            "branch": "",
            "commit_sha": "",
            "pr_url": "",
            "pushed": False,
            "changed_files": [],
            "attempts": [],
            "failure_code": "provider-timeout",
            "failure_class": "transient",
            "retry_after_seconds": 0,
            "message": "temporary outage",
        }
        self.monday.delivery_runs[delivery_id] = dict(transient)
        self.client.updates = [update]
        started = time.time()
        runner = TelegramRunner(
            self.client,
            service,
            self.state,
            poll_timeout=1,
        )
        with patch("telegram_bot.state.time.time", return_value=started):
            self.assertEqual(runner.run_once(), 1)

        retry_id = f"{delivery_id}-r2"
        self.monday.delivery_runs[retry_id] = {
            **transient,
            "delivery_id": retry_id,
            "pr_url": "git@github.com:example/mondayos#55",
        }
        worker = TelegramJobWorker(
            service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )
        with patch("telegram_bot.state.time.time", return_value=started + 61):
            self.assertTrue(worker.run_delivery_retry_once())

        completed = self.state.completed_job(100)
        self.assertEqual(self.state.delivery_retry(100), {})
        self.assertEqual(completed["delivery_retry"]["attempt"], 2)
        self.assertTrue(completed["delivery_result"]["publication_uncertain"])
        self.assertEqual(completed["delivery_result"]["pr_url"], "")
        self.assertFalse(any(
            call[1]["delivery_id"] == f"{delivery_id}-r3"
            for call in self.monday.build_calls
        ))

    def test_intermediate_rate_limit_pauses_notice_not_next_build(self):
        root_delivery_id = self._seed_delivery_retry(101, now=100.0)
        self.state.acknowledge(101)

        class RateLimitedNoticeClient(FakeTelegramClient):
            def send_message(self, chat_id, text):
                if "delivery run 3/6" in text:
                    raise TelegramRateLimitError(3_600)
                return super().send_message(chat_id, text)

        retry_two = f"{root_delivery_id}-r2"
        self.monday.delivery_runs[retry_two] = {
            "delivery_id": retry_two,
            "task_id": "TASK-0001",
            "status": "failed",
            "phase": "failed",
            "success": False,
            "branch": "",
            "commit_sha": "",
            "pr_url": "",
            "pushed": False,
            "changed_files": [],
            "attempts": [],
            "failure_code": "provider-timeout",
            "failure_class": "transient",
            "retry_after_seconds": 0,
            "message": "temporary outage",
        }
        client = RateLimitedNoticeClient()
        service = TelegramBotService(self.monday, client, self.state, self.config)
        worker = TelegramJobWorker(
            service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )

        with (
            patch("telegram_bot.state.time.time", return_value=161.0),
            patch("telegram_bot.service.time.time", return_value=161.0),
        ):
            self.assertTrue(worker.run_delivery_retry_once())

        retry = self.state.delivery_retry(101)
        self.assertEqual(retry["delivery_id"], f"{root_delivery_id}-r3")
        self.assertEqual(retry["failure_count"], 0)
        self.assertEqual(retry["next_attempt_at"], 461.0)

        with patch("telegram_bot.state.time.time", return_value=161.0):
            self.assertTrue(worker.run_delivery_notice_once())

        unchanged = self.state.delivery_retry(101)
        self.assertEqual(unchanged["failure_count"], 0)
        self.assertEqual(unchanged["next_attempt_at"], 461.0)
        notice = json.loads(
            self.config.state_path.read_text(encoding="utf-8")
        )["delivery_notices"]["101"]
        self.assertEqual(notice["next_attempt_at"], 3_761.0)

    def test_expired_notification_retry_uses_notification_due_not_old_deadline(self):
        started = 1_000.0
        self._seed_delivery_retry(93, now=started, delay_seconds=60)
        self.state.acknowledge(93)
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )

        class FailingExpiryClient(FakeTelegramClient):
            def send_message(self, chat_id, text):
                if "within 24 hours" in text:
                    raise TelegramAPIError("temporary outage", retryable=True)
                return super().send_message(chat_id, text)

        client = FailingExpiryClient()
        service = TelegramBotService(self.monday, client, self.state, config)
        worker = TelegramJobWorker(
            service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )
        expired_at = started + 86_401
        with (
            patch("telegram_bot.state.time.time", return_value=expired_at),
            patch("telegram_bot.service.time.time", return_value=expired_at),
        ):
            self.assertTrue(worker.run_delivery_retry_once())

        retry = self.state.delivery_retry(93)
        self.assertEqual(retry["outcome_phase"], "expired")
        self.assertEqual(retry["failure_count"], 1)
        self.assertEqual(retry["next_attempt_at"], expired_at + 60)
        self.assertIsNone(self.state.claim_due_delivery_retry(now=expired_at + 1))
        self.assertEqual(self.monday.build_calls, [])

    def test_started_retry_reconciles_same_identity_after_expiry(self):
        started = 100.0
        root_delivery_id = self._seed_delivery_retry(102, now=started)
        self.state.acknowledge(102)
        retry_id = f"{root_delivery_id}-r2"

        class CrashAfterPublicationMonday(FakeMonday):
            crash = True

            def build(self, action, **kwargs):
                response = super().build(action, **kwargs)
                if action == "run" and self.crash:
                    self.crash = False
                    raise SystemExit("crash after publication")
                return response

        monday = CrashAfterPublicationMonday()
        service = TelegramBotService(
            monday,
            self.client,
            self.state,
            self.config,
        )
        worker = TelegramJobWorker(
            service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )
        with (
            patch("telegram_bot.state.time.time", return_value=started + 61),
            patch("telegram_bot.service.time.time", return_value=started + 61),
            self.assertRaisesRegex(SystemExit, "crash after publication"),
        ):
            worker.run_delivery_retry_once()

        retry = self.state.delivery_retry(102)
        self.assertTrue(retry["attempt_started"])
        self.assertEqual(retry["delivery_id"], retry_id)

        restarted_state = TelegramState(self.config.state_path)
        restarted_service = TelegramBotService(
            monday,
            self.client,
            restarted_state,
            self.config,
        )
        restarted_worker = TelegramJobWorker(
            restarted_service,
            restarted_state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )
        after_expiry = started + 86_401
        with (
            patch("telegram_bot.state.time.time", return_value=after_expiry),
            patch("telegram_bot.service.time.time", return_value=after_expiry),
        ):
            self.assertTrue(restarted_worker.run_delivery_retry_once())

        completed = restarted_state.completed_job(102)
        self.assertEqual(
            [call[1]["delivery_id"] for call in monday.build_calls],
            [retry_id, retry_id],
        )
        self.assertEqual(completed["delivery_status"], "pr-open")
        self.assertEqual(
            completed["delivery_result"]["pr_url"],
            "https://github.com/example/mondayos/pull/55",
        )
        self.assertEqual(completed["delivery_retry"]["phase"], "completed")
        self.assertEqual(completed["follow_up"]["phase"], "pending")
        self.assertEqual(restarted_state.delivery_retry(102), {})

    def test_post_expiry_nonterminal_reconciliation_stops_as_uncertain(self):
        started = 100.0
        root_delivery_id = self._seed_delivery_retry(103, now=started)
        self.state.acknowledge(103)
        retry_id = f"{root_delivery_id}-r2"
        claimed = self.state.claim_due_delivery_retry(now=started + 61)
        self.assertIsNotNone(claimed)
        self.state.start_delivery_retry_attempt(103, delivery_id=retry_id)
        self.state.defer_delivery_retry(
            103,
            delay_seconds=60,
            now=started + 61,
        )
        self.monday.delivery_runs[retry_id] = {
            "delivery_id": retry_id,
            "task_id": "TASK-0001",
            "status": "publication-pending",
            "phase": "pull-request",
            "success": False,
            "branch": "codex/reconcile-exact-delivery",
            "commit_sha": "",
            "pr_url": "",
            "changed_files": [],
            "attempts": [],
            "message": "untrusted backend diagnostic",
        }
        service = TelegramBotService(
            self.monday,
            self.client,
            self.state,
            self.config,
        )
        worker = TelegramJobWorker(
            service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )
        after_expiry = started + 86_401

        with (
            patch("telegram_bot.state.time.time", return_value=after_expiry),
            patch("telegram_bot.service.time.time", return_value=after_expiry),
        ):
            self.assertTrue(worker.run_delivery_retry_once())

        completed = self.state.completed_job(103)
        self.assertEqual(self.state.delivery_retry(103), {})
        self.assertEqual(completed["delivery_retry"]["phase"], "uncertain")
        self.assertEqual(completed["delivery_status"], "publication-pending")
        self.assertTrue(completed["delivery_result"]["publication_uncertain"])
        self.assertEqual(
            completed["delivery_result"]["failure_code"],
            "reconciliation-unresolved",
        )
        self.assertEqual(
            [call[1]["delivery_id"] for call in self.monday.build_calls],
            [retry_id],
        )
        self.assertFalse(any(
            call[1]["delivery_id"] == f"{root_delivery_id}-r3"
            for call in self.monday.build_calls
        ))
        sent = "\n".join(text for _chat, text in self.client.sent)
        self.assertIn("may have published changes", sent)
        self.assertNotIn("untrusted backend diagnostic", sent)

    def test_post_expiry_exception_stages_uncertain_without_hot_loop(self):
        started = 100.0
        root_delivery_id = self._seed_delivery_retry(104, now=started)
        self.state.acknowledge(104)
        retry_id = f"{root_delivery_id}-r2"
        claimed = self.state.claim_due_delivery_retry(now=started + 61)
        self.assertIsNotNone(claimed)
        self.state.start_delivery_retry_attempt(104, delivery_id=retry_id)
        self.state.defer_delivery_retry(
            104,
            delay_seconds=60,
            now=started + 61,
        )
        secret = "private backend exception detail"

        class RaisingMonday(FakeMonday):
            def build(self, action, **kwargs):
                self.build_calls.append((action, dict(kwargs)))
                raise RuntimeError(secret)

        monday = RaisingMonday()
        service = TelegramBotService(monday, self.client, self.state, self.config)
        worker = TelegramJobWorker(
            service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )
        after_expiry = started + 86_401
        with (
            patch("telegram_bot.state.time.time", return_value=after_expiry),
            patch("telegram_bot.service.time.time", return_value=after_expiry),
        ):
            self.assertTrue(worker.run_delivery_retry_once())
            staged = self.state.delivery_retry(104)
            self.assertEqual(staged["outcome_phase"], "uncertain")
            self.assertTrue(worker.run_delivery_retry_once())

        completed = self.state.completed_job(104)
        self.assertEqual(self.state.delivery_retry(104), {})
        self.assertEqual(completed["delivery_retry"]["phase"], "uncertain")
        self.assertEqual(
            [call[1]["delivery_id"] for call in monday.build_calls],
            [retry_id],
        )
        persisted = self.config.state_path.read_text(encoding="utf-8")
        sent = "\n".join(text for _chat, text in self.client.sent)
        self.assertNotIn(secret, persisted)
        self.assertNotIn(secret, sent)

    def test_post_expiry_bare_api_failure_is_not_authoritative(self):
        started = 100.0
        root_delivery_id = self._seed_delivery_retry(106, now=started)
        self.state.acknowledge(106)
        retry_id = f"{root_delivery_id}-r2"
        self.assertIsNotNone(
            self.state.claim_due_delivery_retry(now=started + 61)
        )
        self.state.start_delivery_retry_attempt(106, delivery_id=retry_id)
        self.state.defer_delivery_retry(
            106,
            delay_seconds=60,
            now=started + 61,
        )
        secret = "private control-plane failure"

        class BareFailureMonday(FakeMonday):
            def build(self, action, **kwargs):
                self.build_calls.append((action, dict(kwargs)))
                return SimpleNamespace(
                    action=action,
                    success=False,
                    message=secret,
                    data={},
                )

        monday = BareFailureMonday()
        service = TelegramBotService(monday, self.client, self.state, self.config)
        worker = TelegramJobWorker(
            service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )
        after_expiry = started + 86_401
        with (
            patch("telegram_bot.state.time.time", return_value=after_expiry),
            patch("telegram_bot.service.time.time", return_value=after_expiry),
        ):
            self.assertTrue(worker.run_delivery_retry_once())

        completed = self.state.completed_job(106)
        self.assertEqual(self.state.delivery_retry(106), {})
        self.assertEqual(completed["delivery_retry"]["phase"], "uncertain")
        self.assertEqual(completed["delivery_status"], "publication-pending")
        self.assertNotEqual(completed["delivery_status"], "failed")
        persisted = self.config.state_path.read_text(encoding="utf-8")
        sent = "\n".join(text for _chat, text in self.client.sent)
        self.assertNotIn(secret, persisted)
        self.assertNotIn(secret, sent)

    def test_uncertain_outcome_notification_failures_release_retry_capacity(self):
        started = 100.0
        root_delivery_id = self._seed_delivery_retry(105, now=started)
        self.state.acknowledge(105)
        retry_id = f"{root_delivery_id}-r2"
        self.assertIsNotNone(
            self.state.claim_due_delivery_retry(now=started + 61)
        )
        self.state.start_delivery_retry_attempt(105, delivery_id=retry_id)
        after_expiry = started + 86_401
        self.state.start_delivery_retry_attempt(
            105,
            delivery_id=retry_id,
            post_expiry_reconciliation=True,
        )
        self.state.defer_delivery_retry(
            105,
            delay_seconds=60,
            now=after_expiry,
        )

        class FailingUncertainNoticeClient(FakeTelegramClient):
            def send_message(self, chat_id, text):
                if "may have published changes" in text:
                    raise TelegramAPIError("temporary outage", retryable=True)
                return super().send_message(chat_id, text)

        client = FailingUncertainNoticeClient()
        service = TelegramBotService(self.monday, client, self.state, self.config)
        worker = TelegramJobWorker(
            service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )
        for offset in (0, 60, 120):
            with patch(
                "telegram_bot.state.time.time",
                return_value=after_expiry + offset,
            ):
                self.assertTrue(
                    worker.run_delivery_retry_once(),
                    f"notification retry was not due at offset {offset}",
                )

        completed = self.state.completed_job(105)
        self.assertEqual(self.state.delivery_retry(105), {})
        self.assertEqual(
            completed["delivery_retry"]["phase"],
            "notification-failed",
        )
        self.assertEqual(
            completed["delivery_retry"]["outcome_phase"],
            "uncertain",
        )
        self.assertFalse(completed["delivery_final_sent"])

    def test_persisted_retry_rejects_changed_source_binding(self):
        self._seed_delivery_retry(94)
        self.state.acknowledge(94)
        payload = json.loads(self.config.state_path.read_text(encoding="utf-8"))
        payload["delivery_retries"]["94"]["project_name"] = "another-project"
        self.config.state_path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(TelegramStateError, "Invalid Telegram state"):
            TelegramState(self.config.state_path)

    def test_retry_route_change_cannot_bypass_bound_private_notice(self):
        self._seed_delivery_retry(95, now=100.0)
        self.state.acknowledge(95)
        payload = json.loads(self.config.state_path.read_text(encoding="utf-8"))
        payload["delivery_retries"]["95"]["chat_id"] = 999
        payload["completed_jobs"]["95"]["chat_id"] = 999
        self.config.state_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(TelegramStateError, "Invalid Telegram state"):
            TelegramState(self.config.state_path)

    def test_expired_retry_route_change_cannot_bypass_bound_private_notice(self):
        self._seed_delivery_retry(99, now=100.0)
        self.state.acknowledge(99)
        payload = json.loads(self.config.state_path.read_text(encoding="utf-8"))
        payload["delivery_retries"]["99"]["chat_id"] = 999
        payload["completed_jobs"]["99"]["chat_id"] = 999
        self.config.state_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(TelegramStateError, "Invalid Telegram state"):
            TelegramState(self.config.state_path)

    def test_pending_fifo_delivery_does_not_starve_due_retry_lane(self):
        self._seed_delivery_retry(96, now=1.0, delay_seconds=1.0)
        self.state.acknowledge(96)
        self.state.enqueue(97, _update(update_id=97, text="/help"))
        self._complete_acceptance_receipt(97)
        stop = threading.Event()
        retried = []

        class PendingService:
            def announce_acceptance_receipt(self, _notice):
                return True

            def handle_update(self, _update):
                raise TelegramUpdatePendingError("still running")

            def retry_delivery(self, update_id, _retry):
                retried.append(update_id)
                stop.set()

        worker = TelegramJobWorker(
            PendingService(),
            self.state,
            stop_event=stop,
            wake_event=threading.Event(),
        )

        worker.run_forever()

        self.assertEqual(retried, [96])
        self.assertEqual(self.state.job(97)["phase"], "waiting")

    def test_permanent_retry_diagnostic_is_not_persisted_or_sent(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )
        self.state.bind_bot_identity(99, "monday_test_bot")
        service = TelegramBotService(self.monday, self.client, self.state, config)
        service.bind_bot_identity(self.client.get_me())
        update = _update(update_id=98, text="/build Redact provider diagnostics")
        delivery_id = service._delivery_id(98)
        root_failure = {
            "delivery_id": delivery_id,
            "task_id": "TASK-0001",
            "status": "failed",
            "phase": "failed",
            "success": False,
            "branch": "",
            "commit_sha": "",
            "pr_url": "",
            "pushed": False,
            "changed_files": [],
            "attempts": [],
            "failure_code": "provider-timeout",
            "failure_class": "transient",
            "retry_after_seconds": 0,
            "message": "first private diagnostic",
        }
        self.monday.delivery_runs[delivery_id] = root_failure
        self.client.updates = [update]
        started = time.time()
        runner = TelegramRunner(
            self.client,
            service,
            self.state,
            poll_timeout=1,
        )
        with patch("telegram_bot.state.time.time", return_value=started):
            self.assertEqual(runner.run_once(), 1)

        retry_id = f"{delivery_id}-r2"
        secret = "vendor stack trace with secret internals"
        self.monday.delivery_runs[retry_id] = {
            **root_failure,
            "delivery_id": retry_id,
            "failure_code": "provider-invalid-response",
            "failure_class": "permanent",
            "retry_after_seconds": 0,
            "message": secret,
        }
        worker = TelegramJobWorker(
            service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )
        with patch("telegram_bot.state.time.time", return_value=started + 61):
            self.assertTrue(worker.run_delivery_retry_once())

        persisted = self.config.state_path.read_text(encoding="utf-8")
        sent = "\n".join(text for _chat, text in self.client.sent)
        self.assertNotIn(secret, persisted)
        self.assertNotIn(secret, sent)

    def test_enqueue_is_idempotent_and_rejects_changed_duplicate_content(self):
        request = _update(update_id=10, text="Build exactly once")

        self.assertEqual(self.state.enqueue(10, request), (True, 1))
        self.assertEqual(self.state.enqueue(10, request), (False, 1))
        self.assertEqual(self.state.pending_count, 1)
        self.assertEqual(self.state.next_offset, 11)

        changed = _update(update_id=10, text="Build something different")
        with self.assertRaisesRegex(TelegramStateError, "does not match"):
            self.state.enqueue(10, changed)
        with self.assertRaisesRegex(TelegramStateError, "queue key"):
            self.state.enqueue(11, request)

        self.state.acknowledge(10)
        self.assertEqual(self.state.enqueue(10, request), (False, 0))
        self.assertEqual(self.state.pending_count, 0)

    def test_enqueue_atomically_creates_request_free_acceptance_receipt(self):
        secret = "private objective must not enter the receipt outbox"

        self.assertEqual(
            self.state.enqueue(15, _update(update_id=15, text=secret)),
            (True, 1),
        )

        payload = json.loads(self.config.state_path.read_text(encoding="utf-8"))
        receipt = payload["acceptance_receipts"]["15"]
        self.assertEqual(payload["version"], 8)
        self.assertEqual(payload["last_update_id"], 15)
        self.assertEqual(receipt["kind"], "acceptance-receipt")
        self.assertEqual(receipt["phase"], "pending")
        self.assertEqual(receipt["origin_user_id"], 7)
        self.assertEqual(receipt["origin_chat_id"], 7)
        self.assertEqual(receipt["origin_chat_type"], "private")
        self.assertEqual(receipt["request_digest"], payload["jobs"]["15"]["request_digest"])
        self.assertNotIn("request", receipt)
        self.assertNotIn(secret, json.dumps(receipt))
        self.assertEqual(self.state.pending_acceptance_receipt_count, 1)
        self.assertIsNone(self.state.claim_next())

    def test_oldest_unresolved_receipt_blocks_newer_confirmed_job(self):
        self.state.enqueue(16, _update(update_id=16, text="first"))
        self.state.enqueue(17, _update(update_id=17, text="second"))
        self.state.complete_acceptance_receipt(17, delivered=True)

        self.assertIsNone(self.state.claim_next())

        self.state.complete_acceptance_receipt(16, delivered=True)
        claimed = self.state.claim_next()
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed[0], 16)

    def test_acceptance_receipt_backoff_is_bounded_and_survives_restart(self):
        self.state.enqueue(18, _update(update_id=18, text="retry receipt"))
        self.state.defer_acceptance_receipt(
            18,
            delay_seconds=9,
            now=100.0,
        )

        self.assertEqual(self.state.due_acceptance_receipt_ids(now=108.0), [])
        restarted = TelegramState(self.config.state_path)
        self.assertEqual(
            restarted.due_acceptance_receipt_ids(now=109.0),
            [18],
        )
        payload = json.loads(self.config.state_path.read_text(encoding="utf-8"))
        receipt = payload["acceptance_receipts"]["18"]
        self.assertEqual(receipt["failure_count"], 1)
        self.assertEqual(receipt["deferred_at"], 100.0)
        self.assertEqual(receipt["next_attempt_at"], 109.0)

    def test_legacy_active_job_migrates_to_pending_receipt_without_rewrite(self):
        path = self.root / "legacy-receipt-state.json"
        state = TelegramState(path)
        state.bind_bot_identity(99, "monday_test_bot")
        state.enqueue(19, _update(update_id=19, text="legacy active work"))
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["version"] = 5
        payload.pop("acceptance_receipts")
        for name in _ORIGIN_KEYS:
            payload["jobs"]["19"].pop(name)
        path.write_text(json.dumps(payload), encoding="utf-8")

        migrated = TelegramState(path)

        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8"))["version"],
            5,
        )
        self.assertEqual(migrated.pending_acceptance_receipt_count, 1)
        self.assertEqual(migrated.due_acceptance_receipt_ids(), [19])
        self.assertIsNone(migrated.claim_next())
        migrated.bind_bot_identity(99, "monday_test_bot")
        persisted = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["version"], 8)
        self.assertIn("19", persisted["acceptance_receipts"])

    def test_legacy_replay_atomically_attaches_bound_receipt(self):
        path = self.root / "legacy-replay-state.json"
        path.write_text(json.dumps({
            "version": 5,
            "bot_id": 99,
            "bot_username": "monday_test_bot",
            "last_update_id": 20,
            "cancellation_sequence": 0,
            "jobs": {
                "21": {"task_id": "TASK-0001"},
            },
            "completed_jobs": {},
            "failed_jobs": {},
            "delivery_retries": {},
        }), encoding="utf-8")
        state = TelegramState(path)

        self.assertEqual(
            state.enqueue(21, _update(update_id=21, text="resume checkpoint")),
            (True, 1),
        )

        payload = json.loads(path.read_text(encoding="utf-8"))
        receipt = payload["acceptance_receipts"]["21"]
        self.assertEqual(payload["version"], 8)
        self.assertEqual(receipt["phase"], "sent")
        self.assertEqual(
            receipt["request_digest"],
            payload["jobs"]["21"]["request_digest"],
        )
        claimed = state.claim_next()
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed[0], 21)

    def test_legacy_started_job_is_not_discarded_by_receipt_migration(self):
        path = self.root / "legacy-started-receipt-state.json"
        state = TelegramState(path)
        state.bind_bot_identity(99, "monday_test_bot")
        state.enqueue(22, _update(update_id=22, text="recover started work"))
        state.set_job(22, phase="waiting", task_id="TASK-0001")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["version"] = 5
        payload.pop("acceptance_receipts")
        path.write_text(json.dumps(payload), encoding="utf-8")

        migrated = TelegramState(path)

        self.assertEqual(migrated.pending_acceptance_receipt_count, 0)
        self.assertEqual(migrated.due_acceptance_receipt_ids(), [])
        claimed = migrated.claim_next()
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed[0], 22)
        self.assertEqual(migrated.job(22)["task_id"], "TASK-0001")

    def test_current_receipt_tampering_fails_closed_on_load(self):
        def changed_route(payload):
            payload["acceptance_receipts"]["20"]["origin_chat_id"] = 999

        def changed_digest(payload):
            payload["acceptance_receipts"]["20"]["request_digest"] = "a" * 64

        def changed_key(payload):
            payload["acceptance_receipts"]["21"] = (
                payload["acceptance_receipts"].pop("20")
            )

        def missing_receipt(payload):
            payload["acceptance_receipts"].pop("20")

        for label, mutate in (
            ("route", changed_route),
            ("digest", changed_digest),
            ("key", changed_key),
            ("missing", missing_receipt),
        ):
            with self.subTest(label=label):
                path = self.root / f"tampered-receipt-{label}.json"
                state = TelegramState(path)
                state.enqueue(20, _update(update_id=20, text="bound receipt"))
                payload = json.loads(path.read_text(encoding="utf-8"))
                mutate(payload)
                path.write_text(json.dumps(payload), encoding="utf-8")

                with self.assertRaises(TelegramStateError):
                    TelegramState(path)

    def test_pending_phase_cannot_be_forged_for_started_job(self):
        path = self.root / "tampered-started-receipt.json"
        state = TelegramState(path)
        state.enqueue(23, _update(update_id=23, text="started checkpoint"))
        state.complete_acceptance_receipt(23, delivered=True)
        claimed = state.claim_next()
        self.assertIsNotNone(claimed)
        state.set_job(23, phase="waiting", task_id="TASK-0001")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["acceptance_receipts"]["23"]["phase"] = "pending"
        path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(TelegramStateError):
            TelegramState(path)

    def test_boolean_request_identity_with_matching_digests_fails_closed(self):
        path = self.root / "tampered-boolean-request-identity.json"
        state = TelegramState(path)
        state.enqueue(1, _update(update_id=1, text="strict identity"))
        payload = json.loads(path.read_text(encoding="utf-8"))
        record = payload["jobs"]["1"]
        record["request"]["update_id"] = True
        canonical = json.dumps(
            record["request"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        record["request_digest"] = digest
        payload["acceptance_receipts"]["1"]["request_digest"] = digest
        path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(TelegramStateError):
            TelegramState(path)

    def test_job_ids_are_stable_and_strictly_validated(self):
        self.assertEqual(job_id(42), "JOB-42")
        self.assertEqual(parse_job_id("JOB-42"), 42)
        self.assertEqual(parse_job_id(" job-42 "), 42)

        for invalid in ("42", "JOB-", "JOB-042", "JOB--1", True, -1):
            with self.subTest(invalid=invalid):
                if isinstance(invalid, int):
                    with self.assertRaises(TelegramStateError):
                        job_id(invalid)
                else:
                    with self.assertRaises(ValueError):
                        parse_job_id(invalid)

    def test_full_inbox_writes_request_free_terminal_tombstone_atomically(self):
        with patch("telegram_bot.state.MAX_PENDING_JOBS", 2):
            self.assertEqual(
                self.state.enqueue(10, _update(update_id=10, text="first")),
                (True, 1),
            )
            self.assertEqual(
                self.state.enqueue(11, _update(update_id=11, text="second")),
                (True, 2),
            )
            rejected = _update(update_id=12, text="never persist full objective")
            self.assertEqual(self.state.enqueue(12, rejected), (False, 0))

            self.assertEqual(self.state.pending_count, 2)
            self.assertEqual(self.state.next_offset, 13)
            tombstone = self.state.completed_job(12)
            self.assertEqual(tombstone["phase"], "queue-full")
            self.assertEqual(tombstone["notice_status"], "pending")
            self.assertEqual(tombstone["notice_failure_count"], 0)
            self.assertEqual(tombstone["notice_deferred_at"], 0.0)
            self.assertEqual(tombstone["notice_next_attempt_at"], 0.0)
            self.assertEqual(tombstone["origin_user_id"], 7)
            self.assertEqual(tombstone["origin_chat_id"], 7)
            self.assertEqual(tombstone["origin_chat_type"], "private")
            self.assertNotIn("request", tombstone)
            self.assertNotIn(
                "never persist full objective",
                self.config.state_path.read_text(encoding="utf-8"),
            )

            # Exact replay is terminal; changed content cannot reuse the id.
            self.assertEqual(self.state.enqueue(12, rejected), (False, 0))
            self.assertEqual(self.state.next_offset, 13)
            with self.assertRaisesRegex(TelegramStateError, "does not match"):
                self.state.enqueue(
                    12,
                    _update(update_id=12, text="changed rejected objective"),
                )
            self.assertEqual(
                self.state.queue_full_notice(12),
                {"user_id": 7, "chat_id": 7, "chat_type": "private"},
            )
            self.state.complete_queue_full_notice(12, delivered=True)
            self.assertEqual(self.state.next_offset, 13)
            self.assertEqual(
                self.state.completed_job(12)["notice_status"],
                "sent",
            )
            self.assertTrue(
                _ORIGIN_KEYS.isdisjoint(self.state.completed_job(12))
            )
            self.assertNotIn(
                "notice_next_attempt_at",
                self.state.completed_job(12),
            )
            self.assertEqual(self.state.queue_full_notice(12), {})

    def test_full_inbox_keeps_minimal_group_origin_for_routing(self):
        group = _update(
            update_id=14,
            user_id=7,
            chat_id=-100,
            text="rejected group objective",
        )
        group["message"]["chat"]["type"] = "group"

        with patch("telegram_bot.state.MAX_PENDING_JOBS", 1):
            self.state.enqueue(13, _update(update_id=13, text="fills inbox"))
            self.assertEqual(self.state.enqueue(14, group), (False, 0))

        tombstone = self.state.completed_job(14)
        self.assertEqual(tombstone["origin_user_id"], 7)
        self.assertEqual(tombstone["origin_chat_id"], -100)
        self.assertEqual(tombstone["origin_chat_type"], "group")
        self.assertEqual(
            self.state.queue_full_notice(14),
            {"user_id": 7, "chat_id": -100, "chat_type": "group"},
        )
        self.assertNotIn("request", tombstone)
        self.assertNotIn(
            "rejected group objective",
            self.config.state_path.read_text(encoding="utf-8"),
        )

    def test_pending_queue_full_notice_survives_completed_history_eviction(self):
        with (
            patch("telegram_bot.state.MAX_PENDING_JOBS", 1),
            patch("telegram_bot.state._MAX_COMPLETED_JOBS", 1),
        ):
            self.state.acknowledge(1)
            self.state.enqueue(10, _update(update_id=10, text="fills capacity"))
            self.state.enqueue(11, _update(update_id=11, text="refused safely"))
            self.state.acknowledge(10)

        self.assertEqual(
            self.state.queue_full_notice(11),
            {"user_id": 7, "chat_id": 7, "chat_type": "private"},
        )
        self.assertEqual(
            self.state.completed_job(11)["notice_status"],
            "pending",
        )
        self.assertEqual(self.state.pending_queue_full_notice_count, 1)
        self.assertEqual(self.state.pending_queue_full_notice_ids(), [11])

    def test_queue_full_notice_backoff_is_bounded_and_survives_restart(self):
        with patch("telegram_bot.state.MAX_PENDING_JOBS", 1):
            self.state.enqueue(15, _update(update_id=15, text="fills capacity"))
            self.state.enqueue(16, _update(update_id=16, text="refused safely"))
        self.state.defer_queue_full_notice(
            16,
            delay_seconds=9,
            now=100.0,
        )

        self.assertEqual(self.state.due_queue_full_notice_ids(now=108.0), [])
        restarted = TelegramState(self.config.state_path)
        self.assertEqual(
            restarted.due_queue_full_notice_ids(now=109.0),
            [16],
        )
        notice = restarted.completed_job(16)
        self.assertEqual(notice["notice_status"], "retrying")
        self.assertEqual(notice["notice_failure_count"], 1)
        self.assertEqual(notice["notice_deferred_at"], 100.0)
        self.assertEqual(notice["notice_next_attempt_at"], 109.0)

    def test_legacy_queue_full_notice_fails_closed_and_advances_offset(self):
        path = self.root / "legacy-capacity-notice-state.json"
        state = TelegramState(path)
        state.bind_bot_identity(99, "monday_test_bot")
        with patch("telegram_bot.state.MAX_PENDING_JOBS", 1):
            state.enqueue(18, _update(update_id=18, text="fills capacity"))
            state.enqueue(19, _update(update_id=19, text="legacy refusal"))
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["version"] = 6
        payload["last_update_id"] = 18
        notice = payload["completed_jobs"]["19"]
        for name in (
            "notice_failure_count",
            "notice_deferred_at",
            "notice_next_attempt_at",
        ):
            notice.pop(name)
        path.write_text(json.dumps(payload), encoding="utf-8")

        migrated = TelegramState(path)

        persisted_before_mutation = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(persisted_before_mutation["version"], 6)
        self.assertEqual(persisted_before_mutation["last_update_id"], 18)
        self.assertEqual(migrated.next_offset, 20)
        self.assertEqual(migrated.due_queue_full_notice_ids(), [])
        self.assertEqual(
            migrated.completed_job(19)["notice_status"],
            "failed",
        )
        self.assertTrue(
            _ORIGIN_KEYS.isdisjoint(migrated.completed_job(19))
        )
        migrated.bind_bot_identity(99, "monday_test_bot")
        persisted = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["version"], 8)
        self.assertEqual(persisted["last_update_id"], 19)
        self.assertEqual(
            persisted["completed_jobs"]["19"]["notice_status"],
            "failed",
        )

    def test_current_queue_full_retry_metadata_tampering_fails_closed(self):
        path = self.root / "tampered-capacity-notice-state.json"
        state = TelegramState(path)
        with patch("telegram_bot.state.MAX_PENDING_JOBS", 1):
            state.enqueue(20, _update(update_id=20, text="fills capacity"))
            state.enqueue(21, _update(update_id=21, text="refused safely"))
        payload = json.loads(path.read_text(encoding="utf-8"))
        notice = payload["completed_jobs"]["21"]
        notice.update({
            "notice_status": "retrying",
            "notice_failure_count": 0,
            "notice_deferred_at": 100.0,
            "notice_next_attempt_at": 109.0,
        })
        path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(TelegramStateError):
            TelegramState(path)

    def test_active_queue_full_notice_rejects_unexpected_persisted_fields(self):
        path = self.root / "extra-active-capacity-field-state.json"
        state = TelegramState(path)
        with patch("telegram_bot.state.MAX_PENDING_JOBS", 1):
            state.enqueue(20, _update(update_id=20, text="fills capacity"))
            state.enqueue(21, _update(update_id=21, text="refused safely"))
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["completed_jobs"]["21"]["request"] = {
            "message": {"text": "must never survive in a notice"},
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(TelegramStateError):
            TelegramState(path)

    def test_terminal_queue_full_notice_rejects_unexpected_persisted_fields(self):
        path = self.root / "extra-terminal-capacity-field-state.json"
        state = TelegramState(path)
        with patch("telegram_bot.state.MAX_PENDING_JOBS", 1):
            state.enqueue(22, _update(update_id=22, text="fills capacity"))
            state.enqueue(23, _update(update_id=23, text="refused safely"))
        state.complete_queue_full_notice(23, delivered=False)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["completed_jobs"]["23"]["request_text"] = (
            "must never survive in a tombstone"
        )
        path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(TelegramStateError):
            TelegramState(path)

    def test_legacy_queue_full_migration_scrubs_unexpected_request_data(self):
        path = self.root / "legacy-capacity-secret-state.json"
        state = TelegramState(path)
        state.bind_bot_identity(99, "monday_test_bot")
        with patch("telegram_bot.state.MAX_PENDING_JOBS", 1):
            state.enqueue(24, _update(update_id=24, text="fills capacity"))
            state.enqueue(25, _update(update_id=25, text="legacy refusal"))
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["version"] = 6
        notice = payload["completed_jobs"]["25"]
        for name in (
            "notice_binding",
            "notice_failure_count",
            "notice_deferred_at",
            "notice_next_attempt_at",
        ):
            notice.pop(name)
        secret = "legacy request secret must be scrubbed"
        notice["request"] = {"message": {"text": secret}}
        path.write_text(json.dumps(payload), encoding="utf-8")

        migrated = TelegramState(path)

        self.assertNotIn("request", migrated.completed_job(25))
        self.assertIn(secret, path.read_text(encoding="utf-8"))
        migrated.bind_bot_identity(99, "monday_test_bot")
        persisted = path.read_text(encoding="utf-8")
        self.assertNotIn(secret, persisted)
        self.assertEqual(
            set(json.loads(persisted)["completed_jobs"]["25"]),
            {
                "phase",
                "request_digest",
                "notice_status",
            },
        )

    def test_queue_full_notice_rejects_private_route_redirect(self):
        path = self.root / "redirected-private-capacity-state.json"
        state = TelegramState(path)
        with patch("telegram_bot.state.MAX_PENDING_JOBS", 1):
            state.enqueue(26, _update(update_id=26, text="fills capacity"))
            state.enqueue(27, _update(update_id=27, text="refused safely"))
        payload = json.loads(path.read_text(encoding="utf-8"))
        notice = payload["completed_jobs"]["27"]
        notice["origin_user_id"] = 8
        notice["origin_chat_id"] = 8
        path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(TelegramStateError):
            TelegramState(path)

    def test_queue_full_notice_rejects_allowed_group_route_redirect(self):
        path = self.root / "redirected-group-capacity-state.json"
        state = TelegramState(path)
        with patch("telegram_bot.state.MAX_PENDING_JOBS", 1):
            state.enqueue(28, _update(update_id=28, text="fills capacity"))
            state.enqueue(29, _update(update_id=29, text="refused safely"))
        payload = json.loads(path.read_text(encoding="utf-8"))
        notice = payload["completed_jobs"]["29"]
        notice.update({
            "origin_user_id": 7,
            "origin_chat_id": -100,
            "origin_chat_type": "group",
        })
        path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(TelegramStateError):
            TelegramState(path)

    def test_queue_full_version_downgrade_cannot_authorize_redirect(self):
        path = self.root / "downgraded-redirect-capacity-state.json"
        state = TelegramState(path)
        state.bind_bot_identity(99, "monday_test_bot")
        with patch("telegram_bot.state.MAX_PENDING_JOBS", 1):
            state.enqueue(30, _update(update_id=30, text="fills capacity"))
            state.enqueue(31, _update(update_id=31, text="refused safely"))
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["version"] = 6
        payload["last_update_id"] = 30
        notice = payload["completed_jobs"]["31"]
        notice["origin_user_id"] = 8
        notice["origin_chat_id"] = 8
        for name in (
            "notice_binding",
            "notice_failure_count",
            "notice_deferred_at",
            "notice_next_attempt_at",
        ):
            notice.pop(name)
        path.write_text(json.dumps(payload), encoding="utf-8")

        migrated = TelegramState(path)

        self.assertEqual(migrated.next_offset, 32)
        self.assertEqual(migrated.queue_full_notice(31), {})
        self.assertEqual(
            migrated.completed_job(31)["notice_status"],
            "failed",
        )
        self.assertTrue(
            _ORIGIN_KEYS.isdisjoint(migrated.completed_job(31))
        )

    def test_terminal_queue_full_notice_recompletion_is_monotonic(self):
        with patch("telegram_bot.state.MAX_PENDING_JOBS", 1):
            self.state.enqueue(32, _update(update_id=32, text="fills capacity"))
            self.state.enqueue(33, _update(update_id=33, text="refused safely"))

        self.state.complete_queue_full_notice(33, delivered=False)
        self.state.complete_queue_full_notice(33, delivered=True)

        self.assertEqual(
            self.state.completed_job(33),
            {
                "phase": "queue-full",
                "request_digest": self.state.completed_job(33)["request_digest"],
                "notice_status": "failed",
            },
        )

    def test_pending_queue_full_notice_outbox_is_bounded(self):
        with (
            patch("telegram_bot.state.MAX_PENDING_JOBS", 1),
            patch("telegram_bot.state._MAX_ACTIVE_QUEUE_FULL_NOTICES", 2),
        ):
            self.state.enqueue(25, _update(update_id=25, text="fills capacity"))
            self.state.enqueue(26, _update(update_id=26, text="first refusal"))
            self.state.enqueue(27, _update(update_id=27, text="second refusal"))
            self.state.enqueue(28, _update(update_id=28, text="bounded refusal"))

        self.assertEqual(self.state.next_offset, 29)
        self.assertEqual(self.state.pending_queue_full_notice_count, 2)
        self.assertEqual(self.state.pending_queue_full_notice_ids(), [26, 27])
        bounded = self.state.completed_job(28)
        self.assertEqual(bounded["notice_status"], "failed")
        self.assertTrue(_ORIGIN_KEYS.isdisjoint(bounded))
        self.assertNotIn("notice_next_attempt_at", bounded)
        self.assertEqual(self.state.queue_full_notice(28), {})

    def test_existing_over_capacity_inbox_loads_and_drains(self):
        with patch("telegram_bot.state.MAX_PENDING_JOBS", 4):
            for update_id in (20, 21, 22):
                self.assertTrue(self.state.enqueue(
                    update_id,
                    _update(update_id=update_id, text=f"request {update_id}"),
                )[0])
                self._complete_acceptance_receipt(update_id)

        with patch("telegram_bot.state.MAX_PENDING_JOBS", 2):
            reloaded = TelegramState(self.config.state_path)
            self.assertEqual(reloaded.pending_count, 3)
            self.assertEqual(
                reloaded.enqueue(23, _update(update_id=23, text="still full")),
                (False, 0),
            )
            reloaded.complete_queue_full_notice(23, delivered=True)
            claimed = reloaded.claim_next()
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed[0], 20)
            reloaded.acknowledge(20)
            claimed = reloaded.claim_next()
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed[0], 21)
            reloaded.acknowledge(21)
            self.assertEqual(reloaded.pending_count, 1)
            self.assertEqual(
                reloaded.enqueue(24, _update(update_id=24, text="capacity freed")),
                (True, 2),
            )

    def test_queue_summary_is_origin_scoped_and_contains_no_request_text(self):
        secret = "private objective that must not appear in queue summaries"
        self.state.enqueue(30, _update(update_id=30, text=secret))
        self.state.enqueue(
            31,
            _update(update_id=31, user_id=8, chat_id=8, text="someone else's job"),
        )
        self.state.enqueue(32, _update(update_id=32, text="later own job"))

        own = self.state.queue_summary(7, 7)

        self.assertEqual([row["job_id"] for row in own], ["JOB-30", "JOB-32"])
        self.assertEqual([row["position"] for row in own], [1, 3])
        self.assertTrue(all(row["phase"] == "queued" for row in own))
        self.assertTrue(all(row["cancellable"] for row in own))
        self.assertEqual(
            set().union(*(row.keys() for row in own)),
            {"job_id", "phase", "position", "cancellable"},
        )
        self.assertNotIn(secret, json.dumps(own))
        self.assertEqual(
            [row["job_id"] for row in self.state.queue_summary(8, 8)],
            ["JOB-31"],
        )
        self.assertEqual(self.state.queue_summary(9, 9), [])

    def test_private_controls_manage_own_group_jobs_without_exposing_others(self):
        own_group = _update(
            update_id=33,
            user_id=7,
            chat_id=-100,
            text="own group objective",
        )
        own_group["message"]["chat"]["type"] = "supergroup"
        foreign_group = _update(
            update_id=34,
            user_id=8,
            chat_id=-100,
            text="foreign group objective",
        )
        foreign_group["message"]["chat"]["type"] = "group"

        self.state.enqueue(33, own_group)
        self.state.enqueue(34, foreign_group)

        # The request-free receipt outbox duplicates only the minimal origin
        # and binds it back to the canonical request before every delivery.
        self.assertEqual(
            {key: self.state.job(33)[key] for key in _ORIGIN_KEYS},
            {
                "origin_user_id": 7,
                "origin_chat_id": -100,
                "origin_chat_type": "supergroup",
            },
        )
        summary = self.state.queue_summary(7, 7)
        self.assertEqual([row["job_id"] for row in summary], ["JOB-33"])
        self.assertTrue(summary[0]["cancellable"])
        self.assertEqual(
            self.state.cancel_queued("JOB-34", user_id=7, chat_id=7),
            {"status": "not-found", "job_id": "JOB-34"},
        )
        self.assertEqual(
            self.state.cancel_queued("JOB-33", user_id=7, chat_id=7)["status"],
            "cancelled",
        )
        tombstone = self.state.completed_job(33)
        self.assertEqual(tombstone["origin_user_id"], 7)
        self.assertEqual(tombstone["origin_chat_id"], -100)
        self.assertEqual(tombstone["origin_chat_type"], "supergroup")

    def test_invalid_positive_group_chat_id_cannot_be_managed(self):
        invalid_group = _update(
            update_id=35,
            user_id=7,
            chat_id=100,
            text="invalid group routing",
        )
        invalid_group["message"]["chat"]["type"] = "group"
        with self.assertRaisesRegex(TelegramStateError, "receipt destination"):
            self.state.enqueue(35, invalid_group)

        self.assertEqual(self.state.queue_summary(7, 7), [])

    def test_ordinary_completion_strips_legacy_saved_origin_fields(self):
        self.state.enqueue(36, _update(update_id=36, text="legacy origin"))
        self.state.set_job(
            36,
            origin_user_id=7,
            origin_chat_id=7,
            origin_chat_type="private",
        )

        # Loaded records from the earlier schema remain manageable until they
        # finish, then ordinary completion sheds the redundant identity fields.
        self.assertEqual(
            [row["job_id"] for row in self.state.queue_summary(7, 7)],
            ["JOB-36"],
        )
        self.state.acknowledge(36)

        completed = self.state.completed_job(36)
        self.assertTrue(_ORIGIN_KEYS.isdisjoint(completed))
        persisted = json.loads(self.config.state_path.read_text(encoding="utf-8"))
        self.assertTrue(_ORIGIN_KEYS.isdisjoint(persisted["completed_jobs"]["36"]))

    def test_queued_cancellation_is_origin_bound_durable_and_frees_capacity(self):
        secret = "cancelled objective must be erased"
        with patch("telegram_bot.state.MAX_PENDING_JOBS", 1):
            self.state.enqueue(40, _update(update_id=40, text=secret))
            self.assertEqual(
                self.state.cancel_queued("JOB-40", user_id=8, chat_id=8),
                {"status": "not-found", "job_id": "JOB-40"},
            )
            cancelled = self.state.cancel_queued(
                "job-40",
                user_id=7,
                chat_id=7,
            )
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertFalse(cancelled["cancellable"])
            self.assertEqual(self.state.pending_count, 0)
            tombstone = self.state.completed_job(40)
            self.assertEqual(tombstone["phase"], "cancelled")
            self.assertNotIn("request", tombstone)
            self.assertNotIn(secret, self.config.state_path.read_text(encoding="utf-8"))

            restarted = TelegramState(self.config.state_path)
            self.assertEqual(
                restarted.cancel_queued("JOB-40", user_id=7, chat_id=7)["status"],
                "already-cancelled",
            )
            self.assertEqual(
                restarted.cancel_queued("JOB-40", user_id=8, chat_id=8),
                {"status": "not-found", "job_id": "JOB-40"},
            )
            self.assertEqual(
                restarted.enqueue(41, _update(update_id=41, text="new work")),
                (True, 1),
            )

    def test_recent_cancellation_survives_bounded_control_history_churn(self):
        with patch("telegram_bot.state._MAX_COMPLETED_JOBS", 2):
            self.state.enqueue(100, _update(update_id=100, text="first cancellation"))
            self.state.cancel_queued("JOB-100", user_id=7, chat_id=7)
            self.state.enqueue(1, _update(update_id=1, text="old queued target"))
            self.assertEqual(
                self.state.cancel_queued(
                    "JOB-1",
                    user_id=7,
                    chat_id=7,
                )["status"],
                "cancelled",
            )
            restarted = TelegramState(self.config.state_path)
            restarted.enqueue(50, _update(update_id=50, text="third cancellation"))
            restarted.cancel_queued("JOB-50", user_id=7, chat_id=7)
            # The inline /cancel command itself then adds an ordinary terminal
            # record. Neither sorted JSON keys nor that control acknowledgement
            # may immediately evict the more recent old-id target cancellation.
            restarted.acknowledge(200)

            final = TelegramState(self.config.state_path)
            self.assertEqual(
                final.cancel_queued(
                    "JOB-1",
                    user_id=7,
                    chat_id=7,
                )["status"],
                "already-cancelled",
            )
            payload = json.loads(self.config.state_path.read_text(encoding="utf-8"))
            self.assertLessEqual(len(payload["completed_jobs"]), 2)
            self.assertNotIn("100", payload["completed_jobs"])

    def test_cancellation_refuses_claimed_retrying_and_side_effectful_jobs(self):
        phases = ("running", "waiting", "retrying")
        for update_id, phase in enumerate(phases, start=50):
            self.state.enqueue(
                update_id,
                _update(update_id=update_id, text=f"{phase} request"),
            )
            self.state.set_job(update_id, phase=phase)
            result = self.state.cancel_queued(
                job_id(update_id),
                user_id=7,
                chat_id=7,
            )
            self.assertEqual(result["status"], "not-cancellable")
            self.assertEqual(result["phase"], phase)
            self.assertTrue(self.state.job(update_id).get("request"))

        self.state.enqueue(60, _update(update_id=60, text="checkpointed request"))
        self.state.set_job(60, task_id="TASK-0001")
        result = self.state.cancel_queued("JOB-60", user_id=7, chat_id=7)
        self.assertEqual(result["status"], "not-cancellable")
        self.assertFalse(result["cancellable"])
        summary = {
            row["job_id"]: row for row in self.state.queue_summary(7, 7)
        }
        self.assertFalse(summary["JOB-60"]["cancellable"])

    def test_claim_and_cancel_race_has_one_linearized_winner(self):
        self.state.enqueue(61, _update(update_id=61, text="race safely"))
        self._complete_acceptance_receipt(61)
        barrier = threading.Barrier(3)
        outcome = {}

        def claim() -> None:
            barrier.wait()
            outcome["claim"] = self.state.claim_next()

        def cancel() -> None:
            barrier.wait()
            outcome["cancel"] = self.state.cancel_queued(
                "JOB-61", user_id=7, chat_id=7,
            )

        claim_thread = threading.Thread(target=claim)
        cancel_thread = threading.Thread(target=cancel)
        claim_thread.start()
        cancel_thread.start()
        barrier.wait()
        claim_thread.join(5)
        cancel_thread.join(5)

        self.assertFalse(claim_thread.is_alive())
        self.assertFalse(cancel_thread.is_alive())
        if outcome["cancel"]["status"] == "cancelled":
            self.assertIsNone(outcome["claim"])
            self.assertEqual(self.state.completed_job(61)["phase"], "cancelled")
        else:
            self.assertEqual(outcome["cancel"]["status"], "not-cancellable")
            self.assertEqual(outcome["claim"][0], 61)
            self.assertEqual(self.state.job(61)["phase"], "running")

    def test_worker_claims_fifo_and_processes_only_one_request_per_call(self):
        handled = []

        class RecordingService:
            def handle_update(self, update):
                handled.append(update["update_id"])

        # Deliberately enqueue out of order: Telegram update ids define FIFO.
        self.state.enqueue(12, _update(update_id=12, text="second"))
        self.state.enqueue(11, _update(update_id=11, text="first"))
        self._complete_acceptance_receipt(12)
        self._complete_acceptance_receipt(11)
        worker = TelegramJobWorker(
            RecordingService(),
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )

        self.assertTrue(worker.run_once())
        self.assertEqual(handled, [11])
        self.assertEqual(self.state.pending_count, 1)
        self.assertTrue(worker.run_once())
        self.assertEqual(handled, [11, 12])
        self.assertEqual(self.state.pending_count, 0)
        self.assertFalse(worker.run_once())

    def test_intake_persists_work_without_running_monday_until_worker_drain(self):
        self.client.updates = [
            _update(update_id=50, text="Build the first request"),
            _update(update_id=51, text="Build the second request"),
        ]
        runner = TelegramRunner(
            self.client, self.service, self.state, poll_timeout=1,
        )

        self.assertEqual(runner.ingest_once(), 2)

        self.assertEqual(self.state.next_offset, 52)
        self.assertEqual(self.state.pending_count, 2)
        self.assertFalse(self.monday.tasks)
        self.assertEqual(self.monday.team_calls, 0)
        self.assertEqual(self.monday.build_executions, 0)

        # The compatibility run drains the already durable inbox even though
        # this poll contains no new Telegram updates.
        self.assertEqual(runner.run_once(), 0)
        self.assertEqual(len(self.monday.tasks), 2)
        self.assertEqual(self.monday.team_calls, 2)
        self.assertEqual(self.state.pending_count, 0)

    def test_transient_receipt_failure_recovers_without_telegram_redelivery(self):
        update = _update(
            update_id=53,
            text="Build only after my durable receipt",
        )

        class FailFirstReceiptClient(FakeTelegramClient):
            def __init__(self):
                super().__init__([update])
                self.receipt_attempts = []
                self.fail_receipt = True

            def send_message(self, chat_id, text):
                if text.startswith("Request saved as JOB-53"):
                    self.receipt_attempts.append((chat_id, text))
                    if self.fail_receipt:
                        raise TelegramAPIError("temporary outage", retryable=True)
                return super().send_message(chat_id, text)

        client = FailFirstReceiptClient()
        service = TelegramBotService(self.monday, client, self.state, self.config)
        runner = TelegramRunner(client, service, self.state, poll_timeout=1)

        self.assertEqual(runner.ingest_once(), 1)
        self.assertEqual(self.state.pending_acceptance_receipt_count, 1)
        self.assertIsNone(self.state.claim_next())
        self.assertFalse(self.monday.tasks)

        payload = json.loads(self.config.state_path.read_text(encoding="utf-8"))
        due_at = payload["acceptance_receipts"]["53"]["next_attempt_at"]
        client.fail_receipt = False
        client.updates = []
        restarted_state = TelegramState(self.config.state_path)
        restarted_service = TelegramBotService(
            self.monday,
            client,
            restarted_state,
            self.config,
        )
        restarted_runner = TelegramRunner(
            client,
            restarted_service,
            restarted_state,
            poll_timeout=1,
        )

        with patch("telegram_bot.state.time.time", return_value=due_at + 1):
            self.assertEqual(restarted_runner.run_once(), 0)

        self.assertEqual(len(client.receipt_attempts), 2)
        self.assertEqual(client.receipt_attempts[0], client.receipt_attempts[1])
        self.assertEqual(len(self.monday.tasks), 1)
        self.assertEqual(restarted_state.pending_count, 0)
        self.assertEqual(restarted_state.pending_acceptance_receipt_count, 0)

    def test_receipt_rate_limit_defers_all_due_sends_without_blocking_poll(self):
        self.state.enqueue(59, _update(update_id=59, text="first receipt"))
        self.state.enqueue(60, _update(update_id=60, text="second receipt"))

        class RateLimitedReceiptClient(FakeTelegramClient):
            def __init__(self):
                super().__init__()
                self.send_attempts = 0

            def send_message(self, chat_id, text):
                self.send_attempts += 1
                raise TelegramRateLimitError(9)

        client = RateLimitedReceiptClient()
        service = TelegramBotService(self.monday, client, self.state, self.config)
        runner = TelegramRunner(client, service, self.state, poll_timeout=1)
        before = time.time()

        with patch.object(
            self.state,
            "_write",
            wraps=self.state._write,
        ) as persist:
            self.assertEqual(runner.ingest_once(), 0)

        self.assertEqual(client.send_attempts, 1)
        self.assertEqual(persist.call_count, 1)
        self.assertEqual(client.offsets, [61])
        payload = json.loads(self.config.state_path.read_text(encoding="utf-8"))
        for update_id in (59, 60):
            receipt = payload["acceptance_receipts"][str(update_id)]
            self.assertEqual(receipt["phase"], "retrying")
            self.assertGreaterEqual(receipt["next_attempt_at"], before + 9)
        restarted = TelegramState(self.config.state_path)
        self.assertEqual(
            restarted.due_acceptance_receipt_ids(now=before + 8),
            [],
        )

    def test_receipt_drain_renews_intake_dispatch_for_each_network_send(self):
        self.state.enqueue(57, _update(update_id=57, text="first receipt"))
        self.state.enqueue(58, _update(update_id=58, text="second receipt"))
        now = time.time()
        self.state.mark_worker_started(now=now)
        runner = TelegramRunner(
            self.client,
            self.service,
            self.state,
            poll_timeout=1,
        )
        runner._runtime_leases_enabled = True

        with patch.object(
            runner,
            "_mark_intake_dispatch",
            wraps=runner._mark_intake_dispatch,
        ) as renew:
            runner._drain_acceptance_receipts()

        self.assertEqual(renew.call_count, 2)
        self.assertEqual(self.state.pending_acceptance_receipt_count, 0)
        intake = self.state.worker_status()["intake"]
        self.assertEqual(intake["state"], "active")
        self.assertEqual(intake["operation"], "dispatch")
        self.assertTrue(intake["ready"])

    def test_receipt_rate_limit_is_durable_before_send_helper_returns(self):
        self.state.enqueue(61, _update(update_id=61, text="durable cooldown"))

        class RateLimitedReceiptClient(FakeTelegramClient):
            def send_message(self, chat_id, text):
                raise TelegramRateLimitError(9)

        client = RateLimitedReceiptClient()
        service = TelegramBotService(self.monday, client, self.state, self.config)
        runner = TelegramRunner(client, service, self.state, poll_timeout=1)
        started = time.time()

        pause = runner._deliver_acceptance_receipt(
            61,
            rate_limit_scope=[61],
        )

        self.assertEqual(pause, 9.0)
        payload = json.loads(self.config.state_path.read_text(encoding="utf-8"))
        receipt = payload["acceptance_receipts"]["61"]
        self.assertEqual(receipt["phase"], "retrying")
        self.assertGreaterEqual(receipt["next_attempt_at"], started + 9)

    def test_receipt_rate_limit_caps_untrusted_huge_retry_hint(self):
        self.state.enqueue(62, _update(update_id=62, text="bounded receipt"))

        class HugeRateLimitClient(FakeTelegramClient):
            def send_message(self, chat_id, text):
                error = TelegramRateLimitError(1)
                error.retry_after = 10**10_000
                raise error

        client = HugeRateLimitClient()
        service = TelegramBotService(self.monday, client, self.state, self.config)
        runner = TelegramRunner(client, service, self.state, poll_timeout=1)
        started = time.time()

        self.assertEqual(runner.ingest_once(), 0)

        payload = json.loads(self.config.state_path.read_text(encoding="utf-8"))
        receipt = payload["acceptance_receipts"]["62"]
        self.assertEqual(receipt["phase"], "retrying")
        self.assertGreaterEqual(receipt["next_attempt_at"], started + 86_400)
        self.assertLess(receipt["next_attempt_at"], started + 86_401)

    def test_immediate_receipt_rate_limit_is_durable_before_later_update_crash(self):
        first = _update(update_id=63, text="persist my receipt cooldown")
        second = _update(update_id=64, text="fail later in this batch")

        class RateLimitedReceiptClient(FakeTelegramClient):
            def send_message(self, chat_id, text):
                raise TelegramRateLimitError(9)

        client = RateLimitedReceiptClient([first, second])
        service = TelegramBotService(self.monday, client, self.state, self.config)
        normalized_first = service.normalize_update(first)

        def normalize(update):
            if update.get("update_id") == 64:
                raise RuntimeError("simulated later intake crash")
            return normalized_first

        runner = TelegramRunner(client, service, self.state, poll_timeout=1)
        started = time.time()
        with (
            patch.object(service, "normalize_update", side_effect=normalize),
            patch.object(service, "report_error"),
            self.assertRaises(TelegramUpdateRetryError),
        ):
            runner.ingest_once()

        payload = json.loads(self.config.state_path.read_text(encoding="utf-8"))
        receipt = payload["acceptance_receipts"]["63"]
        self.assertEqual(receipt["phase"], "retrying")
        self.assertGreaterEqual(receipt["next_attempt_at"], started + 9)
        restarted = TelegramState(self.config.state_path)
        self.assertEqual(
            restarted.due_acceptance_receipt_ids(now=started + 8),
            [],
        )

    def test_crash_after_receipt_send_replays_identical_text_and_runs_once(self):
        update = _update(update_id=54, text="Recover ambiguous receipt send")
        self.client.updates = [update]
        runner = TelegramRunner(
            self.client,
            self.service,
            self.state,
            poll_timeout=1,
        )

        with (
            patch.object(
                self.state,
                "complete_acceptance_receipt",
                side_effect=RuntimeError("simulated crash after remote send"),
            ),
            self.assertRaises(RuntimeError),
        ):
            runner.ingest_once()

        self.assertEqual(self.state.pending_acceptance_receipt_count, 1)
        self.assertFalse(self.monday.tasks)
        restarted_state = TelegramState(self.config.state_path)
        restarted_service = TelegramBotService(
            self.monday,
            self.client,
            restarted_state,
            self.config,
        )
        restarted_runner = TelegramRunner(
            self.client,
            restarted_service,
            restarted_state,
            poll_timeout=1,
        )
        self.client.updates = []

        self.assertEqual(restarted_runner.run_once(), 0)

        receipts = [
            text for _chat, text in self.client.sent
            if text.startswith("Request saved as JOB-54")
        ]
        self.assertEqual(len(receipts), 2)
        self.assertEqual(receipts[0], receipts[1])
        self.assertEqual(len(self.monday.tasks), 1)

    def test_permanent_receipt_failure_scrubs_request_and_never_runs(self):
        secret = "never execute or retain this unreachable request"

        class BlockedReceiptClient(FakeTelegramClient):
            def send_message(self, chat_id, text):
                if text.startswith("Request saved as JOB-55"):
                    raise TelegramAPIError(
                        "chat unavailable",
                        retryable=False,
                        status_code=403,
                    )
                return super().send_message(chat_id, text)

        client = BlockedReceiptClient([
            _update(update_id=55, text=secret),
        ])
        service = TelegramBotService(self.monday, client, self.state, self.config)
        runner = TelegramRunner(client, service, self.state, poll_timeout=1)

        self.assertEqual(runner.run_once(), 1)

        self.assertEqual(self.state.job(55), {})
        self.assertEqual(self.state.failed_job(55)["phase"], "receipt-failed")
        self.assertEqual(self.state.pending_count, 0)
        self.assertEqual(self.state.pending_acceptance_receipt_count, 0)
        self.assertFalse(self.monday.tasks)
        self.assertNotIn(
            secret,
            self.config.state_path.read_text(encoding="utf-8"),
        )

    def test_receipt_retry_rechecks_allowlist_and_drops_revoked_work(self):
        secret = "revoked work must be scrubbed before execution"
        update = _update(update_id=56, text=secret)

        class FailingReceiptClient(FakeTelegramClient):
            def __init__(self):
                super().__init__([update])
                self.attempts = 0

            def send_message(self, chat_id, text):
                if text.startswith("Request saved as JOB-56"):
                    self.attempts += 1
                    raise TelegramAPIError("temporary outage", retryable=True)
                return super().send_message(chat_id, text)

        client = FailingReceiptClient()
        service = TelegramBotService(self.monday, client, self.state, self.config)
        runner = TelegramRunner(client, service, self.state, poll_timeout=1)
        self.assertEqual(runner.ingest_once(), 1)
        payload = json.loads(self.config.state_path.read_text(encoding="utf-8"))
        due_at = payload["acceptance_receipts"]["56"]["next_attempt_at"]

        revoked = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({8}),
            project_root=self.root,
            provider="fake",
        )
        restarted_state = TelegramState(self.config.state_path)
        restarted_service = TelegramBotService(
            self.monday,
            client,
            restarted_state,
            revoked,
        )
        restarted_runner = TelegramRunner(
            client,
            restarted_service,
            restarted_state,
            poll_timeout=1,
        )
        client.updates = []

        with patch("telegram_bot.state.time.time", return_value=due_at + 1):
            self.assertEqual(restarted_runner.run_once(), 0)

        self.assertEqual(client.attempts, 1)
        self.assertEqual(restarted_state.job(56), {})
        self.assertEqual(
            restarted_state.failed_job(56)["phase"],
            "receipt-failed",
        )
        self.assertFalse(self.monday.tasks)
        self.assertNotIn(
            secret,
            self.config.state_path.read_text(encoding="utf-8"),
        )

    def test_cancel_stays_responsive_during_transient_receipt_failure(self):
        request = _update(update_id=57, text="cancel during receipt outage")
        cancel = _update(update_id=58, text="/cancel JOB-57")

        class ReceiptOutageClient(FakeTelegramClient):
            def __init__(self):
                super().__init__([request, cancel])
                self.receipt_attempts = 0

            def send_message(self, chat_id, text):
                if text.startswith("Request saved as JOB-57"):
                    self.receipt_attempts += 1
                    raise TelegramAPIError("temporary outage", retryable=True)
                return super().send_message(chat_id, text)

        client = ReceiptOutageClient()
        service = TelegramBotService(self.monday, client, self.state, self.config)
        runner = TelegramRunner(client, service, self.state, poll_timeout=1)

        self.assertEqual(runner.run_once(), 2)

        self.assertEqual(client.receipt_attempts, 1)
        self.assertEqual(self.state.completed_job(57)["phase"], "cancelled")
        self.assertEqual(self.state.pending_acceptance_receipt_count, 0)
        self.assertEqual(self.state.job(57), {})
        self.assertFalse(self.monday.tasks)
        self.assertIn(
            "JOB-57 is cancelled. It will not run.",
            [text for _chat, text in client.sent],
        )

    def test_unauthorized_update_body_is_never_persisted(self):
        secret = "never-persist-this-private-message"
        self.client.updates = [
            _update(update_id=52, user_id=999, chat_id=999, text=secret),
        ]
        runner = TelegramRunner(
            self.client, self.service, self.state, poll_timeout=1,
        )

        self.assertEqual(runner.ingest_once(), 1)

        self.assertEqual(self.state.next_offset, 53)
        self.assertEqual(self.state.pending_count, 0)
        self.assertEqual(self.state.job(52), {})
        persisted = self.config.state_path.read_text(encoding="utf-8")
        self.assertNotIn(secret, persisted)
        self.assertNotIn('"999"', persisted)
        self.assertFalse(self.monday.tasks)
        self.assertFalse(self.client.sent)

    def test_runner_announces_job_handle_and_never_readmits_full_replay(self):
        rejected_text = "full inbox objective must never be retained"
        self.client.updates = [
            _update(update_id=60, text="accepted objective"),
            _update(update_id=61, text=rejected_text),
        ]
        runner = TelegramRunner(
            self.client, self.service, self.state, poll_timeout=1,
        )

        with patch("telegram_bot.state.MAX_PENDING_JOBS", 1):
            self.assertEqual(runner.ingest_once(), 2)

        sent = [text for _chat_id, text in self.client.sent]
        self.assertIn("Request saved as JOB-60", sent[0])
        self.assertIn("inbox is full", sent[1])
        self.assertEqual(self.state.pending_count, 1)
        self.assertEqual(self.state.completed_job(61)["phase"], "queue-full")
        self.assertNotIn(
            rejected_text,
            self.config.state_path.read_text(encoding="utf-8"),
        )

        self.state.cancel_queued("JOB-60", user_id=7, chat_id=7)
        self.client.updates = [_update(update_id=61, text=rejected_text)]
        sent_before = len(self.client.sent)
        self.assertEqual(runner.ingest_once(), 1)
        self.assertEqual(self.state.pending_count, 0)
        self.assertEqual(len(self.client.sent), sent_before)

    def test_transient_queue_full_notice_does_not_block_later_cancel(self):
        self.state.enqueue(50, _update(update_id=50, text="fills capacity"))
        self._complete_acceptance_receipt(50)
        refused = _update(update_id=51, text="refused while send is down")
        cancel = _update(update_id=52, text="/cancel JOB-50")

        class CapacityNoticeFailsClient(FakeTelegramClient):
            def __init__(self):
                super().__init__([refused, cancel])
                self.capacity_attempts = 0

            def send_message(self, chat_id, text):
                if "inbox is full" in text:
                    self.capacity_attempts += 1
                    raise TelegramAPIError("temporary outage", retryable=True)
                return super().send_message(chat_id, text)

        client = CapacityNoticeFailsClient()
        service = TelegramBotService(self.monday, client, self.state, self.config)
        runner = TelegramRunner(client, service, self.state, poll_timeout=1)

        with patch("telegram_bot.state.MAX_PENDING_JOBS", 1):
            self.assertEqual(runner.ingest_once(), 2)

        self.assertEqual(client.capacity_attempts, 1)
        self.assertEqual(self.state.next_offset, 53)
        self.assertEqual(self.state.pending_count, 0)
        self.assertEqual(self.state.completed_job(50)["phase"], "cancelled")
        self.assertEqual(
            self.state.completed_job(51)["notice_status"],
            "retrying",
        )
        self.assertIn(
            "JOB-50 is cancelled. It will not run.",
            [text for _chat, text in client.sent],
        )

    def test_queue_full_rate_limit_atomically_defers_due_cohort_and_polls(self):
        self.state.enqueue(53, _update(update_id=53, text="fills capacity"))
        self._complete_acceptance_receipt(53)
        with patch("telegram_bot.state.MAX_PENDING_JOBS", 1):
            self.state.enqueue(54, _update(update_id=54, text="first refusal"))
            self.state.enqueue(55, _update(update_id=55, text="second refusal"))

        class RateLimitedCapacityClient(FakeTelegramClient):
            def __init__(self):
                super().__init__()
                self.send_attempts = 0

            def send_message(self, chat_id, text):
                self.send_attempts += 1
                raise TelegramRateLimitError(9)

        client = RateLimitedCapacityClient()
        service = TelegramBotService(self.monday, client, self.state, self.config)
        runner = TelegramRunner(client, service, self.state, poll_timeout=1)
        started = time.time()

        with patch.object(
            self.state,
            "_write",
            wraps=self.state._write,
        ) as persist:
            self.assertEqual(runner.ingest_once(), 0)

        self.assertEqual(client.send_attempts, 1)
        self.assertEqual(client.offsets, [56])
        self.assertEqual(persist.call_count, 1)
        for update_id in (54, 55):
            notice = self.state.completed_job(update_id)
            self.assertEqual(notice["notice_status"], "retrying")
            self.assertGreaterEqual(
                notice["notice_next_attempt_at"],
                started + 9,
            )
        restarted = TelegramState(self.config.state_path)
        self.assertEqual(
            restarted.due_queue_full_notice_ids(now=started + 8),
            [],
        )

    def test_queue_full_rate_limit_caps_huge_retry_hint_before_return(self):
        self.state.enqueue(56, _update(update_id=56, text="fills capacity"))
        self._complete_acceptance_receipt(56)
        with patch("telegram_bot.state.MAX_PENDING_JOBS", 1):
            self.state.enqueue(57, _update(update_id=57, text="bounded refusal"))

        class HugeRateLimitClient(FakeTelegramClient):
            def send_message(self, chat_id, text):
                error = TelegramRateLimitError(1)
                error.retry_after = 10**10_000
                raise error

        client = HugeRateLimitClient()
        service = TelegramBotService(self.monday, client, self.state, self.config)
        runner = TelegramRunner(client, service, self.state, poll_timeout=1)
        started = time.time()

        pause = runner._deliver_queue_full_notice(
            57,
            rate_limit_scope=[57],
        )

        self.assertEqual(pause, 86_400.0)
        notice = self.state.completed_job(57)
        self.assertEqual(notice["notice_status"], "retrying")
        self.assertGreaterEqual(
            notice["notice_next_attempt_at"],
            started + 86_400,
        )
        self.assertLess(
            notice["notice_next_attempt_at"],
            started + 86_401,
        )

    def test_queue_full_notice_retries_after_restart_without_holding_offset(self):
        rejected_text = "retry notice without retaining this objective"
        self.state.enqueue(60, _update(update_id=60, text="fills capacity"))
        self._complete_acceptance_receipt(60)
        refused = _update(update_id=61, text=rejected_text)

        class RetryOnceClient(FakeTelegramClient):
            def __init__(self):
                super().__init__([refused])
                self.attempted = []

            def send_message(self, chat_id, text):
                self.attempted.append((chat_id, text))
                if len(self.attempted) == 1:
                    raise TelegramAPIError("temporary outage", retryable=True)
                return super().send_message(chat_id, text)

        client = RetryOnceClient()
        service = TelegramBotService(self.monday, client, self.state, self.config)
        runner = TelegramRunner(client, service, self.state, poll_timeout=1)
        with patch("telegram_bot.state.MAX_PENDING_JOBS", 1):
            self.assertEqual(runner.ingest_once(), 1)

        self.assertEqual(self.state.next_offset, 62)
        self.assertEqual(
            self.state.completed_job(61)["notice_status"],
            "retrying",
        )
        due_at = self.state.completed_job(61)["notice_next_attempt_at"]
        self.assertNotIn(
            rejected_text,
            self.config.state_path.read_text(encoding="utf-8"),
        )

        restarted = TelegramState(self.config.state_path)
        # Recovery is state-driven. Telegram may have aged the original update
        # out of its own temporary queue during a long outage.
        client.updates = []
        restarted_service = TelegramBotService(
            self.monday, client, restarted, self.config,
        )
        restarted_runner = TelegramRunner(
            client, restarted_service, restarted, poll_timeout=1,
        )
        self.assertEqual(restarted_runner.ingest_once(), 0)
        self.assertEqual(len(client.attempted), 1)
        self.assertEqual(restarted.next_offset, 62)
        self.assertEqual(
            restarted.completed_job(61)["notice_status"],
            "retrying",
        )

        with patch("telegram_bot.state.time.time", return_value=due_at + 1):
            self.assertEqual(restarted_runner.ingest_once(), 0)
        self.assertEqual(restarted.next_offset, 62)
        self.assertEqual(
            restarted.completed_job(61)["notice_status"],
            "sent",
        )
        self.assertEqual(restarted.pending_queue_full_notice_count, 0)
        self.assertEqual(client.attempted[0], client.attempted[1])

    def test_permanent_queue_full_notice_failure_does_not_wedge_intake(self):
        self.state.enqueue(65, _update(update_id=65, text="fills capacity"))
        self._complete_acceptance_receipt(65)
        refused = _update(update_id=66, text="permanently refused objective")

        class PermanentlyFailingClient(FakeTelegramClient):
            def send_message(self, chat_id, text):
                raise TelegramAPIError("chat unavailable", retryable=False)

        client = PermanentlyFailingClient([refused])
        service = TelegramBotService(self.monday, client, self.state, self.config)
        runner = TelegramRunner(client, service, self.state, poll_timeout=1)
        with patch("telegram_bot.state.MAX_PENDING_JOBS", 1):
            self.assertEqual(runner.ingest_once(), 1)

        self.assertEqual(self.state.next_offset, 67)
        self.assertEqual(
            self.state.completed_job(66)["notice_status"],
            "failed",
        )

    def test_queue_full_retry_respects_allowlist_revocation(self):
        self.state.enqueue(67, _update(update_id=67, text="fills capacity"))
        self._complete_acceptance_receipt(67)
        refused = _update(update_id=68, text="revoked destination request")

        class FirstSendFails(FakeTelegramClient):
            def __init__(self):
                super().__init__([refused])
                self.attempted = []

            def send_message(self, chat_id, text):
                self.attempted.append((chat_id, text))
                raise TelegramAPIError("temporary outage", retryable=True)

        client = FirstSendFails()
        service = TelegramBotService(self.monday, client, self.state, self.config)
        runner = TelegramRunner(client, service, self.state, poll_timeout=1)
        with patch("telegram_bot.state.MAX_PENDING_JOBS", 1):
            self.assertEqual(runner.ingest_once(), 1)
        due_at = self.state.completed_job(68)["notice_next_attempt_at"]

        revoked = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({8}),
            project_root=self.root,
            provider="fake",
        )
        restarted = TelegramState(self.config.state_path)
        client.updates = []
        revoked_service = TelegramBotService(
            self.monday, client, restarted, revoked,
        )
        revoked_runner = TelegramRunner(
            client, revoked_service, restarted, poll_timeout=1,
        )
        with patch("telegram_bot.state.time.time", return_value=due_at + 1):
            self.assertEqual(revoked_runner.ingest_once(), 0)
        self.assertEqual(len(client.attempted), 1)
        self.assertEqual(restarted.next_offset, 69)
        self.assertEqual(
            restarted.completed_job(68)["notice_status"],
            "failed",
        )

    def test_queue_full_retry_tampered_destination_fails_closed_on_load(self):
        self.state.enqueue(69, _update(update_id=69, text="fills capacity"))
        self._complete_acceptance_receipt(69)
        refused = _update(update_id=70, text="never redirect this notice")
        with patch("telegram_bot.state.MAX_PENDING_JOBS", 1):
            self.state.enqueue(70, refused)

        payload = json.loads(self.config.state_path.read_text(encoding="utf-8"))
        payload["completed_jobs"]["70"]["origin_chat_id"] = 999
        self.config.state_path.write_text(json.dumps(payload), encoding="utf-8")

        client = FakeTelegramClient()
        with self.assertRaises(TelegramStateError):
            TelegramState(self.config.state_path)
        self.assertFalse(client.sent)

    def test_intake_controls_are_private_origin_bound_and_validate_input(self):
        private_text = "private own objective"
        foreign_text = "private foreign objective"
        self.state.enqueue(70, _update(update_id=70, text=private_text))
        self.state.enqueue(
            71,
            _update(
                update_id=71,
                user_id=8,
                chat_id=8,
                text=foreign_text,
            ),
        )
        own_group = _update(
            update_id=72,
            user_id=7,
            chat_id=-100,
            text="own allowed-group objective",
        )
        own_group["message"]["chat"]["type"] = "group"
        self.state.enqueue(72, own_group)

        self.assertTrue(self.service.handle_intake_control(
            _update(update_id=80, text="/queue")
        ))
        queue_text = self.client.sent[-1][1]
        self.assertIn("JOB-70", queue_text)
        self.assertIn("JOB-72", queue_text)
        self.assertNotIn("JOB-71", queue_text)
        self.assertNotIn(private_text, queue_text)
        self.assertNotIn(foreign_text, queue_text)

        self.assertTrue(self.service.handle_intake_control(
            _update(update_id=81, text="/cancel JOB-71")
        ))
        foreign_result = self.client.sent[-1][1]
        self.assertEqual(
            foreign_result,
            "JOB-71 is not queued and was not cancelled.",
        )
        self.assertTrue(self.service.handle_intake_control(
            _update(update_id=82, text="/cancel JOB-999")
        ))
        self.assertEqual(
            self.client.sent[-1][1],
            "JOB-999 is not queued and was not cancelled.",
        )
        self.assertTrue(self.service.handle_intake_control(
            _update(update_id=83, text="/cancel not-a-job")
        ))
        self.assertEqual(self.client.sent[-1][1], "Use /cancel JOB-123")
        self.assertTrue(self.service.handle_intake_control(
            _update(update_id=84, text="/cancel JOB-72")
        ))
        self.assertEqual(
            self.client.sent[-1][1],
            "JOB-72 is cancelled. It will not run.",
        )

        sent_before = len(self.client.sent)
        self.assertFalse(self.service.handle_intake_control(
            _update(update_id=85, user_id=999, chat_id=999, text="/queue")
        ))
        group = _update(update_id=86, chat_id=-100, text="/queue")
        group["message"]["chat"]["type"] = "group"
        group_config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            allowed_chat_ids=frozenset({-100}),
            project_root=self.root,
            provider="fake",
        )
        group_service = TelegramBotService(
            self.monday, self.client, self.state, group_config,
        )
        self.assertFalse(group_service.handle_intake_control(group))
        self.assertEqual(len(self.client.sent), sent_before)
        self.assertFalse(self.monday.tasks)

    def test_cancel_reply_retry_is_idempotent_and_permanent_failure_acks(self):
        self.state.enqueue(90, _update(update_id=90, text="cancel me once"))
        self._complete_acceptance_receipt(90)
        command = _update(update_id=91, text="/cancel JOB-90")

        class RetryOnceClient(FakeTelegramClient):
            def __init__(self):
                super().__init__([command])
                self.attempted = []

            def send_message(self, chat_id, text):
                self.attempted.append((chat_id, text))
                if len(self.attempted) == 1:
                    raise TelegramAPIError("temporary outage", retryable=True)
                return super().send_message(chat_id, text)

        retry_client = RetryOnceClient()
        retry_service = TelegramBotService(
            self.monday, retry_client, self.state, self.config,
        )
        retry_runner = TelegramRunner(
            retry_client, retry_service, self.state, poll_timeout=1,
        )
        with self.assertRaises(TelegramAPIError):
            retry_runner.ingest_once()
        self.assertEqual(self.state.completed_job(90)["phase"], "cancelled")
        self.assertEqual(self.state.next_offset, 91)

        retry_client.updates = [command]
        self.assertEqual(retry_runner.ingest_once(), 1)
        self.assertEqual(self.state.next_offset, 92)
        self.assertEqual(retry_client.attempted[0], retry_client.attempted[1])
        self.assertEqual(
            retry_client.attempted[1][1],
            "JOB-90 is cancelled. It will not run.",
        )

        self.state.enqueue(100, _update(update_id=100, text="cancel permanently"))
        self._complete_acceptance_receipt(100)
        permanent_command = _update(update_id=101, text="/cancel JOB-100")

        class PermanentlyFailingClient(FakeTelegramClient):
            def send_message(self, chat_id, text):
                raise TelegramAPIError("chat unavailable", retryable=False)

        permanent_client = PermanentlyFailingClient([permanent_command])
        permanent_service = TelegramBotService(
            self.monday, permanent_client, self.state, self.config,
        )
        permanent_runner = TelegramRunner(
            permanent_client, permanent_service, self.state, poll_timeout=1,
        )
        self.assertEqual(permanent_runner.ingest_once(), 1)
        self.assertEqual(self.state.completed_job(100)["phase"], "cancelled")
        self.assertEqual(self.state.next_offset, 102)
        self.assertFalse(self.monday.tasks)

    def test_corrupt_state_fails_closed_instead_of_replaying_updates(self):
        self.config.state_path.parent.mkdir(parents=True)
        self.config.state_path.write_text("not-json", encoding="utf-8")
        with self.assertRaises(TelegramStateError):
            TelegramState(self.config.state_path)

    def test_claim_revalidates_persisted_request_before_execution(self):
        def changed_text(payload):
            payload["jobs"]["10"]["request"]["message"]["text"] = "tampered"

        def changed_identity(payload):
            payload["jobs"]["11"] = payload["jobs"].pop("10")

        def changed_phase(payload):
            payload["jobs"]["10"]["phase"] = "unexpected"

        def changed_origin_with_matching_digest(payload):
            record = payload["jobs"]["10"]
            record["request"]["message"]["chat"]["type"] = "channel"
            canonical = json.dumps(
                record["request"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            record["request_digest"] = hashlib.sha256(
                canonical.encode("utf-8")
            ).hexdigest()

        for label, mutate in (
            ("digest", changed_text),
            ("identity", changed_identity),
            ("phase", changed_phase),
            ("origin", changed_origin_with_matching_digest),
        ):
            with self.subTest(label=label):
                path = self.root / f"tampered-{label}.json"
                state = TelegramState(path)
                state.enqueue(10, _update(update_id=10, text="original"))
                payload = json.loads(path.read_text(encoding="utf-8"))
                mutate(payload)
                path.write_text(json.dumps(payload), encoding="utf-8")

                with self.assertRaises(TelegramStateError):
                    TelegramState(path)

    def test_only_one_worker_can_hold_instance_lock(self):
        with InstanceLock(self.config.lock_path):
            with self.assertRaisesRegex(TelegramStateError, "already running"):
                with InstanceLock(self.config.lock_path):
                    pass

    def test_instance_lock_invalidates_a_prior_ready_runtime_immediately(self):
        now = time.time()
        self.state.mark_worker_started(now=now)
        self.state.mark_runtime_lane(
            "intake",
            "active",
            "poll",
            timeout_seconds=60,
            now=now,
        )
        self.state.mark_runtime_lane("worker", "idle", "idle", now=now)
        self.assertTrue(self.state.worker_status()["runtime_ready"])

        with InstanceLock(self.config.lock_path):
            stopped = self.state.worker_status()
            self.assertFalse(stopped["runtime_ready"])
            self.assertEqual(stopped["intake"]["state"], "stopped")
            self.assertEqual(stopped["worker"]["state"], "stopped")

    def test_runner_advances_offset_only_after_handling(self):
        self.client.updates = [_update(update_id=12, text="/help")]
        runner = TelegramRunner(
            self.client, self.service, self.state, poll_timeout=1,
        )
        self.assertEqual(runner.run_once(), 1)
        self.assertEqual(self.state.next_offset, 13)
        self.assertEqual(self.client.offsets, [None])

    def test_background_intake_keeps_polling_while_worker_is_blocked(self):
        first_started = threading.Event()
        release_first = threading.Event()
        second_queued = threading.Event()
        stop = threading.Event()
        errors = []

        class BlockingMonday(FakeMonday):
            def team(self, action, **kwargs):
                if action == "run":
                    first_started.set()
                    if not release_first.wait(5):
                        raise AssertionError("test did not release the active job")
                return super().team(action, **kwargs)

        class SequencedClient(FakeTelegramClient):
            def __init__(self):
                super().__init__()
                self.poll_count = 0

            def get_updates(self, *, offset, timeout):
                self.offsets.append(offset)
                self.poll_count += 1
                if self.poll_count == 1:
                    return [_update(update_id=100, text="Build the slow request")]
                if self.poll_count == 2:
                    first_started.wait(5)
                    return [_update(update_id=101, text="Build the queued request")]
                stop.wait(5)
                return []

            def send_message(self, chat_id, text):
                result = super().send_message(chat_id, text)
                if "Request saved as JOB-101" in text:
                    second_queued.set()
                return result

        monday = BlockingMonday()
        client = SequencedClient()
        service = TelegramBotService(monday, client, self.state, self.config)
        runner = TelegramRunner(
            client,
            service,
            self.state,
            poll_timeout=1,
            stop_event=stop,
        )

        def run() -> None:
            try:
                runner.run_forever()
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        self.assertTrue(second_queued.wait(5))

        self.assertEqual(self.state.next_offset, 102)
        self.assertEqual(self.state.pending_count, 2)
        self.assertEqual(self.state.job(100)["phase"], "running")
        self.assertEqual(self.state.job(101)["phase"], "queued")
        self.assertEqual(len(monday.tasks), 1)
        self.assertEqual(monday.team_calls, 0)

        # A clean stop finishes the active checkpoint but deliberately leaves
        # the second durable request for the next process to recover.
        runner.stop()
        release_first.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.state.pending_count, 1)
        self.assertEqual(self.state.job(101)["phase"], "queued")

    def test_queue_and_cancel_stay_responsive_while_worker_is_blocked(self):
        first_started = threading.Event()
        release_first = threading.Event()
        cancellation_sent = threading.Event()
        stop = threading.Event()
        errors = []

        class BlockingMonday(FakeMonday):
            def team(self, action, **kwargs):
                if action == "run":
                    first_started.set()
                    if not release_first.wait(5):
                        raise AssertionError("test did not release the active job")
                return super().team(action, **kwargs)

        class ControlClient(FakeTelegramClient):
            def __init__(self):
                super().__init__()
                self.poll_count = 0

            def get_updates(self, *, offset, timeout):
                self.offsets.append(offset)
                self.poll_count += 1
                if self.poll_count == 1:
                    return [_update(update_id=110, text="Build the slow request")]
                if self.poll_count == 2:
                    if not first_started.wait(5):
                        raise AssertionError("active job never started")
                    return [_update(update_id=111, text="Never execute this request")]
                if self.poll_count == 3:
                    return [_update(update_id=112, text="/queue")]
                if self.poll_count == 4:
                    return [_update(update_id=113, text="/cancel JOB-111")]
                stop.wait(5)
                return []

            def send_message(self, chat_id, text):
                result = super().send_message(chat_id, text)
                if text == "JOB-111 is cancelled. It will not run.":
                    cancellation_sent.set()
                return result

        monday = BlockingMonday()
        client = ControlClient()
        service = TelegramBotService(monday, client, self.state, self.config)
        runner = TelegramRunner(
            client,
            service,
            self.state,
            poll_timeout=1,
            stop_event=stop,
        )

        def run() -> None:
            try:
                runner.run_forever()
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(cancellation_sent.wait(5))
            self.assertEqual(self.state.completed_job(111)["phase"], "cancelled")
            self.assertEqual(self.state.job(110)["phase"], "running")
            self.assertEqual(len(monday.tasks), 1)
            self.assertNotIn(
                "Never execute this request",
                [task["objective"] for task in monday.tasks.values()],
            )
            queue_replies = [
                text for _chat_id, text in client.sent
                if text.startswith("Your Monday inbox:")
            ]
            self.assertEqual(len(queue_replies), 1)
            self.assertIn("JOB-110 · running", queue_replies[0])
            self.assertIn("JOB-111 · queued", queue_replies[0])
            self.assertNotIn("Build the slow request", queue_replies[0])
            self.assertNotIn("Never execute this request", queue_replies[0])
        finally:
            runner.stop()
            release_first.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])

    def test_restart_drains_running_and_queued_jobs_with_same_delivery_id(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )
        self.state.bind_bot_identity(99, "monday_test_bot")
        service = TelegramBotService(self.monday, self.client, self.state, config)
        service.bind_bot_identity(self.client.get_me())
        build_update = _update(update_id=70, text="/build Recover this build")
        help_update = _update(update_id=71, text="/help")
        delivery_id = service._delivery_id(70)
        self.monday.delivery_runs[delivery_id] = {
            "delivery_id": delivery_id,
            "task_id": "TASK-0001",
            "status": "running",
            "phase": "validating",
            "success": False,
            "branch": "codex/task-0001-12345678",
            "commit_sha": "",
            "pr_url": "",
            "changed_files": ["app.py"],
            "attempts": [{"number": 1, "status": "validating"}],
            "message": "Validation is still running.",
        }
        normalized = service.normalize_update(build_update)
        self.assertIsNotNone(normalized)
        self.state.enqueue(70, normalized)
        self._complete_acceptance_receipt(70)
        worker = TelegramJobWorker(
            service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )

        with self.assertRaises(TelegramUpdatePendingError):
            worker.run_once()

        saved = self.state.job(70)
        self.assertEqual(saved["phase"], "waiting")
        self.assertEqual(saved["delivery_id"], delivery_id)
        normalized_help = service.normalize_update(help_update)
        self.assertIsNotNone(normalized_help)
        self.state.enqueue(71, normalized_help)
        self.assertEqual(self.state.pending_count, 2)

        self.monday.delivery_runs[delivery_id].update({
            "status": "pr-open",
            "phase": "completed",
            "success": True,
            "commit_sha": "a" * 40,
            "pr_url": "https://github.com/example/mondayos/pull/55",
            "message": "Pull request opened.",
        })
        restarted_state = TelegramState(config.state_path)
        restarted_service = TelegramBotService(
            self.monday, self.client, restarted_state, config,
        )
        restarted_service.bind_bot_identity(self.client.get_me())
        restarted_runner = TelegramRunner(
            self.client,
            restarted_service,
            restarted_state,
            poll_timeout=1,
        )

        self.assertEqual(restarted_runner.run_once(), 0)

        self.assertEqual(restarted_state.pending_count, 0)
        self.assertEqual(len(self.monday.tasks), 1)
        self.assertEqual(
            [request["delivery_id"] for _action, request in self.monday.build_calls],
            [delivery_id, delivery_id],
        )
        self.assertEqual(
            restarted_state.completed_job(70)["delivery_id"], delivery_id,
        )

    def test_live_build_persists_request_free_pr_follow_up_in_tombstone(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )
        service = TelegramBotService(self.monday, self.client, self.state, config)
        runner = TelegramRunner(self.client, service, self.state, poll_timeout=1)
        runner.start()
        request_text = "/build weatherbot :: Ship the private launch plan"
        self.client.updates = [_update(update_id=72, text=request_text)]

        self.assertEqual(runner.run_once(), 1)

        tombstone = self.state.completed_job(72)
        follow_up = tombstone["follow_up"]
        self.assertNotIn("request", tombstone)
        self.assertEqual(follow_up["kind"], "pr-lifecycle")
        self.assertEqual(follow_up["phase"], "pending")
        self.assertEqual(follow_up["task_id"], "TASK-0001")
        self.assertEqual(follow_up["project_name"], "weatherbot")
        self.assertEqual(follow_up["chat_id"], 7)
        self.assertGreater(follow_up["expires_at"], follow_up["created_at"])
        self.assertEqual(
            follow_up["pr_url"], "https://github.com/example/mondayos/pull/55",
        )
        persisted = config.state_path.read_text(encoding="utf-8")
        self.assertNotIn(request_text, persisted)
        self.assertNotIn("private launch plan", persisted)

    def test_live_build_reports_when_pr_watcher_capacity_is_full(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )
        service = TelegramBotService(self.monday, self.client, self.state, config)
        runner = TelegramRunner(self.client, service, self.state, poll_timeout=1)
        runner.start()
        self.state.set_job(720)
        self.state.start_follow_up(
            720,
            {
                "kind": "pr-lifecycle",
                "phase": "pending",
                "next_poll_at": time.time() + 3_600,
            },
        )
        self.state.acknowledge(720)
        self.client.updates = [_update(update_id=721, text="/build Ship safely")]

        with patch("telegram_bot.state._MAX_ACTIVE_FOLLOW_UPS", 1):
            self.assertEqual(runner.run_once(), 1)

        follow_up = self.state.completed_job(721)["follow_up"]
        self.assertEqual(follow_up["phase"], "not-started-capacity")
        payload = json.loads(config.state_path.read_text(encoding="utf-8"))
        active = [
            record["follow_up"]
            for record in payload["completed_jobs"].values()
            if record.get("follow_up", {}).get("phase")
            in {"pending", "polling", "retrying"}
        ]
        self.assertEqual(len(active), 1)
        self.assertIn("500-watcher limit is full", self.client.sent[-1][1])
        self.assertIn("pull request is unaffected", self.client.sent[-1][1].lower())

    def test_pr_follow_up_notifies_only_changes_and_stops_after_merge(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )
        service = TelegramBotService(self.monday, self.client, self.state, config)
        runner = TelegramRunner(self.client, service, self.state, poll_timeout=1)
        runner.start()
        self.client.updates = [
            _update(update_id=73, text="/build weatherbot :: Ship safely"),
        ]
        runner.run_once()
        tombstone = self.state.completed_job(73)
        delivery_id = tombstone["delivery_id"]
        base = {
            "delivery_id": delivery_id,
            "task_id": "TASK-0001",
            "project_name": "weatherbot",
            "pr_url": "https://github.com/example/mondayos/pull/55",
            "merge_commit_sha": "",
        }
        self.monday.observe_results[delivery_id] = [
            {**base, "pr_state": "open", "checks_status": "pending"},
            {**base, "pr_state": "open", "checks_status": "pending"},
            {**base, "pr_state": "open", "checks_status": "passed"},
            {
                **base,
                "pr_state": "merged",
                "checks_status": "passed",
                "merge_commit_sha": "b" * 40,
            },
        ]
        worker = TelegramJobWorker(
            service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )
        baseline = len(self.client.sent)

        self.assertTrue(worker.run_follow_up_once())
        self.assertIn("checks are running", self.client.sent[-1][1])
        first_notice_count = len(self.client.sent)

        self.state.update_follow_up(73, phase="pending", next_poll_at=0.0)
        self.assertTrue(worker.run_follow_up_once())
        self.assertEqual(len(self.client.sent), first_notice_count)

        self.state.update_follow_up(73, phase="pending", next_poll_at=0.0)
        self.assertTrue(worker.run_follow_up_once())
        self.assertIn("checks passed", self.client.sent[-1][1])

        self.state.update_follow_up(73, phase="pending", next_poll_at=0.0)
        self.assertTrue(worker.run_follow_up_once())
        self.assertIn("was merged", self.client.sent[-1][1])
        self.assertIn("bbbbbbbbbbbb", self.client.sent[-1][1])
        follow_up = self.state.completed_job(73)["follow_up"]
        self.assertEqual(follow_up["phase"], "completed")
        self.assertEqual(follow_up["pr_state"], "merged")
        self.assertFalse(worker.run_follow_up_once())
        self.assertGreater(len(self.client.sent), baseline)
        observe_calls = [
            kwargs for action, kwargs in self.monday.build_calls
            if action == "observe"
        ]
        self.assertEqual(len(observe_calls), 4)
        self.assertTrue(all(row["delivery_id"] == delivery_id for row in observe_calls))
        self.assertTrue(all(row["project"] == "weatherbot" for row in observe_calls))

    def test_pr_follow_up_survives_restart_and_stops_when_closed(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )
        service = TelegramBotService(self.monday, self.client, self.state, config)
        runner = TelegramRunner(self.client, service, self.state, poll_timeout=1)
        runner.start()
        self.client.updates = [_update(update_id=74, text="/build Ship it")]
        runner.run_once()
        delivery_id = self.state.completed_job(74)["delivery_id"]
        self.monday.observe_results[delivery_id] = {
            "delivery_id": delivery_id,
            "task_id": "TASK-0001",
            "project_name": "",
            "pr_url": "https://github.com/example/mondayos/pull/55",
            "pr_state": "closed",
            "checks_status": "failed",
            "merge_commit_sha": "",
        }

        restarted_state = TelegramState(config.state_path)
        restarted_service = TelegramBotService(
            self.monday, self.client, restarted_state, config,
        )
        restarted_service.bind_bot_identity(self.client.get_me())
        worker = TelegramJobWorker(
            restarted_service,
            restarted_state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )

        self.assertTrue(worker.run_follow_up_once())

        follow_up = restarted_state.completed_job(74)["follow_up"]
        self.assertEqual(follow_up["phase"], "completed")
        self.assertEqual(follow_up["pr_state"], "closed")
        self.assertIn("closed without merging", self.client.sent[-1][1])
        self.assertNotIn("request", restarted_state.completed_job(74))

    def test_pr_follow_up_outages_back_off_without_persisting_diagnostics(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )
        service = TelegramBotService(self.monday, self.client, self.state, config)
        runner = TelegramRunner(self.client, service, self.state, poll_timeout=1)
        runner.start()
        self.client.updates = [_update(update_id=75, text="/build Ship it")]
        runner.run_once()
        delivery_id = self.state.completed_job(75)["delivery_id"]
        self.monday.observe_results[delivery_id] = [
            {"success": False, "message": "raw remote log must not persist"},
            {"success": False, "message": "another private diagnostic"},
        ]
        worker = TelegramJobWorker(
            service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )
        before = time.time()

        self.assertTrue(worker.run_follow_up_once())

        first = self.state.completed_job(75)["follow_up"]
        self.assertEqual(first["phase"], "retrying")
        self.assertEqual(first["failure_count"], 1)
        self.assertGreaterEqual(first["next_poll_at"], before + 14)
        self.assertLessEqual(first["next_poll_at"], before + 17)
        self.assertFalse(worker.run_follow_up_once())

        self.state.update_follow_up(75, phase="retrying", next_poll_at=0.0)
        second_before = time.time()
        self.assertTrue(worker.run_follow_up_once())
        second = self.state.completed_job(75)["follow_up"]
        self.assertEqual(second["failure_count"], 2)
        self.assertGreaterEqual(second["next_poll_at"], second_before + 29)
        self.assertLessEqual(second["next_poll_at"], second_before + 32)
        persisted = config.state_path.read_text(encoding="utf-8")
        self.assertNotIn("raw remote log", persisted)
        self.assertNotIn("private diagnostic", persisted)

    def test_pr_follow_up_honors_telegram_rate_limit_beyond_observer_cap(self):
        self.state.set_job(
            76,
            follow_up={
                "kind": "pr-lifecycle",
                "phase": "pending",
                "next_poll_at": 0.0,
                "failure_count": 0,
            },
        )
        self.state.acknowledge(76)
        service = MagicMock()
        service.poll_follow_up.side_effect = TelegramRateLimitError(3_600)
        worker = TelegramJobWorker(
            service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )
        before = time.time()

        self.assertTrue(worker.run_follow_up_once())

        follow_up = self.state.completed_job(76)["follow_up"]
        self.assertEqual(follow_up["phase"], "retrying")
        self.assertEqual(follow_up["failure_count"], 1)
        self.assertGreaterEqual(follow_up["next_poll_at"], before + 3_599)
        self.assertLessEqual(follow_up["next_poll_at"], before + 3_602)
        self.assertFalse(worker.run_follow_up_once())

    def test_permanent_telegram_failure_stops_pr_follow_up_without_diagnostic(self):
        self.state.set_job(
            77,
            follow_up={
                "kind": "pr-lifecycle",
                "phase": "pending",
                "next_poll_at": 0.0,
                "failure_count": 0,
            },
        )
        self.state.acknowledge(77)
        service = MagicMock()
        service.poll_follow_up.side_effect = TelegramAPIError(
            "private destination diagnostic",
            retryable=False,
            status_code=403,
        )
        worker = TelegramJobWorker(
            service,
            self.state,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )

        self.assertTrue(worker.run_follow_up_once())

        follow_up = self.state.completed_job(77)["follow_up"]
        self.assertEqual(follow_up["phase"], "notification-failed")
        self.assertEqual(follow_up["failure_count"], 1)
        self.assertEqual(follow_up["next_poll_at"], 0.0)
        self.assertFalse(worker.run_follow_up_once())
        persisted = self.config.state_path.read_text(encoding="utf-8")
        self.assertNotIn("private destination diagnostic", persisted)

    def test_pr_follow_up_expiry_survives_restart_and_frees_capacity(self):
        first = {
            "kind": "pr-lifecycle",
            "phase": "pending",
            "next_poll_at": 1_000.0,
            "failure_count": 0,
        }
        self.state.set_job(78)
        with (
            patch("telegram_bot.state._MAX_ACTIVE_FOLLOW_UPS", 1),
            patch("telegram_bot.state._FOLLOW_UP_MAX_AGE_SECONDS", 10),
        ):
            started = self.state.start_follow_up(78, first, now=100.0)
            self.state.acknowledge(78)
            self.assertEqual(started["expires_at"], 110.0)

            restarted = TelegramState(self.config.state_path)
            self.assertIsNone(restarted.claim_due_follow_up(now=111.0))
            self.assertEqual(
                restarted.completed_job(78)["follow_up"]["phase"],
                "expired",
            )

            restarted.set_job(79)
            second = restarted.start_follow_up(79, first, now=111.0)

        self.assertEqual(second["phase"], "pending")
        self.assertEqual(second["expires_at"], 121.0)

    def test_completed_telegram_tombstones_remain_bounded(self):
        with patch("telegram_bot.state._MAX_COMPLETED_JOBS", 3):
            for update_id in range(80, 84):
                self.state.set_job(update_id)
                self.state.acknowledge(update_id)

        payload = json.loads(self.config.state_path.read_text(encoding="utf-8"))
        self.assertEqual(sorted(payload["completed_jobs"]), ["81", "82", "83"])

    def test_blocked_pr_observation_does_not_block_telegram_intake(self):
        observation_started = threading.Event()
        release_observation = threading.Event()
        request_saved = threading.Event()
        stop = threading.Event()
        errors = []
        delivery_id = "delivery-telegram-" + "c" * 20

        class BlockingMonday(FakeMonday):
            def build(self, action, **kwargs):
                if action == "observe":
                    observation_started.set()
                    if not release_observation.wait(5):
                        raise AssertionError("test did not release PR observation")
                return super().build(action, **kwargs)

        class SequencedClient(FakeTelegramClient):
            def get_updates(self, *, offset, timeout):
                self.offsets.append(offset)
                if len(self.offsets) == 1:
                    if not observation_started.wait(5):
                        raise AssertionError("PR observation never started")
                    return [_update(update_id=100, text="/help")]
                stop.wait(5)
                return []

            def send_message(self, chat_id, text):
                result = super().send_message(chat_id, text)
                if "Request saved" in text:
                    request_saved.set()
                return result

        monday = BlockingMonday()
        monday.delivery_runs[delivery_id] = {
            "task_id": "TASK-0001",
            "pr_url": "https://github.com/example/mondayos/pull/55",
        }
        client = SequencedClient()
        self.state.bind_bot_identity(99, "monday_test_bot")
        self.state.set_job(
            76,
            follow_up={
                "kind": "pr-lifecycle",
                "phase": "pending",
                "delivery_id": delivery_id,
                "task_id": "TASK-0001",
                "project_name": "",
                "chat_id": 7,
                "pr_url": "https://github.com/example/mondayos/pull/55",
                "failure_count": 0,
                "next_poll_at": 0.0,
                "pr_state": "open",
                "checks_status": "unknown",
                "merge_commit_sha": "",
                "last_observation_key": "",
                "last_notified_key": "",
            },
        )
        self.state.acknowledge(76)
        service = TelegramBotService(monday, client, self.state, self.config)
        service.bind_bot_identity(client.get_me())
        runner = TelegramRunner(
            client,
            service,
            self.state,
            poll_timeout=1,
            stop_event=stop,
        )

        def run() -> None:
            try:
                runner.run_forever()
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        self.assertTrue(request_saved.wait(5))

        self.assertEqual(self.state.next_offset, 101)
        self.assertEqual(self.state.job(100)["phase"], "queued")
        self.assertTrue(observation_started.is_set())

        runner.stop()
        release_observation.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.state.job(100)["phase"], "queued")

    def test_nonterminal_live_build_remains_pending_until_runner_reconciles_it(self):
        created = self.monday.task(
            "create", title="Existing", objective="Ship it", priority="P2",
        )
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )
        service = TelegramBotService(self.monday, self.client, self.state, config)
        service.bind_bot_identity(self.client.get_me())
        update = _update(update_id=27, text=f"/deliver {created.task_id}")
        delivery_id = service._delivery_id(27)
        self.monday.delivery_runs[delivery_id] = {
            "delivery_id": delivery_id,
            "task_id": created.task_id,
            "status": "running",
            "phase": "validating",
            "success": False,
            "branch": "codex/task-0001-12345678",
            "commit_sha": "",
            "pr_url": "",
            "changed_files": ["app.py"],
            "attempts": [{"number": 1, "status": "validating"}],
            "message": "Validation is still running.",
        }
        runner = TelegramRunner(self.client, service, self.state, poll_timeout=1)

        self.client.updates = [update]
        with self.assertRaises(TelegramUpdatePendingError):
            runner.run_once()

        pending = self.state.job(27)
        self.assertFalse(pending["delivery_terminal"])
        self.assertNotIn("attempt_count", pending)
        self.assertEqual(pending["phase"], "waiting")
        self.assertEqual(self.state.next_offset, 28)
        self.assertFalse(self.state.is_terminal(27))

        self.monday.delivery_runs[delivery_id].update({
            "status": "pr-open",
            "phase": "completed",
            "success": True,
            "commit_sha": "a" * 40,
            "pr_url": "https://github.com/example/mondayos/pull/55",
            "message": "Pull request opened.",
        })
        self.client.updates = [update]

        self.assertEqual(runner.run_once(), 1)
        self.assertEqual(self.state.next_offset, 28)
        self.assertEqual(self.state.completed_job(27)["delivery_status"], "pr-open")
        self.assertEqual(len(self.monday.build_calls), 2)

    def test_pending_update_uses_backoff_without_failure_counting(self):
        stop = threading.Event()
        waits = []
        update = _update(update_id=28, text="/help")

        class PendingService:
            def announce_acceptance_receipt(self, _notice):
                return True

            def handle_update(self, _update):
                raise TelegramUpdatePendingError("still running")

        def wait(seconds):
            waits.append(seconds)
            if len(waits) == 3:
                stop.set()
            return True

        self.client.updates = [update]
        runner = TelegramRunner(
            self.client,
            PendingService(),
            self.state,
            poll_timeout=1,
        )
        self.assertEqual(runner.ingest_once(), 1)
        worker = TelegramJobWorker(
            PendingService(),
            self.state,
            stop_event=stop,
            wake_event=threading.Event(),
            wait=wait,
        )

        worker.run_forever()

        self.assertEqual(waits, [1, 2, 4])
        self.assertEqual(self.client.offsets, [None])
        self.assertEqual(self.state.job(28)["phase"], "waiting")
        self.assertEqual(self.state.next_offset, 29)
        self.assertEqual(self.state.pending_count, 1)
        self.assertNotIn("attempt_count", self.state.job(28))
        self.assertFalse(self.state.is_terminal(28))

    def test_same_bot_identity_retains_persisted_offset(self):
        self.state.bind_bot_identity(99, "monday_test_bot")
        self.state.acknowledge(61)
        self.client.identity = {"id": 99, "username": "Renamed_Monday_Bot"}
        runner = TelegramRunner(self.client, self.service, self.state, poll_timeout=1)

        runner.start()
        runner.run_once()

        self.assertEqual(self.state.next_offset, 62)
        self.assertEqual(self.client.offsets, [62])

    def test_bot_rename_cannot_invalidate_an_addressed_queued_command(self):
        self.client.identity = {"id": 99, "username": "old_monday_bot"}
        runner = TelegramRunner(
            self.client, self.service, self.state, poll_timeout=1,
        )
        runner.start()
        self.client.updates = [
            _update(
                update_id=62,
                text="/build@old_monday_bot Keep this queued request",
            ),
        ]

        self.assertEqual(runner.ingest_once(), 1)
        saved = self.state.job(62)["request"]["message"]["text"]
        self.assertEqual(saved, "/build Keep this queued request")
        self.assertFalse(self.monday.tasks)

        self.client.identity = {"id": 99, "username": "new_monday_bot"}
        restarted_state = TelegramState(self.config.state_path)
        restarted_service = TelegramBotService(
            self.monday, self.client, restarted_state, self.config,
        )
        restarted = TelegramRunner(
            self.client, restarted_service, restarted_state, poll_timeout=1,
        )
        restarted.start()

        self.assertEqual(restarted.run_once(), 0)
        self.assertEqual(len(self.monday.tasks), 1)
        self.assertEqual(self.monday.team_calls, 1)
        self.assertEqual(restarted_state.pending_count, 0)

    def test_changed_bot_identity_resets_offset_before_polling(self):
        self.state.bind_bot_identity(88, "old_bot")
        self.state.set_job(4, task_id="TASK-OLD")
        self.state.acknowledge(61)
        self.monday.task(
            "create",
            title="Old bot task",
            objective="Do not reconcile this for the new bot.",
            priority="P2",
            context="Requested through Telegram. Idempotency marker: "
            "telegram:update:4:bot:88",
        )
        self.client.identity = {"id": 99, "username": "monday_test_bot"}
        self.client.updates = [_update(update_id=4, text="Build the new bot request")]
        runner = TelegramRunner(self.client, self.service, self.state, poll_timeout=1)

        runner.start()
        runner.run_once()

        self.assertEqual(self.client.offsets, [None])
        self.assertEqual(self.state.next_offset, 5)
        self.assertFalse(self.state.is_terminal(61))
        self.assertEqual(self.state.job(4), {})
        self.assertEqual(len(self.monday.tasks), 2)
        newest = self.monday.tasks["TASK-0002"]
        self.assertIn("telegram:update:4:bot:99", newest["context"])

    def test_completed_tombstone_suppresses_an_unexpected_replay(self):
        update = _update(update_id=14, text="/help")
        self.client.updates = [update]
        runner = TelegramRunner(self.client, self.service, self.state, poll_timeout=1)
        runner.run_once()
        sent = len(self.client.sent)

        self.client.updates = [update]
        self.assertEqual(runner.run_once(), 1)

        self.assertEqual(len(self.client.sent), sent)
        self.assertTrue(self.state.is_terminal(14))

    def test_retryable_final_send_replays_without_duplicate_task_or_team(self):
        update = _update(update_id=15, text="Build a retry-safe endpoint")

        class FailingFinalSendClient(FakeTelegramClient):
            def __init__(self):
                super().__init__([update])
                self.send_count = 0
                self.fail_enabled = True

            def send_message(self, chat_id, text):
                self.send_count += 1
                if self.fail_enabled and self.send_count == 5:
                    raise TelegramAPIError("temporary outage", retryable=True)
                return super().send_message(chat_id, text)

        client = FailingFinalSendClient()
        service = TelegramBotService(self.monday, client, self.state, self.config)
        runner = TelegramRunner(client, service, self.state, poll_timeout=1)

        with self.assertRaises(TelegramAPIError):
            runner.run_once()
        self.assertEqual(len(self.monday.tasks), 1)
        self.assertEqual(self.monday.team_calls, 1)
        self.assertEqual(self.state.next_offset, 16)
        self.assertEqual(self.state.job(15)["phase"], "waiting")

        client.fail_enabled = False
        client.updates = [update]
        self.assertEqual(runner.run_once(), 1)
        self.assertEqual(len(self.monday.tasks), 1)
        self.assertEqual(self.monday.team_calls, 1)
        self.assertEqual(self.state.next_offset, 16)

    def test_start_verifies_identity_and_removes_webhook_without_dropping_updates(self):
        runner = TelegramRunner(self.client, self.service, self.state)
        identity = runner.start()
        self.assertEqual(identity["username"], "monday_test_bot")
        self.assertTrue(self.client.webhook_deleted)

    def test_rate_limit_waits_requested_interval(self):
        stop = threading.Event()
        waits = []

        class LimitedClient(FakeTelegramClient):
            def get_updates(self, *, offset, timeout):
                raise TelegramRateLimitError(9)

        def wait(seconds):
            waits.append(seconds)
            stop.set()
            return True

        client = LimitedClient()
        runner = TelegramRunner(
            client,
            self.service,
            self.state,
            stop_event=stop,
            wait=wait,
        )
        runner.run_forever()
        self.assertEqual(waits, [9])

    def test_daemon_bounds_hostile_rate_limit_before_wait_and_marks_backoff(self):
        stop = threading.Event()
        waits = []
        observed = []
        state = self.state

        class LimitedClient(FakeTelegramClient):
            def get_updates(self, *, offset, timeout):
                error = TelegramRateLimitError(1)
                error.retry_after = 10**10_000
                raise error

        def wait(seconds):
            waits.append(seconds)
            observed.append(state.worker_status())
            stop.set()
            return True

        runner = TelegramRunner(
            LimitedClient(),
            self.service,
            state,
            stop_event=stop,
            wait=wait,
        )

        runner.run_forever()

        self.assertEqual(waits, [86_400.0])
        intake = observed[0]["intake"]
        self.assertEqual(intake["state"], "backoff")
        self.assertEqual(intake["operation"], "rate-limit")
        self.assertFalse(intake["ready"])
        self.assertEqual(intake["resume_at"] - intake["progress_at"], 86_400.0)

    def test_worker_rate_limit_backoff_stays_unready_until_new_progress(self):
        self.state.enqueue(36, _update(update_id=36, text="/help"))
        self._complete_acceptance_receipt(36)
        now = time.time()
        self.state.mark_worker_started(now=now)
        self.state.mark_runtime_lane(
            "intake",
            "active",
            "poll",
            timeout_seconds=60,
            now=now,
        )
        stop = threading.Event()
        waits = []
        observed = []

        class RateLimitedService:
            def handle_update(self, update):
                error = TelegramRateLimitError(1)
                error.retry_after = 10**10_000
                raise error

        def wait(seconds):
            waits.append(seconds)
            observed.append(self.state.worker_status())
            stop.set()
            return True

        worker = TelegramJobWorker(
            RateLimitedService(),
            self.state,
            stop_event=stop,
            wake_event=threading.Event(),
            wait=wait,
        )
        worker.enable_runtime_leases(True)

        worker.run_forever()

        self.assertEqual(waits, [86_400.0])
        checkpoint = observed[0]["worker"]
        self.assertEqual(checkpoint["state"], "backoff")
        self.assertEqual(checkpoint["operation"], "rate-limit")
        self.assertFalse(checkpoint["ready"])
        after_resume = self.state.worker_status(
            now=checkpoint["resume_at"] + 1.0,
            stale_after_seconds=90_000.0,
        )
        self.assertTrue(after_resume["heartbeat_fresh"])
        self.assertFalse(after_resume["worker"]["ready"])

    def test_unexpected_handler_failure_retries_then_dead_letters(self):
        class BrokenService:
            def __init__(self):
                self.notices = []

            def announce_acceptance_receipt(self, _notice):
                return True

            def handle_update(self, _update):
                raise RuntimeError("boom")

            def report_error(self, _update, *, will_retry):
                self.notices.append(will_retry)

        service = BrokenService()
        runner = TelegramRunner(self.client, service, self.state, poll_timeout=1)
        update = _update(update_id=31, text="/help")

        for expected_attempt in (1, 2):
            self.client.updates = [update]
            with self.assertRaises(TelegramUpdateRetryError):
                runner.run_once()
            self.assertEqual(self.state.next_offset, 32)
            self.assertEqual(self.state.job(31)["attempt_count"], expected_attempt)

        self.client.updates = [update]
        self.assertEqual(runner.run_once(), 1)
        self.assertEqual(self.state.next_offset, 32)
        self.assertEqual(self.state.failed_job(31)["attempt_count"], 3)
        self.assertEqual(service.notices, [True, True, False])

    def test_permanent_error_notice_failure_does_not_escape_retry_lifecycle(self):
        class BrokenService:
            def announce_acceptance_receipt(self, _notice):
                return True

            def handle_update(self, _update):
                raise RuntimeError("handler failed")

            def report_error(self, _update, *, will_retry):
                raise TelegramAPIError(
                    "user blocked bot",
                    retryable=False,
                    status_code=403,
                )

        update = _update(update_id=35, text="/help")
        self.client.updates = [update]
        runner = TelegramRunner(self.client, BrokenService(), self.state, poll_timeout=1)

        with self.assertRaises(TelegramUpdateRetryError):
            runner.run_once()

        self.assertEqual(self.state.job(35)["attempt_count"], 1)
        self.assertEqual(self.state.next_offset, 36)
        self.assertFalse(self.state.is_terminal(35))

    def test_permanent_send_failure_dead_letters_only_that_update(self):
        handled = []

        class SelectiveService:
            def announce_acceptance_receipt(self, _notice):
                return True

            def handle_update(self, update):
                if update["update_id"] == 40:
                    raise TelegramAPIError(
                        "Telegram sendMessage failed with HTTP 403",
                        retryable=False,
                        status_code=403,
                    )
                handled.append(update["update_id"])

        self.client.updates = [
            _update(update_id=40, text="/help"),
            _update(update_id=41, text="/help"),
        ]
        runner = TelegramRunner(self.client, SelectiveService(), self.state)

        self.assertEqual(runner.run_once(), 2)
        self.assertEqual(handled, [41])
        self.assertEqual(self.state.next_offset, 42)
        self.assertEqual(self.state.failed_job(40)["phase"], "dead-letter")


class TestHttpTelegramClient(unittest.TestCase):
    @staticmethod
    def _response(result):
        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = json.dumps({"ok": True, "result": result}).encode()
        return response

    @staticmethod
    def _raw_response(payload):
        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = json.dumps(payload).encode()
        return response

    @patch("urllib.request.urlopen")
    def test_get_updates_uses_offset_timeout_and_message_filter(self, urlopen):
        urlopen.return_value = self._response([{"update_id": 4}])
        client = HttpTelegramClient("token")
        rows = client.get_updates(offset=4, timeout=20)
        self.assertEqual(rows, [{"update_id": 4}])
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode())
        self.assertEqual(payload["offset"], 4)
        self.assertEqual(payload["timeout"], 20)
        self.assertEqual(payload["allowed_updates"], ["message"])
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 30)

    @patch("urllib.request.urlopen")
    def test_send_message_splits_at_telegram_limit(self, urlopen):
        urlopen.return_value = self._response({"message_id": 1})
        client = HttpTelegramClient("token")
        client.send_message(7, "x" * 5000)
        self.assertEqual(urlopen.call_count, 2)
        for call in urlopen.call_args_list:
            payload = json.loads(call.args[0].data.decode())
            self.assertLessEqual(len(payload["text"]), 4096)

    @patch("urllib.request.urlopen")
    def test_rate_limit_is_structured_and_token_never_appears(self, urlopen):
        body = BytesIO(json.dumps({
            "ok": False,
            "description": "Too Many Requests for secret-token",
            "parameters": {"retry_after": 12},
        }).encode())
        urlopen.side_effect = HTTPError("ignored", 429, "rate", {}, body)
        client = HttpTelegramClient("secret-token")
        with self.assertRaises(TelegramRateLimitError) as raised:
            client.get_me()
        self.assertEqual(raised.exception.retry_after, 12)
        self.assertNotIn("secret-token", str(raised.exception))

    @patch("urllib.request.urlopen")
    def test_retry_after_is_bounded_before_exception_rendering(self, urlopen):
        body = BytesIO(json.dumps({
            "ok": False,
            "error_code": 429,
            "parameters": {"retry_after": 10**1_000},
        }).encode())
        urlopen.side_effect = HTTPError("ignored", 429, "rate", {}, body)

        with self.assertRaises(TelegramRateLimitError) as raised:
            HttpTelegramClient("token").get_me()

        self.assertEqual(raised.exception.retry_after, 86_400)
        self.assertIn("86400", str(raised.exception))
        direct = TelegramRateLimitError(10**10_000)
        self.assertEqual(direct.retry_after, 86_400)
        self.assertLess(len(str(direct)), 100)

    @patch("urllib.request.urlopen")
    def test_oversized_retry_after_text_uses_bounded_default(self, urlopen):
        body = BytesIO(json.dumps({
            "ok": False,
            "error_code": 429,
            "parameters": {"retry_after": "9" * 10_000},
        }).encode())
        urlopen.side_effect = HTTPError("ignored", 429, "rate", {}, body)

        with self.assertRaises(TelegramRateLimitError) as raised:
            HttpTelegramClient("token").get_me()

        self.assertEqual(raised.exception.retry_after, 5)

    @patch("urllib.request.urlopen")
    def test_http_429_without_retry_after_uses_retryable_default(self, urlopen):
        body = BytesIO(json.dumps({
            "ok": False,
            "error_code": 429,
            "description": "Too Many Requests",
        }).encode())
        urlopen.side_effect = HTTPError("ignored", 429, "rate", {}, body)

        with self.assertRaises(TelegramRateLimitError) as raised:
            HttpTelegramClient("token").get_me()

        self.assertTrue(raised.exception.retryable)
        self.assertGreater(raised.exception.retry_after, 0)

    @patch("urllib.request.urlopen")
    def test_api_429_without_retry_after_uses_retryable_default(self, urlopen):
        urlopen.return_value = self._raw_response({
            "ok": False,
            "error_code": 429,
            "description": "Too Many Requests",
        })

        with self.assertRaises(TelegramRateLimitError) as raised:
            HttpTelegramClient("token").get_me()

        self.assertTrue(raised.exception.retryable)
        self.assertGreater(raised.exception.retry_after, 0)

    @patch("urllib.request.urlopen", side_effect=OSError("secret-token"))
    def test_transport_error_is_sanitized(self, _urlopen):
        client = HttpTelegramClient("secret-token")
        with self.assertRaises(TelegramAPIError) as raised:
            client.get_me()
        self.assertNotIn("secret-token", str(raised.exception))

    def test_malformed_token_is_rejected_without_echoing_it(self):
        secret = "secret token"
        with self.assertRaises(ValueError) as raised:
            HttpTelegramClient(secret)
        self.assertNotIn(secret, str(raised.exception))


class TestTelegramIdentify(unittest.TestCase):
    @staticmethod
    def _mark_runtime_ready(state: TelegramState) -> None:
        now = time.time()
        state.mark_worker_started(now=now)
        state.mark_runtime_lane(
            "intake",
            "active",
            "poll",
            timeout_seconds=60,
            now=now,
        )
        state.mark_runtime_lane("worker", "idle", "idle", now=now)

    def test_health_json_is_secret_free_and_reports_running_worker(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "123:super-secret-token",
            "MONDAYOS_TELEGRAM_ALLOWED_USER_IDS": "7",
        }, clear=True), patch(
            "telegram_bot.state.instance_lock_is_held",
            return_value=True,
        ):
            root = Path(tmp)
            state = TelegramState(root / "logs" / "telegram" / "state.json")
            state.bind_bot_identity(99, "monday_bot")
            self._mark_runtime_ready(state)
            args = SimpleNamespace(
                project_root=tmp,
                health=True,
                json=True,
                identify=False,
                provider="",
                once=False,
            )
            output = StringIO()

            with redirect_stdout(output):
                result = _cmd_telegram(args)

        payload = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertTrue(payload["healthy"])
        self.assertTrue(payload["service"]["running"])
        self.assertTrue(payload["service"]["heartbeat_fresh"])
        self.assertTrue(payload["service"]["runtime_ready"])
        self.assertEqual(payload["service"]["intake"]["operation"], "poll")
        self.assertTrue(payload["service"]["intake"]["ready"])
        self.assertEqual(payload["service"]["worker"]["operation"], "idle")
        self.assertTrue(payload["service"]["worker"]["ready"])
        self.assertEqual(payload["service"]["pending_jobs"], 0)
        self.assertEqual(payload["service"]["max_pending_jobs"], 25)
        self.assertTrue(payload["service"]["accepting_work"])
        self.assertEqual(payload["service"]["pending_capacity_notices"], 0)
        self.assertEqual(payload["service"]["pending_acceptance_receipts"], 0)
        self.assertEqual(payload["service"]["pending_delivery_notices"], 0)
        self.assertIsNone(payload["build"]["ready"])
        self.assertNotIn("super-secret-token", output.getvalue())

    def test_health_fails_when_intake_stalls_behind_fresh_supervisor(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "123:super-secret-token",
            "MONDAYOS_TELEGRAM_ALLOWED_USER_IDS": "7",
        }, clear=True), patch(
            "telegram_bot.state.instance_lock_is_held",
            return_value=True,
        ):
            root = Path(tmp)
            state = TelegramState(root / "logs" / "telegram" / "state.json")
            state.bind_bot_identity(99, "monday_bot")
            started = time.time() - 120
            state.mark_worker_started(now=started)
            state.mark_runtime_lane(
                "intake",
                "active",
                "poll",
                timeout_seconds=1,
                now=started,
            )
            state.mark_runtime_lane("worker", "idle", "idle", now=started)
            state.mark_worker_heartbeat()
            args = SimpleNamespace(
                project_root=tmp,
                health=True,
                json=True,
                identify=False,
                provider="",
                once=False,
            )
            json_output = StringIO()
            with redirect_stdout(json_output):
                json_result = _cmd_telegram(args)

            args.json = False
            human_output = StringIO()
            with redirect_stdout(human_output):
                human_result = _cmd_telegram(args)

        payload = json.loads(json_output.getvalue())
        self.assertEqual(json_result, 1)
        self.assertEqual(human_result, 1)
        self.assertTrue(payload["service"]["heartbeat_fresh"])
        self.assertFalse(payload["service"]["runtime_ready"])
        self.assertFalse(payload["service"]["intake"]["ready"])
        self.assertTrue(payload["service"]["worker"]["ready"])
        self.assertIn("Intake", human_output.getvalue())
        self.assertIn("active/poll; not ready", human_output.getvalue())
        self.assertIn("Worker", human_output.getvalue())
        self.assertIn("idle/idle; ready", human_output.getvalue())
        self.assertNotIn("super-secret-token", json_output.getvalue())

    def test_health_reports_readable_state_when_legacy_heartbeat_fails_closed(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "123:super-secret-token",
            "MONDAYOS_TELEGRAM_ALLOWED_USER_IDS": "7",
        }, clear=True), patch(
            "telegram_bot.state.instance_lock_is_held",
            return_value=True,
        ):
            root = Path(tmp)
            state = TelegramState(root / "logs" / "telegram" / "state.json")
            state.bind_bot_identity(99, "monday_bot")
            state.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
            state.heartbeat_path.write_text(json.dumps({
                "started_at": 1.0,
                "heartbeat_at": 2.0,
            }), encoding="utf-8")
            args = SimpleNamespace(
                project_root=tmp,
                health=True,
                json=True,
                identify=False,
                provider="",
                once=False,
            )
            output = StringIO()
            with redirect_stdout(output):
                result = _cmd_telegram(args)

        payload = json.loads(output.getvalue())
        self.assertEqual(result, 1)
        self.assertFalse(payload["healthy"])
        self.assertTrue(payload["service"]["state_readable"])
        self.assertTrue(payload["service"]["running"])
        self.assertFalse(payload["service"]["heartbeat_fresh"])
        self.assertFalse(payload["service"]["runtime_ready"])
        self.assertIn("heartbeat", payload["service"]["heartbeat_reason"].lower())
        self.assertNotIn("super-secret-token", output.getvalue())

    def test_health_is_unhealthy_for_unresolved_acceptance_receipt(self):
        secret = "receipt health must never reveal this objective"
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "123:super-secret-token",
            "MONDAYOS_TELEGRAM_ALLOWED_USER_IDS": "7",
        }, clear=True), patch(
            "telegram_bot.state.instance_lock_is_held",
            return_value=True,
        ):
            root = Path(tmp)
            state = TelegramState(root / "logs" / "telegram" / "state.json")
            state.bind_bot_identity(99, "monday_bot")
            self._mark_runtime_ready(state)
            state.enqueue(1, _update(update_id=1, text=secret))
            args = SimpleNamespace(
                project_root=tmp,
                health=True,
                json=True,
                identify=False,
                provider="",
                once=False,
            )
            output = StringIO()

            with redirect_stdout(output):
                result = _cmd_telegram(args)

        payload = json.loads(output.getvalue())
        self.assertEqual(result, 1)
        self.assertFalse(payload["healthy"])
        self.assertEqual(payload["service"]["pending_jobs"], 1)
        self.assertEqual(payload["service"]["pending_acceptance_receipts"], 1)
        self.assertNotIn(secret, output.getvalue())
        self.assertNotIn("super-secret-token", output.getvalue())

    def test_health_json_is_unhealthy_while_capacity_notice_is_unresolved(self):
        secret = "refused request must stay out of health output"
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "123:super-secret-token",
            "MONDAYOS_TELEGRAM_ALLOWED_USER_IDS": "7",
        }, clear=True), patch(
            "telegram_bot.state.instance_lock_is_held",
            return_value=True,
        ):
            root = Path(tmp)
            state = TelegramState(root / "logs" / "telegram" / "state.json")
            state.bind_bot_identity(99, "monday_bot")
            self._mark_runtime_ready(state)
            with patch("telegram_bot.state.MAX_PENDING_JOBS", 1):
                state.enqueue(1, _update(update_id=1, text="fills temporary cap"))
                state.enqueue(2, _update(update_id=2, text=secret))
            state.acknowledge(1)
            args = SimpleNamespace(
                project_root=tmp,
                health=True,
                json=True,
                identify=False,
                provider="",
                once=False,
            )
            output = StringIO()

            with redirect_stdout(output):
                result = _cmd_telegram(args)

        payload = json.loads(output.getvalue())
        self.assertEqual(result, 1)
        self.assertFalse(payload["healthy"])
        self.assertEqual(payload["service"]["pending_jobs"], 0)
        self.assertTrue(payload["service"]["accepting_work"])
        self.assertEqual(payload["service"]["pending_capacity_notices"], 1)
        self.assertNotIn(secret, output.getvalue())
        self.assertNotIn("super-secret-token", output.getvalue())

    def test_health_json_full_inbox_is_not_ready_for_new_work(self):
        secret = "private full-inbox objective"
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "123:super-secret-token",
            "MONDAYOS_TELEGRAM_ALLOWED_USER_IDS": "7",
        }, clear=True), patch(
            "telegram_bot.state.instance_lock_is_held",
            return_value=True,
        ):
            root = Path(tmp)
            state = TelegramState(root / "logs" / "telegram" / "state.json")
            state.bind_bot_identity(99, "monday_bot")
            self._mark_runtime_ready(state)
            for update_id in range(1, 26):
                self.assertTrue(state.enqueue(
                    update_id,
                    _update(update_id=update_id, text=f"{secret} {update_id}"),
                )[0])
            args = SimpleNamespace(
                project_root=tmp,
                health=True,
                json=True,
                identify=False,
                provider="",
                once=False,
            )
            output = StringIO()

            with redirect_stdout(output):
                result = _cmd_telegram(args)

        payload = json.loads(output.getvalue())
        self.assertEqual(result, 1)
        self.assertFalse(payload["healthy"])
        self.assertEqual(payload["service"]["pending_jobs"], 25)
        self.assertEqual(payload["service"]["max_pending_jobs"], 25)
        self.assertFalse(payload["service"]["accepting_work"])
        self.assertNotIn(secret, output.getvalue())
        self.assertNotIn("super-secret-token", output.getvalue())

    def test_health_reports_legacy_over_capacity_and_human_queue_usage(self):
        secret = "private legacy-over-capacity objective"
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "123:super-secret-token",
            "MONDAYOS_TELEGRAM_ALLOWED_USER_IDS": "7",
        }, clear=True), patch(
            "telegram_bot.state.instance_lock_is_held",
            return_value=True,
        ):
            root = Path(tmp)
            state = TelegramState(root / "logs" / "telegram" / "state.json")
            state.bind_bot_identity(99, "monday_bot")
            self._mark_runtime_ready(state)
            with patch("telegram_bot.state.MAX_PENDING_JOBS", 26):
                for update_id in range(1, 27):
                    self.assertTrue(state.enqueue(
                        update_id,
                        _update(update_id=update_id, text=f"{secret} {update_id}"),
                    )[0])
            args = SimpleNamespace(
                project_root=tmp,
                health=True,
                json=True,
                identify=False,
                provider="",
                once=False,
            )
            json_output = StringIO()
            with redirect_stdout(json_output):
                json_result = _cmd_telegram(args)

            args.json = False
            human_output = StringIO()
            with redirect_stdout(human_output):
                human_result = _cmd_telegram(args)

        payload = json.loads(json_output.getvalue())
        self.assertEqual(json_result, 1)
        self.assertEqual(human_result, 1)
        self.assertFalse(payload["healthy"])
        self.assertEqual(payload["service"]["pending_jobs"], 26)
        self.assertEqual(payload["service"]["max_pending_jobs"], 25)
        self.assertFalse(payload["service"]["accepting_work"])
        self.assertIn("Telegram health: UNHEALTHY", human_output.getvalue())
        self.assertIn(
            "Inbox        : 26/25 pending; not accepting work",
            human_output.getvalue(),
        )
        self.assertNotIn(secret, json_output.getvalue())
        self.assertNotIn(secret, human_output.getvalue())
        self.assertNotIn("super-secret-token", json_output.getvalue())

    def test_health_json_fails_closed_when_live_build_is_not_ready(self):
        response = SimpleNamespace(
            success=True,
            data={"ready": False},
            message="Build capabilities are unavailable",
        )
        monday = MagicMock()
        monday.build.return_value = response
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "123:abc",
            "MONDAYOS_TELEGRAM_ALLOWED_USER_IDS": "7",
            "MONDAYOS_TELEGRAM_LIVE_BUILD": "true",
        }, clear=True), patch(
            "telegram_bot.state.instance_lock_is_held",
            return_value=True,
        ), patch("monday.cli._monday", return_value=monday):
            root = Path(tmp)
            state = TelegramState(root / "logs" / "telegram" / "state.json")
            state.bind_bot_identity(99, "monday_bot")
            self._mark_runtime_ready(state)
            args = SimpleNamespace(
                project_root=tmp,
                health=True,
                json=True,
                identify=False,
                provider="",
                once=False,
            )
            output = StringIO()

            with redirect_stdout(output):
                result = _cmd_telegram(args)

        payload = json.loads(output.getvalue())
        self.assertEqual(result, 1)
        self.assertFalse(payload["healthy"])
        self.assertTrue(payload["build"]["required"])
        self.assertFalse(payload["build"]["ready"])

    def test_health_json_reports_missing_configuration(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            args = SimpleNamespace(
                project_root=tmp,
                health=True,
                json=True,
                identify=False,
                provider="",
                once=False,
            )
            output = StringIO()

            with redirect_stdout(output):
                result = _cmd_telegram(args)

        payload = json.loads(output.getvalue())
        self.assertEqual(result, 1)
        self.assertFalse(payload["healthy"])
        self.assertIn("TELEGRAM_BOT_TOKEN", payload["configuration"]["reason"])

    def test_health_json_redacts_secrets_from_capability_failures(self):
        token = "123:telegram-secret-value"
        provider_key = "deepseek-secret-value"
        monday = MagicMock()
        monday.build.side_effect = RuntimeError(f"failed with {token} and {provider_key}")
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": token,
            "MONDAYOS_TELEGRAM_ALLOWED_USER_IDS": "7",
            "MONDAYOS_TELEGRAM_LIVE_BUILD": "true",
            "DEEPSEEK_API_KEY": provider_key,
        }, clear=True), patch(
            "telegram_bot.state.instance_lock_is_held",
            return_value=True,
        ), patch("monday.cli._monday", return_value=monday):
            root = Path(tmp)
            state = TelegramState(root / "logs" / "telegram" / "state.json")
            state.bind_bot_identity(99, "monday_bot")
            self._mark_runtime_ready(state)
            args = SimpleNamespace(
                project_root=tmp,
                health=True,
                json=True,
                identify=False,
                provider="",
                once=False,
            )
            output = StringIO()

            with redirect_stdout(output):
                result = _cmd_telegram(args)

        self.assertEqual(result, 1)
        self.assertNotIn(token, output.getvalue())
        self.assertNotIn(provider_key, output.getvalue())
        self.assertIn("***REDACTED***", output.getvalue())

    def test_health_is_unhealthy_for_unresolved_delivery_notice(self):
        secret = "completed request must stay out of delivery health"
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "123:super-secret-token",
            "MONDAYOS_TELEGRAM_ALLOWED_USER_IDS": "7",
        }, clear=True), patch(
            "telegram_bot.state.instance_lock_is_held",
            return_value=True,
        ), patch.object(
            TelegramState,
            "pending_delivery_notice_count",
            1,
            create=True,
        ):
            root = Path(tmp)
            state = TelegramState(root / "logs" / "telegram" / "state.json")
            state.bind_bot_identity(99, "monday_bot")
            self._mark_runtime_ready(state)
            args = SimpleNamespace(
                project_root=tmp,
                health=True,
                json=True,
                identify=False,
                provider="",
                once=False,
            )
            json_output = StringIO()
            with redirect_stdout(json_output):
                json_result = _cmd_telegram(args)

            args.json = False
            human_output = StringIO()
            with redirect_stdout(human_output):
                human_result = _cmd_telegram(args)

        payload = json.loads(json_output.getvalue())
        self.assertEqual(json_result, 1)
        self.assertEqual(human_result, 1)
        self.assertFalse(payload["healthy"])
        self.assertEqual(payload["service"]["pending_delivery_notices"], 1)
        self.assertIn("Deliveries   : 1 pending", human_output.getvalue())
        self.assertNotIn(secret, json_output.getvalue())
        self.assertNotIn(secret, human_output.getvalue())
        self.assertNotIn("super-secret-token", json_output.getvalue())

    @patch("telegram_bot.client.HttpTelegramClient")
    def test_identify_holds_worker_lock_and_does_not_confirm_updates(self, client_type):
        with TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"TELEGRAM_BOT_TOKEN": "123:abc"}, clear=False,
        ):
            root = Path(tmp)
            client = client_type.return_value
            client.get_me.return_value = {"id": 99, "username": "monday_bot"}
            client.get_updates.return_value = [_update(update_id=50, text="/start")]
            args = SimpleNamespace(
                project_root=str(root), identify=True, provider="", once=False,
            )

            self.assertEqual(_cmd_telegram(args), 0)
            client.get_updates.assert_called_once_with(offset=None, timeout=1)

            with InstanceLock(root / "logs" / "telegram" / "bot.lock"):
                with self.assertRaisesRegex(TelegramStateError, "already running"):
                    _cmd_telegram(args)

    def test_normal_start_keeps_blank_provider_for_role_fallback(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "123:abc",
            "MONDAYOS_TELEGRAM_ALLOWED_USER_IDS": "7",
        }, clear=False), patch("monday.Monday") as monday_type, patch(
            "monday.provider_env.provider_config", return_value=None,
        ), patch(
            "monday.provider_env.provider_configs", return_value=[],
        ), patch("telegram_bot.client.HttpTelegramClient"), patch(
            "telegram_bot.state.TelegramState",
        ) as state_type, patch(
            "telegram_bot.service.TelegramBotService"
        ) as service_type, patch(
            "telegram_bot.runner.TelegramRunner",
        ) as runner_type:
            os.environ.pop("MONDAYOS_TELEGRAM_PROVIDER", None)
            runner_type.return_value.start.return_value = {
                "id": 99, "username": "monday_bot",
            }
            runner_type.return_value.run_once.return_value = 0
            args = SimpleNamespace(
                project_root=tmp, identify=False, provider="", once=True,
            )

            self.assertEqual(_cmd_telegram(args), 0)

        monday_type.assert_called_once()
        state_type.return_value.mark_worker_stopped.assert_called_once_with()
        config = service_type.call_args.args[3]
        self.assertEqual(config.provider, "")

    def test_one_shot_replaces_legacy_heartbeat_with_stopped_runtime(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "123:abc",
            "MONDAYOS_TELEGRAM_ALLOWED_USER_IDS": "7",
        }, clear=False), patch("monday.Monday"), patch(
            "monday.provider_env.provider_config", return_value=None,
        ), patch(
            "monday.provider_env.provider_configs", return_value=[],
        ), patch("telegram_bot.client.HttpTelegramClient"), patch(
            "telegram_bot.service.TelegramBotService"
        ), patch(
            "telegram_bot.runner.TelegramRunner",
        ) as runner_type:
            root = Path(tmp)
            state = TelegramState(root / "logs" / "telegram" / "state.json")
            state.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
            state.heartbeat_path.write_text(json.dumps({
                "started_at": 1.0,
                "heartbeat_at": 2.0,
            }), encoding="utf-8")
            runner_type.return_value.start.return_value = {
                "id": 99,
                "username": "monday_bot",
            }
            runner_type.return_value.run_once.return_value = 0
            args = SimpleNamespace(
                project_root=tmp,
                identify=False,
                provider="",
                once=True,
            )

            self.assertEqual(_cmd_telegram(args), 0)

            stopped = TelegramState(
                root / "logs" / "telegram" / "state.json"
            ).worker_status()

        self.assertFalse(stopped["heartbeat_fresh"])
        self.assertFalse(stopped["runtime_ready"])
        self.assertEqual(stopped["intake"]["state"], "stopped")
        self.assertEqual(stopped["worker"]["state"], "stopped")


class TestTelegramMondayIntegration(unittest.TestCase):
    def test_real_monday_public_api_runs_all_five_fake_agent_stages(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = TelegramConfig(
                token=_fake_token(),
                allowed_user_ids=frozenset({7}),
                project_root=root,
                provider="fake",
            )
            monday = Monday(MondayConfig(project_root=root))
            client = FakeTelegramClient()
            state = TelegramState(config.state_path)
            service = TelegramBotService(monday, client, state, config)

            service.handle_update(_update(text="Build a retry-safe health endpoint"))

            tasks = monday.task("list_active")
            self.assertEqual(tasks.data["count"], 1)
            task_id = tasks.data["tasks"][0]["id"]
            self.assertEqual(tasks.data["tasks"][0]["status"], "review")
            history = monday.team("history", task_id=task_id)
            self.assertEqual(history.data["count"], 1)
            self.assertEqual(history.data["runs"][0]["status"], "awaiting-approval")
            self.assertEqual(len(history.data["runs"][0]["stages"]), 5)

    def test_telegram_cannot_approve_a_non_gate_team_child(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = TelegramConfig(
                token=_fake_token(),
                allowed_user_ids=frozenset({7}),
                project_root=root,
                provider="fake",
            )
            monday = Monday(MondayConfig(project_root=root))
            client = FakeTelegramClient()
            service = TelegramBotService(
                monday,
                client,
                TelegramState(config.state_path),
                config,
            )
            service.handle_update(_update())
            team = monday.team("history", limit=1).data["runs"][0]
            non_gate_child = team["child_run_ids"][0]

            service.handle_update(
                _update(update_id=11, text=f"/approve {non_gate_child}")
            )

            task = monday.task("get", task_id=team["task_id"])
            self.assertEqual(task.data["status"], "review")
            current_team = monday.team("history", limit=1).data["runs"][0]
            self.assertEqual(current_team["status"], "awaiting-approval")
            self.assertEqual(current_team["approval"], {})
            self.assertIn("cannot be reviewed directly", client.sent[-1][1])

    def test_telegram_approval_is_idempotent_and_opposite_is_immutable(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = TelegramConfig(
                token=_fake_token(),
                allowed_user_ids=frozenset({7}),
                project_root=root,
                provider="fake",
            )
            monday = Monday(MondayConfig(project_root=root))
            client = FakeTelegramClient()
            service = TelegramBotService(
                monday,
                client,
                TelegramState(config.state_path),
                config,
            )
            service.handle_update(_update())
            run = monday.team("history", limit=1).data["runs"][0]
            approval = run["approval_run_id"]

            service.handle_update(_update(update_id=11, text=f"/approve {approval}"))
            approved_run = next(
                item
                for item in monday.agent("history", limit=500).data["runs"]
                if item["run_id"] == approval
            )
            original_approval = dict(approved_run["approval"])
            service.handle_update(_update(update_id=12, text=f"/approve {approval}"))

            task = monday.task("get", task_id=run["task_id"])
            self.assertEqual(task.data["status"], "completed")
            self.assertIn("already approved", client.sent[-1][1])

            # Simulate a crash after the child decision but before its parent
            # checkpoint. Even an opposite command must reconcile the stored
            # first decision before reporting that it was not changed.
            parent_path = root / "logs" / "agents" / f"{run['team_run_id']}.json"
            parent = json.loads(parent_path.read_text(encoding="utf-8"))
            parent["status"] = "awaiting-approval"
            parent["success"] = True
            parent["approval"] = {}
            parent_path.write_text(json.dumps(parent), encoding="utf-8")

            service.handle_update(
                _update(update_id=13, text=f"/reject {approval} changed my mind")
            )

            reviewed_run = next(
                item
                for item in monday.agent("history", limit=500).data["runs"]
                if item["run_id"] == approval
            )
            self.assertEqual(reviewed_run["approval"], original_approval)
            self.assertEqual(
                monday.team("history", limit=1).data["runs"][0]["status"],
                "completed",
            )
            self.assertIn("already approved", client.sent[-1][1])
            self.assertIn("not changed", client.sent[-1][1])
