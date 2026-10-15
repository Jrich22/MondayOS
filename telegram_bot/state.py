"""Durable Telegram offset and job state."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import threading
import time
from pathlib import Path
from typing import Any, TextIO, TypeGuard

from core.atomic import write_json_atomic

_MAX_OBSERVATION_RETRY_SECONDS = 300.0
_MAX_TELEGRAM_RATE_LIMIT_SECONDS = 86_400.0
MAX_PENDING_JOBS = 25
_MAX_ACTIVE_FOLLOW_UPS = 500
_MAX_ACTIVE_DELIVERY_RETRIES = 500
_MAX_COMPLETED_JOBS = 1_000
_FOLLOW_UP_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
_DELIVERY_RETRY_MAX_AGE_SECONDS = 24 * 60 * 60
_MAX_DELIVERY_RETRY_RUNS = 6
_MAX_DELIVERY_RETRY_NOTIFICATION_FAILURES = 3
_MAX_ACCEPTANCE_RECEIPT_RETRY_SECONDS = 86_400.0
_MAX_QUEUE_FULL_NOTICE_RETRY_SECONDS = 86_400.0
_MAX_ACTIVE_QUEUE_FULL_NOTICES = 500
_MAX_DELIVERY_NOTICE_RETRY_SECONDS = 86_400.0
_MAX_ACTIVE_DELIVERY_NOTICES = 500
_RUNTIME_HEARTBEAT_VERSION = 2
_MAX_RUNTIME_HEARTBEAT_BYTES = 16_384
_MAX_RUNTIME_BACKOFF_SECONDS = 86_400.0
_RUNTIME_HEARTBEAT_FIELDS = frozenset({
    "version", "started_at", "stopped_at", "supervisor", "lanes",
})
_RUNTIME_SUPERVISOR_FIELDS = frozenset({"pulse_at"})
_RUNTIME_LANE_FIELDS = frozenset({
    "state", "operation", "progress_at", "deadline_at", "resume_at",
})
_RUNTIME_LANE_STATES = frozenset({
    "starting", "idle", "active", "backoff", "stopped",
})
_RUNTIME_ACTIVE_LEASES: dict[str, dict[str, float]] = {
    "intake": {
        "poll": 80.0,
        "dispatch": 300.0,
    },
    "worker": {
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
    },
}
_RUNTIME_BACKOFF_OPERATIONS = frozenset({"rate-limit", "retry"})
_DELIVERY_ID = re.compile(r"^delivery-[A-Za-z0-9-]{1,80}$")
_TASK_ID = re.compile(r"^TASK-\d{1,64}(?:-[a-z0-9]{1,12})?$", re.IGNORECASE)
_PROJECT_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FAILURE_CODE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{7,64}$")
_GITHUB_PR_URL = re.compile(r"^https://github\.com/[^\s/]+/[^\s/]+/pull/\d+$")
_JOB_ID = re.compile(r"^JOB-(0|[1-9]\d*)$", re.IGNORECASE)
_ORIGIN_FIELDS = frozenset({
    "origin_user_id", "origin_chat_id", "origin_chat_type",
})
_PRISTINE_QUEUE_FIELDS = frozenset({
    "phase", "request", "request_digest", *_ORIGIN_FIELDS,
})
_ACCEPTANCE_RECEIPT_PHASES = frozenset({"pending", "retrying", "sent"})
_QUEUE_FULL_NOTICE_STATUSES = frozenset({
    "pending", "retrying", "sent", "failed",
})
_QUEUE_FULL_NOTICE_TERMINAL_FIELDS = frozenset({
    "phase", "request_digest", "notice_status",
})
_QUEUE_FULL_NOTICE_ACTIVE_FIELDS = frozenset({
    *_QUEUE_FULL_NOTICE_TERMINAL_FIELDS,
    *_ORIGIN_FIELDS,
    "notice_binding",
    "notice_failure_count",
    "notice_deferred_at",
    "notice_next_attempt_at",
})
_DELIVERY_NOTICE_PHASES = frozenset({"pending", "retrying"})
_DELIVERY_NOTICE_KINDS = frozenset({"retry-scheduled", "terminal-result"})
_DELIVERY_NOTICE_FIELDS = frozenset({
    "kind", "phase", "notice_kind", "task_id", "delivery_id",
    *_ORIGIN_FIELDS,
    "follow_up_phase", "retry_unavailable_phase",
    "source_binding", "failure_count", "deferred_at", "next_attempt_at",
})
_DELIVERY_NOTICE_FOLLOW_UP_PHASES = frozenset({
    "", "pending", "polling", "retrying", "not-started-capacity",
})
_DELIVERY_NOTICE_UNAVAILABLE_PHASES = frozenset({
    "", "not-started-capacity", "not-started-deadline",
})
_TERMINAL_DELIVERY_STATUSES = frozenset({
    "blocked", "cancelled", "completed", "failed", "interrupted", "pr-open",
    "publication-pending", "rejected", "succeeded",
})


class TelegramStateError(RuntimeError):
    pass


def job_id(update_id: int) -> str:
    """Return the stable public identifier for one Telegram inbox job."""
    return f"JOB-{_update_id(update_id)}"


def parse_job_id(value: Any) -> int:
    """Validate a public inbox job identifier and return its update id."""
    if not isinstance(value, str):
        raise ValueError("Telegram job ids must use JOB-<update_id>")
    candidate = value.strip()
    match = _JOB_ID.fullmatch(candidate)
    if match is None:
        raise ValueError("Telegram job ids must use JOB-<update_id>")
    try:
        return _update_id(int(match.group(1)))
    except (TelegramStateError, TypeError, ValueError) as exc:
        raise ValueError(
            "Telegram job ids must use JOB-<update_id>"
        ) from exc


class TelegramState:
    """Atomic state that prevents acknowledged updates from being replayed."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        with self._lock:
            self._data = self._read()

    def bind_bot_identity(self, bot_id: Any, username: str = "") -> bool:
        """Bind offsets/tombstones to one Telegram bot.

        Returns ``True`` when the identity changed and the prior bot's update
        state was cleared. Telegram update ids are scoped to a bot, so carrying
        an offset or tombstone set across bot tokens could silently skip the new
        bot's pending updates.
        """
        normalized_id = _positive_id(bot_id)
        normalized_username = str(username or "").strip().lstrip("@").lower()
        with self._lock:
            current_id = self._data.get("bot_id")
            changed = current_id is not None and current_id != normalized_id
            legacy_with_state = current_id is None and _has_update_state(self._data)

            if changed or legacy_with_state:
                self._data.update({
                    "last_update_id": -1,
                    "cancellation_sequence": 0,
                    "jobs": {},
                    "completed_jobs": {},
                    "failed_jobs": {},
                    "delivery_retries": {},
                    "acceptance_receipts": {},
                    "delivery_notices": {},
                    "delivery_notice_pause_until": 0.0,
                })

            needs_write = (
                changed
                or legacy_with_state
                or current_id != normalized_id
                or self._data.get("bot_username") != normalized_username
                or self._data.get("version") != 8
            )
            self._data.update({
                "version": 8,
                "bot_id": normalized_id,
                "bot_username": normalized_username,
            })
            if needs_write:
                self._write()
            return changed or legacy_with_state

    @property
    def next_offset(self) -> int | None:
        with self._lock:
            value = int(self._data.get("last_update_id", -1))
            return value + 1 if value >= 0 else None

    @property
    def pending_count(self) -> int:
        """Return the number of requests that still need worker attention."""
        with self._lock:
            return sum(
                1
                for value in self._data.setdefault("jobs", {}).values()
                if _is_pending(value)
            )

    @property
    def pending_queue_full_notice_count(self) -> int:
        """Return capacity refusals whose request-free notice is unresolved."""
        with self._lock:
            return sum(
                1
                for value in self._data.setdefault("completed_jobs", {}).values()
                if _queue_full_notice_pending(value)
            )

    def due_queue_full_notice_ids(
        self,
        *,
        now: float | None = None,
    ) -> list[int]:
        """Return request-free capacity notices whose retry is due."""
        current = time.time() if now is None else _finite_nonnegative(now, time.time())
        with self._lock:
            due: list[int] = []
            for key, record in self._data.setdefault("completed_jobs", {}).items():
                if not _queue_full_notice_pending(record):
                    continue
                update_id = _queue_full_notice_key(key)
                notice = _normalized_queue_full_notice(record, update_id)
                if notice["next_attempt_at"] <= current:
                    due.append(update_id)
            return sorted(due)

    def defer_queue_full_notice(
        self,
        update_id: int,
        *,
        delay_seconds: float,
        now: float | None = None,
    ) -> None:
        """Persist bounded capacity-notice backoff without diagnostics."""
        self.defer_queue_full_notices(
            [update_id],
            delay_seconds=delay_seconds,
            now=now,
        )

    def defer_queue_full_notices(
        self,
        update_ids: list[int],
        *,
        delay_seconds: float,
        now: float | None = None,
    ) -> None:
        """Atomically defer a cohort of due capacity notices."""
        normalized_ids = sorted({_update_id(value) for value in update_ids})
        current = time.time() if now is None else _finite_nonnegative(now, time.time())
        delay = _bounded_delay(
            delay_seconds,
            _MAX_QUEUE_FULL_NOTICE_RETRY_SECONDS,
        )
        with self._lock:
            pending: list[tuple[dict[str, Any], dict[str, Any]]] = []
            completed = self._data.setdefault("completed_jobs", {})
            for normalized_id in normalized_ids:
                record = completed.get(str(normalized_id))
                if not _queue_full_notice_pending(record):
                    continue
                notice = _normalized_queue_full_notice(record, normalized_id)
                pending.append((record, notice))
            for record, notice in pending:
                record.update({
                    "notice_status": "retrying",
                    "notice_failure_count": int(notice["failure_count"]) + 1,
                    "notice_deferred_at": current,
                    "notice_next_attempt_at": current + delay,
                })
            if pending:
                self._write()

    @property
    def pending_acceptance_receipt_count(self) -> int:
        """Return accepted jobs whose stable handle is not yet delivered."""
        with self._lock:
            return sum(
                1
                for value in self._data.setdefault(
                    "acceptance_receipts", {}
                ).values()
                if _acceptance_receipt_pending(value)
            )

    def due_acceptance_receipt_ids(
        self,
        *,
        now: float | None = None,
    ) -> list[int]:
        """Return request-free receipt work that is due in update order."""
        current = time.time() if now is None else _finite_nonnegative(now, time.time())
        with self._lock:
            receipts = self._data.setdefault("acceptance_receipts", {})
            due: list[int] = []
            for key, value in receipts.items():
                update_id = _receipt_key(key)
                receipt = _normalized_acceptance_receipt(value)
                if _acceptance_receipt_pending(receipt) and (
                    _finite_nonnegative(receipt.get("next_attempt_at"), 0.0)
                    <= current
                ):
                    due.append(update_id)
            return sorted(due)

    def acceptance_receipt_notice(self, update_id: int) -> dict[str, object]:
        """Return the safe, source-bound destination for one due receipt."""
        normalized_id = _update_id(update_id)
        key = str(normalized_id)
        with self._lock:
            record = self._data.setdefault("jobs", {}).get(key)
            receipt = self._data.setdefault("acceptance_receipts", {}).get(key)
            if not isinstance(record, dict) or not isinstance(receipt, dict):
                return {}
            normalized = _normalized_acceptance_receipt(receipt)
            _require_acceptance_receipt_binding(
                normalized,
                record,
                normalized_id,
                retry=self._data.setdefault("delivery_retries", {}).get(key),
            )
            if not _acceptance_receipt_pending(normalized):
                return {}
            return {
                "job_id": job_id(normalized_id),
                "user_id": normalized["origin_user_id"],
                "chat_id": normalized["origin_chat_id"],
                "chat_type": normalized["origin_chat_type"],
            }

    def defer_acceptance_receipt(
        self,
        update_id: int,
        *,
        delay_seconds: float,
        now: float | None = None,
    ) -> None:
        """Persist bounded backoff without retaining Telegram diagnostics."""
        self.defer_acceptance_receipts(
            [update_id],
            delay_seconds=delay_seconds,
            now=now,
        )

    def defer_acceptance_receipts(
        self,
        update_ids: list[int],
        *,
        delay_seconds: float,
        now: float | None = None,
    ) -> None:
        """Atomically persist one bounded backoff across a due receipt set."""
        normalized_ids = sorted({_update_id(value) for value in update_ids})
        current = time.time() if now is None else _finite_nonnegative(now, time.time())
        delay = _bounded_delay(
            delay_seconds,
            _MAX_ACCEPTANCE_RECEIPT_RETRY_SECONDS,
        )
        with self._lock:
            pending: list[tuple[dict[str, Any], dict[str, Any]]] = []
            jobs = self._data.setdefault("jobs", {})
            receipts = self._data.setdefault("acceptance_receipts", {})
            retries = self._data.setdefault("delivery_retries", {})
            for normalized_id in normalized_ids:
                key = str(normalized_id)
                record = jobs.get(key)
                receipt = receipts.get(key)
                if not isinstance(record, dict) or not isinstance(receipt, dict):
                    continue
                normalized = _normalized_acceptance_receipt(receipt)
                _require_acceptance_receipt_binding(
                    normalized,
                    record,
                    normalized_id,
                    retry=retries.get(key),
                )
                if _acceptance_receipt_pending(normalized):
                    pending.append((receipt, normalized))
            for receipt, normalized in pending:
                receipt.update({
                    "phase": "retrying",
                    "failure_count": int(normalized["failure_count"]) + 1,
                    "deferred_at": current,
                    "next_attempt_at": current + delay,
                })
            if pending:
                self._write()

    def complete_acceptance_receipt(
        self,
        update_id: int,
        *,
        delivered: bool,
    ) -> None:
        """Permit execution after delivery, or scrub work that cannot notify."""
        normalized_id = _update_id(update_id)
        key = str(normalized_id)
        with self._lock:
            jobs = self._data.setdefault("jobs", {})
            receipts = self._data.setdefault("acceptance_receipts", {})
            record = jobs.get(key)
            receipt = receipts.get(key)
            if not isinstance(record, dict) or not isinstance(receipt, dict):
                # Cancellation can win after the remote send but before its
                # checkpoint. Never resurrect or recreate cancelled work.
                return
            normalized = _normalized_acceptance_receipt(receipt)
            _require_acceptance_receipt_binding(
                normalized,
                record,
                normalized_id,
                retry=self._data.setdefault("delivery_retries", {}).get(key),
            )
            if not _acceptance_receipt_pending(normalized):
                return
            if delivered:
                receipt.update({
                    "phase": "sent",
                    "deferred_at": 0.0,
                    "next_attempt_at": 0.0,
                })
                self._write()
                return

            digest = str(record.get("request_digest") or "")
            jobs.pop(key)
            receipts.pop(key)
            failed = self._data.setdefault("failed_jobs", {})
            failed[key] = {
                "phase": "receipt-failed",
                "request_digest": digest,
            }
            self._advance(normalized_id)
            _bound(failed, 500)
            self._write()

    def pending_queue_full_notice_ids(self) -> list[int]:
        """Return durable capacity notices in Telegram update order."""
        with self._lock:
            pending: list[int] = []
            for key, value in self._data.setdefault("completed_jobs", {}).items():
                if not _queue_full_notice_pending(value):
                    continue
                update_id = _queue_full_notice_key(key)
                _normalized_queue_full_notice(value, update_id)
                pending.append(update_id)
            return sorted(pending)

    def queue_summary(
        self,
        user_id: int,
        chat_id: int,
    ) -> list[dict[str, object]]:
        """Return active jobs the user can manage from their private chat."""
        private_origin = _private_origin(user_id, chat_id)
        with self._lock:
            retries = self._data.setdefault("delivery_retries", {})
            active = sorted(
                (
                    (_update_id(int(key)), value)
                    for key, value in self._data.setdefault("jobs", {}).items()
                    if _is_pending(value)
                ),
                key=lambda item: item[0],
            )
            summary: list[dict[str, object]] = []
            for position, (update_id, record) in enumerate(active, start=1):
                if not _owned_by_private_control(record, private_origin):
                    continue
                summary.append({
                    "job_id": job_id(update_id),
                    "phase": _safe_job_phase(record.get("phase")),
                    "position": position,
                    "cancellable": _queued_job_is_cancellable(
                        record,
                        retry=retries.get(str(update_id)),
                    ),
                })
            return summary

    def cancel_queued(
        self,
        job: str | int,
        *,
        user_id: int,
        chat_id: int,
    ) -> dict[str, object]:
        """Cancel one pristine queued request owned by the private caller.

        The caller may also manage jobs they submitted from a group. Ownership
        mismatches deliberately look identical to unknown job ids. Once a
        worker has claimed or checkpointed any work, cancellation is refused so
        this method can never race or pretend to undo an external effect.
        """
        private_origin = _private_origin(user_id, chat_id)
        try:
            update_id = parse_job_id(job) if isinstance(job, str) else _update_id(job)
        except (TelegramStateError, ValueError):
            return {"status": "not-found", "job_id": ""}
        public_id = job_id(update_id)
        key = str(update_id)
        with self._lock:
            completed = self._data.setdefault("completed_jobs", {})
            terminal = completed.get(key)
            if (
                isinstance(terminal, dict)
                and terminal.get("phase") == "cancelled"
                and _owned_by_private_control(terminal, private_origin)
            ):
                return {
                    "status": "already-cancelled",
                    "job_id": public_id,
                    "phase": "cancelled",
                    "cancellable": False,
                }

            jobs = self._data.setdefault("jobs", {})
            record = jobs.get(key)
            if (
                not isinstance(record, dict)
                or not _owned_by_private_control(record, private_origin)
            ):
                return {"status": "not-found", "job_id": public_id}

            retry = self._data.setdefault("delivery_retries", {}).get(key)
            if not _queued_job_is_cancellable(record, retry=retry):
                return {
                    "status": "not-cancellable",
                    "job_id": public_id,
                    "phase": _safe_job_phase(record.get("phase")),
                    "cancellable": False,
                }

            request = record.get("request")
            if not isinstance(request, dict):  # guarded by the predicate above
                raise TelegramStateError(
                    f"Telegram update {update_id} lost its queued request"
                )
            _normalized, digest = _canonical_request(request)
            stored_digest = str(record.get("request_digest") or "")
            if stored_digest and stored_digest != digest:
                raise TelegramStateError(
                    f"Telegram update {update_id} does not match its saved request"
                )
            origin = _saved_origin(record)
            if origin is None:  # guarded by the ownership check above
                raise TelegramStateError(
                    f"Telegram update {update_id} lost its origin"
                )
            tombstone = _origin_tombstone(
                phase="cancelled",
                digest=stored_digest or digest,
                origin=origin,
            )
            tombstone["cancellation_sequence"] = _next_cancellation_sequence(
                self._data
            )
            jobs.pop(key)
            self._data.setdefault("acceptance_receipts", {}).pop(key, None)
            completed[key] = tombstone
            self._advance(update_id)
            _bound_completed(
                completed,
                _MAX_COMPLETED_JOBS,
                protected={
                    *self._data.setdefault("delivery_retries", {}),
                    *self._data.setdefault("delivery_notices", {}),
                    key,
                },
            )
            self._write()
            return {
                "status": "cancelled",
                "job_id": public_id,
                "phase": "cancelled",
                "cancellable": False,
            }

    @property
    def heartbeat_path(self) -> Path:
        """Small liveness record kept separate from the potentially large queue."""
        return self.path.with_name("heartbeat.json")

    def mark_worker_started(self, *, now: float | None = None) -> None:
        """Record a new daemon lifetime without persisting process identifiers."""
        with self._lock:
            timestamp = _worker_timestamp(now)
            # Starting a new lifetime deliberately replaces legacy or corrupt
            # heartbeat data. No authorization or queue state lives here.
            write_json_atomic(
                self.heartbeat_path,
                _new_runtime_heartbeat(timestamp),
            )

    def mark_worker_heartbeat(self, *, now: float | None = None) -> None:
        """Refresh only the independent supervisor liveness pulse."""
        with self._lock:
            timestamp = _worker_timestamp(now)
            worker = self._read_worker_status()
            if not worker:
                raise TelegramStateError(
                    "Telegram runtime heartbeat has not been started"
                )
            worker = _normalized_runtime_heartbeat(worker, now=timestamp)
            if worker["stopped_at"] is not None:
                raise TelegramStateError(
                    "Telegram runtime heartbeat is already stopped"
                )
            worker["supervisor"]["pulse_at"] = timestamp
            write_json_atomic(self.heartbeat_path, worker)

    def mark_worker_stopped(self, *, now: float | None = None) -> None:
        """Record a clean daemon stop while preserving the prior lifetime."""
        with self._lock:
            timestamp = _worker_timestamp(now)
            try:
                worker = self._read_worker_status()
                if worker:
                    worker = _normalized_runtime_heartbeat(worker, now=timestamp)
                else:
                    worker = _new_runtime_heartbeat(timestamp)
            except TelegramStateError:
                # Heartbeat state is ephemeral and carries no authority. A
                # shutdown or one-shot run must be able to replace a corrupt or
                # legacy record with an explicitly unhealthy stopped record.
                worker = _new_runtime_heartbeat(timestamp)
            worker = _stopped_runtime_heartbeat(worker, timestamp)
            write_json_atomic(self.heartbeat_path, worker)

    def mark_runtime_lane(
        self,
        lane: str,
        state: str,
        operation: str,
        timeout_seconds: float = 0,
        resume_after_seconds: float = 0,
        now: float | None = None,
    ) -> None:
        """Record bounded progress for one secret-free runtime lane."""
        with self._lock:
            timestamp = _worker_timestamp(now)
            checkpoint = _runtime_lane_checkpoint(
                lane,
                state,
                operation,
                timestamp,
                timeout_seconds=timeout_seconds,
                resume_after_seconds=resume_after_seconds,
            )
            worker = self._read_worker_status()
            if not worker:
                raise TelegramStateError(
                    "Telegram runtime heartbeat has not been started"
                )
            worker = _normalized_runtime_heartbeat(worker, now=timestamp)
            if worker["stopped_at"] is not None:
                raise TelegramStateError(
                    "Telegram runtime heartbeat is already stopped"
                )
            worker["lanes"][lane] = checkpoint
            write_json_atomic(self.heartbeat_path, worker)

    def worker_status(
        self,
        *,
        now: float | None = None,
        stale_after_seconds: float = 90.0,
    ) -> dict[str, Any]:
        """Return strict supervisor and progress-lane health."""
        if (
            isinstance(stale_after_seconds, bool)
            or not isinstance(stale_after_seconds, (int, float))
            or not math.isfinite(float(stale_after_seconds))
            or stale_after_seconds <= 0
        ):
            raise ValueError("stale_after_seconds must be a positive finite number")
        with self._lock:
            worker = self._read_worker_status()
            # Read first when using the wall clock so a separately constructed
            # health reader cannot reject a checkpoint atomically replaced a
            # few microseconds after it entered this method.
            timestamp = _worker_timestamp(now)
            if not worker:
                return _missing_runtime_status()
            worker = _normalized_runtime_heartbeat(worker, now=timestamp)
        started_at = worker["started_at"]
        heartbeat_at = worker["supervisor"]["pulse_at"]
        stopped_at = worker["stopped_at"]
        age = timestamp - heartbeat_at
        fresh = bool(
            stopped_at is None
            and age <= float(stale_after_seconds)
        )
        intake = _runtime_lane_summary(
            worker["lanes"]["intake"],
            now=timestamp,
            supervisor_fresh=fresh,
        )
        worker_lane = _runtime_lane_summary(
            worker["lanes"]["worker"],
            now=timestamp,
            supervisor_fresh=fresh,
        )
        return {
            "started_at": started_at,
            "heartbeat_at": heartbeat_at,
            "stopped_at": stopped_at,
            "heartbeat_age_seconds": age,
            "heartbeat_fresh": fresh,
            "runtime_ready": bool(intake["ready"] and worker_lane["ready"]),
            "intake": intake,
            "worker": worker_lane,
        }

    def _read_worker_status(self) -> dict[str, Any]:
        descriptor = -1
        try:
            flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.heartbeat_path, flags)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size > _MAX_RUNTIME_HEARTBEAT_BYTES
            ):
                raise TelegramStateError(
                    f"Invalid Telegram heartbeat at {self.heartbeat_path}"
                )
            raw = os.read(descriptor, _MAX_RUNTIME_HEARTBEAT_BYTES + 1)
            if len(raw) > _MAX_RUNTIME_HEARTBEAT_BYTES:
                raise TelegramStateError(
                    f"Invalid Telegram heartbeat at {self.heartbeat_path}"
                )
            worker = json.loads(raw.decode("utf-8"))
        except FileNotFoundError:
            return {}
        except TelegramStateError:
            raise
        except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise TelegramStateError(
                f"Cannot read Telegram heartbeat at {self.heartbeat_path}"
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if not isinstance(worker, dict):
            raise TelegramStateError(
                f"Invalid Telegram heartbeat at {self.heartbeat_path}"
            )
        try:
            return _normalized_runtime_heartbeat(worker)
        except (TelegramStateError, TypeError, ValueError) as exc:
            raise TelegramStateError(
                f"Invalid Telegram heartbeat at {self.heartbeat_path}"
            ) from exc

    def enqueue(self, update_id: int, request: dict[str, Any]) -> tuple[bool, int]:
        """Durably enqueue or reject one Telegram request.

        The returned boolean reports whether a new queue entry was created. The
        integer is that entry's current FIFO position, or zero for a terminal
        replay. Telegram update ids are bot-scoped and immutable, so seeing the
        same id with different content is treated as corrupt input. Accepted
        work and capacity refusals advance the polling offset immediately. A
        refused request keeps only a request-free notice outbox, so delivery
        recovery never depends on Telegram retaining or replaying the update.
        """
        normalized_id = _update_id(update_id)
        normalized_request, digest = _canonical_request(request)
        request_id = _update_id(normalized_request.get("update_id"))
        if request_id != normalized_id:
            raise TelegramStateError(
                "Telegram queue key does not match the saved request update id"
            )
        origin = _request_origin(normalized_request)
        key = str(normalized_id)
        with self._lock:
            terminal = self._terminal_record(key)
            if terminal is not None:
                _verify_digest(terminal, digest, normalized_id)
                if not terminal.get("request_digest"):
                    terminal["request_digest"] = digest
                self._advance(normalized_id)
                self._write()
                return False, 0

            jobs = self._data.setdefault("jobs", {})
            existing = jobs.get(key)
            if existing is not None:
                if not isinstance(existing, dict):
                    raise TelegramStateError(
                        f"Invalid Telegram job state for update {normalized_id}"
                    )
                prior_digest = str(existing.get("request_digest") or "")
                if prior_digest:
                    _verify_digest(existing, digest, normalized_id)
                    created = False
                else:
                    # Version 2 checkpoints had no request envelope. Preserve
                    # their delivery/run fields and attach the replayed update
                    # so the new worker can resume them safely.
                    if origin is None:
                        raise TelegramStateError(
                            "Telegram queue request has no valid receipt destination"
                        )
                    existing["request"] = normalized_request
                    existing["request_digest"] = digest
                    existing.setdefault("phase", "queued")
                    _save_origin(existing, origin)
                    receipt_phase = (
                        "pending"
                        if _queued_job_is_cancellable(
                            existing,
                            retry=self._data.setdefault(
                                "delivery_retries", {}
                            ).get(key),
                        )
                        else "sent"
                    )
                    self._data.setdefault("acceptance_receipts", {})[key] = {
                        "kind": "acceptance-receipt",
                        "phase": receipt_phase,
                        "request_digest": digest,
                        "origin_user_id": origin[0],
                        "origin_chat_id": origin[1],
                        "origin_chat_type": origin[2],
                        "failure_count": 0,
                        "deferred_at": 0.0,
                        "next_attempt_at": 0.0,
                    }
                    created = True
                self._advance(normalized_id)
                self._write()
                return created, self._queue_position(normalized_id)

            if sum(1 for value in jobs.values() if _is_pending(value)) >= MAX_PENDING_JOBS:
                completed = self._data.setdefault("completed_jobs", {})
                tombstone = _origin_tombstone(
                    phase="queue-full",
                    digest=digest,
                    origin=origin,
                )
                active_notices = sum(
                    1 for value in completed.values()
                    if _queue_full_notice_pending(value)
                )
                if origin is not None and (
                    active_notices < _MAX_ACTIVE_QUEUE_FULL_NOTICES
                ):
                    tombstone.update({
                        "notice_status": "pending",
                        "notice_binding": _queue_full_notice_binding(
                            normalized_id,
                            digest,
                            origin,
                        ),
                        "notice_failure_count": 0,
                        "notice_deferred_at": 0.0,
                        "notice_next_attempt_at": 0.0,
                    })
                else:
                    tombstone["notice_status"] = "failed"
                    for name in _ORIGIN_FIELDS:
                        tombstone.pop(name, None)
                completed[key] = tombstone
                self._advance(normalized_id)
                _bound_completed(
                    completed,
                    _MAX_COMPLETED_JOBS,
                    protected={
                        *self._data.setdefault("delivery_retries", {}),
                        *self._data.setdefault("delivery_notices", {}),
                    },
                )
                self._write()
                return False, 0

            if origin is None:
                raise TelegramStateError(
                    "Telegram queue request has no valid receipt destination"
                )
            record = {
                "phase": "queued",
                "request": normalized_request,
                "request_digest": digest,
            }
            _save_origin(record, origin)
            jobs[key] = record
            self._data.setdefault("acceptance_receipts", {})[key] = {
                "kind": "acceptance-receipt",
                "phase": "pending",
                "request_digest": digest,
                "origin_user_id": origin[0],
                "origin_chat_id": origin[1],
                "origin_chat_type": origin[2],
                "failure_count": 0,
                "deferred_at": 0.0,
                "next_attempt_at": 0.0,
            }
            self._advance(normalized_id)
            self._write()
            return True, self._queue_position(normalized_id)

    def claim_next(self) -> tuple[int, dict[str, Any]] | None:
        """Claim the oldest resumable request for the single build worker."""
        with self._lock:
            jobs = self._data.setdefault("jobs", {})
            candidates: dict[int, dict[str, Any]] = {}
            for key, value in jobs.items():
                try:
                    update_id = _update_id(int(key))
                except (TelegramStateError, TypeError, ValueError) as exc:
                    raise TelegramStateError(
                        "Invalid Telegram queued job key"
                    ) from exc
                if key != str(update_id) or not isinstance(value, dict):
                    raise TelegramStateError("Invalid Telegram queued job state")
                if "request" not in value:
                    continue
                if not _is_pending(value):
                    raise TelegramStateError(
                        f"Telegram update {update_id} has an invalid queued phase"
                    )
                candidates[update_id] = _validated_saved_request(update_id, value)
            if not candidates:
                return None
            update_id = min(candidates)
            record = jobs[str(update_id)]
            receipt = self._data.setdefault("acceptance_receipts", {}).get(
                str(update_id)
            )
            if not isinstance(receipt, dict):
                raise TelegramStateError(
                    f"Telegram update {update_id} lost its acceptance receipt"
                )
            normalized_receipt = _normalized_acceptance_receipt(receipt)
            _require_acceptance_receipt_binding(
                normalized_receipt,
                record,
                update_id,
                retry=self._data.setdefault("delivery_retries", {}).get(
                    str(update_id)
                ),
            )
            if normalized_receipt["phase"] != "sent":
                return None
            record["phase"] = "running"
            self._write()
            return update_id, copy.deepcopy(candidates[update_id])

    def mark_waiting(self, update_id: int) -> None:
        """Return a claimed request to the resumable queue without losing it."""
        normalized_id = _update_id(update_id)
        with self._lock:
            record = self._data.setdefault("jobs", {}).get(str(normalized_id))
            if not isinstance(record, dict) or not isinstance(record.get("request"), dict):
                raise TelegramStateError(
                    f"Telegram update {normalized_id} is not queued"
                )
            record["phase"] = "waiting"
            self._write()

    @property
    def pending_delivery_notice_count(self) -> int:
        """Return terminal delivery notices whose send is unresolved."""
        with self._lock:
            return sum(
                1
                for value in self._data.setdefault("delivery_notices", {}).values()
                if _delivery_notice_pending(value)
            )

    def due_delivery_notice_ids(
        self,
        *,
        now: float | None = None,
    ) -> list[int]:
        """Return source-bound terminal notices whose retry is due."""
        current = time.time() if now is None else _finite_nonnegative(now, time.time())
        with self._lock:
            pause_until = _finite_nonnegative(
                self._data.get("delivery_notice_pause_until"),
                0.0,
            )
            if current < pause_until:
                return []
            due: list[int] = []
            notices = self._data.setdefault("delivery_notices", {})
            completed = self._data.setdefault("completed_jobs", {})
            retries = self._data.setdefault("delivery_retries", {})
            for key, value in notices.items():
                update_id = _delivery_notice_key(key)
                notice = _normalized_delivery_notice(value)
                source = completed.get(key)
                _require_delivery_notice_binding(
                    notice,
                    source,
                    update_id,
                    retry=retries.get(key),
                )
                retry = retries.get(key)
                if (
                    notice["notice_kind"] == "retry-scheduled"
                    and _delivery_retry_active(retry)
                    and _delivery_retry_due(retry, current) <= current
                ):
                    # The build retry now supersedes this schedule notice. A
                    # daemon claims it before this lane; one-shot mode leaves
                    # both durable rather than sending already-stale timing.
                    continue
                if notice["next_attempt_at"] <= current:
                    due.append(update_id)
            return sorted(due)

    def delivery_notice(self, update_id: int) -> dict[str, object]:
        """Return one safe semantic notice without rehydrating request text."""
        normalized_id = _update_id(update_id)
        key = str(normalized_id)
        with self._lock:
            value = self._data.setdefault("delivery_notices", {}).get(key)
            source = self._data.setdefault("completed_jobs", {}).get(key)
            if not isinstance(value, dict) or not isinstance(source, dict):
                return {}
            notice = _normalized_delivery_notice(value)
            retry = self._data.setdefault("delivery_retries", {}).get(key)
            _require_delivery_notice_binding(
                notice,
                source,
                normalized_id,
                retry=retry,
            )
            payload: dict[str, object] = {
                "update_id": normalized_id,
                "notice_kind": notice["notice_kind"],
                "user_id": notice["origin_user_id"],
                "chat_id": notice["origin_chat_id"],
                "chat_type": notice["origin_chat_type"],
                "task_id": notice["task_id"],
                "delivery_id": notice["delivery_id"],
                "source_binding": notice["source_binding"],
            }
            if notice["notice_kind"] == "retry-scheduled":
                if not isinstance(retry, dict):  # guarded by the binding check
                    raise TelegramStateError(
                        f"Telegram update {normalized_id} lost its delivery retry"
                    )
                payload.update({
                    "attempt": retry["attempt"],
                    "max_attempts": retry["max_attempts"],
                    "next_attempt_at": retry["next_attempt_at"],
                })
            else:
                safe_result = _normalize_delivery_outcome(
                    source.get("delivery_result")
                )
                payload.update({
                    "result": safe_result,
                    "follow_up_phase": notice["follow_up_phase"],
                    "retry_unavailable_phase": notice[
                        "retry_unavailable_phase"
                    ],
                })
            return payload

    def defer_delivery_notice(
        self,
        update_id: int,
        *,
        source_binding: str,
        delay_seconds: float,
        now: float | None = None,
    ) -> None:
        """Persist bounded terminal-notice backoff without diagnostics."""
        normalized_id = _update_id(update_id)
        with self._lock:
            value = self._data.setdefault("delivery_notices", {}).get(
                str(normalized_id)
            )
            notice = _normalized_delivery_notice(value)
            if notice["source_binding"] != source_binding:
                raise TelegramStateError(
                    f"Telegram update {normalized_id} delivery notice changed"
                )
        self.defer_delivery_notices(
            [normalized_id],
            delay_seconds=delay_seconds,
            now=now,
            pause_all=False,
        )

    def defer_delivery_notices(
        self,
        update_ids: list[int],
        *,
        delay_seconds: float,
        now: float | None = None,
        pause_all: bool = True,
    ) -> None:
        """Atomically defer a cohort of terminal notices after Telegram 429."""
        if not isinstance(pause_all, bool):
            raise TelegramStateError("Invalid Telegram delivery notice pause")
        normalized_ids = sorted({_update_id(value) for value in update_ids})
        current = time.time() if now is None else _finite_nonnegative(now, time.time())
        delay = _bounded_delay(
            delay_seconds,
            _MAX_DELIVERY_NOTICE_RETRY_SECONDS,
        )
        with self._lock:
            pending: list[tuple[dict[str, Any], dict[str, Any]]] = []
            notices = self._data.setdefault("delivery_notices", {})
            completed = self._data.setdefault("completed_jobs", {})
            retries = self._data.setdefault("delivery_retries", {})
            for normalized_id in normalized_ids:
                key = str(normalized_id)
                value = notices.get(key)
                source = completed.get(key)
                if not isinstance(value, dict) or not isinstance(source, dict):
                    continue
                notice = _normalized_delivery_notice(value)
                _require_delivery_notice_binding(
                    notice,
                    source,
                    normalized_id,
                    retry=retries.get(key),
                )
                pending.append((value, notice))
            for value, notice in pending:
                value.update({
                    "phase": "retrying",
                    "failure_count": int(notice["failure_count"]) + 1,
                    "deferred_at": current,
                    "next_attempt_at": current + delay,
                })
            if pause_all:
                prior_pause = _finite_nonnegative(
                    self._data.get("delivery_notice_pause_until"),
                    0.0,
                )
                self._data["delivery_notice_pause_until"] = min(
                    max(prior_pause, current + delay),
                    current + _MAX_DELIVERY_NOTICE_RETRY_SECONDS,
                )
            if pending or pause_all:
                self._write()

    def complete_delivery_notice(
        self,
        update_id: int,
        *,
        source_binding: str,
        delivered: bool,
    ) -> None:
        """Finish one terminal notice and scrub its private route."""
        normalized_id = _update_id(update_id)
        key = str(normalized_id)
        with self._lock:
            notices = self._data.setdefault("delivery_notices", {})
            value = notices.get(key)
            source = self._data.setdefault("completed_jobs", {}).get(key)
            if not isinstance(value, dict):
                return
            notice = _normalized_delivery_notice(value)
            if notice["source_binding"] != source_binding:
                raise TelegramStateError(
                    f"Telegram update {normalized_id} delivery notice changed"
                )
            _require_delivery_notice_binding(
                notice,
                source,
                normalized_id,
                retry=self._data.setdefault("delivery_retries", {}).get(key),
            )
            if not isinstance(source, dict):  # guarded by the binding check
                raise TelegramStateError(
                    f"Telegram update {normalized_id} lost its completed source"
                )
            notices.pop(key)
            source["delivery_final_sent"] = bool(delivered)
            source["delivery_notice"] = {
                "phase": "sent" if delivered else "notification-failed",
                "notice_kind": notice["notice_kind"],
            }
            _bound_completed(
                self._data.setdefault("completed_jobs", {}),
                _MAX_COMPLETED_JOBS,
                protected={
                    *self._data.setdefault("delivery_retries", {}),
                    *notices,
                },
            )
            self._write()

    def job(self, update_id: int) -> dict[str, Any]:
        with self._lock:
            jobs = self._data.setdefault("jobs", {})
            value = jobs.get(str(update_id), {})
            return copy.deepcopy(value) if isinstance(value, dict) else {}

    def set_job(self, update_id: int, **fields: Any) -> None:
        with self._lock:
            jobs = self._data.setdefault("jobs", {})
            record = jobs.setdefault(str(update_id), {})
            record.update(copy.deepcopy(fields))
            self._write()

    def record_failure(self, update_id: int) -> int:
        """Persist and return the number of handler attempts for an update."""
        with self._lock:
            jobs = self._data.setdefault("jobs", {})
            record = jobs.setdefault(str(update_id), {})
            attempts = int(record.get("attempt_count", 0)) + 1
            record.update({"attempt_count": attempts, "phase": "retrying"})
            self._write()
            return attempts

    def dead_letter(self, update_id: int) -> None:
        """Advance past a poison update while retaining its safe checkpoint."""
        with self._lock:
            key = str(update_id)
            jobs = self._data.setdefault("jobs", {})
            record = copy.deepcopy(jobs.pop(key, {}))
            self._data.setdefault("acceptance_receipts", {}).pop(key, None)
            record.pop("request", None)
            record["phase"] = "dead-letter"
            retry = self._data.setdefault("delivery_retries", {}).pop(key, None)
            if _delivery_retry_active(retry):
                record["delivery_retry"] = {
                    "phase": "notification-failed",
                    "root_delivery_id": retry.get("root_delivery_id", ""),
                    "delivery_id": retry.get("delivery_id", ""),
                    "attempt": retry.get("attempt", 0),
                    "max_attempts": retry.get("max_attempts", 0),
                    "failure_code": retry.get("failure_code", ""),
                }
            failed = self._data.setdefault("failed_jobs", {})
            failed[key] = record
            self._advance(update_id)
            _bound(failed, 500)
            self._write()

    def failed_job(self, update_id: int) -> dict[str, Any]:
        with self._lock:
            value = self._data.setdefault("failed_jobs", {}).get(str(update_id), {})
            return copy.deepcopy(value) if isinstance(value, dict) else {}

    def completed_job(self, update_id: int) -> dict[str, Any]:
        with self._lock:
            value = self._data.setdefault("completed_jobs", {}).get(str(update_id), {})
            return copy.deepcopy(value) if isinstance(value, dict) else {}

    def delivery_retry(self, update_id: int) -> dict[str, Any]:
        """Return a detached retry checkpoint for status and tests."""
        with self._lock:
            value = self._data.setdefault("delivery_retries", {}).get(
                str(update_id),
                {},
            )
            return copy.deepcopy(value) if isinstance(value, dict) else {}

    def schedule_delivery_retry(
        self,
        update_id: int,
        retry: dict[str, Any],
        *,
        delay_seconds: float,
        expires_at: float | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Reserve one request-free retry before acknowledging its source update.

        The retry contains only immutable task/delivery routing data. The
        original Telegram request body remains in the active job until normal
        acknowledgement removes it, and is never copied into this scheduler.
        """
        normalized_id = _update_id(update_id)
        current = time.time() if now is None else _finite_nonnegative(now, time.time())
        deadline = (
            current + _DELIVERY_RETRY_MAX_AGE_SECONDS
            if expires_at is None
            else min(
                _finite_nonnegative(expires_at, 0.0),
                current + _DELIVERY_RETRY_MAX_AGE_SECONDS,
            )
        )
        candidate = _normalize_delivery_retry(retry)
        candidate.update({
            "phase": "pending",
            "attempt_started": False,
            "post_expiry_reconciliation_started": False,
            "failure_count": 0,
            "next_attempt_at": current + _bounded_delay(
                delay_seconds,
                _MAX_TELEGRAM_RATE_LIMIT_SECONDS,
            ),
            "created_at": current,
            "expires_at": deadline,
        })
        if candidate["next_attempt_at"] >= candidate["expires_at"]:
            candidate.update({
                "phase": "not-started-deadline",
                "next_attempt_at": 0.0,
            })
            return candidate
        key = str(normalized_id)
        with self._lock:
            source = self._data.setdefault("jobs", {}).get(key)
            if not isinstance(source, dict) or not isinstance(source.get("request"), dict):
                raise TelegramStateError(
                    f"Telegram update {normalized_id} is not queued"
                )
            _require_retry_source_binding(candidate, source, normalized_id)
            retries = self._data.setdefault("delivery_retries", {})
            existing = retries.get(key)
            if isinstance(existing, dict):
                _require_retry_binding(existing, candidate, normalized_id)
                return copy.deepcopy(existing)
            active = sum(
                1
                for value in retries.values()
                if _delivery_retry_active(value)
                and (
                    isinstance(value.get("outcome"), dict)
                    or _finite_nonnegative(value.get("expires_at"), current) > current
                )
            )
            if (
                active >= _MAX_ACTIVE_DELIVERY_RETRIES
                or len(retries) >= _MAX_COMPLETED_JOBS
            ):
                candidate.update({
                    "phase": "not-started-capacity",
                    "next_attempt_at": 0.0,
                })
                return candidate
            retries[key] = candidate
            self._write()
            return copy.deepcopy(candidate)

    def claim_due_delivery_retry(
        self,
        *,
        now: float | None = None,
    ) -> tuple[int, dict[str, Any]] | None:
        """Claim the oldest due retry without rehydrating Telegram request text."""
        current = time.time() if now is None else _finite_nonnegative(now, time.time())
        with self._lock:
            candidates: list[tuple[float, int]] = []
            retries = self._data.setdefault("delivery_retries", {})
            for key, retry in retries.items():
                if not _delivery_retry_active(retry):
                    continue
                source = self._data.setdefault("completed_jobs", {}).get(key)
                if not isinstance(source, dict) or "request" in source:
                    continue
                due = _delivery_retry_due(retry, current)
                if due <= current:
                    candidates.append((due, int(key)))
            if not candidates:
                return None
            _due, update_id = min(candidates)
            retry = self._delivery_retry_record(update_id)
            key = str(update_id)
            notices = self._data.setdefault("delivery_notices", {})
            notice_value = notices.get(key)
            if isinstance(notice_value, dict):
                notice = _normalized_delivery_notice(notice_value)
                source = self._data.setdefault("completed_jobs", {}).get(key)
                _require_delivery_notice_binding(
                    notice,
                    source,
                    update_id,
                    retry=retry,
                )
                if notice["notice_kind"] == "retry-scheduled":
                    notices.pop(key)
                    if isinstance(source, dict):
                        source["delivery_notice"] = {
                            "phase": "superseded",
                            "notice_kind": "retry-scheduled",
                        }
            retry["phase"] = "running"
            self._write()
            return update_id, copy.deepcopy(retry)

    def reschedule_delivery_retry(
        self,
        update_id: int,
        *,
        delivery_id: str,
        attempt: int,
        delay_seconds: float,
        failure_code: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Atomically advance a retry and replace its semantic schedule notice."""
        normalized_id = _update_id(update_id)
        current = time.time() if now is None else _finite_nonnegative(now, time.time())
        with self._lock:
            retry = self._delivery_retry_record(normalized_id)
            key = str(normalized_id)
            source = self._data.setdefault("completed_jobs", {}).get(key)
            if not isinstance(source, dict) or "request" in source:
                raise TelegramStateError(
                    f"Telegram update {normalized_id} has no safe retry source"
                )
            prospective_source = copy.deepcopy(source)
            prospective_source["delivery_final_sent"] = False
            prospective_source.pop("delivery_notice", None)
            try:
                canonical_result = _normalize_delivery_outcome(
                    prospective_source.get("delivery_result")
                )
            except (TelegramStateError, TypeError, ValueError) as exc:
                raise TelegramStateError(
                    f"Telegram update {normalized_id} has an invalid terminal result"
                ) from exc
            prospective_source["delivery_result"] = canonical_result
            prospective_source["delivery_status"] = canonical_result["status"]
            prospective_source["delivery_phase"] = canonical_result["phase"]
            maximum = _retry_integer(retry.get("max_attempts"), "max_attempts")
            prior_attempt = _retry_integer(retry.get("attempt"), "attempt")
            if attempt != prior_attempt + 1 or attempt > maximum:
                raise TelegramStateError("Invalid Telegram delivery retry attempt")
            expected = _retry_delivery_id(str(retry.get("root_delivery_id") or ""), attempt)
            if delivery_id != expected:
                raise TelegramStateError("Invalid Telegram delivery retry identity")
            due_at = current + _bounded_delay(
                delay_seconds,
                _MAX_TELEGRAM_RATE_LIMIT_SECONDS,
            )
            if due_at >= _finite_nonnegative(retry.get("expires_at"), current):
                rejected = copy.deepcopy(retry)
                rejected["phase"] = "deadline-exceeded"
                return rejected
            candidate = copy.deepcopy(retry)
            candidate.update({
                "phase": "pending",
                "delivery_id": delivery_id,
                "attempt": attempt,
                "attempt_started": False,
                "post_expiry_reconciliation_started": False,
                "failure_code": _normalized_failure_code(failure_code),
                "failure_count": 0,
                "next_attempt_at": due_at,
            })
            notices = self._data.setdefault("delivery_notices", {})
            existing_notice = notices.get(key)
            if isinstance(existing_notice, dict):
                normalized_notice = _normalized_delivery_notice(existing_notice)
                _require_delivery_notice_binding(
                    normalized_notice,
                    source,
                    normalized_id,
                    retry=retry,
                )
            chat_id = candidate.get("chat_id")
            if (
                isinstance(chat_id, bool)
                or not isinstance(chat_id, int)
                or chat_id <= 0
            ):
                raise TelegramStateError(
                    f"Telegram update {normalized_id} lost its retry destination"
                )
            candidate_notice = _new_delivery_notice(
                normalized_id,
                prospective_source,
                (chat_id, chat_id, "private"),
                retry=candidate,
            )
            active = sum(
                1 for notice_key, value in notices.items()
                if notice_key != key and _delivery_notice_pending(value)
            )
            new_notice: dict[str, Any] | None = None
            if existing_notice is not None or active < _MAX_ACTIVE_DELIVERY_NOTICES:
                new_notice = candidate_notice
            self._data.setdefault("delivery_retries", {})[key] = candidate
            source["delivery_final_sent"] = False
            source["delivery_result"] = canonical_result
            source["delivery_status"] = canonical_result["status"]
            source["delivery_phase"] = canonical_result["phase"]
            if new_notice is None:
                notices.pop(key, None)
                source["delivery_notice"] = {
                    "phase": "not-started-capacity",
                    "notice_kind": "retry-scheduled",
                }
            else:
                notices[key] = new_notice
                source.pop("delivery_notice", None)
            self._write()
            return copy.deepcopy(candidate)

    def start_delivery_retry_attempt(
        self,
        update_id: int,
        *,
        delivery_id: str,
        post_expiry_reconciliation: bool = False,
    ) -> dict[str, Any]:
        """Write-ahead the exact delivery identity before invoking MondayOS."""
        if not isinstance(post_expiry_reconciliation, bool):
            raise TelegramStateError("Invalid Telegram delivery retry attempt")
        normalized_id = _update_id(update_id)
        with self._lock:
            retry = self._delivery_retry_record(normalized_id)
            if retry.get("delivery_id") != delivery_id:
                raise TelegramStateError(
                    "Telegram delivery retry attempt changed identity"
                )
            if isinstance(retry.get("outcome"), dict):
                raise TelegramStateError(
                    "Telegram delivery retry attempt already has an outcome"
                )
            changed = False
            if retry.get("attempt_started") is not True:
                retry["attempt_started"] = True
                changed = True
            if (
                post_expiry_reconciliation
                and retry.get("post_expiry_reconciliation_started") is not True
            ):
                retry["post_expiry_reconciliation_started"] = True
                changed = True
            if changed:
                self._write()
            return copy.deepcopy(retry)

    def defer_delivery_retry(
        self,
        update_id: int,
        *,
        delay_seconds: float,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Retry the same identity after an uncertain or notification failure."""
        normalized_id = _update_id(update_id)
        current = time.time() if now is None else _finite_nonnegative(now, time.time())
        with self._lock:
            retry = self._delivery_retry_record(normalized_id)
            failures = max(0, int(retry.get("failure_count", 0))) + 1
            if (
                not isinstance(retry.get("outcome"), dict)
                and retry.get("post_expiry_reconciliation_started") is True
                and current >= _finite_nonnegative(retry.get("expires_at"), current)
            ):
                retry.update({
                    "outcome_phase": "uncertain",
                    "outcome": _uncertain_delivery_outcome(
                        str(retry.get("delivery_id") or "")
                    ),
                    "phase": "running",
                    "failure_count": 0,
                    "next_attempt_at": current,
                })
                self._write()
                return copy.deepcopy(retry)
            if (
                isinstance(retry.get("outcome"), dict)
                and failures >= _MAX_DELIVERY_RETRY_NOTIFICATION_FAILURES
            ):
                outcome, outcome_phase = _staged_delivery_outcome(retry)
                return self._complete_delivery_retry(
                    normalized_id,
                    retry,
                    outcome,
                    outcome_phase=outcome_phase,
                    summary_phase="notification-failed",
                    notification_sent=False,
                )
            deferred_until = current + _bounded_delay(
                delay_seconds,
                _MAX_TELEGRAM_RATE_LIMIT_SECONDS,
            )
            retry.update({
                "phase": "retrying",
                "failure_count": failures,
                "next_attempt_at": max(
                    _finite_nonnegative(retry.get("next_attempt_at"), current),
                    deferred_until,
                ),
            })
            self._write()
            return copy.deepcopy(retry)

    def stage_delivery_retry_outcome(
        self,
        update_id: int,
        *,
        phase: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Durably stage a bounded outcome before attempting its notification."""
        normalized_id = _update_id(update_id)
        if phase not in {"completed", "failed", "expired", "uncertain"}:
            raise TelegramStateError("Invalid Telegram delivery retry outcome")
        outcome = _normalize_delivery_outcome(result)
        with self._lock:
            retry = self._delivery_retry_record(normalized_id)
            if outcome["delivery_id"] != retry.get("delivery_id"):
                raise TelegramStateError("Telegram delivery retry outcome changed identity")
            existing = retry.get("outcome")
            existing_phase = str(retry.get("outcome_phase") or "")
            if existing is not None and (existing != outcome or existing_phase != phase):
                raise TelegramStateError("Telegram delivery retry outcome changed")
            retry.update({
                "outcome_phase": phase,
                "outcome": outcome,
                "phase": "running",
                "failure_count": 0,
                "next_attempt_at": time.time(),
            })
            self._write()
            return copy.deepcopy(outcome)

    def finish_delivery_retry(self, update_id: int) -> dict[str, Any]:
        """Promote a staged outcome after its Telegram notification succeeds."""
        normalized_id = _update_id(update_id)
        with self._lock:
            retry = self._delivery_retry_record(normalized_id)
            outcome, outcome_phase = _staged_delivery_outcome(retry)
            return self._complete_delivery_retry(
                normalized_id,
                retry,
                outcome,
                outcome_phase=outcome_phase,
                summary_phase=outcome_phase,
                notification_sent=True,
            )

    def stop_delivery_retry(self, update_id: int, *, phase: str) -> dict[str, Any]:
        """Stop retry work when its Telegram destination is permanently gone."""
        normalized_id = _update_id(update_id)
        if phase != "notification-failed":
            raise TelegramStateError("Invalid Telegram delivery retry stop reason")
        key = str(normalized_id)
        with self._lock:
            retry = self._delivery_retry_record(normalized_id)
            if isinstance(retry.get("outcome"), dict):
                outcome, outcome_phase = _staged_delivery_outcome(retry)
                completed = self._data.setdefault("completed_jobs", {})
                record = completed.get(key)
                follow_up = record.get("follow_up") if isinstance(record, dict) else None
                if _follow_up_active(follow_up):
                    follow_up.update({
                        "phase": "notification-failed",
                        "next_poll_at": 0.0,
                    })
                return self._complete_delivery_retry(
                    normalized_id,
                    retry,
                    outcome,
                    outcome_phase=outcome_phase,
                    summary_phase=phase,
                    notification_sent=False,
                )
            completed = self._data.setdefault("completed_jobs", {})
            record = completed.get(key)
            if not isinstance(record, dict) or "request" in record:
                raise TelegramStateError(
                    f"Telegram update {normalized_id} has no safe completed tombstone"
                )
            summary = {
                "phase": phase,
                "root_delivery_id": retry.get("root_delivery_id", ""),
                "delivery_id": "",
                "attempt": max(
                    1,
                    _retry_integer(retry.get("attempt"), "attempt") - 1,
                ),
                "max_attempts": retry.get("max_attempts", 0),
                "failure_code": retry.get("failure_code", ""),
            }
            summary["delivery_id"] = (
                retry.get("root_delivery_id", "")
                if summary["attempt"] == 1
                else _retry_delivery_id(
                    str(retry.get("root_delivery_id") or ""),
                    int(summary["attempt"]),
                )
            )
            record["delivery_retry"] = summary
            self._data.setdefault("delivery_retries", {}).pop(key, None)
            self._write()
            return copy.deepcopy(summary)

    def _complete_delivery_retry(
        self,
        update_id: int,
        retry: dict[str, Any],
        outcome: dict[str, Any],
        *,
        outcome_phase: str,
        summary_phase: str,
        notification_sent: bool,
    ) -> dict[str, Any]:
        key = str(update_id)
        completed = self._data.setdefault("completed_jobs", {})
        record = completed.get(key)
        if not isinstance(record, dict) or "request" in record:
            raise TelegramStateError(
                f"Telegram update {update_id} has no safe completed tombstone"
            )
        summary = {
            "phase": summary_phase,
            "outcome_phase": outcome_phase,
            "root_delivery_id": retry.get("root_delivery_id", ""),
            "delivery_id": retry.get("delivery_id", ""),
            "attempt": retry.get("attempt", 0),
            "max_attempts": retry.get("max_attempts", 0),
            "failure_code": outcome.get("failure_code", ""),
        }
        record.update({
            "delivery_id": outcome["delivery_id"],
            "delivery_terminal": True,
            "delivery_final_sent": notification_sent,
            "delivery_status": outcome["status"],
            "delivery_phase": outcome["phase"],
            "delivery_result": outcome,
            "delivery_retry": summary,
        })
        self._data.setdefault("delivery_retries", {}).pop(key, None)
        _bound_completed(
            completed,
            _MAX_COMPLETED_JOBS,
            protected={
                *self._data.setdefault("delivery_retries", {}),
                *self._data.setdefault("delivery_notices", {}),
            },
        )
        self._write()
        return copy.deepcopy(summary)

    def _delivery_retry_record(self, update_id: int) -> dict[str, Any]:
        retry = self._data.setdefault("delivery_retries", {}).get(str(update_id))
        if not isinstance(retry, dict) or not _delivery_retry_active(retry):
            raise TelegramStateError(
                f"Telegram update {update_id} has no active delivery retry"
            )
        key = str(update_id)
        source = self._data.setdefault("jobs", {}).get(key)
        if isinstance(source, dict):
            if not isinstance(source.get("request"), dict):
                raise TelegramStateError(
                    f"Telegram update {update_id} delivery retry lost its source"
                )
        else:
            source = self._data.setdefault("completed_jobs", {}).get(key)
            if not isinstance(source, dict) or "request" in source:
                raise TelegramStateError(
                    f"Telegram update {update_id} delivery retry lost its source"
                )
        _require_retry_source_binding(retry, source, update_id)
        return retry

    def claim_due_follow_up(
        self,
        *,
        now: float | None = None,
    ) -> tuple[int, dict[str, Any]] | None:
        """Claim one due lifecycle follow-up from a request-free tombstone.

        Follow-ups deliberately remain outside the main FIFO.  They contain no
        Telegram request body and can therefore be polled after the accepted
        build request has reached its terminal replay tombstone.  A persisted
        ``polling`` phase is reclaimable after restart; the process lock still
        guarantees that only one live Telegram worker can claim it at a time.
        """
        current = time.time() if now is None else float(now)
        with self._lock:
            expired = self._expire_follow_ups(current)
            completed = self._data.setdefault("completed_jobs", {})
            candidates: list[tuple[float, int]] = []
            for key, record in completed.items():
                if not isinstance(record, dict):
                    continue
                if _delivery_retry_active(
                    self._data.setdefault("delivery_retries", {}).get(key)
                ):
                    continue
                follow_up = record.get("follow_up")
                if not _follow_up_active(follow_up):
                    continue
                due = _finite_nonnegative(follow_up.get("next_poll_at"), 0.0)
                if due <= current:
                    candidates.append((due, int(key)))
            if not candidates:
                if expired:
                    self._write()
                return None
            _due, update_id = min(candidates)
            follow_up = completed[str(update_id)]["follow_up"]
            follow_up["phase"] = "polling"
            self._write()
            return update_id, copy.deepcopy(follow_up)

    def start_follow_up(
        self,
        update_id: int,
        follow_up: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Atomically start one bounded, expiring lifecycle watcher."""
        normalized_id = _update_id(update_id)
        current = time.time() if now is None else _finite_nonnegative(now, time.time())
        candidate = copy.deepcopy(follow_up)
        if (
            not isinstance(candidate, dict)
            or candidate.get("kind") != "pr-lifecycle"
            or candidate.get("phase") != "pending"
        ):
            raise TelegramStateError("Invalid Telegram lifecycle follow-up")
        with self._lock:
            key = str(normalized_id)
            record = self._data.setdefault("jobs", {}).get(key)
            from_completed = not isinstance(record, dict)
            if not isinstance(record, dict):
                record = self._data.setdefault("completed_jobs", {}).get(key)
            if not isinstance(record, dict):
                raise TelegramStateError(
                    f"Telegram update {normalized_id} has no durable record"
                )
            if from_completed and "request" in record:
                raise TelegramStateError(
                    "completed Telegram tombstones must not retain requests"
                )
            existing = record.get("follow_up")
            if isinstance(existing, dict):
                return copy.deepcopy(existing)
            self._expire_follow_ups(current)
            active = sum(
                1
                for bucket in ("jobs", "completed_jobs")
                for value in self._data.setdefault(bucket, {}).values()
                if isinstance(value, dict)
                and _follow_up_active(value.get("follow_up"))
            )
            candidate.update({
                "created_at": current,
                "expires_at": current + _FOLLOW_UP_MAX_AGE_SECONDS,
            })
            if active >= _MAX_ACTIVE_FOLLOW_UPS:
                candidate.update({
                    "phase": "not-started-capacity",
                    "next_poll_at": 0.0,
                })
            record["follow_up"] = candidate
            self._write()
            return copy.deepcopy(candidate)

    def update_follow_up(self, update_id: int, **fields: Any) -> dict[str, Any]:
        """Update one claimed follow-up without permitting identity rebinding."""
        normalized_id = _update_id(update_id)
        with self._lock:
            record = self._data.setdefault("completed_jobs", {}).get(str(normalized_id))
            if not isinstance(record, dict):
                raise TelegramStateError(
                    f"Telegram update {normalized_id} has no completed tombstone"
                )
            if "request" in record:
                raise TelegramStateError("completed Telegram tombstones must not retain requests")
            follow_up = record.get("follow_up")
            if not isinstance(follow_up, dict):
                raise TelegramStateError(
                    f"Telegram update {normalized_id} has no lifecycle follow-up"
                )
            immutable = {
                "kind", "delivery_id", "task_id", "project_name", "chat_id", "pr_url",
                "created_at", "expires_at",
            }
            for name in immutable.intersection(fields):
                if fields[name] != follow_up.get(name):
                    raise TelegramStateError(
                        f"Telegram update {normalized_id} follow-up cannot change {name}"
                    )
            follow_up.update(copy.deepcopy(fields))
            self._write()
            return copy.deepcopy(follow_up)

    def defer_follow_up(
        self,
        update_id: int,
        *,
        delay_seconds: float,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Retry a failed observation later without retaining remote diagnostics."""
        return self._defer_follow_up(
            update_id,
            delay_seconds=delay_seconds,
            maximum_delay=_MAX_OBSERVATION_RETRY_SECONDS,
            now=now,
        )

    def defer_rate_limited_follow_up(
        self,
        update_id: int,
        *,
        retry_after: float,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Honor Telegram's retry window independently of observer backoff."""
        return self._defer_follow_up(
            update_id,
            delay_seconds=retry_after,
            maximum_delay=_MAX_TELEGRAM_RATE_LIMIT_SECONDS,
            now=now,
        )

    def mark_follow_up_notification_failed(self, update_id: int) -> dict[str, Any]:
        """Stop a watcher after Telegram permanently rejects its destination."""
        normalized_id = _update_id(update_id)
        with self._lock:
            record = self._data.setdefault("completed_jobs", {}).get(str(normalized_id))
            follow_up = record.get("follow_up") if isinstance(record, dict) else None
            if not isinstance(follow_up, dict):
                raise TelegramStateError(
                    f"Telegram update {normalized_id} has no lifecycle follow-up"
                )
            if not _follow_up_active(follow_up):
                return copy.deepcopy(follow_up)
            failures = max(0, int(follow_up.get("failure_count", 0))) + 1
            follow_up.update({
                "phase": "notification-failed",
                "failure_count": failures,
                "next_poll_at": 0.0,
            })
            # The Telegram diagnostic can contain destination-specific data.
            # The terminal phase is sufficient for operators and eviction.
            follow_up.pop("error", None)
            self._write()
            return copy.deepcopy(follow_up)

    def _defer_follow_up(
        self,
        update_id: int,
        *,
        delay_seconds: float,
        maximum_delay: float,
        now: float | None,
    ) -> dict[str, Any]:
        normalized_id = _update_id(update_id)
        current = time.time() if now is None else _finite_nonnegative(now, time.time())
        delay = _bounded_delay(delay_seconds, maximum_delay)
        with self._lock:
            record = self._data.setdefault("completed_jobs", {}).get(str(normalized_id))
            follow_up = record.get("follow_up") if isinstance(record, dict) else None
            if not isinstance(follow_up, dict):
                raise TelegramStateError(
                    f"Telegram update {normalized_id} has no lifecycle follow-up"
                )
            if not _follow_up_active(follow_up):
                return copy.deepcopy(follow_up)
            failures = max(0, int(follow_up.get("failure_count", 0))) + 1
            follow_up.update({
                "phase": "retrying",
                "failure_count": failures,
                "next_poll_at": current + delay,
            })
            # Raw GitHub/CLI/Telegram errors are intentionally not persisted or
            # sent. The failure counter is enough to drive bounded backoff.
            follow_up.pop("error", None)
            self._write()
            return copy.deepcopy(follow_up)

    def next_follow_up_delay(self, *, now: float | None = None) -> float | None:
        """Return the bounded wait until the next background action."""
        current = time.time() if now is None else float(now)
        with self._lock:
            expired = self._expire_follow_ups(current)
            due_times: list[float] = []
            completed = self._data.setdefault("completed_jobs", {})
            retries = self._data.setdefault("delivery_retries", {})
            for key, record in completed.items():
                if _delivery_retry_active(retries.get(key)):
                    continue
                follow_up = record.get("follow_up") if isinstance(record, dict) else None
                if _follow_up_active(follow_up):
                    due_times.append(min(
                        _finite_nonnegative(follow_up.get("next_poll_at"), current),
                        _finite_nonnegative(follow_up.get("expires_at"), current),
                    ))
            for key, retry in retries.items():
                if _delivery_retry_active(retry):
                    source = completed.get(key)
                    if isinstance(source, dict) and "request" not in source:
                        due_times.append(_delivery_retry_due(retry, current))
            for notice in self._data.setdefault("delivery_notices", {}).values():
                if _delivery_notice_pending(notice):
                    normalized = _normalized_delivery_notice(notice)
                    pause_until = _finite_nonnegative(
                        self._data.get("delivery_notice_pause_until"),
                        0.0,
                    )
                    due_times.append(max(
                        normalized["next_attempt_at"],
                        pause_until,
                    ))
            if expired:
                self._write()
            if not due_times:
                return None
            return min(300.0, max(0.0, min(due_times) - current))

    def is_terminal(self, update_id: int) -> bool:
        with self._lock:
            key = str(update_id)
            return self._terminal_record(key) is not None

    def queue_full_notice(self, update_id: int) -> dict[str, object]:
        """Return the request-free destination for a pending capacity notice."""
        normalized_id = _update_id(update_id)
        with self._lock:
            record = self._data.setdefault("completed_jobs", {}).get(
                str(normalized_id)
            )
            if not _queue_full_notice_pending(record):
                return {}
            notice = _normalized_queue_full_notice(record, normalized_id)
            return {
                "user_id": notice["origin_user_id"],
                "chat_id": notice["origin_chat_id"],
                "chat_type": notice["origin_chat_type"],
            }

    def complete_queue_full_notice(
        self,
        update_id: int,
        *,
        delivered: bool,
    ) -> None:
        """Finish a capacity notice and scrub its temporary destination."""
        normalized_id = _update_id(update_id)
        key = str(normalized_id)
        with self._lock:
            completed = self._data.setdefault("completed_jobs", {})
            record = completed.get(key)
            if not isinstance(record, dict) or record.get("phase") != "queue-full":
                raise TelegramStateError(
                    f"Telegram update {normalized_id} has no queue-full notice"
                )
            status = record.get("notice_status")
            if status not in _QUEUE_FULL_NOTICE_STATUSES:
                raise TelegramStateError(
                    f"Telegram update {normalized_id} has invalid notice state"
                )
            _normalized_queue_full_notice(record, normalized_id)
            final_status = status
            if status in {"pending", "retrying"}:
                final_status = "sent" if delivered else "failed"
            completed[key] = {
                "phase": "queue-full",
                "request_digest": record["request_digest"],
                "notice_status": final_status,
            }
            self._advance(normalized_id)
            _bound_completed(
                completed,
                _MAX_COMPLETED_JOBS,
                protected={
                    *self._data.setdefault("delivery_retries", {}),
                    *self._data.setdefault("delivery_notices", {}),
                },
            )
            self._write()

    def reaffirm_terminal(self, update_id: int) -> None:
        """Advance the offset for a replay without replacing its tombstone."""
        with self._lock:
            record = self._terminal_record(str(_update_id(update_id)))
            if _queue_full_notice_pending(record):
                raise TelegramStateError(
                    f"Telegram update {update_id} still needs its capacity notice"
                )
            self._advance(update_id)
            self._write()

    def acknowledge(self, update_id: int) -> None:
        with self._lock:
            normalized_id = _update_id(update_id)
            key = str(normalized_id)
            jobs = self._data.setdefault("jobs", {})
            record = copy.deepcopy(jobs.get(key, {}))
            origin = _saved_origin(record)
            record.pop("request", None)
            for name in _ORIGIN_FIELDS:
                record.pop(name, None)
            record["phase"] = "completed"
            notices = self._data.setdefault("delivery_notices", {})
            new_notice: dict[str, Any] | None = None
            if (
                record.get("delivery_terminal") is True
                and record.get("delivery_final_sent") is False
            ):
                if origin is None:
                    raise TelegramStateError(
                        f"Telegram update {normalized_id} lost its notice destination"
                    )
                try:
                    canonical_result = _normalize_delivery_outcome(
                        record.get("delivery_result")
                    )
                except (TelegramStateError, TypeError, ValueError) as exc:
                    raise TelegramStateError(
                        f"Telegram update {normalized_id} has an invalid terminal result"
                    ) from exc
                record["delivery_result"] = canonical_result
                record["delivery_status"] = canonical_result["status"]
                record["delivery_phase"] = canonical_result["phase"]
                retry = self._data.setdefault("delivery_retries", {}).get(key)
                candidate_notice = _new_delivery_notice(
                    normalized_id,
                    record,
                    origin,
                    retry=retry,
                )
                active = sum(
                    1 for value in notices.values()
                    if _delivery_notice_pending(value)
                )
                if active >= _MAX_ACTIVE_DELIVERY_NOTICES:
                    record["delivery_notice"] = {
                        "phase": "not-started-capacity",
                        "notice_kind": _delivery_notice_kind(retry),
                    }
                else:
                    new_notice = candidate_notice
            jobs.pop(key, None)
            self._data.setdefault("acceptance_receipts", {}).pop(key, None)
            completed = self._data.setdefault("completed_jobs", {})
            completed[key] = record
            if new_notice is not None:
                notices[key] = new_notice
            self._advance(normalized_id)
            _bound_completed(
                completed,
                _MAX_COMPLETED_JOBS,
                protected={
                    *self._data.setdefault("delivery_retries", {}),
                    *notices,
                },
            )
            self._write()

    def _expire_follow_ups(self, now: float) -> bool:
        changed = False
        for bucket in ("jobs", "completed_jobs"):
            for record in self._data.setdefault(bucket, {}).values():
                follow_up = record.get("follow_up") if isinstance(record, dict) else None
                if not _follow_up_active(follow_up):
                    continue
                expires_at = _finite_nonnegative(follow_up.get("expires_at"), 0.0)
                if expires_at <= 0:
                    created_at = _finite_nonnegative(
                        follow_up.get("created_at"),
                        now,
                    )
                    follow_up.update({
                        "created_at": created_at,
                        "expires_at": created_at + _FOLLOW_UP_MAX_AGE_SECONDS,
                    })
                    expires_at = created_at + _FOLLOW_UP_MAX_AGE_SECONDS
                    changed = True
                if expires_at <= now:
                    follow_up.update({"phase": "expired", "next_poll_at": 0.0})
                    follow_up.pop("error", None)
                    changed = True
        return changed

    def _advance(self, update_id: int) -> None:
        with self._lock:
            self._data["last_update_id"] = max(
                int(self._data.get("last_update_id", -1)), update_id,
            )

    def _terminal_record(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            completed = self._data.setdefault("completed_jobs", {})
            failed = self._data.setdefault("failed_jobs", {})
            value = completed.get(key)
            if value is None:
                value = failed.get(key)
            return value if isinstance(value, dict) else None

    def _queue_position(self, update_id: int) -> int:
        with self._lock:
            return sum(
                1
                for key, value in self._data.setdefault("jobs", {}).items()
                if int(key) <= update_id and _is_pending(value)
            )

    def _read(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return {
                    "version": 8,
                    "bot_id": None,
                    "bot_username": "",
                    "last_update_id": -1,
                    "cancellation_sequence": 0,
                    "jobs": {},
                    "completed_jobs": {},
                    "failed_jobs": {},
                    "delivery_retries": {},
                    "acceptance_receipts": {},
                    "delivery_notices": {},
                    "delivery_notice_pause_until": 0.0,
                }
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise TelegramStateError(
                    f"Cannot read Telegram state at {self.path}"
                ) from exc
            if not isinstance(payload, dict) or any(
                not isinstance(payload.get(name, {}), dict)
                for name in (
                    "jobs", "completed_jobs", "failed_jobs", "delivery_retries",
                    "acceptance_receipts", "delivery_notices",
                )
            ):
                raise TelegramStateError(f"Invalid Telegram state at {self.path}")
            stored_bot_id = payload.get("bot_id")
            if stored_bot_id is not None:
                try:
                    payload["bot_id"] = _positive_id(stored_bot_id)
                except ValueError as exc:
                    raise TelegramStateError(
                        f"Invalid Telegram state at {self.path}"
                    ) from exc
            if not isinstance(payload.get("bot_username", ""), str):
                raise TelegramStateError(f"Invalid Telegram state at {self.path}")
            try:
                _migrate_legacy_delivery_results(payload)
                _validate_loaded_queue_full_notices(payload)
                _validate_loaded_acceptance_receipts(payload)
                _validate_loaded_delivery_retries(payload)
                _validate_loaded_delivery_notices(payload)
            except (TelegramStateError, TypeError, ValueError) as exc:
                raise TelegramStateError(
                    f"Invalid Telegram state at {self.path}"
                ) from exc
            return payload

    def _write(self) -> None:
        with self._lock:
            self._data["version"] = 8
            self._data.setdefault("delivery_notice_pause_until", 0.0)
            write_json_atomic(self.path, self._data)


def _bound(records: dict[str, Any], limit: int) -> None:
    if len(records) <= limit:
        return
    for key in sorted(records, key=int)[:-limit]:
        records.pop(key, None)


def _bound_completed(
    records: dict[str, Any],
    limit: int,
    *,
    protected: set[str] | None = None,
) -> None:
    """Bound replay tombstones without discarding active background work.

    Cancellation tombstones are evicted after ordinary history so a retryable
    cancellation reply can safely re-run the command and receive the same
    result. Among cancellations, a durable monotonic sequence preserves the
    most recently performed cancellation even when JSON key sorting reorders
    its older target update id across restart.
    """
    if len(records) <= limit:
        return
    removable = [
        key for key in records
        if key not in (protected or set())
        and not _queue_full_notice_pending(records[key])
        and not _follow_up_active(
            records[key].get("follow_up")
            if isinstance(records[key], dict)
            else None
        )
    ]
    removable.sort(key=lambda key: (
        isinstance(records[key], dict)
        and records[key].get("phase") == "cancelled",
        _cancellation_sequence(records[key])
        if isinstance(records[key], dict)
        and records[key].get("phase") == "cancelled"
        else int(key),
        int(key),
    ))
    for key in removable[: max(0, len(records) - limit)]:
        records.pop(key, None)


def _follow_up_active(value: Any) -> TypeGuard[dict[str, Any]]:
    return (
        isinstance(value, dict)
        and value.get("kind") == "pr-lifecycle"
        and value.get("phase") in {"pending", "polling", "retrying"}
    )


def _queue_full_notice_key(value: Any) -> int:
    try:
        update_id = _update_id(int(value))
    except (TelegramStateError, TypeError, ValueError) as exc:
        raise TelegramStateError("Invalid Telegram queue-full notice key") from exc
    if value != str(update_id):
        raise TelegramStateError("Invalid Telegram queue-full notice key")
    return update_id


def _queue_full_notice_binding(
    update_id: int,
    digest: str,
    origin: tuple[int, int, str],
) -> str:
    payload = {
        "kind": "queue-full-notice-binding-v1",
        "update_id": _update_id(update_id),
        "request_digest": digest,
        "origin_user_id": origin[0],
        "origin_chat_id": origin[1],
        "origin_chat_type": origin[2],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalized_queue_full_notice(
    value: Any,
    update_id: int,
) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("phase") != "queue-full":
        raise TelegramStateError("Invalid Telegram queue-full notice")
    status = value.get("notice_status")
    digest = value.get("request_digest")
    if (
        status not in _QUEUE_FULL_NOTICE_STATUSES
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        raise TelegramStateError("Invalid Telegram queue-full notice")

    if status in {"sent", "failed"}:
        if set(value) != _QUEUE_FULL_NOTICE_TERMINAL_FIELDS:
            raise TelegramStateError(
                "Terminal Telegram queue-full notice retained unexpected state"
            )
        return {
            "status": status,
            "failure_count": 0,
            "deferred_at": 0.0,
            "next_attempt_at": 0.0,
            "origin_user_id": None,
            "origin_chat_id": None,
            "origin_chat_type": None,
        }

    if set(value) != _QUEUE_FULL_NOTICE_ACTIVE_FIELDS:
        raise TelegramStateError(
            "Active Telegram queue-full notice retained unexpected state"
        )
    origin = _validated_origin(
        value.get("origin_user_id"),
        value.get("origin_chat_id"),
        value.get("origin_chat_type"),
    )
    binding = value.get("notice_binding")
    if (
        not isinstance(binding, str)
        or binding != _queue_full_notice_binding(update_id, digest, origin)
    ):
        raise TelegramStateError(
            "Telegram queue-full notice changed source binding"
        )
    failure_count = value.get("notice_failure_count")
    if (
        isinstance(failure_count, bool)
        or not isinstance(failure_count, int)
        or failure_count < 0
    ):
        raise TelegramStateError("Invalid Telegram queue-full notice retry state")
    deferred_at = _strict_timestamp(
        value.get("notice_deferred_at"),
        allow_zero=True,
    )
    next_attempt_at = _strict_timestamp(
        value.get("notice_next_attempt_at"),
        allow_zero=True,
    )
    if (
        next_attempt_at < deferred_at
        or next_attempt_at - deferred_at
        > _MAX_QUEUE_FULL_NOTICE_RETRY_SECONDS + 1
        or (
            status == "pending"
            and (failure_count or deferred_at or next_attempt_at)
        )
        or (
            status == "retrying"
            and (
                failure_count < 1
                or deferred_at <= 0
                or next_attempt_at <= deferred_at
            )
        )
    ):
        raise TelegramStateError("Invalid Telegram queue-full notice retry state")
    return {
        "status": status,
        "failure_count": failure_count,
        "deferred_at": deferred_at,
        "next_attempt_at": next_attempt_at,
        "origin_user_id": origin[0],
        "origin_chat_id": origin[1],
        "origin_chat_type": origin[2],
    }


def _validate_loaded_queue_full_notices(payload: dict[str, Any]) -> None:
    version = payload.get("version", 0)
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or not 0 <= version <= 8
    ):
        raise TelegramStateError("Invalid Telegram state version")
    last_update_id = payload.get("last_update_id", -1)
    if (
        isinstance(last_update_id, bool)
        or not isinstance(last_update_id, int)
        or last_update_id < -1
    ):
        raise TelegramStateError("Invalid Telegram update offset")

    active_count = 0
    completed = payload.setdefault("completed_jobs", {})
    for key, record in sorted(completed.items(), key=lambda item: int(item[0])):
        if not isinstance(record, dict) or record.get("phase") != "queue-full":
            continue
        update_id = _queue_full_notice_key(key)
        if version < 7:
            status = record.get("notice_status", "pending")
            digest = record.get("request_digest")
            if (
                not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            ):
                raise TelegramStateError("Invalid legacy queue-full notice")
            if status == "pending":
                # A pre-v7 route has no binding to its original update. Do not
                # turn mutable legacy routing fields into a new authorization
                # to message someone during migration. Advance past the old
                # refusal and retain only its request-free terminal digest.
                record = {
                    "phase": "queue-full",
                    "request_digest": digest,
                    "notice_status": "failed",
                }
            elif status in {"sent", "failed"}:
                record = {
                    "phase": "queue-full",
                    "request_digest": digest,
                    "notice_status": status,
                }
            else:
                raise TelegramStateError("Invalid legacy queue-full notice")
            completed[key] = record
            payload["last_update_id"] = max(
                int(payload.get("last_update_id", -1)),
                update_id,
            )
        elif last_update_id < update_id:
            raise TelegramStateError(
                f"Telegram queue-full notice {update_id} exceeds saved offset"
            )

        notice = _normalized_queue_full_notice(record, update_id)
        if notice["status"] in {"pending", "retrying"}:
            active_count += 1
            if active_count > _MAX_ACTIVE_QUEUE_FULL_NOTICES:
                raise TelegramStateError("Too many pending queue-full notices")

    if version < 7:
        _bound_completed(
            completed,
            _MAX_COMPLETED_JOBS,
            protected=set(payload.setdefault("delivery_retries", {})),
        )


def _delivery_notice_pending(value: Any) -> TypeGuard[dict[str, Any]]:
    return (
        isinstance(value, dict)
        and value.get("kind") == "delivery-notice"
        and value.get("phase") in _DELIVERY_NOTICE_PHASES
    )


def _delivery_notice_key(value: Any) -> int:
    try:
        update_id = _update_id(int(value))
    except (TelegramStateError, TypeError, ValueError) as exc:
        raise TelegramStateError("Invalid Telegram delivery notice key") from exc
    if value != str(update_id):
        raise TelegramStateError("Invalid Telegram delivery notice key")
    return update_id


def _delivery_notice_kind(retry: Any) -> str:
    return "retry-scheduled" if _delivery_retry_active(retry) else "terminal-result"


def _delivery_notice_source_payload(
    source: Any,
    update_id: int,
    *,
    retry: Any,
) -> dict[str, Any]:
    if (
        not isinstance(source, dict)
        or "request" in source
        or source.get("phase") != "completed"
        or source.get("delivery_terminal") is not True
        or source.get("delivery_final_sent") is not False
    ):
        raise TelegramStateError(
            f"Telegram update {update_id} has no safe terminal notice source"
        )
    task_id = str(source.get("task_id") or "").upper()
    delivery_id = str(source.get("delivery_id") or "")
    chat_id = source.get("chat_id")
    request_digest = source.get("request_digest")
    workflow = source.get("workflow")
    project_name = source.get("project_name", "")
    result = source.get("delivery_result")
    if (
        not _TASK_ID.fullmatch(task_id)
        or not _DELIVERY_ID.fullmatch(delivery_id)
        or not isinstance(request_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", request_digest) is None
        or workflow != "live-build"
        or not isinstance(project_name, str)
        or len(project_name) > 160
        or any(not character.isprintable() for character in project_name)
        or isinstance(chat_id, bool)
        or not isinstance(chat_id, int)
        or chat_id == 0
        or not isinstance(result, dict)
        or str(result.get("delivery_id") or "") != delivery_id
        or str(result.get("status") or "").lower()
        not in _TERMINAL_DELIVERY_STATUSES
        or result.get("message") not in (None, "")
    ):
        raise TelegramStateError(
            f"Telegram update {update_id} has an invalid terminal notice source"
        )
    try:
        normalized_result = _normalize_delivery_outcome(result)
        serialized_result = json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        serialized_normalized = json.dumps(
            normalized_result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TelegramStateError, TypeError, ValueError) as exc:
        raise TelegramStateError(
            f"Telegram update {update_id} has an invalid terminal result"
        ) from exc
    if serialized_result != serialized_normalized:
        raise TelegramStateError(
            f"Telegram update {update_id} has a non-canonical terminal result"
        )
    notice_kind = _delivery_notice_kind(retry)
    retry_identity: dict[str, Any] = {}
    if notice_kind == "retry-scheduled":
        if not isinstance(retry, dict):  # guarded by notice_kind
            raise TelegramStateError("Invalid Telegram delivery notice retry")
        normalized_retry = _normalize_delivery_retry(retry)
        _require_retry_source_binding(normalized_retry, source, update_id)
        retry_identity = {
            "root_delivery_id": normalized_retry["root_delivery_id"],
            "delivery_id": normalized_retry["delivery_id"],
            "attempt": normalized_retry["attempt"],
            "max_attempts": normalized_retry["max_attempts"],
            "next_attempt_at": _strict_timestamp(retry.get("next_attempt_at")),
        }
    try:
        result_digest = hashlib.sha256(
            serialized_result.encode("utf-8")
        ).hexdigest()
    except (TypeError, ValueError) as exc:
        raise TelegramStateError(
            f"Telegram update {update_id} has an invalid terminal result"
        ) from exc
    return {
        "notice_kind": notice_kind,
        "task_id": task_id,
        "delivery_id": delivery_id,
        "chat_id": chat_id,
        "request_digest": request_digest,
        "workflow": workflow,
        "project_name": project_name,
        "result_digest": result_digest,
        "retry": retry_identity,
    }


def _delivery_notice_binding(
    update_id: int,
    source: dict[str, Any],
    origin: tuple[int, int, str],
    *,
    retry: Any,
    follow_up_phase: str,
    retry_unavailable_phase: str,
) -> str:
    payload = {
        "kind": "delivery-notice-binding-v1",
        "update_id": _update_id(update_id),
        **_delivery_notice_source_payload(source, update_id, retry=retry),
        "origin_user_id": origin[0],
        "origin_chat_id": origin[1],
        "origin_chat_type": origin[2],
        "follow_up_phase": follow_up_phase,
        "retry_unavailable_phase": retry_unavailable_phase,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _new_delivery_notice(
    update_id: int,
    source: dict[str, Any],
    origin: tuple[int, int, str],
    *,
    retry: Any,
) -> dict[str, Any]:
    source_payload = _delivery_notice_source_payload(
        source,
        update_id,
        retry=retry,
    )
    if (
        origin[2] != "private"
        or origin[0] != origin[1]
        or source_payload["chat_id"] != origin[1]
    ):
        raise TelegramStateError(
            f"Telegram update {update_id} changed its terminal notice route"
        )
    follow_up = source.get("follow_up")
    follow_up_phase = (
        str(follow_up.get("phase") or "")
        if isinstance(follow_up, dict)
        else ""
    )
    if follow_up_phase not in _DELIVERY_NOTICE_FOLLOW_UP_PHASES:
        follow_up_phase = ""
    retry_summary = source.get("delivery_retry")
    retry_unavailable_phase = (
        str(retry_summary.get("phase") or "")
        if isinstance(retry_summary, dict)
        else ""
    )
    if retry_unavailable_phase not in _DELIVERY_NOTICE_UNAVAILABLE_PHASES:
        retry_unavailable_phase = ""
    return {
        "kind": "delivery-notice",
        "phase": "pending",
        "notice_kind": source_payload["notice_kind"],
        "task_id": source_payload["task_id"],
        "delivery_id": source_payload["delivery_id"],
        "origin_user_id": origin[0],
        "origin_chat_id": origin[1],
        "origin_chat_type": origin[2],
        "follow_up_phase": follow_up_phase,
        "retry_unavailable_phase": retry_unavailable_phase,
        "source_binding": _delivery_notice_binding(
            update_id,
            source,
            origin,
            retry=retry,
            follow_up_phase=follow_up_phase,
            retry_unavailable_phase=retry_unavailable_phase,
        ),
        "failure_count": 0,
        "deferred_at": 0.0,
        "next_attempt_at": 0.0,
    }


def _normalized_delivery_notice(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != _DELIVERY_NOTICE_FIELDS
        or value.get("kind") != "delivery-notice"
        or value.get("phase") not in _DELIVERY_NOTICE_PHASES
        or value.get("notice_kind") not in _DELIVERY_NOTICE_KINDS
    ):
        raise TelegramStateError("Invalid Telegram delivery notice")
    task_id = str(value.get("task_id") or "").upper()
    delivery_id = str(value.get("delivery_id") or "")
    binding = value.get("source_binding")
    follow_up_phase = value.get("follow_up_phase")
    retry_unavailable_phase = value.get("retry_unavailable_phase")
    failure_count = value.get("failure_count")
    if (
        not _TASK_ID.fullmatch(task_id)
        or not _DELIVERY_ID.fullmatch(delivery_id)
        or not isinstance(binding, str)
        or re.fullmatch(r"[0-9a-f]{64}", binding) is None
        or follow_up_phase not in _DELIVERY_NOTICE_FOLLOW_UP_PHASES
        or retry_unavailable_phase not in _DELIVERY_NOTICE_UNAVAILABLE_PHASES
        or isinstance(failure_count, bool)
        or not isinstance(failure_count, int)
        or failure_count < 0
    ):
        raise TelegramStateError("Invalid Telegram delivery notice")
    origin = _validated_origin(
        value.get("origin_user_id"),
        value.get("origin_chat_id"),
        value.get("origin_chat_type"),
    )
    deferred_at = _strict_timestamp(value.get("deferred_at"), allow_zero=True)
    next_attempt_at = _strict_timestamp(
        value.get("next_attempt_at"),
        allow_zero=True,
    )
    phase = str(value.get("phase"))
    if (
        next_attempt_at < deferred_at
        or next_attempt_at - deferred_at > _MAX_DELIVERY_NOTICE_RETRY_SECONDS + 1
        or (phase == "pending" and (failure_count or deferred_at or next_attempt_at))
        or (phase == "retrying" and (
            failure_count < 1 or deferred_at <= 0 or next_attempt_at <= deferred_at
        ))
    ):
        raise TelegramStateError("Invalid Telegram delivery notice retry state")
    return {
        "kind": "delivery-notice",
        "phase": phase,
        "notice_kind": value["notice_kind"],
        "task_id": task_id,
        "delivery_id": delivery_id,
        "origin_user_id": origin[0],
        "origin_chat_id": origin[1],
        "origin_chat_type": origin[2],
        "follow_up_phase": follow_up_phase,
        "retry_unavailable_phase": retry_unavailable_phase,
        "source_binding": binding,
        "failure_count": failure_count,
        "deferred_at": deferred_at,
        "next_attempt_at": next_attempt_at,
    }


def _require_delivery_notice_binding(
    notice: dict[str, Any],
    source: Any,
    update_id: int,
    *,
    retry: Any,
) -> None:
    source_payload = _delivery_notice_source_payload(
        source,
        update_id,
        retry=retry,
    )
    origin = _validated_origin(
        notice.get("origin_user_id"),
        notice.get("origin_chat_id"),
        notice.get("origin_chat_type"),
    )
    if origin[2] != "private" or origin[0] != origin[1]:
        raise TelegramStateError(
            f"Telegram update {update_id} has a non-private delivery notice route"
        )
    expected = {
        "notice_kind": source_payload["notice_kind"],
        "task_id": source_payload["task_id"],
        "delivery_id": source_payload["delivery_id"],
        "origin_chat_id": source_payload["chat_id"],
        "source_binding": _delivery_notice_binding(
            update_id,
            source,
            origin,
            retry=retry,
            follow_up_phase=str(notice.get("follow_up_phase") or ""),
            retry_unavailable_phase=str(
                notice.get("retry_unavailable_phase") or ""
            ),
        ),
    }
    if any(notice.get(name) != value for name, value in expected.items()):
        raise TelegramStateError(
            f"Telegram update {update_id} delivery notice changed source binding"
        )


def _validate_loaded_delivery_notices(payload: dict[str, Any]) -> None:
    version = payload.get("version", 0)
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or not 0 <= version <= 8
    ):
        raise TelegramStateError("Invalid Telegram state version")
    notices = payload.setdefault("delivery_notices", {})
    if version < 8:
        # Older state has no route-bound outbox. Never infer permission to send
        # from historical `delivery_final_sent=False` tombstones.
        notices.clear()
        payload["delivery_notice_pause_until"] = 0.0
        return
    pause_until = _strict_timestamp(
        payload.setdefault("delivery_notice_pause_until", 0.0),
        allow_zero=True,
    )
    if pause_until > time.time() + _MAX_DELIVERY_NOTICE_RETRY_SECONDS + 1:
        raise TelegramStateError("Invalid Telegram delivery notice pause")
    payload["delivery_notice_pause_until"] = pause_until
    completed = payload.setdefault("completed_jobs", {})
    retries = payload.setdefault("delivery_retries", {})
    if len(notices) > _MAX_ACTIVE_DELIVERY_NOTICES:
        raise TelegramStateError("Too many pending Telegram delivery notices")
    for key, value in notices.items():
        update_id = _delivery_notice_key(key)
        notice = _normalized_delivery_notice(value)
        _require_delivery_notice_binding(
            notice,
            completed.get(key),
            update_id,
            retry=retries.get(key),
        )
        notices[key] = notice


def _acceptance_receipt_pending(value: Any) -> TypeGuard[dict[str, Any]]:
    return (
        isinstance(value, dict)
        and value.get("kind") == "acceptance-receipt"
        and value.get("phase") in {"pending", "retrying"}
    )


def _receipt_key(value: Any) -> int:
    try:
        update_id = _update_id(int(value))
    except (TelegramStateError, TypeError, ValueError) as exc:
        raise TelegramStateError("Invalid Telegram acceptance receipt key") from exc
    if value != str(update_id):
        raise TelegramStateError("Invalid Telegram acceptance receipt key")
    return update_id


def _normalized_acceptance_receipt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("kind") != "acceptance-receipt":
        raise TelegramStateError("Invalid Telegram acceptance receipt")
    phase = value.get("phase")
    digest = value.get("request_digest")
    failure_count = value.get("failure_count")
    if (
        phase not in _ACCEPTANCE_RECEIPT_PHASES
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or isinstance(failure_count, bool)
        or not isinstance(failure_count, int)
        or failure_count < 0
    ):
        raise TelegramStateError("Invalid Telegram acceptance receipt")
    origin = _validated_origin(
        value.get("origin_user_id"),
        value.get("origin_chat_id"),
        value.get("origin_chat_type"),
    )
    deferred_at = _strict_timestamp(
        value.get("deferred_at"),
        allow_zero=True,
    )
    next_attempt_at = _strict_timestamp(
        value.get("next_attempt_at"),
        allow_zero=True,
    )
    if (
        next_attempt_at < deferred_at
        or next_attempt_at - deferred_at
        > _MAX_ACCEPTANCE_RECEIPT_RETRY_SECONDS + 1
        or (phase == "pending" and (failure_count or deferred_at or next_attempt_at))
        or (phase == "retrying" and (
            failure_count < 1 or deferred_at <= 0 or next_attempt_at <= deferred_at
        ))
        or (phase == "sent" and (deferred_at or next_attempt_at))
    ):
        raise TelegramStateError("Invalid Telegram acceptance receipt retry state")
    return {
        "kind": "acceptance-receipt",
        "phase": phase,
        "request_digest": digest,
        "origin_user_id": origin[0],
        "origin_chat_id": origin[1],
        "origin_chat_type": origin[2],
        "failure_count": failure_count,
        "deferred_at": deferred_at,
        "next_attempt_at": next_attempt_at,
    }


def _require_acceptance_receipt_binding(
    receipt: dict[str, Any],
    record: dict[str, Any],
    update_id: int,
    *,
    retry: Any,
) -> None:
    request = _validated_saved_request(update_id, record)
    origin = _request_origin(request)
    expected = {
        "request_digest": record.get("request_digest"),
        "origin_user_id": origin[0] if origin else None,
        "origin_chat_id": origin[1] if origin else None,
        "origin_chat_type": origin[2] if origin else None,
    }
    if any(receipt.get(name) != value for name, value in expected.items()):
        raise TelegramStateError(
            f"Telegram update {update_id} acceptance receipt changed source binding"
        )
    if _acceptance_receipt_pending(receipt) and not _queued_job_is_cancellable(
        record,
        retry=retry,
    ):
        raise TelegramStateError(
            f"Telegram update {update_id} acceptance receipt changed job phase"
        )


def _validate_loaded_acceptance_receipts(payload: dict[str, Any]) -> None:
    jobs = payload.setdefault("jobs", {})
    receipts = payload.setdefault("acceptance_receipts", {})
    version = payload.get("version", 0)
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or not 0 <= version <= 8
    ):
        raise TelegramStateError("Invalid Telegram state version")

    if version < 6:
        for key, record in sorted(jobs.items(), key=lambda item: int(item[0])):
            update_id = _receipt_key(key)
            if not isinstance(record, dict):
                raise TelegramStateError("Invalid legacy Telegram queued job")
            request = record.get("request")
            if not isinstance(request, dict):
                continue
            if not _is_pending(record):
                raise TelegramStateError("Invalid legacy Telegram queued job")
            normalized, digest = _canonical_request(request)
            if _update_id(normalized.get("update_id")) != update_id:
                raise TelegramStateError(
                    f"Telegram update {update_id} changed its saved identity"
                )
            stored_digest = record.get("request_digest")
            if stored_digest and stored_digest != digest:
                raise TelegramStateError(
                    f"Telegram update {update_id} does not match its saved request"
                )
            origin = _request_origin(normalized)
            if origin is None:
                raise TelegramStateError(
                    f"Telegram update {update_id} lost its receipt destination"
                )
            if any(name in record for name in _ORIGIN_FIELDS):
                if _saved_origin(record) != origin:
                    raise TelegramStateError(
                        f"Telegram update {update_id} changed its saved origin"
                    )
            record["request"] = normalized
            record["request_digest"] = digest
            _save_origin(record, origin)
            # Only pristine legacy work is safe to gate on a newly synthesized
            # receipt. A running/waiting/retrying or checkpointed v5 job may
            # already own external effects; grandfather it as receipt-sent so
            # revocation or a permanent send failure cannot discard recovery.
            receipt_phase = (
                "pending"
                if _queued_job_is_cancellable(
                    record,
                    retry=payload.setdefault("delivery_retries", {}).get(key),
                )
                else "sent"
            )
            receipts.setdefault(key, {
                "kind": "acceptance-receipt",
                "phase": receipt_phase,
                "request_digest": digest,
                "origin_user_id": origin[0],
                "origin_chat_id": origin[1],
                "origin_chat_type": origin[2],
                "failure_count": 0,
                "deferred_at": 0.0,
                "next_attempt_at": 0.0,
            })

    for key, value in receipts.items():
        update_id = _receipt_key(key)
        record = jobs.get(key)
        if not isinstance(record, dict) or not _is_pending(record):
            raise TelegramStateError(
                f"Telegram update {update_id} acceptance receipt lost its job"
            )
        normalized = _normalized_acceptance_receipt(value)
        _require_acceptance_receipt_binding(
            normalized,
            record,
            update_id,
            retry=payload.setdefault("delivery_retries", {}).get(key),
        )
        receipts[key] = normalized

    for key, record in jobs.items():
        update_id = _receipt_key(key)
        if isinstance(record, dict) and isinstance(record.get("request"), dict):
            if key not in receipts:
                raise TelegramStateError(
                    f"Telegram update {update_id} lost its acceptance receipt"
                )


def _delivery_retry_active(value: Any) -> TypeGuard[dict[str, Any]]:
    return (
        isinstance(value, dict)
        and value.get("kind") == "delivery-retry"
        and value.get("phase") in {"pending", "running", "retrying"}
    )


def _normalize_delivery_retry(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("kind") != "delivery-retry":
        raise TelegramStateError("Invalid Telegram delivery retry")
    root_delivery_id = str(value.get("root_delivery_id") or "")
    delivery_id = str(value.get("delivery_id") or "")
    task_id = str(value.get("task_id") or "")
    project_name = str(value.get("project_name") or "")
    chat_id = value.get("chat_id")
    attempt_started = value.get("attempt_started", False)
    post_expiry_reconciliation_started = value.get(
        "post_expiry_reconciliation_started",
        False,
    )
    attempt = _retry_integer(value.get("attempt"), "attempt")
    maximum = _retry_integer(value.get("max_attempts"), "max_attempts")
    if (
        not _DELIVERY_ID.fullmatch(root_delivery_id)
        or not _DELIVERY_ID.fullmatch(delivery_id)
        or not _TASK_ID.fullmatch(task_id)
        or project_name and not _PROJECT_ID.fullmatch(project_name)
        or isinstance(chat_id, bool)
        or not isinstance(chat_id, int)
        or chat_id <= 0
        or not isinstance(attempt_started, bool)
        or not isinstance(post_expiry_reconciliation_started, bool)
        or (post_expiry_reconciliation_started and not attempt_started)
        or not 2 <= attempt <= maximum <= _MAX_DELIVERY_RETRY_RUNS
        or delivery_id != _retry_delivery_id(root_delivery_id, attempt)
    ):
        raise TelegramStateError("Invalid Telegram delivery retry")
    return {
        "kind": "delivery-retry",
        "root_delivery_id": root_delivery_id,
        "delivery_id": delivery_id,
        "task_id": task_id.upper(),
        "project_name": project_name,
        "chat_id": chat_id,
        "attempt": attempt,
        "max_attempts": maximum,
        "attempt_started": attempt_started,
        "post_expiry_reconciliation_started": (
            post_expiry_reconciliation_started
        ),
        "failure_code": _normalized_failure_code(value.get("failure_code", "")),
    }


def _normalize_delivery_result(
    value: Any,
    *,
    require_terminal: bool,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TelegramStateError("Invalid Telegram delivery retry outcome")
    delivery_id = str(value.get("delivery_id") or "")
    status = str(value.get("status") or "").strip().lower()
    phase = str(value.get("phase") or status)
    failure_class = str(value.get("failure_class") or "")
    retry_after = value.get("retry_after_seconds", 0)
    commit_sha = str(value.get("commit_sha") or "")
    pr_url = str(value.get("pr_url") or "")
    success = value.get("success", False)
    pushed = value.get("pushed", False)
    publication_uncertain = value.get("publication_uncertain", False)
    if (
        not _DELIVERY_ID.fullmatch(delivery_id)
        or not status
        or len(status) > 80
        or any(not character.isprintable() for character in status)
        or (require_terminal and status not in _TERMINAL_DELIVERY_STATUSES)
        or not phase
        or len(phase) > 80
        or any(not character.isprintable() for character in phase)
        or bool(commit_sha and not _COMMIT_SHA.fullmatch(commit_sha))
        or bool(pr_url and not _GITHUB_PR_URL.fullmatch(pr_url))
        or not isinstance(success, bool)
        or not isinstance(pushed, bool)
        or not isinstance(publication_uncertain, bool)
        or failure_class not in {"", "transient", "permanent", "policy"}
        or isinstance(retry_after, bool)
        or not isinstance(retry_after, int)
        or not 0 <= retry_after <= 86_400
    ):
        raise TelegramStateError("Invalid Telegram delivery retry outcome")
    raw_files = value.get("changed_files", [])
    changed_files = (
        [str(item)[:200] for item in raw_files[:50] if isinstance(item, str)]
        if isinstance(raw_files, list)
        else []
    )
    return {
        "delivery_id": delivery_id,
        "success": success,
        "status": status,
        "phase": phase,
        "branch": str(value.get("branch") or "")[:160],
        "commit_sha": commit_sha.lower(),
        "pr_url": pr_url,
        "publication_uncertain": publication_uncertain,
        "pushed": pushed,
        "changed_files": changed_files,
        "attempts": max(0, min(10, int(value.get("attempts", 0) or 0))),
        "failure_code": _normalized_failure_code(value.get("failure_code", "")),
        "failure_class": failure_class,
        "retry_after_seconds": retry_after,
        # Provider, CLI, and GitHub diagnostics never enter this long-lived,
        # request-free notification checkpoint.
        "message": "",
    }


def _normalize_delivery_outcome(value: Any) -> dict[str, Any]:
    return _normalize_delivery_result(value, require_terminal=True)


def _migrate_legacy_delivery_results(payload: dict[str, Any]) -> None:
    """Canonicalize legacy result snapshots before v8 can route notices."""
    version = payload.get("version", 0)
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or not 0 <= version < 8
    ):
        return
    for bucket_name in ("jobs", "completed_jobs", "failed_jobs"):
        bucket = payload.setdefault(bucket_name, {})
        for record in bucket.values():
            if not isinstance(record, dict):
                raise TelegramStateError("Invalid legacy Telegram job")
            if "delivery_result" not in record:
                continue
            record["delivery_result"] = _normalize_delivery_result(
                record.get("delivery_result"),
                require_terminal=record.get("delivery_terminal") is True,
            )


def _require_retry_binding(
    existing: dict[str, Any],
    candidate: dict[str, Any],
    update_id: int,
) -> None:
    immutable = (
        "kind", "root_delivery_id", "delivery_id", "task_id", "project_name",
        "chat_id", "attempt", "max_attempts",
    )
    if any(existing.get(name) != candidate.get(name) for name in immutable):
        raise TelegramStateError(
            f"Telegram update {update_id} delivery retry changed identity"
        )


def _require_retry_source_binding(
    retry: dict[str, Any],
    source: dict[str, Any],
    update_id: int,
) -> None:
    """Bind background work to the exact accepted task and root delivery."""
    expected = {
        "root_delivery_id": str(source.get("delivery_id") or ""),
        "task_id": str(source.get("task_id") or "").upper(),
        "project_name": str(source.get("project_name") or ""),
        "chat_id": source.get("chat_id"),
    }
    if any(retry.get(name) != value for name, value in expected.items()):
        raise TelegramStateError(
            f"Telegram update {update_id} delivery retry changed source binding"
        )
    result = source.get("delivery_result")
    if (
        source.get("delivery_terminal") is not True
        or not isinstance(result, dict)
        or str(result.get("delivery_id") or "") != expected["root_delivery_id"]
        or str(result.get("status") or "").lower() != "failed"
        or str(result.get("failure_class") or "") != "transient"
        or bool(result.get("pushed"))
        or bool(result.get("commit_sha"))
        or bool(result.get("pr_url"))
        or bool(result.get("publication_uncertain"))
    ):
        raise TelegramStateError(
            f"Telegram update {update_id} delivery retry is not authorized"
        )


def _validate_loaded_delivery_retries(payload: dict[str, Any]) -> None:
    retries = payload.setdefault("delivery_retries", {})
    jobs = payload.setdefault("jobs", {})
    completed = payload.setdefault("completed_jobs", {})
    for key, value in list(retries.items()):
        try:
            update_id = _update_id(int(key))
        except (TypeError, ValueError) as exc:
            raise TelegramStateError("Invalid Telegram delivery retry key") from exc
        if key != str(update_id) or not _delivery_retry_active(value):
            raise TelegramStateError("Invalid Telegram delivery retry checkpoint")

        normalized = _normalize_delivery_retry(value)
        failure_count = value.get("failure_count")
        if (
            "attempt_started" not in value
            or not isinstance(value.get("attempt_started"), bool)
            or "post_expiry_reconciliation_started" not in value
            or not isinstance(
                value.get("post_expiry_reconciliation_started"),
                bool,
            )
            or isinstance(failure_count, bool)
            or not isinstance(failure_count, int)
            or failure_count < 0
        ):
            raise TelegramStateError("Invalid Telegram delivery retry checkpoint")
        created_at = _strict_timestamp(value.get("created_at"), allow_zero=True)
        expires_at = _strict_timestamp(value.get("expires_at"))
        next_attempt_at = _strict_timestamp(
            value.get("next_attempt_at"),
            allow_zero=True,
        )
        if (
            expires_at <= created_at
            or expires_at - created_at > _DELIVERY_RETRY_MAX_AGE_SECONDS + 1
            or next_attempt_at < created_at
        ):
            raise TelegramStateError("Invalid Telegram delivery retry window")

        source = jobs.get(key)
        if isinstance(source, dict):
            if not isinstance(source.get("request"), dict):
                raise TelegramStateError("Telegram delivery retry lost its source")
        else:
            source = completed.get(key)
            if not isinstance(source, dict) or "request" in source:
                raise TelegramStateError("Telegram delivery retry lost its source")
        _require_retry_source_binding(normalized, source, update_id)
        normalized.update({
            "phase": str(value.get("phase")),
            "failure_count": failure_count,
            "next_attempt_at": next_attempt_at,
            "created_at": created_at,
            "expires_at": expires_at,
        })
        if "outcome" in value or "outcome_phase" in value:
            normalized.update({
                "outcome": _normalize_delivery_outcome(value.get("outcome")),
                "outcome_phase": str(value.get("outcome_phase") or ""),
            })
            _staged_delivery_outcome(normalized)
        retries[key] = normalized


def _staged_delivery_outcome(
    retry: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    phase = str(retry.get("outcome_phase") or "")
    if phase not in {"completed", "failed", "expired", "uncertain"}:
        raise TelegramStateError("Invalid staged Telegram delivery outcome")
    outcome = _normalize_delivery_outcome(retry.get("outcome"))
    if outcome["delivery_id"] != retry.get("delivery_id"):
        raise TelegramStateError("Staged Telegram delivery outcome changed identity")
    success = outcome["success"]
    failure_code = outcome["failure_code"]
    if (
        (phase == "completed" and not success)
        or (phase in {"failed", "expired", "uncertain"} and success)
        or (phase == "expired" and failure_code != "retry-expired")
        or (phase != "expired" and failure_code == "retry-expired")
        or (
            phase == "uncertain"
            and (
                failure_code != "reconciliation-unresolved"
                or outcome["status"] != "publication-pending"
                or outcome["publication_uncertain"] is not True
                or bool(outcome["commit_sha"])
                or bool(outcome["pr_url"])
            )
        )
        or (phase != "uncertain" and failure_code == "reconciliation-unresolved")
    ):
        raise TelegramStateError("Staged Telegram delivery outcome changed meaning")
    return outcome, phase


def _uncertain_delivery_outcome(delivery_id: str) -> dict[str, Any]:
    """Return a neutral terminal scheduler outcome for unresolved publication."""
    return _normalize_delivery_outcome({
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
    })


def _delivery_retry_due(retry: dict[str, Any], current: float) -> float:
    next_attempt = _finite_nonnegative(retry.get("next_attempt_at"), current)
    if (
        isinstance(retry.get("outcome"), dict)
        or retry.get("attempt_started") is True
    ):
        return next_attempt
    return min(
        next_attempt,
        _finite_nonnegative(retry.get("expires_at"), current),
    )


def _retry_delivery_id(root_delivery_id: str, attempt: int) -> str:
    value = f"{root_delivery_id}-r{attempt}"
    if not _DELIVERY_ID.fullmatch(value):
        raise TelegramStateError("Invalid Telegram delivery retry identity")
    return value


def _retry_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TelegramStateError(f"Invalid Telegram delivery retry {label}")
    return value


def _normalized_failure_code(value: Any) -> str:
    code = str(value or "")
    if not code:
        return ""
    if len(code) > 80 or not _FAILURE_CODE.fullmatch(code):
        raise TelegramStateError("Invalid Telegram delivery retry failure code")
    return code


def _finite_nonnegative(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (OverflowError, TypeError, ValueError):
        return default
    if parsed < 0 or parsed == float("inf") or parsed != parsed:
        return default
    return parsed


def _strict_timestamp(value: Any, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool):
        raise TelegramStateError("Invalid Telegram delivery retry timestamp")
    try:
        parsed = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise TelegramStateError(
            "Invalid Telegram delivery retry timestamp"
        ) from exc
    if (
        parsed != parsed
        or parsed in {float("inf"), float("-inf")}
        or parsed < 0
        or (parsed == 0 and not allow_zero)
    ):
        raise TelegramStateError("Invalid Telegram delivery retry timestamp")
    return parsed


def _bounded_delay(value: Any, maximum: float) -> float:
    parsed = _finite_nonnegative(value, 1.0)
    return min(maximum, max(1.0, parsed))


def _cancellation_sequence(record: dict[str, Any]) -> int:
    value = record.get("cancellation_sequence", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _next_cancellation_sequence(data: dict[str, Any]) -> int:
    current = data.get("cancellation_sequence", 0)
    if isinstance(current, bool) or not isinstance(current, int) or current < 0:
        current = 0
    completed = data.setdefault("completed_jobs", {})
    latest = max(
        (
            _cancellation_sequence(record)
            for record in completed.values()
            if isinstance(record, dict) and record.get("phase") == "cancelled"
        ),
        default=0,
    )
    sequence = max(current, latest) + 1
    data["cancellation_sequence"] = sequence
    return sequence


def _private_origin(user_id: Any, chat_id: Any) -> tuple[int, int]:
    if (
        isinstance(user_id, bool)
        or not isinstance(user_id, int)
        or user_id <= 0
        or isinstance(chat_id, bool)
        or not isinstance(chat_id, int)
        or chat_id <= 0
    ):
        raise TelegramStateError(
            "Telegram private origins require positive integer user and chat ids"
        )
    return user_id, chat_id


def _request_origin(request: Any) -> tuple[int, int, str] | None:
    if not isinstance(request, dict):
        return None
    message = request.get("message")
    if not isinstance(message, dict):
        return None
    sender = message.get("from")
    chat = message.get("chat")
    if (
        not isinstance(sender, dict)
        or not isinstance(chat, dict)
    ):
        return None
    chat_type = chat.get("type")
    try:
        return _validated_origin(sender.get("id"), chat.get("id"), chat_type)
    except TelegramStateError:
        return None


def _validated_origin(
    user_id: Any,
    chat_id: Any,
    chat_type: Any,
) -> tuple[int, int, str]:
    if (
        isinstance(user_id, bool)
        or not isinstance(user_id, int)
        or user_id <= 0
        or isinstance(chat_id, bool)
        or not isinstance(chat_id, int)
        or not isinstance(chat_type, str)
        or chat_type not in {"private", "group", "supergroup"}
        or (chat_type == "private" and chat_id <= 0)
        or (chat_type == "private" and chat_id != user_id)
        or (chat_type in {"group", "supergroup"} and chat_id >= 0)
    ):
        raise TelegramStateError("Invalid Telegram request origin")
    return user_id, chat_id, chat_type


def _saved_origin(record: Any) -> tuple[int, int, str] | None:
    if not isinstance(record, dict):
        return None
    has_saved_origin = any(name in record for name in _ORIGIN_FIELDS)
    request_origin = _request_origin(record.get("request"))
    if not has_saved_origin:
        return request_origin
    if not _ORIGIN_FIELDS.issubset(record):
        return None
    try:
        saved = _validated_origin(
            record.get("origin_user_id"),
            record.get("origin_chat_id"),
            record.get("origin_chat_type"),
        )
    except TelegramStateError:
        return None
    if request_origin is not None and request_origin != saved:
        return None
    return saved


def _owned_by_private_control(
    record: Any,
    private_origin: tuple[int, int],
) -> bool:
    origin = _saved_origin(record)
    if origin is None or origin[0] != private_origin[0]:
        return False
    if origin[2] == "private":
        return origin[:2] == private_origin
    return origin[2] in {"group", "supergroup"}


def _save_origin(
    record: dict[str, Any],
    origin: tuple[int, int, str],
) -> None:
    record.update({
        "origin_user_id": origin[0],
        "origin_chat_id": origin[1],
        "origin_chat_type": origin[2],
    })


def _origin_tombstone(
    *,
    phase: str,
    digest: str,
    origin: tuple[int, int, str] | None,
) -> dict[str, Any]:
    if phase not in {"cancelled", "queue-full"}:
        raise TelegramStateError("Invalid Telegram inbox tombstone phase")
    record: dict[str, Any] = {
        "phase": phase,
        "request_digest": digest,
    }
    if origin is not None:
        _save_origin(record, origin)
    return record


def _queue_full_notice_pending(record: Any) -> bool:
    return (
        isinstance(record, dict)
        and record.get("phase") == "queue-full"
        and record.get("notice_status") in {"pending", "retrying"}
    )


def _queued_job_is_cancellable(record: Any, *, retry: Any) -> bool:
    if (
        not isinstance(record, dict)
        or record.get("phase") != "queued"
        or not isinstance(record.get("request"), dict)
        or not set(record).issubset(_PRISTINE_QUEUE_FIELDS)
        or _saved_origin(record) is None
        or retry is not None
    ):
        return False
    try:
        _normalized, digest = _canonical_request(record["request"])
    except TelegramStateError:
        return False
    stored_digest = str(record.get("request_digest") or "")
    return not stored_digest or stored_digest == digest


def _safe_job_phase(value: Any) -> str:
    phase = str(value or "")
    return phase if phase in {"queued", "running", "waiting", "retrying"} else "unknown"


def _validated_saved_request(
    update_id: int,
    record: dict[str, Any],
) -> dict[str, Any]:
    """Revalidate persisted request bytes immediately before execution."""
    request = record.get("request")
    if not isinstance(request, dict):
        raise TelegramStateError(
            f"Telegram update {update_id} has no valid saved request"
        )
    normalized, digest = _canonical_request(request)
    if _update_id(normalized.get("update_id")) != update_id:
        raise TelegramStateError(
            f"Telegram update {update_id} changed its saved identity"
        )
    stored_digest = record.get("request_digest")
    if (
        not isinstance(stored_digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", stored_digest)
        or stored_digest != digest
    ):
        raise TelegramStateError(
            f"Telegram update {update_id} does not match its saved request"
        )

    message = normalized.get("message")
    if not isinstance(message, dict):
        raise TelegramStateError(
            f"Telegram update {update_id} has an invalid saved message"
        )
    sender = message.get("from")
    chat = message.get("chat")
    valid_text = (
        isinstance(message.get("text"), str)
        and bool(str(message.get("text")).strip())
        and "voice" not in message
    )
    valid_voice = (
        "text" not in message
        and message.get("voice") == {"queued": True}
    )
    if (
        not isinstance(sender, dict)
        or sender.get("is_bot") is not False
        or not isinstance(chat, dict)
        or not (valid_text or valid_voice)
        or _request_origin(normalized) is None
    ):
        raise TelegramStateError(
            f"Telegram update {update_id} has an invalid saved message"
        )
    if any(name in record for name in _ORIGIN_FIELDS):
        saved_origin = _saved_origin(record)
        if saved_origin is None or saved_origin != _request_origin(normalized):
            raise TelegramStateError(
                f"Telegram update {update_id} changed its saved origin"
            )
    return normalized


def _canonical_request(request: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Return a detached JSON request and its stable content digest."""
    if not isinstance(request, dict):
        raise TelegramStateError("Telegram queue requests must be JSON objects")
    try:
        canonical = json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        normalized = json.loads(canonical)
    except (TypeError, ValueError) as exc:
        raise TelegramStateError(
            "Telegram queue requests must contain only JSON values"
        ) from exc
    if not isinstance(normalized, dict):  # pragma: no cover - guarded above
        raise TelegramStateError("Telegram queue requests must be JSON objects")
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return normalized, digest


def _is_pending(value: Any) -> bool:
    return isinstance(value, dict) and value.get("phase") in {
        "queued", "running", "waiting", "retrying",
    }


def _update_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TelegramStateError("Telegram update ids must be non-negative integers")
    return value


def _verify_digest(record: dict[str, Any], digest: str, update_id: int) -> None:
    prior = str(record.get("request_digest") or "")
    if prior and prior != digest:
        raise TelegramStateError(
            f"Telegram update {update_id} does not match its saved request"
        )


def _positive_id(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("Telegram bot identity must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Telegram bot identity must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError("Telegram bot identity must be a positive integer")
    return parsed


def _has_update_state(data: dict[str, Any]) -> bool:
    try:
        if int(data.get("last_update_id", -1)) >= 0:
            return True
    except (TypeError, ValueError):
        return True
    try:
        if float(data.get("delivery_notice_pause_until", 0.0)) > 0:
            return True
    except (TypeError, ValueError):
        return True
    return any(bool(data.get(name)) for name in (
        "jobs", "completed_jobs", "failed_jobs", "delivery_retries",
        "acceptance_receipts", "delivery_notices",
    ))


class InstanceLock:
    """Fail fast when another MondayOS Telegram worker already owns the state."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._file: TextIO | None = None

    def __enter__(self) -> InstanceLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._file.close()
            self._file = None
            raise TelegramStateError("Another MondayOS Telegram worker is already running") from exc
        try:
            # The lock is shared by daemon, one-shot, and identify modes. Clear
            # any prior ready lease before the new owner performs network or
            # startup work so health never mistakes a utility process for the
            # daemon that previously crashed.
            timestamp = _worker_timestamp()
            write_json_atomic(
                self.path.with_name("heartbeat.json"),
                _stopped_runtime_heartbeat(
                    _new_runtime_heartbeat(timestamp),
                    timestamp,
                ),
            )
        except BaseException:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            self._file.close()
            self._file = None
            raise
        return self

    def __exit__(self, *_args: Any) -> None:
        if self._file is not None:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            self._file.close()
            self._file = None


def instance_lock_is_held(path: Path) -> bool:
    """Return whether an existing worker owns the lock without creating files."""
    lock_path = Path(path)
    try:
        lock_file = lock_path.open("r", encoding="utf-8")
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise TelegramStateError(f"Cannot read Telegram lock at {lock_path}") from exc
    try:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        return False
    except OSError as exc:
        raise TelegramStateError(f"Cannot inspect Telegram lock at {lock_path}") from exc
    finally:
        lock_file.close()


def _worker_timestamp(value: float | None = None) -> float:
    timestamp = time.time() if value is None else value
    if (
        isinstance(timestamp, bool)
        or not isinstance(timestamp, (int, float))
    ):
        raise ValueError("worker timestamp must be a non-negative finite number")
    try:
        normalized = float(timestamp)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            "worker timestamp must be a non-negative finite number"
        ) from exc
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError("worker timestamp must be a non-negative finite number")
    return normalized


def _optional_worker_timestamp(value: Any) -> float | None:
    if value is None:
        return None
    return _worker_timestamp(value)


def _new_runtime_heartbeat(timestamp: float) -> dict[str, Any]:
    return {
        "version": _RUNTIME_HEARTBEAT_VERSION,
        "started_at": timestamp,
        "stopped_at": None,
        "supervisor": {"pulse_at": timestamp},
        "lanes": {
            lane: _runtime_lane_checkpoint(
                lane,
                "starting",
                "starting",
                timestamp,
                timeout_seconds=0.0,
                resume_after_seconds=0.0,
            )
            for lane in ("intake", "worker")
        },
    }


def _stopped_runtime_heartbeat(
    worker: dict[str, Any],
    timestamp: float,
) -> dict[str, Any]:
    worker["stopped_at"] = timestamp
    for lane in ("intake", "worker"):
        worker["lanes"][lane] = _runtime_lane_checkpoint(
            lane,
            "stopped",
            "stopped",
            timestamp,
            timeout_seconds=0.0,
            resume_after_seconds=0.0,
        )
    return worker


def _runtime_duration(value: Any, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise ValueError(f"{label} must be a non-negative finite number")
    return float(value)


def _runtime_lane_checkpoint(
    lane: str,
    state: str,
    operation: str,
    timestamp: float,
    *,
    timeout_seconds: Any,
    resume_after_seconds: Any,
) -> dict[str, Any]:
    if lane not in _RUNTIME_ACTIVE_LEASES:
        raise ValueError("runtime lane must be intake or worker")
    if state not in _RUNTIME_LANE_STATES:
        raise ValueError("invalid runtime lane state")
    timeout = _runtime_duration(timeout_seconds, "timeout_seconds")
    resume_after = _runtime_duration(
        resume_after_seconds,
        "resume_after_seconds",
    )

    deadline_at: float | None = None
    resume_at: float | None = None
    if state == "active":
        maximum = _RUNTIME_ACTIVE_LEASES[lane].get(operation)
        if maximum is None:
            raise ValueError("invalid active runtime lane operation")
        if timeout <= 0 or timeout > maximum:
            raise ValueError(
                f"timeout_seconds must be between 0 and {maximum:g}"
            )
        if resume_after != 0:
            raise ValueError("active runtime lanes cannot set resume_after_seconds")
        deadline_at = timestamp + timeout
    elif state == "backoff":
        if operation not in _RUNTIME_BACKOFF_OPERATIONS:
            raise ValueError("invalid backoff runtime lane operation")
        if timeout != 0:
            raise ValueError("backoff runtime lanes cannot set timeout_seconds")
        if not 0 < resume_after <= _MAX_RUNTIME_BACKOFF_SECONDS:
            raise ValueError(
                "resume_after_seconds must be between 0 and 86400"
            )
        resume_at = timestamp + resume_after
    else:
        expected_operation = state
        if state == "idle" and lane != "worker":
            raise ValueError("only the worker runtime lane can be idle")
        if operation != expected_operation:
            raise ValueError(f"runtime {state} state requires {state} operation")
        if timeout != 0 or resume_after != 0:
            raise ValueError(
                f"runtime {state} state cannot set a timeout or resume delay"
            )

    return {
        "state": state,
        "operation": operation,
        "progress_at": timestamp,
        "deadline_at": deadline_at,
        "resume_at": resume_at,
    }


def _normalized_runtime_lane(
    value: Any,
    lane: str,
    *,
    started_at: float,
    stopped_at: float | None,
    now: float | None,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _RUNTIME_LANE_FIELDS:
        raise TelegramStateError("Invalid Telegram runtime lane")
    state = value.get("state")
    operation = value.get("operation")
    if not isinstance(state, str) or not isinstance(operation, str):
        raise TelegramStateError("Invalid Telegram runtime lane")
    try:
        progress_at = _worker_timestamp(value.get("progress_at"))
        deadline_at = _optional_worker_timestamp(value.get("deadline_at"))
        resume_at = _optional_worker_timestamp(value.get("resume_at"))
    except ValueError as exc:
        raise TelegramStateError("Invalid Telegram runtime lane timestamp") from exc
    if progress_at < started_at or (now is not None and progress_at > now):
        raise TelegramStateError("Invalid Telegram runtime lane progress")
    if stopped_at is not None and progress_at > stopped_at:
        raise TelegramStateError("Invalid Telegram runtime lane stop order")

    if state == "active":
        maximum = _RUNTIME_ACTIVE_LEASES[lane].get(operation)
        if (
            maximum is None
            or deadline_at is None
            or resume_at is not None
            or deadline_at <= progress_at
            or deadline_at - progress_at > maximum
        ):
            raise TelegramStateError("Invalid Telegram runtime active lease")
    elif state == "backoff":
        if (
            operation not in _RUNTIME_BACKOFF_OPERATIONS
            or deadline_at is not None
            or resume_at is None
            or resume_at <= progress_at
            or resume_at - progress_at > _MAX_RUNTIME_BACKOFF_SECONDS
        ):
            raise TelegramStateError("Invalid Telegram runtime backoff")
    else:
        if (
            state not in _RUNTIME_LANE_STATES
            or operation != state
            or deadline_at is not None
            or resume_at is not None
            or (state == "idle" and lane != "worker")
        ):
            raise TelegramStateError("Invalid Telegram runtime lane state")

    return {
        "state": state,
        "operation": operation,
        "progress_at": progress_at,
        "deadline_at": deadline_at,
        "resume_at": resume_at,
    }


def _normalized_runtime_heartbeat(
    value: Any,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _RUNTIME_HEARTBEAT_FIELDS:
        raise TelegramStateError("Invalid Telegram runtime heartbeat")
    version = value.get("version")
    supervisor = value.get("supervisor")
    lanes = value.get("lanes")
    if (
        isinstance(version, bool)
        or version != _RUNTIME_HEARTBEAT_VERSION
        or not isinstance(supervisor, dict)
        or set(supervisor) != _RUNTIME_SUPERVISOR_FIELDS
        or not isinstance(lanes, dict)
        or set(lanes) != {"intake", "worker"}
    ):
        raise TelegramStateError("Invalid Telegram runtime heartbeat")
    try:
        started_at = _worker_timestamp(value.get("started_at"))
        stopped_at = _optional_worker_timestamp(value.get("stopped_at"))
        pulse_at = _worker_timestamp(supervisor.get("pulse_at"))
    except ValueError as exc:
        raise TelegramStateError(
            "Invalid Telegram runtime heartbeat timestamp"
        ) from exc
    if (
        pulse_at < started_at
        or (stopped_at is not None and stopped_at < pulse_at)
        or (now is not None and (
            started_at > now
            or pulse_at > now
            or (stopped_at is not None and stopped_at > now)
        ))
    ):
        raise TelegramStateError("Invalid Telegram runtime heartbeat order")

    normalized_lanes = {
        lane: _normalized_runtime_lane(
            lanes[lane],
            lane,
            started_at=started_at,
            stopped_at=stopped_at,
            now=now,
        )
        for lane in ("intake", "worker")
    }
    if stopped_at is not None and any(
        checkpoint["state"] != "stopped"
        or checkpoint["progress_at"] != stopped_at
        for checkpoint in normalized_lanes.values()
    ):
        raise TelegramStateError("Invalid stopped Telegram runtime heartbeat")
    return {
        "version": _RUNTIME_HEARTBEAT_VERSION,
        "started_at": started_at,
        "stopped_at": stopped_at,
        "supervisor": {"pulse_at": pulse_at},
        "lanes": normalized_lanes,
    }


def _runtime_lane_summary(
    checkpoint: dict[str, Any],
    *,
    now: float,
    supervisor_fresh: bool,
) -> dict[str, Any]:
    state = checkpoint["state"]
    ready = bool(
        supervisor_fresh
        and (
            (state == "active" and now <= checkpoint["deadline_at"])
            or (state == "idle" and checkpoint["operation"] == "idle")
        )
    )
    return {**copy.deepcopy(checkpoint), "ready": ready}


def _missing_runtime_status() -> dict[str, Any]:
    missing_lane = {
        "state": None,
        "operation": None,
        "progress_at": None,
        "deadline_at": None,
        "resume_at": None,
        "ready": False,
    }
    return {
        "started_at": None,
        "heartbeat_at": None,
        "stopped_at": None,
        "heartbeat_age_seconds": None,
        "heartbeat_fresh": False,
        "runtime_ready": False,
        "intake": copy.deepcopy(missing_lane),
        "worker": copy.deepcopy(missing_lane),
    }
