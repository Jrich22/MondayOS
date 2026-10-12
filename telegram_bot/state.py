"""Durable Telegram offset and job state."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import threading
from pathlib import Path
from typing import Any, TextIO

from core.atomic import write_json_atomic


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
                })

            needs_write = (
                changed
                or legacy_with_state
                or current_id != normalized_id
                or self._data.get("bot_username") != normalized_username
                or self._data.get("version") != 3
            )
            self._data.update({
                "version": 3,
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
            _bound(completed, 500)
            self._write()

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
                    "version": 3,
                    "bot_id": None,
                    "bot_username": "",
                    "last_update_id": -1,
                    "jobs": {},
                    "completed_jobs": {},
                    "failed_jobs": {},
                }
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise TelegramStateError(
                    f"Cannot read Telegram state at {self.path}"
                ) from exc
            if not isinstance(payload, dict) or any(
                not isinstance(payload.get(name, {}), dict)
                for name in ("jobs", "completed_jobs", "failed_jobs")
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
            return payload

    def _write(self) -> None:
        with self._lock:
            self._data["version"] = 3
            write_json_atomic(self.path, self._data)


def _bound(records: dict[str, Any], limit: int) -> None:
    if len(records) <= limit:
        return
    for key in sorted(records, key=int)[:-limit]:
        records.pop(key, None)


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
        "jobs", "completed_jobs", "failed_jobs",
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
