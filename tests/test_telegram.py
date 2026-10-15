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
        TelegramRunner(self.client, service, self.state).start()

        service.handle_update(_update(text="/build Ship a health endpoint"))

        self.assertEqual(len(self.monday.tasks), 1)
        self.assertEqual(self.monday.team_calls, 0)
        self.assertEqual(self.monday.build_executions, 1)
        action, request = self.monday.build_calls[-1]
        self.assertEqual(action, "run")
        self.assertEqual(request["task_id"], "TASK-0001")
        self.assertRegex(request["delivery_id"], r"^delivery-telegram-[0-9a-f]{20}$")
        job = self.state.job(10)
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

        with self.assertRaises(TelegramUpdatePendingError):
            service.handle_update(update)

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
        service.handle_update(update)

        settled = self.state.job(20)
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
            project_name="",
            delivery_id=root_delivery_id,
            delivery_terminal=True,
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
            5,
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
        self.assertNotIn("private provider diagnostic", "\n".join(
            text for _chat, text in self.client.sent
        ))

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

        with self.assertRaises(TelegramAPIError):
            runner.run_once()

        self.assertEqual(self.state.job(90)["phase"], "waiting")
        self.assertTrue(self.state.delivery_retry(90))
        self.assertEqual(len(self.monday.build_calls), 1)

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

        self.assertEqual(restarted_runner.run_once(), 0)
        self.assertTrue(restarted_state.is_terminal(90))
        self.assertTrue(restarted_state.delivery_retry(90))
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

    def test_retryable_intermediate_notice_preserves_next_build_deadline(self):
        config = TelegramConfig(
            token=_fake_token(),
            allowed_user_ids=frozenset({7}),
            project_root=self.root,
            live_build=True,
        )

        class FailingThirdRunNoticeClient(FakeTelegramClient):
            def send_message(self, chat_id, text):
                if "delivery run 3/6" in text:
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
        with patch("telegram_bot.state.time.time", return_value=started + 122):
            self.assertFalse(worker.run_delivery_retry_once())
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

    def test_intermediate_rate_limit_extends_next_build_deadline(self):
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
        self.assertEqual(retry["failure_count"], 1)
        self.assertEqual(retry["next_attempt_at"], 3_761.0)

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

    def test_retry_route_must_still_be_an_allowed_private_user(self):
        self._seed_delivery_retry(95, now=100.0)
        self.state.acknowledge(95)
        payload = json.loads(self.config.state_path.read_text(encoding="utf-8"))
        payload["delivery_retries"]["95"]["chat_id"] = 999
        payload["completed_jobs"]["95"]["chat_id"] = 999
        self.config.state_path.write_text(json.dumps(payload), encoding="utf-8")
        reloaded = TelegramState(self.config.state_path)
        service = TelegramBotService(self.monday, self.client, reloaded, self.config)
        worker = TelegramJobWorker(
            service,
            reloaded,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )

        with patch("telegram_bot.state.time.time", return_value=200.0):
            self.assertTrue(worker.run_delivery_retry_once())

        self.assertEqual(self.monday.build_calls, [])
        self.assertEqual(self.client.sent, [])
        self.assertEqual(reloaded.delivery_retry(95), {})
        self.assertEqual(
            reloaded.completed_job(95)["delivery_retry"]["phase"],
            "notification-failed",
        )

    def test_revoked_retry_destination_after_expiry_stops_without_hot_loop(self):
        self._seed_delivery_retry(99, now=100.0)
        self.state.acknowledge(99)
        payload = json.loads(self.config.state_path.read_text(encoding="utf-8"))
        payload["delivery_retries"]["99"]["chat_id"] = 999
        payload["completed_jobs"]["99"]["chat_id"] = 999
        self.config.state_path.write_text(json.dumps(payload), encoding="utf-8")
        reloaded = TelegramState(self.config.state_path)
        service = TelegramBotService(self.monday, self.client, reloaded, self.config)
        worker = TelegramJobWorker(
            service,
            reloaded,
            stop_event=threading.Event(),
            wake_event=threading.Event(),
        )

        with patch("telegram_bot.state.time.time", return_value=90_000.0):
            self.assertTrue(worker.run_delivery_retry_once())

        self.assertEqual(reloaded.delivery_retry(99), {})
        self.assertIsNone(reloaded.claim_due_delivery_retry(now=90_001.0))
        self.assertEqual(self.monday.build_calls, [])

    def test_pending_fifo_delivery_does_not_starve_due_retry_lane(self):
        self._seed_delivery_retry(96, now=1.0, delay_seconds=1.0)
        self.state.acknowledge(96)
        self.state.enqueue(97, _update(update_id=97, text="/help"))
        stop = threading.Event()
        retried = []

        class PendingService:
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
            self.assertEqual(self.state.next_offset, 12)
            tombstone = self.state.completed_job(12)
            self.assertEqual(tombstone["phase"], "queue-full")
            self.assertEqual(tombstone["notice_status"], "pending")
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
            self.assertEqual(self.state.next_offset, 12)
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

    def test_existing_over_capacity_inbox_loads_and_drains(self):
        with patch("telegram_bot.state.MAX_PENDING_JOBS", 4):
            for update_id in (20, 21, 22):
                self.assertTrue(self.state.enqueue(
                    update_id,
                    _update(update_id=update_id, text=f"request {update_id}"),
                )[0])

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

        # Active records derive ownership from the minimal saved request; they
        # do not duplicate the sender or chat identity into checkpoint fields.
        self.assertTrue(_ORIGIN_KEYS.isdisjoint(self.state.job(33)))
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
        self.state.enqueue(35, invalid_group)

        self.assertEqual(self.state.queue_summary(7, 7), [])
        self.assertEqual(
            self.state.cancel_queued("JOB-35", user_id=7, chat_id=7),
            {"status": "not-found", "job_id": "JOB-35"},
        )

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

    def test_queue_full_notice_retries_after_restart_then_advances_offset(self):
        rejected_text = "retry notice without retaining this objective"
        self.state.enqueue(60, _update(update_id=60, text="fills capacity"))
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
        with (
            patch("telegram_bot.state.MAX_PENDING_JOBS", 1),
            self.assertRaises(TelegramAPIError),
        ):
            runner.ingest_once()

        self.assertEqual(self.state.next_offset, 61)
        self.assertEqual(
            self.state.completed_job(61)["notice_status"],
            "pending",
        )
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
        self.assertEqual(restarted.next_offset, 62)
        self.assertEqual(
            restarted.completed_job(61)["notice_status"],
            "sent",
        )
        self.assertEqual(restarted.pending_queue_full_notice_count, 0)
        self.assertEqual(client.attempted[0], client.attempted[1])

    def test_permanent_queue_full_notice_failure_does_not_wedge_intake(self):
        self.state.enqueue(65, _update(update_id=65, text="fills capacity"))
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
        with (
            patch("telegram_bot.state.MAX_PENDING_JOBS", 1),
            self.assertRaises(TelegramAPIError),
        ):
            runner.ingest_once()

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
        self.assertEqual(revoked_runner.ingest_once(), 0)
        self.assertEqual(len(client.attempted), 1)
        self.assertEqual(restarted.next_offset, 69)
        self.assertEqual(
            restarted.completed_job(68)["notice_status"],
            "failed",
        )

    def test_queue_full_retry_does_not_send_to_tampered_destination(self):
        self.state.enqueue(69, _update(update_id=69, text="fills capacity"))
        refused = _update(update_id=70, text="never redirect this notice")
        with patch("telegram_bot.state.MAX_PENDING_JOBS", 1):
            self.state.enqueue(70, refused)

        payload = json.loads(self.config.state_path.read_text(encoding="utf-8"))
        payload["completed_jobs"]["70"]["origin_chat_id"] = 999
        self.config.state_path.write_text(json.dumps(payload), encoding="utf-8")

        restarted = TelegramState(self.config.state_path)
        client = FakeTelegramClient()
        service = TelegramBotService(self.monday, client, restarted, self.config)
        runner = TelegramRunner(client, service, restarted, poll_timeout=1)
        self.assertEqual(runner.ingest_once(), 0)
        self.assertFalse(client.sent)
        self.assertEqual(restarted.next_offset, 71)
        self.assertEqual(
            restarted.completed_job(70)["notice_status"],
            "failed",
        )

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

                restarted = TelegramState(path)
                with self.assertRaises(TelegramStateError):
                    restarted.claim_next()

    def test_only_one_worker_can_hold_instance_lock(self):
        with InstanceLock(self.config.lock_path):
            with self.assertRaisesRegex(TelegramStateError, "already running"):
                with InstanceLock(self.config.lock_path):
                    pass

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
                if "Queue position: 2" in text:
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

    def test_unexpected_handler_failure_retries_then_dead_letters(self):
        class BrokenService:
            def __init__(self):
                self.notices = []

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
            state.mark_worker_started()
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
        self.assertEqual(payload["service"]["pending_jobs"], 0)
        self.assertEqual(payload["service"]["max_pending_jobs"], 25)
        self.assertTrue(payload["service"]["accepting_work"])
        self.assertEqual(payload["service"]["pending_capacity_notices"], 0)
        self.assertIsNone(payload["build"]["ready"])
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
            state.mark_worker_started()
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
            state.mark_worker_started()
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
            state.mark_worker_started()
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
            state.mark_worker_started()
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
            state.mark_worker_started()
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
