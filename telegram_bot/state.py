"""Durable Telegram offset and job state."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Any, TextIO, TypeGuard

from core.atomic import write_json_atomic

_MAX_OBSERVATION_RETRY_SECONDS = 300.0
_MAX_TELEGRAM_RATE_LIMIT_SECONDS = 86_400.0
_MAX_ACTIVE_FOLLOW_UPS = 500
_MAX_ACTIVE_DELIVERY_RETRIES = 500
_MAX_COMPLETED_JOBS = 1_000
_FOLLOW_UP_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
_DELIVERY_RETRY_MAX_AGE_SECONDS = 24 * 60 * 60
_MAX_DELIVERY_RETRY_RUNS = 6
_MAX_DELIVERY_RETRY_NOTIFICATION_FAILURES = 3
_DELIVERY_ID = re.compile(r"^delivery-[A-Za-z0-9-]{1,80}$")
_TASK_ID = re.compile(r"^TASK-\d{1,64}(?:-[a-z0-9]{1,12})?$", re.IGNORECASE)
_PROJECT_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FAILURE_CODE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{7,64}$")
_GITHUB_PR_URL = re.compile(r"^https://github\.com/[^\s/]+/[^\s/]+/pull/\d+$")
_TERMINAL_DELIVERY_STATUSES = frozenset({
    "blocked", "cancelled", "completed", "failed", "interrupted", "pr-open",
    "publication-pending", "rejected", "succeeded",
})


class TelegramStateError(RuntimeError):
    pass


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
                    "jobs": {},
                    "completed_jobs": {},
                    "failed_jobs": {},
                    "delivery_retries": {},
                })

            needs_write = (
                changed
                or legacy_with_state
                or current_id != normalized_id
                or self._data.get("bot_username") != normalized_username
                or self._data.get("version") != 5
            )
            self._data.update({
                "version": 5,
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

    def enqueue(self, update_id: int, request: dict[str, Any]) -> tuple[bool, int]:
        """Durably enqueue one Telegram request and advance its polling offset.

        The returned boolean reports whether a new queue entry was created. The
        integer is that entry's current FIFO position, or zero for a terminal
        replay. Telegram update ids are bot-scoped and immutable, so seeing the
        same id with different content is treated as corrupt input.
        """
        normalized_id = _update_id(update_id)
        normalized_request, digest = _canonical_request(request)
        request_id = _update_id(normalized_request.get("update_id"))
        if request_id != normalized_id:
            raise TelegramStateError(
                "Telegram queue key does not match the saved request update id"
            )
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
                    existing["request"] = normalized_request
                    existing["request_digest"] = digest
                    existing.setdefault("phase", "queued")
                    created = True
                self._advance(normalized_id)
                self._write()
                return created, self._queue_position(normalized_id)

            jobs[key] = {
                "phase": "queued",
                "request": normalized_request,
                "request_digest": digest,
            }
            self._advance(normalized_id)
            self._write()
            return True, self._queue_position(normalized_id)

    def claim_next(self) -> tuple[int, dict[str, Any]] | None:
        """Claim the oldest resumable request for the single build worker."""
        with self._lock:
            jobs = self._data.setdefault("jobs", {})
            candidates = [
                int(key)
                for key, value in jobs.items()
                if _is_pending(value) and isinstance(value.get("request"), dict)
            ]
            if not candidates:
                return None
            update_id = min(candidates)
            record = jobs[str(update_id)]
            record["phase"] = "running"
            self._write()
            return update_id, copy.deepcopy(record["request"])

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
        now: float | None = None,
    ) -> dict[str, Any]:
        """Reserve one request-free retry before acknowledging its source update.

        The retry contains only immutable task/delivery routing data. The
        original Telegram request body remains in the active job until normal
        acknowledgement removes it, and is never copied into this scheduler.
        """
        normalized_id = _update_id(update_id)
        current = time.time() if now is None else _finite_nonnegative(now, time.time())
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
            "expires_at": current + _DELIVERY_RETRY_MAX_AGE_SECONDS,
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
        """Advance a claimed transient failure to its next immutable identity."""
        normalized_id = _update_id(update_id)
        current = time.time() if now is None else _finite_nonnegative(now, time.time())
        with self._lock:
            retry = self._delivery_retry_record(normalized_id)
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
            retry.update({
                "phase": "pending",
                "delivery_id": delivery_id,
                "attempt": attempt,
                "attempt_started": False,
                "post_expiry_reconciliation_started": False,
                "failure_code": _normalized_failure_code(failure_code),
                "failure_count": 0,
                "next_attempt_at": due_at,
            })
            self._write()
            return copy.deepcopy(retry)

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
            protected=set(self._data.setdefault("delivery_retries", {})),
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
            if expired:
                self._write()
            if not due_times:
                return None
            return min(300.0, max(0.0, min(due_times) - current))

    def is_terminal(self, update_id: int) -> bool:
        with self._lock:
            key = str(update_id)
            return self._terminal_record(key) is not None

    def reaffirm_terminal(self, update_id: int) -> None:
        """Advance the offset for a replay without replacing its tombstone."""
        with self._lock:
            self._advance(update_id)
            self._write()

    def acknowledge(self, update_id: int) -> None:
        with self._lock:
            key = str(update_id)
            jobs = self._data.setdefault("jobs", {})
            record = copy.deepcopy(jobs.pop(key, {}))
            record.pop("request", None)
            record["phase"] = "completed"
            completed = self._data.setdefault("completed_jobs", {})
            completed[key] = record
            self._advance(update_id)
            _bound_completed(
                completed,
                _MAX_COMPLETED_JOBS,
                protected=set(self._data.setdefault("delivery_retries", {})),
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
                    "version": 5,
                    "bot_id": None,
                    "bot_username": "",
                    "last_update_id": -1,
                    "jobs": {},
                    "completed_jobs": {},
                    "failed_jobs": {},
                    "delivery_retries": {},
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
                _validate_loaded_delivery_retries(payload)
            except (TelegramStateError, TypeError, ValueError) as exc:
                raise TelegramStateError(
                    f"Invalid Telegram state at {self.path}"
                ) from exc
            return payload

    def _write(self) -> None:
        with self._lock:
            self._data["version"] = 5
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
    """Bound replay tombstones without discarding an active PR observer."""
    if len(records) <= limit:
        return
    removable = [
        key for key in sorted(records, key=int)
        if key not in (protected or set()) and not _follow_up_active(
            records[key].get("follow_up") if isinstance(records[key], dict) else None
        )
    ]
    for key in removable[: max(0, len(records) - limit)]:
        records.pop(key, None)


def _follow_up_active(value: Any) -> TypeGuard[dict[str, Any]]:
    return (
        isinstance(value, dict)
        and value.get("kind") == "pr-lifecycle"
        and value.get("phase") in {"pending", "polling", "retrying"}
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


def _normalize_delivery_outcome(value: Any) -> dict[str, Any]:
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
        or status not in _TERMINAL_DELIVERY_STATUSES
        or not phase
        or len(phase) > 80
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
    return any(bool(data.get(name)) for name in (
        "jobs", "completed_jobs", "failed_jobs", "delivery_retries",
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
        return self

    def __exit__(self, *_args: Any) -> None:
        if self._file is not None:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            self._file.close()
            self._file = None
