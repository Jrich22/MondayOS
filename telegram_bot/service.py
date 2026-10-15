"""Telegram commands mapped exclusively onto the public MondayOS API."""
from __future__ import annotations

import hashlib
import re
import time
import uuid
from collections.abc import Callable
from typing import Any

from agents.types import is_agent_run_id
from brain.providers.base import safe_provider_identifier
from telegram_bot.client import TelegramAPIError, TelegramClient
from telegram_bot.config import TelegramConfig
from telegram_bot.state import TelegramState, job_id, parse_job_id

_TASK_ID = re.compile(
    r"^TASK-\d{1,64}(?:-[a-z0-9]{1,12})?$",
    re.IGNORECASE,
)
_DELIVERY_ID = re.compile(r"^delivery-[A-Za-z0-9-]{1,80}$")
_PROJECT_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{7,64}$")
_FULL_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")
_PROJECT_BUILD_DELIMITER = "::"
_FOLLOW_UP_POLL_SECONDS = 30.0
_FOLLOW_UP_SLOW_POLL_SECONDS = 300.0
_FOLLOW_UP_MAX_BACKOFF_SECONDS = 300.0
_DELIVERY_RETRY_DELAYS_SECONDS = (60, 300, 900, 3_600, 10_800)
_DELIVERY_RETRY_RECONCILE_SECONDS = 60
_DELIVERY_RETRY_WINDOW_SECONDS = 24 * 60 * 60
_MAX_DELIVERY_RUNS = 1 + len(_DELIVERY_RETRY_DELAYS_SECONDS)
_TERMINAL_PR_STATES = frozenset({"merged", "closed", "diverged"})
_DELIVERY_NOTICE_FOLLOW_UP_PHASES = frozenset({
    "", "pending", "polling", "retrying", "not-started-capacity",
})
_DELIVERY_NOTICE_UNAVAILABLE_PHASES = frozenset({
    "", "not-started-capacity", "not-started-deadline",
})
_RUNTIME_DELIVERY_OPERATIONS = frozenset({
    "preflight",
    "preparing",
    "implementing",
    "validating",
    "reviewing",
    "committing",
    "publication-ready",
    "pushing",
    "pull-request",
})
_RUNTIME_PROGRESS_OPERATIONS = _RUNTIME_DELIVERY_OPERATIONS | {"job", "team-stage"}
_ROLE_NAMES = {
    "cpo": "Product strategy",
    "lead-engineer": "Technical design",
    "qa": "Quality assurance",
    "security": "Security review",
    "reviewer": "Final product review",
}


class TelegramUpdatePendingError(RuntimeError):
    """A durable live delivery is still running and must be reconciled later."""


class TelegramBotService:
    """Authorized Telegram requests mapped onto the public MondayOS API."""

    def __init__(
        self,
        monday: Any,
        client: TelegramClient,
        state: TelegramState,
        config: TelegramConfig,
    ) -> None:
        self._monday = monday
        self._client = client
        self._state = state
        self._config = config
        self._bot_id = 0
        self._bot_username = ""
        self._runtime_progress_reporter: Callable[[str], None] | None = None

    def bind_bot_identity(self, identity: dict[str, Any]) -> None:
        """Record the verified getMe username used for addressed commands."""
        self._bot_id = _integer(identity.get("id"), 0)
        self._bot_username = str(identity.get("username") or "").strip().lstrip("@").lower()

    def bind_runtime_progress(
        self,
        reporter: Callable[[str], None] | None,
    ) -> None:
        """Bind daemon-only worker lease renewal to fixed semantic operations."""
        if reporter is not None and not callable(reporter):
            raise TypeError("runtime progress reporter must be callable")
        self._runtime_progress_reporter = reporter

    def normalize_update(self, update: dict[str, Any]) -> dict[str, Any] | None:
        """Return the minimum authorized request safe to persist in the inbox.

        Telegram payloads contain considerably more identity and message data
        than MondayOS needs.  Intake therefore applies the same allowlist and
        command-addressing rules as execution, then stores only the update,
        sender, chat, chat type, and request text (or a voice marker).  Ignored
        and unauthorized updates never enter the durable queue.
        """
        update_id = _integer(update.get("update_id"), -1)
        message = update.get("message")
        if update_id < 0 or not isinstance(message, dict):
            return None
        context = self._authorized_context(message)
        if context is None:
            return None
        chat_id, user_id, chat_type = context
        text = str(message.get("text") or "").strip()
        has_voice = bool(message.get("voice"))
        if not text and not has_voice:
            return None

        if text:
            parsed = _parse(text, self._bot_username)
            if parsed is None:
                return None
            command, argument = parsed
            if chat_type != "private" and command not in {
                "build", "run", "status", "help",
            }:
                return None
            # Addressing is an intake concern. Persist a canonical command
            # without @username so a later rename of this same bot cannot make
            # already-authorized queued work fail its second parse at execution.
            head, separator, _tail = text.partition(" ")
            if head.startswith("/") and "@" in head:
                text = head.partition("@")[0]
                if separator and argument:
                    text = f"{text} {argument}"

        normalized_message: dict[str, Any] = {
            "from": {"id": user_id, "is_bot": False},
            "chat": {"id": chat_id, "type": chat_type},
        }
        if text:
            normalized_message["text"] = text
        else:
            normalized_message["voice"] = {"queued": True}
        return {"update_id": update_id, "message": normalized_message}

    def announce_acceptance_receipt(self, notice: dict[str, Any]) -> bool:
        """Reauthorize and deliver one fixed, request-free job receipt."""
        handle = str(notice.get("job_id") or "").upper()
        try:
            parse_job_id(handle)
        except ValueError as exc:
            raise ValueError("Invalid Telegram acceptance receipt job id") from exc
        user_id = notice.get("user_id")
        chat_id = notice.get("chat_id")
        chat_type = str(notice.get("chat_type") or "")
        if (
            isinstance(user_id, bool)
            or not isinstance(user_id, int)
            or user_id <= 0
            or isinstance(chat_id, bool)
            or not isinstance(chat_id, int)
            or chat_id == 0
            or chat_type not in {"private", "group", "supergroup"}
            or (chat_type == "private" and chat_id != user_id)
            or (chat_type in {"group", "supergroup"} and chat_id > 0)
        ):
            raise ValueError("Invalid Telegram acceptance receipt destination")
        if user_id not in self._config.allowed_user_ids:
            return False
        if (
            chat_type in {"group", "supergroup"}
            and chat_id not in self._config.allowed_chat_ids
        ):
            return False
        text = (
            f"Request saved as {handle}. Use /queue to view it or "
            f"/cancel {handle} before it starts."
            if chat_type == "private"
            else f"Request saved as {handle}. Open Monday's private chat to "
            "view or cancel your queued requests."
        )
        self._client.send_message(chat_id, text)
        return True

    def announce_queue_full(self, update: dict[str, Any]) -> None:
        """Report a durable capacity refusal without repeating request text."""
        message = update.get("message")
        if not isinstance(message, dict):
            return
        context = self._authorized_context(message)
        if context is None:
            return
        self.announce_queue_full_notice({
            "user_id": context[1],
            "chat_id": context[0],
            "chat_type": context[2],
        })

    def announce_queue_full_notice(self, notice: dict[str, Any]) -> bool:
        """Reauthorize and send one request-free capacity notice."""
        user_id = notice.get("user_id")
        chat_id = notice.get("chat_id")
        chat_type = str(notice.get("chat_type") or "")
        if (
            isinstance(user_id, bool)
            or not isinstance(user_id, int)
            or user_id <= 0
            or isinstance(chat_id, bool)
            or not isinstance(chat_id, int)
            or chat_id == 0
            or chat_type not in {"private", "group", "supergroup"}
            or (chat_type == "private" and chat_id < 0)
            or (chat_type in {"group", "supergroup"} and chat_id > 0)
            or (chat_type == "private" and chat_id != user_id)
        ):
            raise ValueError("Invalid Telegram queue-full notice destination")
        if user_id not in self._config.allowed_user_ids:
            return False
        if (
            chat_type in {"group", "supergroup"}
            and chat_id not in self._config.allowed_chat_ids
        ):
            return False
        guidance = (
            "Use /queue or /cancel JOB-ID, then send it again later."
            if chat_type == "private"
            else "Open Monday's private chat and use /queue or /cancel JOB-ID, "
            "then send it again later."
        )
        self._client.send_message(
            chat_id,
            "Monday's inbox is full, so this request was not saved. "
            + guidance,
        )
        return True

    def deliver_delivery_notice(self, notice: dict[str, Any]) -> bool:
        """Reauthorize and send one request-free terminal delivery notice."""
        user_id = notice.get("user_id")
        chat_id = notice.get("chat_id")
        chat_type = str(notice.get("chat_type") or "")
        task_id = str(notice.get("task_id") or "").upper()
        delivery_id = str(notice.get("delivery_id") or "")
        notice_kind = str(notice.get("notice_kind") or "")
        follow_up_phase = notice.get("follow_up_phase", "")
        retry_unavailable_phase = notice.get("retry_unavailable_phase", "")
        if (
            isinstance(user_id, bool)
            or not isinstance(user_id, int)
            or user_id <= 0
            or isinstance(chat_id, bool)
            or not isinstance(chat_id, int)
            or chat_id != user_id
            or chat_type != "private"
            or not _TASK_ID.fullmatch(task_id)
            or not _DELIVERY_ID.fullmatch(delivery_id)
            or notice_kind not in {"retry-scheduled", "terminal-result"}
            or not isinstance(follow_up_phase, str)
            or follow_up_phase not in _DELIVERY_NOTICE_FOLLOW_UP_PHASES
            or not isinstance(retry_unavailable_phase, str)
            or retry_unavailable_phase not in _DELIVERY_NOTICE_UNAVAILABLE_PHASES
            or (
                notice_kind == "retry-scheduled"
                and (follow_up_phase or retry_unavailable_phase)
            )
        ):
            raise ValueError("Invalid Telegram delivery notice")
        if user_id not in self._config.allowed_user_ids:
            return False

        if notice_kind == "retry-scheduled":
            attempt = _integer(notice.get("attempt"), 0)
            maximum = _integer(notice.get("max_attempts"), 0)
            next_attempt_at = _finite_timestamp(notice.get("next_attempt_at"))
            if not 2 <= attempt <= maximum <= _MAX_DELIVERY_RUNS:
                raise ValueError("Invalid Telegram delivery retry notice")
            delay = max(1, min(
                86_400,
                int(max(0.0, next_attempt_at - time.time())),
            ))
            text = _delivery_retry_notice(task_id, attempt, maximum, delay)
        else:
            result = notice.get("result")
            if (
                not isinstance(result, dict)
                or str(result.get("delivery_id") or "") != delivery_id
                or not _delivery_terminal(str(result.get("status") or ""))
            ):
                raise ValueError("Invalid Telegram terminal delivery notice")
            # Provider/CLI diagnostics are not part of this durable semantic
            # notification channel. Render only the structured bounded fields.
            safe_result = dict(result)
            safe_result["message"] = ""
            text = _delivery_result(
                task_id,
                safe_result,
                follow_up_phase=str(follow_up_phase),
            )
            unavailable = _delivery_retry_unavailable_notice(
                {"phase": retry_unavailable_phase}
            )
            if unavailable:
                text = f"{text}\n{unavailable}"
        self._client.send_message(chat_id, text)
        return True

    def handle_intake_control(self, update: dict[str, Any]) -> bool:
        """Handle private state-only controls while model/build work is busy."""
        message = update.get("message")
        if not isinstance(message, dict):
            return False
        context = self._authorized_context(message)
        if context is None:
            return False
        chat_id, user_id, chat_type = context
        if chat_type != "private":
            return False
        parsed = _parse(str(message.get("text") or "").strip(), self._bot_username)
        if parsed is None:
            return False
        command, argument = parsed
        if command == "queue":
            if argument:
                self._client.send_message(chat_id, "Use /queue")
                return True
            entries = self._state.queue_summary(user_id=user_id, chat_id=chat_id)
            if not entries:
                self._client.send_message(chat_id, "Your Monday inbox is empty.")
                return True
            lines = ["Your Monday inbox:"]
            for entry in entries:
                suffix = " · /cancel " + str(entry["job_id"]) if entry["cancellable"] else ""
                lines.append(
                    f"{entry['job_id']} · {entry['phase']} · "
                    f"position {entry['position']}{suffix}"
                )
            self._client.send_message(chat_id, "\n".join(lines))
            return True
        if command != "cancel":
            return False

        parts = argument.split()
        if len(parts) != 1:
            self._client.send_message(chat_id, "Use /cancel JOB-123")
            return True
        try:
            target = parse_job_id(parts[0])
        except ValueError:
            self._client.send_message(chat_id, "Use /cancel JOB-123")
            return True
        result = self._state.cancel_queued(
            target,
            user_id=user_id,
            chat_id=chat_id,
        )
        status = str(result.get("status") or "not-found")
        handle = str(result.get("job_id") or job_id(target))
        if status in {"cancelled", "already-cancelled"}:
            response = f"{handle} is cancelled. It will not run."
        else:
            # Keep every unsuccessful result indistinguishable and stable
            # across retries as a job moves from running to completed.
            response = f"{handle} is not queued and was not cancelled."
        self._client.send_message(chat_id, response)
        return True

    def handle_update(self, update: dict[str, Any]) -> None:
        update_id = _integer(update.get("update_id"), -1)
        if update_id < 0:
            return
        message = update.get("message")
        if not isinstance(message, dict):
            return
        context = self._authorized_context(message)
        if context is None:
            return
        chat_id, user_id, chat_type = context

        text = str(message.get("text") or "").strip()
        if not text:
            if message.get("voice"):
                self._client.send_message(
                    chat_id,
                    "I received your voice note. Voice transcription is the next input "
                    "increment; send the request as text for now.",
                )
            return

        parsed = _parse(text, self._bot_username)
        if parsed is None:
            return
        command, argument = parsed
        if chat_type != "private" and command not in {"build", "run", "status", "help"}:
            return

        if command in {"queue", "cancel"}:
            self.handle_intake_control(update)
            return

        if command in {"start", "help"}:
            self._client.send_message(
                chat_id,
                _help(
                    live_build=self._config.live_build and chat_type == "private",
                    private_chat=chat_type == "private",
                ),
            )
            return
        if command == "tasks":
            self._send_tasks(chat_id)
            return
        if command == "status":
            self._send_status(chat_id, argument)
            return
        if command in {"approve", "reject"}:
            self._decide(chat_id, user_id, command, argument)
            return
        if command == "run":
            task_id = argument.split(maxsplit=1)[0].upper() if argument else ""
            if not _TASK_ID.fullmatch(task_id):
                self._client.send_message(chat_id, "Use /run TASK-0001")
                return
            self._run_task(update_id, chat_id, task_id)
            return
        if command == "deliver":
            if not self._config.live_build:
                self._client.send_message(
                    chat_id,
                    "Live builds are disabled for this Telegram controller.",
                )
                return
            task_id = argument.split(maxsplit=1)[0].upper() if argument else ""
            if not _TASK_ID.fullmatch(task_id):
                self._client.send_message(chat_id, "Use /deliver TASK-0001")
                return
            self._deliver_existing(update_id, chat_id, task_id)
            return
        if command == "build":
            objective = argument.strip()
            project_name = ""
            if _PROJECT_BUILD_DELIMITER in argument:
                project_name, objective = _project_build_request(argument)
                if not project_name or not objective:
                    self._client.send_message(
                        chat_id,
                        "Use /build PROJECT :: REQUEST to build a registered project.",
                    )
                    return
        elif command:
            self._client.send_message(chat_id, "Unknown command. Send /help for options.")
            return
        else:
            objective = text
            project_name = ""

        if not objective:
            self._client.send_message(chat_id, "Tell me what you want MondayOS to build.")
            return
        if chat_type == "private" and self._config.live_build and command in {"", "build"}:
            self._create_and_deliver(
                update_id,
                chat_id,
                user_id,
                objective,
                project_name=project_name,
            )
        else:
            self._create_and_run(
                update_id,
                chat_id,
                user_id,
                objective,
                project_name=project_name,
            )

    def report_error(self, update: dict[str, Any], *, will_retry: bool) -> None:
        """Send a generic failure only to an authorized originating chat."""
        message = update.get("message")
        if not isinstance(message, dict):
            return
        context = self._authorized_context(message)
        if context is not None:
            if will_retry:
                text = (
                    "MondayOS hit an internal error. Your request is saved and will "
                    "retry automatically from its checkpoint."
                )
            else:
                text = (
                    "MondayOS could not finish after three attempts. The failed "
                    "checkpoint was preserved; send the request again to start a "
                    "fresh run."
                )
            self._client.send_message(context[0], text)

    def poll_follow_up(self, update_id: int, claimed: dict[str, Any]) -> None:
        """Observe one exact PR lifecycle without exposing check output.

        The public build API owns GitHub identity verification and task
        reconciliation. Telegram consumes only its small structured outcome;
        it never requests or retains check logs, annotations, or descriptions.
        """
        delivery_id = str(claimed.get("delivery_id") or "")
        task_id = str(claimed.get("task_id") or "")
        project_name = str(claimed.get("project_name") or "")
        chat_id = _integer(claimed.get("chat_id"), 0)
        pr_url = _github_pr_url(claimed.get("pr_url", ""))
        if (
            not _DELIVERY_ID.fullmatch(delivery_id)
            or not _TASK_ID.fullmatch(task_id)
            or not pr_url
        ):
            raise RuntimeError("Saved Telegram lifecycle follow-up is invalid")
        if chat_id <= 0 or chat_id not in self._config.allowed_user_ids:
            raise TelegramAPIError(
                "Saved Telegram destination is no longer allowed",
                retryable=False,
            )

        kwargs: dict[str, Any] = {"delivery_id": delivery_id}
        if project_name:
            kwargs["project"] = project_name
        response = self._monday.build("observe", **kwargs)
        observation = _lifecycle_snapshot(
            response,
            delivery_id=delivery_id,
            task_id=task_id,
            project_name=project_name,
            pr_url=pr_url,
        )
        if not observation:
            failures = max(0, _integer(claimed.get("failure_count"), 0)) + 1
            self._state.defer_follow_up(
                update_id,
                delay_seconds=_follow_up_backoff(failures),
            )
            return

        pr_state = observation["pr_state"]
        checks_status = observation["checks_status"]
        notice_key = _lifecycle_notice_key(pr_state, checks_status)
        prior_notice = str(claimed.get("last_notified_key") or "")
        text = _lifecycle_notice(task_id, pr_url, observation)
        if text and notice_key != prior_notice:
            self._client.send_message(chat_id, text)
            prior_notice = notice_key

        terminal = pr_state in _TERMINAL_PR_STATES
        self._state.update_follow_up(
            update_id,
            phase="completed" if terminal else "pending",
            failure_count=0,
            next_poll_at=(
                0.0
                if terminal
                else time.time() + _follow_up_poll_interval(checks_status)
            ),
            pr_state=pr_state,
            checks_status=checks_status,
            merge_commit_sha=observation["merge_commit_sha"],
            last_observation_key=notice_key,
            last_notified_key=prior_notice,
        )

    def retry_delivery(self, update_id: int, claimed: dict[str, Any]) -> None:
        """Run one due request-free delivery retry under its saved binding."""
        delivery_id = str(claimed.get("delivery_id") or "")
        root_delivery_id = str(claimed.get("root_delivery_id") or "")
        task_id = str(claimed.get("task_id") or "")
        project_name = str(claimed.get("project_name") or "")
        chat_id = _integer(claimed.get("chat_id"), 0)
        attempt = _integer(claimed.get("attempt"), 0)
        maximum = _integer(claimed.get("max_attempts"), 0)
        attempt_started = claimed.get("attempt_started")
        post_expiry_reconciliation_started = claimed.get(
            "post_expiry_reconciliation_started"
        )
        expires_at = _finite_timestamp(claimed.get("expires_at"))
        if (
            not _DELIVERY_ID.fullmatch(delivery_id)
            or not _DELIVERY_ID.fullmatch(root_delivery_id)
            or not _TASK_ID.fullmatch(task_id)
            or bool(project_name and not _PROJECT_ID.fullmatch(project_name))
            or not 2 <= attempt <= maximum <= _MAX_DELIVERY_RUNS
            or not isinstance(attempt_started, bool)
            or not isinstance(post_expiry_reconciliation_started, bool)
            or (post_expiry_reconciliation_started and not attempt_started)
            or delivery_id != _retry_delivery_id(root_delivery_id, attempt)
        ):
            raise RuntimeError("Saved Telegram delivery retry is invalid")
        if chat_id <= 0 or chat_id not in self._config.allowed_user_ids:
            raise TelegramAPIError(
                "Saved Telegram destination is no longer allowed",
                retryable=False,
            )

        staged = claimed.get("outcome")
        if isinstance(staged, dict):
            outcome_phase = str(claimed.get("outcome_phase") or "")
            completed = self._state.completed_job(update_id)
            follow_up = completed.get("follow_up")
            follow_up_phase = (
                str(follow_up.get("phase") or "")
                if isinstance(follow_up, dict)
                else ""
            )
            self._client.send_message(
                chat_id,
                _delivery_retry_outcome_notice(
                    task_id,
                    staged,
                    attempt=attempt,
                    outcome_phase=outcome_phase,
                    follow_up_phase=follow_up_phase,
                ),
            )
            self._state.finish_delivery_retry(update_id)
            return

        now = time.time()
        if expires_at <= now and not attempt_started:
            result = _expired_delivery_snapshot(delivery_id)
            result = self._state.stage_delivery_retry_outcome(
                update_id,
                phase="expired",
                result=result,
            )
            self._client.send_message(
                chat_id,
                _delivery_retry_outcome_notice(
                    task_id,
                    result,
                    attempt=attempt,
                    outcome_phase="expired",
                    follow_up_phase="",
                ),
            )
            self._state.finish_delivery_retry(update_id)
            return
        if expires_at <= now and post_expiry_reconciliation_started:
            self._finish_uncertain_delivery_retry(
                update_id,
                chat_id=chat_id,
                task_id=task_id,
                delivery_id=delivery_id,
                attempt=attempt,
            )
            return

        kwargs: dict[str, Any] = {
            "task_id": task_id,
            "delivery_id": delivery_id,
            "progress_callback": self._retry_delivery_progress,
        }
        if project_name:
            kwargs["project"] = project_name
        post_expiry_reconciliation = expires_at <= now
        self._state.start_delivery_retry_attempt(
            update_id,
            delivery_id=delivery_id,
            post_expiry_reconciliation=post_expiry_reconciliation,
        )
        response = self._monday.build("run", **kwargs)
        authoritative_snapshot = _has_explicit_delivery_snapshot(response)
        result = _delivery_snapshot(response, fallback_id=delivery_id)
        if not authoritative_snapshot:
            if post_expiry_reconciliation:
                self._finish_uncertain_delivery_retry(
                    update_id,
                    chat_id=chat_id,
                    task_id=task_id,
                    delivery_id=delivery_id,
                    attempt=attempt,
                )
                return
            self._state.defer_delivery_retry(
                update_id,
                delay_seconds=_DELIVERY_RETRY_RECONCILE_SECONDS,
            )
            return
        if result["delivery_id"] != delivery_id:
            raise RuntimeError("MondayOS did not honor the saved retry identity")

        if not _delivery_terminal(str(result.get("status") or "")):
            # Publication reconciliation must keep this exact identity. A new
            # delivery is authorized only for a classified pre-publication
            # transient terminal result.
            if post_expiry_reconciliation:
                self._finish_uncertain_delivery_retry(
                    update_id,
                    chat_id=chat_id,
                    task_id=task_id,
                    delivery_id=delivery_id,
                    attempt=attempt,
                )
                return
            self._state.defer_delivery_retry(
                update_id,
                delay_seconds=_DELIVERY_RETRY_RECONCILE_SECONDS,
            )
            return

        if _safe_transient_delivery_failure(result) and attempt < maximum:
            next_attempt = attempt + 1
            delay = _delivery_retry_delay(
                next_attempt,
                result.get("retry_after_seconds", 0),
            )
            if time.time() + delay < expires_at:
                next_delivery_id = _retry_delivery_id(root_delivery_id, next_attempt)
                rescheduled = self._state.reschedule_delivery_retry(
                    update_id,
                    delivery_id=next_delivery_id,
                    attempt=next_attempt,
                    delay_seconds=delay,
                    failure_code=str(
                        result.get("failure_code") or "temporary-outage"
                    ),
                )
                if rescheduled.get("phase") != "deadline-exceeded":
                    return

        follow_up_phase = self._ensure_lifecycle_follow_up(
            update_id,
            chat_id,
            task_id,
            result,
            project_name,
        )
        outcome_phase = (
            "completed"
            if bool(result.get("success"))
            else (
                "expired"
                if result.get("failure_code") == "retry-expired"
                else "failed"
            )
        )
        result = self._state.stage_delivery_retry_outcome(
            update_id,
            phase=outcome_phase,
            result=result,
        )
        self._client.send_message(
            chat_id,
            _delivery_retry_outcome_notice(
                task_id,
                result,
                attempt=attempt,
                outcome_phase=outcome_phase,
                follow_up_phase=follow_up_phase,
            ),
        )
        self._state.finish_delivery_retry(update_id)

    def _finish_uncertain_delivery_retry(
        self,
        update_id: int,
        *,
        chat_id: int,
        task_id: str,
        delivery_id: str,
        attempt: int,
    ) -> None:
        """Stop bounded recovery without claiming whether publication occurred."""
        result = self._state.stage_delivery_retry_outcome(
            update_id,
            phase="uncertain",
            result=_uncertain_delivery_snapshot(delivery_id),
        )
        self._client.send_message(
            chat_id,
            _delivery_retry_outcome_notice(
                task_id,
                result,
                attempt=attempt,
                outcome_phase="uncertain",
                follow_up_phase="",
            ),
        )
        self._state.finish_delivery_retry(update_id)

    def _authorized_context(self, message: dict[str, Any]) -> tuple[int, int, str] | None:
        sender = message.get("from")
        chat = message.get("chat")
        if not isinstance(sender, dict) or not isinstance(chat, dict):
            return None
        if bool(sender.get("is_bot")):
            return None
        user_id = _integer(sender.get("id"), 0)
        chat_id = _integer(chat.get("id"), 0)
        chat_type = str(chat.get("type") or "")
        if (
            user_id <= 0
            or chat_id == 0
            or chat_type not in {"private", "group", "supergroup"}
            or (chat_type == "private" and chat_id < 0)
            or (chat_type in {"group", "supergroup"} and chat_id > 0)
            or (chat_type == "private" and chat_id != user_id)
        ):
            return None
        if user_id not in self._config.allowed_user_ids:
            return None
        if chat_type != "private" and chat_id not in self._config.allowed_chat_ids:
            return None
        return chat_id, user_id, chat_type

    def _task_for_request(
        self,
        update_id: int,
        chat_id: int,
        user_id: int,
        objective: str,
        *,
        workflow: str,
        project_name: str = "",
    ) -> tuple[str, bool, str]:
        """Create or reconcile the one task owned by this Telegram update."""
        job = self._state.job(update_id)
        recorded_workflow = str(job.get("workflow") or "")
        if recorded_workflow and recorded_workflow != workflow:
            raise RuntimeError(
                "The saved Telegram request belongs to a different workflow"
            )
        task_id = str(job.get("task_id") or "")
        # Once an update owns a task, its target is checkpointed with it. A retry
        # therefore resumes the saved target instead of trusting reparsed input.
        selected_project = (
            str(job.get("project_name") or "")
            if "project_name" in job
            else project_name.strip()
        )
        if not task_id:
            task_id = self._find_task_for_update(update_id)
        created = False
        if not task_id:
            marker = self._idempotency_marker(update_id)
            create_kwargs: dict[str, Any] = {
                "title": _title(objective),
                "objective": objective,
                "task_type": "feature",
                "priority": "P2",
                "created_by": f"human:telegram:{user_id}",
                "context": (
                    "Requested through Telegram. "
                    f"Idempotency marker: {marker}"
                ),
            }
            if selected_project:
                create_kwargs["project"] = selected_project
            response = self._monday.task("create", **create_kwargs)
            if not response.success or not response.task_id:
                self._client.send_message(
                    chat_id,
                    f"I could not create the task: {response.message}",
                )
                return "", False, ""
            task_id = str(response.task_id)
            selected_project = _external_project(response.data)
            created = True
        self._state.set_job(
            update_id,
            task_id=task_id,
            chat_id=chat_id,
            workflow=workflow,
            project_name=selected_project,
        )
        return task_id, created, selected_project

    def _create_and_run(
        self,
        update_id: int,
        chat_id: int,
        user_id: int,
        objective: str,
        *,
        project_name: str = "",
    ) -> None:
        task_id, _created, _selected_project = self._task_for_request(
            update_id,
            chat_id,
            user_id,
            objective,
            workflow="team-review",
            project_name=project_name,
        )
        if not task_id:
            return

        self._client.send_message(
            chat_id,
            f"Created {task_id}. Monday is assigning the agent team now.",
        )
        self._run_task(update_id, chat_id, task_id)

    def _create_and_deliver(
        self,
        update_id: int,
        chat_id: int,
        user_id: int,
        objective: str,
        *,
        project_name: str = "",
    ) -> None:
        task_id, _created, _selected_project = self._task_for_request(
            update_id,
            chat_id,
            user_id,
            objective,
            workflow="live-build",
            project_name=project_name,
        )
        if not task_id:
            return
        job = self._state.job(update_id)
        if not bool(job.get("live_started_announced")):
            self._client.send_message(
                chat_id,
                f"Created {task_id}. Monday is starting its live build now.",
            )
            self._state.set_job(update_id, live_started_announced=True)
        self._run_live_build(update_id, chat_id, task_id)

    def _deliver_existing(self, update_id: int, chat_id: int, task_id: str) -> None:
        task = self._monday.task("get", task_id=task_id)
        if not task.success:
            self._client.send_message(chat_id, task.message)
            return
        job = self._state.job(update_id)
        recorded_task = str(job.get("task_id") or "")
        if recorded_task and recorded_task != task_id:
            raise RuntimeError("The saved Telegram delivery belongs to another task")
        recorded_workflow = str(job.get("workflow") or "")
        if recorded_workflow and recorded_workflow != "live-build":
            raise RuntimeError("The saved Telegram request is not a live build")
        project_name = _external_project(task.data)
        if (
            "project_name" in job
            and str(job.get("project_name") or "") != project_name
        ):
            raise RuntimeError(
                "The saved Telegram delivery belongs to another project"
            )
        self._state.set_job(
            update_id,
            task_id=task_id,
            chat_id=chat_id,
            workflow="live-build",
            project_name=project_name,
        )
        if not bool(job.get("live_started_announced")):
            self._client.send_message(
                chat_id,
                f"Monday is starting a live build for {task_id}.",
            )
            self._state.set_job(update_id, live_started_announced=True)
        self._run_live_build(update_id, chat_id, task_id)

    def _run_live_build(self, update_id: int, chat_id: int, task_id: str) -> None:
        """Run or reconcile one caller-reserved autonomous delivery."""
        job = self._state.job(update_id)
        if bool(job.get("delivery_final_sent")):
            return
        stable_id = self._delivery_id(update_id)
        recorded_id = str(job.get("delivery_id") or "")
        if recorded_id and recorded_id != stable_id:
            raise RuntimeError("The saved Telegram delivery identity does not match")
        project_name = str(job.get("project_name") or "")
        saved_result = job.get("delivery_result")
        if bool(job.get("delivery_terminal")) and isinstance(saved_result, dict):
            if str(saved_result.get("delivery_id") or "") != stable_id:
                raise RuntimeError("The saved Telegram delivery result changed identity")
            retry = self._state.delivery_retry(update_id)
            if (
                not retry
                and not isinstance(job.get("delivery_retry"), dict)
                and _safe_transient_delivery_failure(saved_result)
            ):
                # The terminal result and scheduler checkpoint are separate
                # durable writes. Reconstruct the deterministic first retry if
                # a crash or power loss landed between them.
                retry, _retry_unavailable = self._schedule_initial_delivery_retry(
                    update_id,
                    chat_id,
                    task_id,
                    stable_id,
                    project_name,
                    saved_result,
                    expires_at=_finite_timestamp(
                        job.get("delivery_retry_expires_at")
                    ),
                )
            if retry:
                return
            self._ensure_lifecycle_follow_up(
                update_id,
                chat_id,
                task_id,
                saved_result,
                project_name,
            )
            return

        self._state.set_job(
            update_id,
            task_id=task_id,
            chat_id=chat_id,
            workflow="live-build",
            delivery_id=stable_id,
            delivery_reserved=True,
        )
        build_kwargs: dict[str, Any] = {
            "task_id": task_id,
            "delivery_id": stable_id,
            "progress_callback": lambda event: self._live_progress(
                update_id, chat_id, event,
            ),
        }
        if project_name:
            build_kwargs["project"] = project_name
        response = self._monday.build("run", **build_kwargs)
        result = _delivery_snapshot(response, fallback_id=stable_id)
        if result["delivery_id"] != stable_id:
            raise RuntimeError("MondayOS did not honor the reserved delivery identity")
        terminal = _delivery_terminal(str(result.get("status") or ""))
        retry_expires_at = (
            time.time() + _DELIVERY_RETRY_WINDOW_SECONDS
            if terminal and _safe_transient_delivery_failure(result)
            else 0.0
        )
        self._state.set_job(
            update_id,
            delivery_reserved=False,
            delivery_terminal=terminal,
            delivery_final_sent=False,
            delivery_status=result["status"],
            delivery_phase=result["phase"],
            delivery_result=result,
            delivery_retry_expires_at=retry_expires_at,
        )
        if terminal:
            if _safe_transient_delivery_failure(result):
                retry, _retry_unavailable = self._schedule_initial_delivery_retry(
                    update_id,
                    chat_id,
                    task_id,
                    stable_id,
                    project_name,
                    result,
                    expires_at=retry_expires_at,
                )
                if retry:
                    return
            self._ensure_lifecycle_follow_up(
                update_id,
                chat_id,
                task_id,
                result,
                project_name,
            )
            return

        # A lease-owning worker may still be building this exact delivery.
        # Report its durable state, but leave the result retryable instead of
        # presenting an in-progress snapshot as a final outcome.
        progress = _delivery_progress_text(_delivery_progress_snapshot(result))
        text = progress or _delivery_pending_result(task_id, result)
        prior = str(
            self._state.job(update_id).get("last_delivery_progress_text") or ""
        )
        if text and text != prior:
            self._client.send_message(chat_id, text)
            self._state.set_job(update_id, last_delivery_progress_text=text)
        raise TelegramUpdatePendingError(
            f"Telegram update {update_id} live delivery is still in progress"
        )

    def _schedule_initial_delivery_retry(
        self,
        update_id: int,
        chat_id: int,
        task_id: str,
        stable_id: str,
        project_name: str,
        result: dict[str, Any],
        *,
        expires_at: float,
    ) -> tuple[dict[str, Any], str]:
        """Idempotently checkpoint the first safe retry or its refusal."""
        next_attempt = 2
        delay = _delivery_retry_delay(
            next_attempt,
            result.get("retry_after_seconds", 0),
        )
        if delay < _DELIVERY_RETRY_WINDOW_SECONDS:
            retry = self._state.schedule_delivery_retry(
                update_id,
                {
                    "kind": "delivery-retry",
                    "root_delivery_id": stable_id,
                    "delivery_id": _retry_delivery_id(stable_id, next_attempt),
                    "task_id": task_id,
                    "project_name": project_name,
                    "chat_id": chat_id,
                    "attempt": next_attempt,
                    "max_attempts": _MAX_DELIVERY_RUNS,
                    "failure_code": str(
                        result.get("failure_code") or "temporary-outage"
                    ),
                },
                delay_seconds=delay,
                expires_at=expires_at,
            )
            retry_phase = str(retry.get("phase") or "")
            if retry_phase not in {
                "not-started-capacity",
                "not-started-deadline",
            }:
                return retry, ""
        else:
            retry_phase = "not-started-deadline"

        summary = {
            "phase": retry_phase,
            "root_delivery_id": stable_id,
            "delivery_id": stable_id,
            "attempt": 1,
            "max_attempts": _MAX_DELIVERY_RUNS,
            "failure_code": str(
                result.get("failure_code") or "temporary-outage"
            ),
        }
        self._state.set_job(update_id, delivery_retry=summary)
        return {}, _delivery_retry_unavailable_notice(summary)

    def _delivery_id(self, update_id: int) -> str:
        identity = f"telegram:{self._bot_id}:{update_id}".encode()
        digest = hashlib.sha256(identity).hexdigest()[:20]
        return f"delivery-telegram-{digest}"

    def _ensure_lifecycle_follow_up(
        self,
        update_id: int,
        chat_id: int,
        task_id: str,
        result: dict[str, Any],
        project_name: str,
    ) -> str:
        """Checkpoint a request-free PR observer before the build is acknowledged."""
        if str(result.get("status") or "").lower() != "pr-open":
            return ""
        delivery_id = str(result.get("delivery_id") or "")
        pr_url = _github_pr_url(result.get("pr_url", ""))
        if not bool(result.get("success")) or not _DELIVERY_ID.fullmatch(delivery_id) or not pr_url:
            return ""
        durable = self._state.job(update_id) or self._state.completed_job(update_id)
        current = durable.get("follow_up")
        if isinstance(current, dict):
            return str(current.get("phase") or "")
        follow_up = self._state.start_follow_up(
            update_id,
            {
                "kind": "pr-lifecycle",
                "phase": "pending",
                "delivery_id": delivery_id,
                "task_id": task_id,
                "project_name": project_name,
                "chat_id": chat_id,
                "pr_url": pr_url,
                "failure_count": 0,
                "next_poll_at": 0.0,
                "pr_state": "open",
                "checks_status": "unknown",
                "merge_commit_sha": "",
                "last_observation_key": "",
                "last_notified_key": "",
            },
        )
        return str(follow_up.get("phase") or "")

    def _live_progress(self, update_id: int, chat_id: int, event: Any) -> None:
        snapshot = _delivery_progress_snapshot(event)
        self._report_runtime_progress(_runtime_delivery_operation(snapshot))
        if not snapshot:
            return
        text = _delivery_progress_text(snapshot)
        prior = self._state.job(update_id)
        self._state.set_job(
            update_id,
            delivery_phase=snapshot.get("phase", ""),
            delivery_status=snapshot.get("status", ""),
            delivery_attempt=snapshot.get("attempt", 0),
            delivery_progress=snapshot,
        )
        if not text or text == str(prior.get("last_delivery_progress_text") or ""):
            return
        self._client.send_message(chat_id, text)
        self._state.set_job(update_id, last_delivery_progress_text=text)

    def _retry_delivery_progress(self, event: Any) -> None:
        """Renew a retry build lease without recreating its acknowledged job."""
        snapshot = _delivery_progress_snapshot(event)
        self._report_runtime_progress(_runtime_delivery_operation(snapshot))

    def _report_runtime_progress(self, operation: str) -> None:
        reporter = self._runtime_progress_reporter
        if reporter is None:
            return
        reporter(operation if operation in _RUNTIME_PROGRESS_OPERATIONS else "job")

    def _find_task_for_update(self, update_id: int) -> str:
        marker = self._idempotency_marker(update_id)
        response = self._monday.task("list_active")
        if not response.success:
            # Reconciliation failure is not evidence that no task exists.
            # Failing closed prevents a transient storage problem from creating
            # a second task for the same Telegram update.
            raise RuntimeError("Could not reconcile the saved Telegram task")
        for task in response.data.get("tasks", []):
            if marker in str(task.get("context") or ""):
                return str(task.get("id") or "")
        return ""

    def _idempotency_marker(self, update_id: int) -> str:
        marker = f"telegram:update:{update_id}"
        return f"{marker}:bot:{self._bot_id}" if self._bot_id > 0 else marker

    def _run_task(self, update_id: int, chat_id: int, task_id: str) -> None:
        job = self._state.job(update_id)
        team_run_id = str(job.get("team_run_id") or "")
        team_run = self._existing_team_run(
            task_id,
            team_run_id,
            allow_missing=bool(job.get("team_reserved")),
        )
        prior_status = str((team_run or {}).get("status") or "")
        if team_run is not None and prior_status in {"running", "interrupted"}:
            # The single-process lock proves no old worker is still executing.
            # Mark a still-running parent before starting one replacement. An
            # already-interrupted parent needs the same replacement but must not
            # be mistaken for a finished result and silently reused.
            self._state.set_job(update_id, recovery_pending=True)
            if prior_status == "running":
                interrupted = self._monday.team(
                    "interrupt",
                    team_run_id=team_run_id,
                    reason=(
                        "Telegram worker stopped before completion; a replacement "
                        "run will recover this request."
                    ),
                )
                if not interrupted.success:
                    raise RuntimeError("Could not checkpoint the interrupted team run")
            self._state.set_job(
                update_id,
                team_run_id="",
                interrupted_team_run_id=team_run_id,
                team_reserved=False,
                recovery_pending=False,
            )
            self._client.send_message(
                chat_id,
                "The previous agent run was interrupted. MondayOS saved it and is "
                "starting one recovery run now.",
            )
            team_run = None
            team_run_id = ""
        elif team_run is not None and (
            job.get("team_reserved") or job.get("recovery_pending")
        ):
            # The team reached a durable non-running state before this local
            # reservation was cleared. Reuse that exact result; replacing an
            # awaiting/terminal parent would duplicate provider work.
            self._state.set_job(
                update_id,
                team_run_id=team_run_id,
                team_reserved=False,
                recovery_pending=False,
            )
        if team_run is None:
            if not team_run_id:
                team_run_id = f"team-telegram-{uuid.uuid4().hex[:16]}"
            # Reserve the identity in Telegram state before Monday writes its
            # parent. A crash on either side of that write can now reconcile the
            # exact same ID instead of guessing from task history.
            self._state.set_job(
                update_id,
                task_id=task_id,
                chat_id=chat_id,
                team_run_id=team_run_id,
                team_reserved=True,
                recovery_pending=True,
            )
            response = self._monday.team(
                "run",
                task_id=task_id,
                provider=self._config.provider,
                mode="review",
                team_run_id=team_run_id,
                checkpoint_callback=lambda event: self._checkpoint(
                    update_id, chat_id, task_id, event,
                ),
                progress_callback=lambda event: self._progress(chat_id, event),
            )
            team_run = dict(response.data)
            if response.team_run_id != team_run_id:
                raise RuntimeError("MondayOS did not honor the reserved team run identity")
            self._state.set_job(
                update_id,
                task_id=task_id,
                chat_id=chat_id,
                team_run_id=response.team_run_id,
                team_reserved=False,
                recovery_pending=False,
            )
        self._client.send_message(chat_id, _team_result(task_id, team_run))

    def _existing_team_run(
        self,
        task_id: str,
        team_run_id: str,
        *,
        allow_missing: bool = False,
    ) -> dict[str, Any] | None:
        # A new Telegram update is a new /run request even when this task has
        # older history. Only a team id durably recorded for this exact update
        # is eligible for replay suppression.
        if not team_run_id:
            return None
        response = self._monday.team("get", team_run_id=team_run_id)
        if not response.success:
            if allow_missing:
                return None
            # A durable job points at a specific run. Failure to reconcile that
            # identity is not permission to spend money and run it again.
            raise RuntimeError("Could not reconcile the saved team run")
        run = dict(response.data)
        if str(run.get("task_id") or "") != task_id:
            raise RuntimeError("Saved team run does not belong to this task")
        return run

    def _checkpoint(
        self,
        update_id: int,
        chat_id: int,
        task_id: str,
        event: dict[str, Any],
    ) -> None:
        """Durably bind this update to its team before any model work begins."""
        self._report_runtime_progress("team-stage")
        if event.get("event") == "team_started" and event.get("team_run_id"):
            self._state.set_job(
                update_id,
                task_id=task_id,
                chat_id=chat_id,
                team_run_id=str(event["team_run_id"]),
                team_reserved=False,
                recovery_pending=False,
            )

    def _progress(self, chat_id: int, event: dict[str, Any]) -> None:
        self._report_runtime_progress("team-stage")
        kind = event.get("event")
        if kind == "team_started":
            self._client.send_message(chat_id, "Agent team started.")
        elif kind == "stage_finished":
            role = str(event.get("role") or "agent")
            label = _ROLE_NAMES.get(role, "Agent")
            raw_verdict = str(event.get("verdict") or "")
            raw_status = str(event.get("status") or "")
            verdict = (
                raw_verdict
                if raw_verdict in {"block", "invalid", "needs_changes", "pass"}
                else (
                    raw_status
                    if raw_status in {
                        "blocked",
                        "dry-run",
                        "failed",
                        "review",
                        "skipped",
                        "unavailable",
                        "validation-failed",
                    }
                    else "finished"
                )
            )
            provider = safe_provider_identifier(
                event.get("provider"),
                fallback="custom",
            )
            suffix = f" via {provider}" if provider != "custom" else ""
            self._client.send_message(chat_id, f"{label}: {verdict}{suffix}.")

    def _send_tasks(self, chat_id: int) -> None:
        response = self._monday.task("list_active")
        tasks = response.data.get("tasks", []) if response.success else []
        if not tasks:
            self._client.send_message(chat_id, "There are no active MondayOS tasks.")
            return
        lines = ["Active MondayOS tasks:"]
        for task in tasks[:20]:
            lines.append(f"{task.get('id')}: {task.get('title')} [{task.get('status')}]")
        self._client.send_message(chat_id, "\n".join(lines))

    def _send_status(self, chat_id: int, argument: str) -> None:
        task_id = argument.split(maxsplit=1)[0].upper() if argument else ""
        if task_id:
            if not _TASK_ID.fullmatch(task_id):
                self._client.send_message(chat_id, "Use /status TASK-0001")
                return
            response = self._monday.task("get", task_id=task_id)
            if not response.success:
                self._client.send_message(chat_id, response.message)
                return
            task = response.data
            self._client.send_message(
                chat_id,
                f"{task.get('id')}: {task.get('title')}\nStatus: {task.get('status')}\n"
                f"Priority: {task.get('priority')}",
            )
            return
        status = self._monday.status()
        health = "healthy" if status.healthy else "degraded"
        self._client.send_message(chat_id, f"MondayOS {status.version} is {health}.")

    def _decide(self, chat_id: int, user_id: int, action: str, argument: str) -> None:
        parts = argument.split(maxsplit=1)
        run_id = parts[0] if parts else ""
        note = parts[1] if len(parts) > 1 else ""
        if not run_id:
            self._client.send_message(
                chat_id,
                f"Use /{action} RUN-ID followed by an optional note.",
            )
            return
        if not is_agent_run_id(run_id):
            self._client.send_message(chat_id, f"No agent run {run_id} exists.")
            return
        # The public API does not currently expose an exact get action. An
        # unbounded history lookup is therefore required: a pending review must
        # not become unreachable merely because 500 newer stage runs exist.
        history = self._monday.agent("history", limit=0)
        if not history.success:
            raise RuntimeError("Could not reconcile the requested agent run")
        run = next(
            (item for item in history.data.get("runs", []) if item.get("run_id") == run_id),
            None,
        )
        if run is None:
            self._client.send_message(chat_id, f"No agent run {run_id} exists.")
            return
        wanted = "approved" if action == "approve" else "rejected"
        prior = str((run.get("approval") or {}).get("decision") or "")
        if prior in {"approved", "rejected"}:
            # Re-submit the stored first decision on every terminal retry so
            # AgentRuntime can repair a parent checkpoint that was interrupted.
            # The requested opposite direction never replaces that decision.
            reconciled = self._monday.agent(
                "review",
                run_id=run_id,
                approve=prior == "approved",
                by=f"human:telegram:{user_id}",
                note=note,
            )
            if not reconciled.success:
                self._client.send_message(chat_id, reconciled.message)
                return
            if prior == wanted:
                self._client.send_message(
                    chat_id,
                    f"{run_id} was already {prior}; its team record is reconciled.",
                )
            else:
                self._client.send_message(
                    chat_id,
                    f"{run_id} is already {prior}; it was not changed.",
                )
            return
        response = self._monday.agent(
            "review",
            run_id=run_id,
            approve=action == "approve",
            by=f"human:telegram:{user_id}",
            note=note,
        )
        self._client.send_message(chat_id, response.message)


def _parse(text: str, bot_username: str = "") -> tuple[str, str] | None:
    if not text.startswith("/"):
        return "", text
    head, _, tail = text.partition(" ")
    command_token, separator, addressed_to = head[1:].partition("@")
    if separator:
        expected = str(bot_username or "").strip().lstrip("@").lower()
        if not expected or addressed_to.lower() != expected:
            return None
    command = command_token.lower()
    aliases = {"task": "build", "new": "build"}
    return aliases.get(command, command), tail.strip()


def _title(objective: str) -> str:
    first = objective.strip().splitlines()[0]
    first = re.split(r"(?<=[.!?])\s", first, maxsplit=1)[0].strip()
    return (first[:77].rstrip() + "...") if len(first) > 80 else first


def _project_build_request(argument: str) -> tuple[str, str]:
    """Split the explicit ``PROJECT :: REQUEST`` Telegram build form."""
    project_name, delimiter, objective = argument.partition(_PROJECT_BUILD_DELIMITER)
    if not delimiter:
        return "", argument.strip()
    return project_name.strip(), objective.strip()


def _external_project(task_data: Any) -> str:
    """Return a task's external target; MondayOS itself needs no selector."""
    if not isinstance(task_data, dict):
        return ""
    project_name = str(task_data.get("project") or "").strip()
    return "" if project_name.casefold() == "mondayos" else project_name


def _team_result(task_id: str, run: dict[str, Any]) -> str:
    allowed_statuses = frozenset({
        "awaiting-approval",
        "blocked",
        "changes-requested",
        "completed",
        "dry-run",
        "failed",
        "interrupted",
        "invalid",
        "invalid-verdict",
        "needs-changes",
        "rejected",
    })
    raw_status = str(run.get("status") or "failed")
    status = raw_status if raw_status in allowed_statuses else "failed"
    lines = [f"{task_id} team run: {status}."]
    approval_run_id = str(run.get("approval_run_id") or "")
    if status == "awaiting-approval" and is_agent_run_id(approval_run_id):
        lines.append(f"Review complete. Approve with /approve {approval_run_id}")
    else:
        stopped_at = str(run.get("stopped_at") or "")
        if stopped_at in _ROLE_NAMES:
            lines.append(f"Stopped at {_ROLE_NAMES[stopped_at]}.")
        if status in {"failed", "interrupted"}:
            lines.append(
                "The agent team could not complete this run. Review the saved "
                "run before retrying."
            )
        elif status in {
            "blocked",
            "invalid",
            "invalid-verdict",
            "needs-changes",
            "rejected",
        }:
            lines.append("The agent team stopped before approval.")
        elif status == "changes-requested":
            lines.append("Changes were requested before completion.")
        elif status == "dry-run":
            lines.append("Dry run completed without making provider changes.")
        elif status == "completed":
            lines.append("The agent team completed this run.")
    return "\n".join(lines)


def _delivery_snapshot(response: Any, *, fallback_id: str) -> dict[str, Any]:
    data = getattr(response, "data", {})
    payload = dict(data) if isinstance(data, dict) else {}

    def value(name: str, default: Any = "") -> Any:
        direct = getattr(response, name, None)
        return direct if direct not in (None, "") else payload.get(name, default)

    success = bool(value("success", False))
    status = str(value("status", "") or ("succeeded" if success else "failed"))
    phase = str(value("phase", "") or status)
    raw_files = value("changed_files", [])
    changed_files = (
        [_bounded_text(item, 200) for item in raw_files[:50]]
        if isinstance(raw_files, list)
        else []
    )
    raw_attempts = value("attempts", [])
    if isinstance(raw_attempts, list):
        attempts = len(raw_attempts)
    else:
        attempts = max(0, _integer(raw_attempts, 0))
    failure_class = str(value("failure_class", "") or "").strip().lower()
    if failure_class not in {"", "transient", "permanent", "policy"}:
        failure_class = ""
    retry_after = max(
        0,
        min(86_400, _integer(value("retry_after_seconds", 0), 0)),
    )
    raw_pr_url = value("pr_url", "")
    pr_url = _github_pr_url(raw_pr_url)
    return {
        "delivery_id": str(value("delivery_id", fallback_id) or fallback_id),
        "success": success,
        "status": _bounded_text(status, 80),
        "phase": _bounded_text(phase, 80),
        "branch": _bounded_text(value("branch", ""), 160),
        "commit_sha": _bounded_text(value("commit_sha", ""), 64),
        "pr_url": pr_url,
        "publication_uncertain": bool(str(raw_pr_url or "").strip()) and not pr_url,
        "pushed": bool(value("pushed", False)),
        "changed_files": changed_files,
        "attempts": attempts,
        "failure_code": _safe_failure_code(value("failure_code", "")),
        "failure_class": failure_class,
        "retry_after_seconds": retry_after,
        # Provider, CLI, and repository diagnostics never enter Telegram's
        # durable state. Notices render only bounded semantic fields.
        "message": "",
    }


def _has_explicit_delivery_snapshot(response: Any) -> bool:
    """Require durable identity and status instead of trusting fallback fields."""
    data = getattr(response, "data", {})
    payload = data if isinstance(data, dict) else {}

    def explicit(name: str) -> Any:
        direct = getattr(response, name, None)
        return direct if direct not in (None, "") else payload.get(name)

    return bool(str(explicit("delivery_id") or "").strip()) and bool(
        str(explicit("status") or "").strip()
    )


def _safe_transient_delivery_failure(result: dict[str, Any]) -> bool:
    """Authorize a fresh identity only before publication can have begun."""
    return (
        str(result.get("status") or "").lower() == "failed"
        and str(result.get("failure_class") or "") == "transient"
        and not bool(result.get("pushed"))
        and not str(result.get("commit_sha") or "")
        and not str(result.get("pr_url") or "")
        and not bool(result.get("publication_uncertain"))
    )


def _retry_delivery_id(root_delivery_id: str, attempt: int) -> str:
    value = f"{root_delivery_id}-r{attempt}"
    if not _DELIVERY_ID.fullmatch(value):
        raise RuntimeError("Telegram delivery retry identity is invalid")
    return value


def _delivery_retry_delay(attempt: int, retry_after: Any) -> int:
    index = max(0, min(len(_DELIVERY_RETRY_DELAYS_SECONDS) - 1, attempt - 2))
    controller_delay = _DELIVERY_RETRY_DELAYS_SECONDS[index]
    provider_delay = max(0, min(86_400, _integer(retry_after, 0)))
    return max(controller_delay, provider_delay)


def _delivery_retry_remaining_delay(retry: dict[str, Any]) -> int:
    """Return a stable positive delay for a persisted scheduled retry."""
    return max(
        1,
        int(_finite_timestamp(retry.get("next_attempt_at")) - time.time()),
    )


def _expired_delivery_snapshot(delivery_id: str) -> dict[str, Any]:
    return {
        "delivery_id": delivery_id,
        "success": False,
        "status": "failed",
        "phase": "failed",
        "branch": "",
        "commit_sha": "",
        "pr_url": "",
        "publication_uncertain": False,
        "pushed": False,
        "changed_files": [],
        "attempts": 0,
        "failure_code": "retry-expired",
        "failure_class": "permanent",
        "retry_after_seconds": 0,
        "message": "Automatic recovery window expired.",
    }


def _uncertain_delivery_snapshot(delivery_id: str) -> dict[str, Any]:
    """Represent a bounded stop without asserting that publication failed."""
    return {
        "delivery_id": delivery_id,
        "success": False,
        "status": "publication-pending",
        "phase": "reconciliation-required",
        "branch": "",
        "commit_sha": "",
        "pr_url": "",
        "publication_uncertain": True,
        "pushed": False,
        "changed_files": [],
        "attempts": 0,
        "failure_code": "reconciliation-unresolved",
        "failure_class": "",
        "retry_after_seconds": 0,
        "message": "",
    }


def _runtime_delivery_operation(progress: dict[str, Any]) -> str:
    phase = progress.get("phase")
    if isinstance(phase, str) and phase in _RUNTIME_DELIVERY_OPERATIONS:
        return phase
    return "job"


def _delivery_progress_snapshot(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        payload = event
    else:
        serializer = getattr(event, "to_dict", None)
        if callable(serializer):
            serialized = serializer()
            payload = serialized if isinstance(serialized, dict) else {}
        else:
            attributes = getattr(event, "__dict__", {})
            payload = attributes if isinstance(attributes, dict) else {}
    if not payload:
        return {}
    event_name = _bounded_text(payload.get("event", ""), 80)
    phase = _bounded_text(payload.get("phase", "") or event_name, 80)
    status = _bounded_text(payload.get("status", ""), 80)
    verdict = _bounded_text(payload.get("verdict", ""), 40)
    return {
        "event": event_name,
        "phase": phase,
        "status": status,
        "attempt": max(0, _integer(payload.get("attempt"), 0)),
        "max_attempts": max(0, _integer(payload.get("max_attempts"), 0)),
        "verdict": verdict,
    }


def _delivery_progress_text(progress: dict[str, Any]) -> str:
    marker = " ".join(
        str(progress.get(name) or "")
        for name in ("event", "phase", "status")
    ).lower().replace("_", "-")
    attempt = _integer(progress.get("attempt"), 0)
    maximum = _integer(progress.get("max_attempts"), 0)
    count = f" {attempt}/{maximum}" if attempt and maximum else (f" {attempt}" if attempt else "")
    if "checkout" in marker or "workspace" in marker or "prepar" in marker:
        return "Monday prepared an isolated build workspace."
    if "repair" in marker:
        if count:
            return f"ChatGPT requested changes. Monday is repairing attempt{count}."
        return "ChatGPT requested changes. Monday is repairing the build."
    if any(word in marker for word in ("edit", "build", "implement", "patch")):
        return f"Monday is building attempt{count}." if count else "Monday is building."
    if any(word in marker for word in ("valid", "verify", "test")):
        if "finish" in marker and progress.get("status") in {"passed", "success", "succeeded"}:
            return f"Validation passed for attempt{count}." if count else "Validation passed."
        return (
            f"Monday is validating attempt{count}."
            if count
            else "Monday is validating the build."
        )
    if "review" in marker:
        verdict = str(progress.get("verdict") or "").lower()
        if verdict == "pass":
            return f"ChatGPT approved attempt{count}." if count else "ChatGPT approved the build."
        if verdict in {"needs_changes", "needs-changes"}:
            return (
                f"ChatGPT requested changes on attempt{count}."
                if count
                else "ChatGPT requested changes."
            )
        return (
            f"ChatGPT is reviewing attempt{count}."
            if count
            else "ChatGPT is reviewing the build."
        )
    if any(word in marker for word in ("commit", "push", "github", "pull-request", "deliver")):
        return "The approved change is being published to GitHub."
    return ""


def _delivery_terminal(status: str) -> bool:
    return status.strip().lower() in {
        "blocked",
        "cancelled",
        "completed",
        "failed",
        "interrupted",
        "pr-open",
        "rejected",
        "succeeded",
    }


def _delivery_result(
    task_id: str,
    result: dict[str, Any],
    *,
    follow_up_phase: str = "",
) -> str:
    status = str(result.get("status") or "failed")
    pr_url = _github_pr_url(result.get("pr_url", ""))
    commit = str(result.get("commit_sha") or "")
    success = bool(result.get("success"))
    if success and pr_url:
        lines = [f"{task_id} passed ChatGPT review.", f"Pull request: {pr_url}"]
        if follow_up_phase == "not-started-capacity":
            lines.append(
                "Monday did not start automatic PR monitoring because the "
                "500-watcher limit is full. The pull request is unaffected."
            )
        else:
            lines.append("Monday will keep watching GitHub checks and the pull request.")
    else:
        lines = [f"{task_id} live build: {status}."]
        if pr_url:
            lines.append(f"Pull request: {pr_url}")
    if _COMMIT_SHA.fullmatch(commit):
        lines.append(f"Commit: {commit[:12]}")
    attempts = max(0, _integer(result.get("attempts"), 0))
    if attempts:
        lines.append(f"Attempts: {attempts}")
    changed = result.get("changed_files")
    if isinstance(changed, list) and changed:
        lines.append(f"Changed files: {len(changed)}")
    message = (
        ""
        if str(result.get("failure_class") or "") == "transient"
        else _bounded_text(result.get("message", ""), 300)
    )
    if message and not success:
        lines.append(message)
    return "\n".join(lines)


def _delivery_retry_notice(
    task_id: str,
    attempt: int,
    maximum: int,
    delay_seconds: int,
) -> str:
    if delay_seconds < 120:
        wait = "about a minute"
    elif delay_seconds < 7_200:
        wait = f"about {max(2, round(delay_seconds / 60))} minutes"
    else:
        wait = f"about {max(2, round(delay_seconds / 3_600))} hours"
    return (
        f"{task_id} hit a temporary model or tool outage. Monday saved the "
        f"checkpoint and will try automatically in {wait} "
        f"(delivery run {attempt}/{maximum}). New requests can continue meanwhile."
    )


def _delivery_retry_unavailable_notice(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    phase = str(value.get("phase") or "")
    if phase == "not-started-capacity":
        return (
            "Monday could not start automatic recovery because the 500-retry "
            "safety limit is full. New requests are unaffected."
        )
    if phase == "not-started-deadline":
        return (
            "The provider retry window was outside Monday's 24-hour recovery "
            "window, so no automatic retry was started."
        )
    return ""


def _delivery_retry_result(
    task_id: str,
    result: dict[str, Any],
    *,
    attempt: int,
    follow_up_phase: str,
) -> str:
    base = _delivery_result(
        task_id,
        result,
        follow_up_phase=follow_up_phase,
    )
    if bool(result.get("success")):
        return f"Monday recovered automatically on delivery run {attempt}.\n{base}"
    if str(result.get("failure_class") or "") == "transient":
        return (
            f"Monday made {attempt} delivery runs, but the temporary outage did "
            f"not recover within the retry limit.\n{base}"
        )
    return f"Monday retried the delivery automatically.\n{base}"


def _delivery_retry_outcome_notice(
    task_id: str,
    result: dict[str, Any],
    *,
    attempt: int,
    outcome_phase: str,
    follow_up_phase: str,
) -> str:
    if outcome_phase == "uncertain":
        return (
            f"{task_id} may have published changes, but Monday could not verify "
            "the exact delivery state after its recovery window. Automatic "
            f"retries stopped safely. Review delivery {result.get('delivery_id', '')} "
            "in MondayOS or GitHub before starting new work."
        )
    if outcome_phase == "expired":
        return (
            f"{task_id} could not recover from the temporary outage within "
            "24 hours. Monday preserved every delivery checkpoint."
        )
    return _delivery_retry_result(
        task_id,
        result,
        attempt=attempt,
        follow_up_phase=follow_up_phase,
    )


def _lifecycle_snapshot(
    response: Any,
    *,
    delivery_id: str,
    task_id: str,
    project_name: str,
    pr_url: str,
) -> dict[str, str]:
    """Accept only the bounded public observation contract.

    The API may expose fields directly on ``BuildResponse`` or in ``data``. No
    check names, descriptions, annotations, or logs are copied from either.
    """
    if not bool(getattr(response, "success", False)):
        return {}
    raw_data = getattr(response, "data", {})
    data = dict(raw_data) if isinstance(raw_data, dict) else {}
    nested = data.get("observation")
    if isinstance(nested, dict):
        data = {**data, **nested}

    def value(name: str, *aliases: str) -> Any:
        direct = getattr(response, name, None)
        if direct not in (None, ""):
            return direct
        for key in (name, *aliases):
            if data.get(key) not in (None, ""):
                return data[key]
        return ""

    returned_delivery = str(value("delivery_id") or "")
    returned_task = str(value("task_id") or "")
    returned_project = str(value("project_name", "project") or "")
    returned_url = _github_pr_url(value("pr_url"))
    if returned_delivery != delivery_id:
        return {}
    if returned_task != task_id:
        return {}
    if returned_project != project_name:
        return {}
    if returned_url != pr_url:
        return {}

    pr_state = _normalized_pr_state(value("pr_state", "pull_request_state"))
    checks_status = _normalized_checks_status(
        value("checks_status", "check_status", "ci_status")
    )
    if not pr_state or checks_status not in {
        "pending", "passed", "failed", "no-checks",
    }:
        return {}
    merge_commit = str(value("merge_commit_sha") or "")
    if (
        (pr_state == "merged" and not _FULL_COMMIT_SHA.fullmatch(merge_commit))
        or (pr_state != "merged" and merge_commit)
    ):
        return {}
    return {
        "pr_state": pr_state,
        "checks_status": checks_status or "unknown",
        "merge_commit_sha": merge_commit.lower(),
    }


def _normalized_pr_state(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("_", "-")
    aliases = {
        "opened": "open",
        "merge": "merged",
        "head-diverged": "diverged",
        "sha-diverged": "diverged",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in {"open", *_TERMINAL_PR_STATES} else ""


def _normalized_checks_status(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("_", "-")
    aliases = {
        "success": "passed",
        "succeeded": "passed",
        "green": "passed",
        "failure": "failed",
        "error": "failed",
        "in-progress": "pending",
        "queued": "pending",
        "none": "no-checks",
        "unconfigured": "no-checks",
        "timeout": "timed-out",
    }
    normalized = aliases.get(normalized, normalized)
    allowed = {"pending", "passed", "failed", "no-checks", "timed-out", "unknown"}
    return normalized if normalized in allowed else ""


def _lifecycle_notice_key(pr_state: str, checks_status: str) -> str:
    return pr_state if pr_state in _TERMINAL_PR_STATES else f"open:{checks_status}"


def _lifecycle_notice(
    task_id: str,
    pr_url: str,
    observation: dict[str, str],
) -> str:
    pr_state = observation["pr_state"]
    checks_status = observation["checks_status"]
    if pr_state == "merged":
        lines = [f"{task_id} was merged on GitHub.", f"Pull request: {pr_url}"]
        merge_commit = observation.get("merge_commit_sha", "")
        if merge_commit:
            lines.append(f"Merge commit: {merge_commit[:12]}")
        return "\n".join(lines)
    if pr_state == "closed":
        return f"{task_id} was closed without merging.\nPull request: {pr_url}"
    if pr_state == "diverged":
        return (
            f"{task_id} no longer points to the ChatGPT-approved commit. "
            "Monday stopped monitoring it.\n"
            f"Pull request: {pr_url}"
        )
    if checks_status == "pending":
        return f"GitHub checks are running for {task_id}.\nPull request: {pr_url}"
    if checks_status == "passed":
        return (
            f"GitHub checks passed for {task_id}. The pull request is ready "
            f"for your merge review.\nPull request: {pr_url}"
        )
    if checks_status == "failed":
        return (
            f"GitHub checks failed for {task_id}. Monday will keep watching for "
            f"a rerun or lifecycle change.\nPull request: {pr_url}"
        )
    if checks_status == "no-checks":
        return (
            f"{task_id} has no configured GitHub checks. ChatGPT review passed, "
            f"but the repository reported no CI verification.\nPull request: {pr_url}"
        )
    if checks_status == "timed-out":
        return (
            f"GitHub checks have not completed for {task_id}. Monday will keep "
            f"watching the pull request.\nPull request: {pr_url}"
        )
    return ""


def _follow_up_poll_interval(checks_status: str) -> float:
    return (
        _FOLLOW_UP_POLL_SECONDS
        if checks_status in {"pending", "unknown"}
        else _FOLLOW_UP_SLOW_POLL_SECONDS
    )


def _follow_up_backoff(failure_count: int) -> float:
    exponent = min(5, max(0, int(failure_count) - 1))
    return float(min(_FOLLOW_UP_MAX_BACKOFF_SECONDS, 15.0 * (2 ** exponent)))


def _delivery_pending_result(task_id: str, result: dict[str, Any]) -> str:
    phase = _bounded_text(result.get("phase", ""), 80) or "in progress"
    return (
        f"{task_id} is still {phase}. Monday will reconcile this exact build "
        "the next time the request is checked."
    )


def _github_pr_url(value: Any) -> str:
    url = str(value or "").strip()
    if len(url) > 500 or not url.startswith("https://github.com/") or any(
        char.isspace() for char in url
    ):
        return ""
    return url


def _bounded_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _safe_failure_code(value: Any) -> str:
    code = str(value or "").strip().lower()
    if len(code) > 80 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", code):
        return ""
    return code


def _finite_timestamp(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if parsed <= 0 or parsed == float("inf") or parsed != parsed:
        return 0.0
    return parsed


def _help(*, live_build: bool = False, private_chat: bool = True) -> str:
    build = (
        "/build REQUEST — create, build, review, and open a pull request\n"
        "/build PROJECT :: REQUEST — build a registered project\n"
        "/deliver TASK-ID — build and deliver an existing task\n"
        if live_build
        else (
            "/build REQUEST — create and run a task\n"
            "/build PROJECT :: REQUEST — create a project-bound task\n"
        )
    )
    if private_chat:
        intro = (
            "Send a normal text message and Monday will build it, validate it, have "
            "ChatGPT review it, and open a pull request."
            if live_build
            else "Send a normal text message and MondayOS will create a task and "
            "assign its agent team."
        )
        private_commands = (
            "/tasks — active tasks\n"
            "/queue — show your active Monday inbox\n"
            "/cancel JOB-ID — cancel one queued request\n"
            "/approve RUN-ID — approve a reviewed run\n"
            "/reject RUN-ID REASON — reject a reviewed run\n"
        )
    else:
        intro = "Use these commands in this allowlisted group."
        private_commands = ""
    return (
        f"{intro}\n\n"
        f"{build}"
        "/run TASK-ID — run an existing task\n"
        "/status [TASK-ID] — system or task status\n"
        f"{private_commands}"
        "/help — show these commands"
    )


def _integer(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
