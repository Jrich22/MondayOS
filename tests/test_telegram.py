"""Telegram control-plane tests: no sockets, no real bot, no provider calls."""
from __future__ import annotations

import json
import os
import plistlib
import threading
import unittest
from io import BytesIO
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
from telegram_bot.runner import TelegramRunner, TelegramUpdateRetryError
from telegram_bot.service import (
    TelegramBotService,
    TelegramUpdatePendingError,
    _parse,
    _title,
)
from telegram_bot.state import InstanceLock, TelegramState, TelegramStateError


def _fake_token() -> str:
    return "secret-" + "token"


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

    def task(self, action, **kwargs):
        self.task_calls.append((action, kwargs))
        if action == "create":
            task_id = f"TASK-{len(self.tasks) + 1:04d}"
            task = {
                "id": task_id,
                "title": kwargs["title"],
                "objective": kwargs["objective"],
                "context": kwargs.get("context", ""),
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
    def test_state_survives_restart(self):
        self.state.set_job(10, task_id="TASK-0001")
        reloaded = TelegramState(self.config.state_path)
        self.assertEqual(reloaded.job(10)["task_id"], "TASK-0001")
        reloaded.acknowledge(10)
        final = TelegramState(self.config.state_path)
        self.assertEqual(final.next_offset, 11)
        self.assertEqual(final.job(10), {})
        self.assertEqual(final.completed_job(10)["task_id"], "TASK-0001")

    def test_corrupt_state_fails_closed_instead_of_replaying_updates(self):
        self.config.state_path.parent.mkdir(parents=True)
        self.config.state_path.write_text("not-json", encoding="utf-8")
        with self.assertRaises(TelegramStateError):
            TelegramState(self.config.state_path)

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
        self.assertIsNone(self.state.next_offset)
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

        class PendingClient(FakeTelegramClient):
            def get_updates(self, *, offset, timeout):
                self.offsets.append(offset)
                return [update]

        class PendingService:
            def handle_update(self, _update):
                raise TelegramUpdatePendingError("still running")

        def wait(seconds):
            waits.append(seconds)
            if len(waits) == 3:
                stop.set()
            return True

        client = PendingClient()
        runner = TelegramRunner(
            client,
            PendingService(),
            self.state,
            stop_event=stop,
            wait=wait,
        )

        runner.run_forever()

        self.assertEqual(waits, [1, 2, 4])
        self.assertEqual(client.offsets, [None, None, None])
        self.assertEqual(self.state.job(28), {})
        self.assertIsNone(self.state.next_offset)
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
                if self.fail_enabled and self.send_count == 4:
                    raise TelegramAPIError("temporary outage", retryable=True)
                return super().send_message(chat_id, text)

        client = FailingFinalSendClient()
        service = TelegramBotService(self.monday, client, self.state, self.config)
        runner = TelegramRunner(client, service, self.state, poll_timeout=1)

        with self.assertRaises(TelegramAPIError):
            runner.run_once()
        self.assertEqual(len(self.monday.tasks), 1)
        self.assertEqual(self.monday.team_calls, 1)
        self.assertIsNone(self.state.next_offset)

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
            self.assertIsNone(self.state.next_offset)
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
        ), patch("telegram_bot.service.TelegramBotService") as service_type, patch(
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
