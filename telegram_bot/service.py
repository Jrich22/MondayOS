"""Telegram commands mapped exclusively onto the public MondayOS API."""
from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from telegram_bot.client import TelegramClient
from telegram_bot.config import TelegramConfig
from telegram_bot.state import TelegramState

_TASK_ID = re.compile(
    r"^TASK-\d{1,64}(?:-[a-z0-9]{1,12})?$",
    re.IGNORECASE,
)
_RUN_ID = re.compile(r"^run-[A-Za-z0-9-]{1,80}$")
_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{7,64}$")
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
    """Sequential Telegram-to-MondayOS application service."""

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

    def bind_bot_identity(self, identity: dict[str, Any]) -> None:
        """Record the verified getMe username used for addressed commands."""
        self._bot_id = _integer(identity.get("id"), 0)
        self._bot_username = str(identity.get("username") or "").strip().lstrip("@").lower()

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

        if command in {"start", "help"}:
            self._client.send_message(
                chat_id,
                _help(live_build=self._config.live_build and chat_type == "private"),
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
        elif command:
            self._client.send_message(chat_id, "Unknown command. Send /help for options.")
            return
        else:
            objective = text

        if not objective:
            self._client.send_message(chat_id, "Tell me what you want MondayOS to build.")
            return
        if chat_type == "private" and self._config.live_build and command in {"", "build"}:
            self._create_and_deliver(update_id, chat_id, user_id, objective)
        else:
            self._create_and_run(update_id, chat_id, user_id, objective)

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
        if user_id <= 0 or chat_id == 0:
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
    ) -> tuple[str, bool]:
        """Create or reconcile the one task owned by this Telegram update."""
        job = self._state.job(update_id)
        recorded_workflow = str(job.get("workflow") or "")
        if recorded_workflow and recorded_workflow != workflow:
            raise RuntimeError(
                "The saved Telegram request belongs to a different workflow"
            )
        task_id = str(job.get("task_id") or "")
        if not task_id:
            task_id = self._find_task_for_update(update_id)
        created = False
        if not task_id:
            marker = self._idempotency_marker(update_id)
            response = self._monday.task(
                "create",
                title=_title(objective),
                objective=objective,
                task_type="feature",
                priority="P2",
                created_by=f"human:telegram:{user_id}",
                context=f"Requested through Telegram. Idempotency marker: {marker}",
            )
            if not response.success or not response.task_id:
                self._client.send_message(
                    chat_id,
                    f"I could not create the task: {response.message}",
                )
                return "", False
            task_id = str(response.task_id)
            created = True
        self._state.set_job(
            update_id,
            task_id=task_id,
            chat_id=chat_id,
            workflow=workflow,
        )
        return task_id, created

    def _create_and_run(
        self,
        update_id: int,
        chat_id: int,
        user_id: int,
        objective: str,
    ) -> None:
        task_id, _created = self._task_for_request(
            update_id,
            chat_id,
            user_id,
            objective,
            workflow="team-review",
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
    ) -> None:
        task_id, _created = self._task_for_request(
            update_id,
            chat_id,
            user_id,
            objective,
            workflow="live-build",
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
        self._state.set_job(
            update_id,
            task_id=task_id,
            chat_id=chat_id,
            workflow="live-build",
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
        saved_result = job.get("delivery_result")
        if bool(job.get("delivery_terminal")) and isinstance(saved_result, dict):
            self._client.send_message(chat_id, _delivery_result(task_id, saved_result))
            self._state.set_job(update_id, delivery_final_sent=True)
            return

        stable_id = self._delivery_id(update_id)
        recorded_id = str(job.get("delivery_id") or "")
        if recorded_id and recorded_id != stable_id:
            raise RuntimeError("The saved Telegram delivery identity does not match")
        self._state.set_job(
            update_id,
            task_id=task_id,
            chat_id=chat_id,
            workflow="live-build",
            delivery_id=stable_id,
            delivery_reserved=True,
        )
        response = self._monday.build(
            "run",
            task_id=task_id,
            delivery_id=stable_id,
            progress_callback=lambda event: self._live_progress(
                update_id, chat_id, event,
            ),
        )
        result = _delivery_snapshot(response, fallback_id=stable_id)
        if result["delivery_id"] != stable_id:
            raise RuntimeError("MondayOS did not honor the reserved delivery identity")
        terminal = _delivery_terminal(str(result.get("status") or ""))
        self._state.set_job(
            update_id,
            delivery_reserved=False,
            delivery_terminal=terminal,
            delivery_final_sent=False,
            delivery_status=result["status"],
            delivery_phase=result["phase"],
            delivery_result=result,
        )
        if terminal:
            self._client.send_message(chat_id, _delivery_result(task_id, result))
            self._state.set_job(update_id, delivery_final_sent=True)
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

    def _delivery_id(self, update_id: int) -> str:
        identity = f"telegram:{self._bot_id}:{update_id}".encode()
        digest = hashlib.sha256(identity).hexdigest()[:20]
        return f"delivery-telegram-{digest}"

    def _live_progress(self, update_id: int, chat_id: int, event: Any) -> None:
        snapshot = _delivery_progress_snapshot(event)
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
        kind = event.get("event")
        if kind == "team_started":
            self._client.send_message(chat_id, "Agent team started.")
        elif kind == "stage_finished":
            role = str(event.get("role") or "agent")
            label = _ROLE_NAMES.get(role, role.replace("-", " ").title())
            verdict = str(event.get("verdict") or event.get("status") or "finished")
            provider = str(event.get("provider") or "")
            suffix = f" via {provider}" if provider else ""
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
        if not _RUN_ID.fullmatch(run_id):
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


def _team_result(task_id: str, run: dict[str, Any]) -> str:
    status = str(run.get("status") or "failed")
    message = str(run.get("message") or "")
    lines = [f"{task_id} team run: {status}."]
    if status == "awaiting-approval" and run.get("approval_run_id"):
        lines.append(f"Review complete. Approve with /approve {run['approval_run_id']}")
    elif run.get("stopped_at"):
        lines.append(f"Stopped at {run['stopped_at']}.")
    if message:
        lines.append(message[:500])
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
    return {
        "delivery_id": str(value("delivery_id", fallback_id) or fallback_id),
        "success": success,
        "status": _bounded_text(status, 80),
        "phase": _bounded_text(phase, 80),
        "branch": _bounded_text(value("branch", ""), 160),
        "commit_sha": _bounded_text(value("commit_sha", ""), 64),
        "pr_url": _github_pr_url(value("pr_url", "")),
        "changed_files": changed_files,
        "attempts": attempts,
        "message": _bounded_text(value("message", ""), 300),
    }


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


def _delivery_result(task_id: str, result: dict[str, Any]) -> str:
    status = str(result.get("status") or "failed")
    pr_url = _github_pr_url(result.get("pr_url", ""))
    commit = str(result.get("commit_sha") or "")
    success = bool(result.get("success"))
    if success and pr_url:
        lines = [f"{task_id} passed ChatGPT review.", f"Pull request: {pr_url}"]
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
    message = _bounded_text(result.get("message", ""), 300)
    if message and not success:
        lines.append(message)
    return "\n".join(lines)


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


def _help(*, live_build: bool = False) -> str:
    build = (
        "/build REQUEST — create, build, review, and open a pull request\n"
        "/deliver TASK-ID — build and deliver an existing task\n"
        if live_build
        else "/build REQUEST — create and run a task\n"
    )
    intro = (
        "Send a normal text message and Monday will build it, validate it, have ChatGPT "
        "review it, and open a pull request."
        if live_build
        else "Send a normal text message and MondayOS will create a task and assign its agent team."
    )
    return (
        f"{intro}\n\n"
        f"{build}"
        "/run TASK-ID — run an existing task\n"
        "/status [TASK-ID] — system or task status\n"
        "/tasks — active tasks\n"
        "/approve RUN-ID — approve a reviewed run\n"
        "/reject RUN-ID REASON — reject a reviewed run\n"
        "/help — show these commands"
    )


def _integer(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
