"""Durable Telegram offset and job state."""
from __future__ import annotations

import fcntl
import json
from pathlib import Path
from typing import Any, TextIO

from core.atomic import write_json_atomic


class TelegramStateError(RuntimeError):
    pass


class TelegramState:
    """Atomic state that prevents acknowledged updates from being replayed."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
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
            or self._data.get("version") != 2
        )
        self._data.update({
            "version": 2,
            "bot_id": normalized_id,
            "bot_username": normalized_username,
        })
        if needs_write:
            self._write()
        return changed or legacy_with_state

    @property
    def next_offset(self) -> int | None:
        value = int(self._data.get("last_update_id", -1))
        return value + 1 if value >= 0 else None

    def job(self, update_id: int) -> dict[str, Any]:
        jobs = self._data.setdefault("jobs", {})
        value = jobs.get(str(update_id), {})
        return dict(value) if isinstance(value, dict) else {}

    def set_job(self, update_id: int, **fields: Any) -> None:
        jobs = self._data.setdefault("jobs", {})
        record = jobs.setdefault(str(update_id), {})
        record.update(fields)
        self._write()

    def record_failure(self, update_id: int) -> int:
        """Persist and return the number of handler attempts for an update."""
        jobs = self._data.setdefault("jobs", {})
        record = jobs.setdefault(str(update_id), {})
        attempts = int(record.get("attempt_count", 0)) + 1
        record.update({"attempt_count": attempts, "phase": "retrying"})
        self._write()
        return attempts

    def dead_letter(self, update_id: int) -> None:
        """Advance past a poison update while retaining its safe checkpoint."""
        key = str(update_id)
        jobs = self._data.setdefault("jobs", {})
        record = dict(jobs.pop(key, {}))
        record["phase"] = "dead-letter"
        failed = self._data.setdefault("failed_jobs", {})
        failed[key] = record
        self._advance(update_id)
        _bound(failed, 500)
        self._write()

    def failed_job(self, update_id: int) -> dict[str, Any]:
        value = self._data.setdefault("failed_jobs", {}).get(str(update_id), {})
        return dict(value) if isinstance(value, dict) else {}

    def completed_job(self, update_id: int) -> dict[str, Any]:
        value = self._data.setdefault("completed_jobs", {}).get(str(update_id), {})
        return dict(value) if isinstance(value, dict) else {}

    def is_terminal(self, update_id: int) -> bool:
        key = str(update_id)
        return (
            key in self._data.setdefault("completed_jobs", {})
            or key in self._data.setdefault("failed_jobs", {})
        )

    def reaffirm_terminal(self, update_id: int) -> None:
        """Advance the offset for a replay without replacing its tombstone."""
        self._advance(update_id)
        self._write()

    def acknowledge(self, update_id: int) -> None:
        key = str(update_id)
        jobs = self._data.setdefault("jobs", {})
        record = dict(jobs.pop(key, {}))
        record["phase"] = "completed"
        completed = self._data.setdefault("completed_jobs", {})
        completed[key] = record
        self._advance(update_id)
        _bound(completed, 500)
        self._write()

    def _advance(self, update_id: int) -> None:
        self._data["last_update_id"] = max(
            int(self._data.get("last_update_id", -1)), update_id,
        )

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "version": 2,
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
            raise TelegramStateError(f"Cannot read Telegram state at {self.path}") from exc
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
        write_json_atomic(self.path, self._data)


def _bound(records: dict[str, Any], limit: int) -> None:
    if len(records) <= limit:
        return
    for key in sorted(records, key=int)[:-limit]:
        records.pop(key, None)


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
