"""Bounded liveness recovery for the launchd-owned Telegram daemon.

The ordinary Telegram health report includes readiness concerns such as queue
pressure, pending notices, and provider availability. Restarting cannot repair
those conditions. This watchdog reads only the daemon lock and strict runtime
heartbeat, requires matching samples, and targets one fixed launchd service.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.secure_io import (
    FileIdentity,
    open_directory_beneath,
    open_private_lock_file_at,
    read_text_with_identity_at,
    validate_private_lock_file,
    write_json_atomic_at,
)
from delivery.git import run_command
from telegram_bot.state import (
    TelegramStateError,
    instance_lock_is_held_at,
    read_runtime_status_at,
)

_SERVICE_LABEL = "com.mondayos.telegram"
_RUNTIME_DIRECTORY = Path("logs") / "telegram"
_TRACKER_NAME = "watchdog.json"
_TRACKER_LOCK_NAME = ".watchdog.lock"
_STATE_VERSION = 1
_MAX_STATE_BYTES = 8_192
_REQUIRED_SAMPLES = 2
_MIN_SAMPLE_SPACING_SECONDS = 45.0
_MAX_SAMPLE_SPACING_SECONDS = 150.0
_STARTUP_GRACE_SECONDS = 180.0
_BACKOFF_GRACE_SECONDS = 120.0
_MIN_RESTART_INTERVAL_SECONDS = 300.0
_RESTART_WINDOW_SECONDS = 3_600.0
_MAX_RESTARTS_PER_WINDOW = 3
_LAUNCHCTL_TIMEOUT_SECONDS = 15.0
_RESTARTABLE_REASONS = frozenset(
    {
        "daemon-stopped",
        "intake-backoff-stuck",
        "intake-overdue",
        "intake-starting-stuck",
        "intake-stopped",
        "supervisor-stale",
        "worker-backoff-stuck",
        "worker-overdue",
        "worker-starting-stuck",
        "worker-stopped",
    }
)
_ACTIONS = frozenset(
    {
        "healthy",
        "sampled",
        "restart-requested",
        "restart-failed",
        "restart-pending",
        "restart-throttled",
        "restart-budget-exhausted",
        "restart-verified",
        "service-not-loaded",
        "state-unreadable",
    }
)


@dataclass(frozen=True)
class WatchdogObservation:
    """One secret-free observation of the daemon's process liveness."""

    service_loaded: bool
    lock_held: bool
    generation: float | None
    reason: str
    observed_at: float
    evidence: str = ""

    @property
    def restartable(self) -> bool:
        return self.reason in _RESTARTABLE_REASONS

    @property
    def fingerprint(self) -> str:
        if self.generation is None:
            raise ValueError("restartable observations require an exact generation")
        if len(self.evidence) != 64 or any(
            character not in "0123456789abcdef" for character in self.evidence
        ):
            raise ValueError("restartable observations require bounded evidence")
        return f"{self.generation.hex()}:{self.reason}:{self.evidence}"


@dataclass
class _TrackerLease:
    directory_fd: int
    identity: FileIdentity | None
    tracker: dict[str, Any]


CommandRunner = Callable[[tuple[str, ...]], int]


class TelegramWatchdog:
    """Sample, debounce, and restart one fixed Telegram launchd service."""

    def __init__(
        self,
        *,
        project_root: Path,
        command_runner: CommandRunner | None = None,
        uid: int | None = None,
    ) -> None:
        try:
            root = Path(project_root).resolve(strict=True)
        except OSError as exc:
            raise ValueError("watchdog project root must exist") from exc
        if not root.is_dir():
            raise ValueError("watchdog project root must be a directory")
        resolved_uid = os.getuid() if uid is None else uid
        if (
            isinstance(resolved_uid, bool)
            or not isinstance(resolved_uid, int)
            or resolved_uid < 0
        ):
            raise ValueError("watchdog uid must be a non-negative integer")
        self.project_root = root
        self.uid = resolved_uid
        self.service_target = f"gui/{self.uid}/{_SERVICE_LABEL}"
        self.command_runner = command_runner or self._run_command

    def run(self, *, now: float | None = None) -> dict[str, object]:
        """Run one bounded watchdog sample and optional fixed-label restart."""
        fixed_now = None if now is None else _timestamp(now)
        with _locked_tracker(self.project_root) as lease:
            tracker = lease.tracker
            observation = self._observe(lease.directory_fd, now=fixed_now)
            timestamp = observation.observed_at
            _rebase_tracker_clock(tracker, timestamp)
            tracker["restart_times"] = _recent_restarts(tracker, timestamp)

            pending = self._reconcile_pending(tracker, observation, timestamp)
            if pending is not None:
                _write_tracker(lease)
                return pending
            if not observation.service_loaded:
                _reset_samples(tracker)
                _write_tracker(lease)
                return _report(
                    observation,
                    action="service-not-loaded",
                    tracker=tracker,
                    liveness_ready=False,
                )
            if not observation.restartable:
                _reset_samples(tracker)
                _write_tracker(lease)
                acceptable = _observation_acceptable(observation)
                return _report(
                    observation,
                    action="healthy" if acceptable else "state-unreadable",
                    tracker=tracker,
                    liveness_ready=acceptable,
                )

            _record_sample(tracker, observation, timestamp)
            if not _debounce_complete(tracker, timestamp):
                _write_tracker(lease)
                return _report(
                    observation,
                    action="sampled",
                    tracker=tracker,
                    liveness_ready=False,
                )

            restart_times = list(tracker["restart_times"])
            last_restart_at = _optional_timestamp(tracker.get("last_restart_at"))
            if (
                last_restart_at is not None
                and timestamp - last_restart_at < _MIN_RESTART_INTERVAL_SECONDS
            ):
                _write_tracker(lease)
                return _report(
                    observation,
                    action="restart-throttled",
                    tracker=tracker,
                    liveness_ready=False,
                )
            if len(restart_times) >= _MAX_RESTARTS_PER_WINDOW:
                _write_tracker(lease)
                return _report(
                    observation,
                    action="restart-budget-exhausted",
                    tracker=tracker,
                    liveness_ready=False,
                )

            # Re-read after a second launchd lookup immediately before the
            # irreversible request. A recovered or replaced daemon cancels it.
            confirmed = self._observe(lease.directory_fd, now=fixed_now)
            confirmed_at = confirmed.observed_at
            if (
                not confirmed.restartable
                or confirmed.fingerprint != observation.fingerprint
                or not confirmed.service_loaded
            ):
                _reset_samples(tracker)
                if confirmed.restartable:
                    _record_sample(tracker, confirmed, confirmed.observed_at)
                _write_tracker(lease)
                acceptable = _observation_acceptable(confirmed)
                if not confirmed.service_loaded:
                    action = "service-not-loaded"
                elif acceptable:
                    action = "healthy"
                elif not confirmed.restartable:
                    action = "state-unreadable"
                else:
                    action = "sampled"
                return _report(
                    confirmed,
                    action=action,
                    tracker=tracker,
                    liveness_ready=acceptable,
                )

            # Persist intent before the fixed-label launchd request. A crash
            # then conservatively suppresses duplicates until a new
            # started_at verifies recovery.
            tracker["pending_fingerprint"] = confirmed.fingerprint
            tracker["pending_generation"] = confirmed.generation
            tracker["pending_at"] = confirmed_at
            tracker["last_restart_at"] = confirmed_at
            tracker["restart_times"] = [*restart_times, confirmed_at]
            _reset_samples(tracker)
            previous_last_restart_at = last_restart_at
            _write_tracker(lease)
            final = self._observe(lease.directory_fd, now=fixed_now)
            if (
                not final.restartable
                or final.fingerprint != confirmed.fingerprint
                or not final.service_loaded
            ):
                _clear_pending(tracker)
                tracker["last_restart_at"] = previous_last_restart_at
                tracker["restart_times"] = restart_times
                _reset_samples(tracker)
                if final.restartable:
                    _record_sample(tracker, final, final.observed_at)
                _write_tracker(lease)
                acceptable = _observation_acceptable(final)
                if not final.service_loaded:
                    action = "service-not-loaded"
                elif acceptable:
                    action = "healthy"
                elif final.restartable:
                    action = "sampled"
                else:
                    action = "state-unreadable"
                return _report(
                    final,
                    action=action,
                    tracker=tracker,
                    liveness_ready=acceptable,
                )
            restarted = self._restart_service()
            return _report(
                final,
                action="restart-requested" if restarted else "restart-failed",
                tracker=tracker,
                liveness_ready=False,
            )

    def _observe(
        self,
        directory_fd: int,
        *,
        now: float | None,
    ) -> WatchdogObservation:
        loaded = self._service_loaded()
        if not loaded:
            observed_at = _timestamp(now)
            return WatchdogObservation(False, False, None, "", observed_at)
        try:
            lock_held = instance_lock_is_held_at(directory_fd)
        except TelegramStateError:
            observed_at = _timestamp(now)
            return WatchdogObservation(
                True, False, None, "state-unreadable", observed_at
            )
        if not lock_held:
            observed_at = _timestamp(now)
            return WatchdogObservation(True, False, None, "lock-missing", observed_at)
        try:
            # The helper reads first and samples the wall clock afterward, so
            # a heartbeat atomically replaced during inspection never appears
            # to come from the future. Fixed test time remains deterministic.
            runtime, observed_at = read_runtime_status_at(
                directory_fd,
                now=now,
                stale_after_seconds=90.0,
            )
        except (OSError, TelegramStateError, ValueError):
            observed_at = _timestamp(now)
            return WatchdogObservation(
                True, True, None, "heartbeat-invalid", observed_at
            )
        generation = _optional_timestamp(runtime.get("started_at"))
        if generation is None:
            return WatchdogObservation(
                True, True, None, "heartbeat-missing", observed_at
            )
        stopped_at = _optional_timestamp(runtime.get("stopped_at"))
        if stopped_at is not None:
            # InstanceLock writes this sentinel before runner.start performs
            # Telegram calls. A bounded grace avoids killing a slow startup.
            if (
                stopped_at == generation
                and observed_at - generation <= _STARTUP_GRACE_SECONDS
            ):
                return WatchdogObservation(
                    True,
                    True,
                    generation,
                    "startup-grace",
                    observed_at,
                )
            return WatchdogObservation(
                True,
                True,
                generation,
                "daemon-stopped",
                observed_at,
                evidence=_evidence("daemon-stopped", stopped_at),
            )
        intake = runtime.get("intake")
        worker = runtime.get("worker")
        if not isinstance(intake, dict) or not isinstance(worker, dict):
            return WatchdogObservation(
                True,
                True,
                generation,
                "heartbeat-invalid",
                observed_at,
            )
        lanes = (intake, worker)
        if (
            observed_at - generation <= _STARTUP_GRACE_SECONDS
            and any(lane.get("state") == "starting" for lane in lanes)
        ):
            return WatchdogObservation(
                True,
                True,
                generation,
                "startup-grace",
                observed_at,
            )
        if not bool(runtime.get("heartbeat_fresh")):
            heartbeat_at = _optional_timestamp(runtime.get("heartbeat_at"))
            if heartbeat_at is None:
                return WatchdogObservation(
                    True,
                    True,
                    generation,
                    "heartbeat-invalid",
                    observed_at,
                )
            return WatchdogObservation(
                True,
                True,
                generation,
                "supervisor-stale",
                observed_at,
                evidence=_evidence("supervisor-stale", heartbeat_at),
            )
        for lane_name, lane in zip(("intake", "worker"), lanes, strict=True):
            state_name = lane.get("state")
            if state_name == "active" and not bool(lane.get("ready")):
                return WatchdogObservation(
                    True,
                    True,
                    generation,
                    f"{lane_name}-overdue",
                    observed_at,
                    evidence=_lane_evidence(lane_name, lane),
                )
            if state_name == "stopped":
                return WatchdogObservation(
                    True,
                    True,
                    generation,
                    f"{lane_name}-stopped",
                    observed_at,
                    evidence=_lane_evidence(lane_name, lane),
                )
            if state_name == "starting":
                return WatchdogObservation(
                    True,
                    True,
                    generation,
                    f"{lane_name}-starting-stuck",
                    observed_at,
                    evidence=_lane_evidence(lane_name, lane),
                )
            if state_name == "backoff":
                resume_at = _optional_timestamp(lane.get("resume_at"))
                if (
                    resume_at is not None
                    and observed_at > resume_at + _BACKOFF_GRACE_SECONDS
                ):
                    return WatchdogObservation(
                        True,
                        True,
                        generation,
                        f"{lane_name}-backoff-stuck",
                        observed_at,
                        evidence=_lane_evidence(lane_name, lane),
                    )
        return WatchdogObservation(True, True, generation, "", observed_at)

    def _reconcile_pending(
        self,
        tracker: dict[str, Any],
        observation: WatchdogObservation,
        now: float,
    ) -> dict[str, object] | None:
        if not tracker.get("pending_fingerprint"):
            return None
        prior_generation = _optional_timestamp(tracker.get("pending_generation"))
        _fingerprint, _saved_generation = _normalize_fingerprint(
            tracker.get("pending_fingerprint")
        )
        generation_changed = (
            observation.generation is not None
            and (
                prior_generation is None
                or observation.generation != prior_generation
            )
        )
        if generation_changed:
            _clear_pending(tracker)
            _reset_samples(tracker)
            return _report(
                observation,
                action="restart-verified",
                tracker=tracker,
                liveness_ready=_observation_acceptable(observation),
            )
        if not observation.service_loaded:
            pending_at = _optional_timestamp(tracker.get("pending_at"))
            if (
                pending_at is not None
                and now - pending_at < _MIN_RESTART_INTERVAL_SECONDS
            ):
                return _report(
                    observation,
                    action="service-not-loaded",
                    tracker=tracker,
                    liveness_ready=False,
                )
            _clear_pending(tracker)
            _reset_samples(tracker)
            return None
        if (
            observation.reason == ""
            and observation.lock_held
            and observation.generation is not None
            and observation.generation == prior_generation
        ):
            # Natural recovery of the original generation is healthy, not a
            # verified restart, and must clear pending before future failures.
            _clear_pending(tracker)
            _reset_samples(tracker)
            return _report(
                observation,
                action="healthy",
                tracker=tracker,
                liveness_ready=True,
            )
        pending_at = _optional_timestamp(tracker.get("pending_at"))
        if pending_at is not None and now - pending_at < _MIN_RESTART_INTERVAL_SECONDS:
            return _report(
                observation,
                action="restart-pending",
                tracker=tracker,
                liveness_ready=False,
            )
        _clear_pending(tracker)
        _reset_samples(tracker)
        return None

    def _service_loaded(self) -> bool:
        try:
            return self.command_runner(
                ("/bin/launchctl", "print", self.service_target)
            ) == 0
        except (OSError, RuntimeError, ValueError):
            return False

    def _restart_service(self) -> bool:
        try:
            return self.command_runner(
                ("/bin/launchctl", "kickstart", "-k", self.service_target)
            ) == 0
        except (OSError, RuntimeError, ValueError):
            return False

    def _run_command(self, argv: tuple[str, ...]) -> int:
        try:
            result = run_command(
                argv,
                cwd=self.project_root,
                timeout=_LAUNCHCTL_TIMEOUT_SECONDS,
                max_output_bytes=0,
                check=False,
            )
        except (OSError, RuntimeError, ValueError):
            return 1
        return 0 if result.ok else 1


@contextmanager
def _locked_tracker(project_root: Path) -> Iterator[_TrackerLease]:
    directory_fd = open_directory_beneath(
        project_root,
        _RUNTIME_DIRECTORY,
        create=True,
        private=True,
        private_from=1,
    )
    lock_fd = -1
    locked = False
    try:
        lock_fd = open_private_lock_file_at(directory_fd, _TRACKER_LOCK_NAME)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise TelegramStateError("Another Telegram watchdog run is active") from exc
        locked = True
        validate_private_lock_file(directory_fd, lock_fd, _TRACKER_LOCK_NAME)
        try:
            raw, identity = read_text_with_identity_at(
                directory_fd,
                _TRACKER_NAME,
                max_bytes=_MAX_STATE_BYTES,
            )
        except FileNotFoundError:
            tracker = _new_tracker()
            identity = None
        else:
            try:
                tracker = _normalize_tracker(json.loads(raw))
            except (TypeError, ValueError, RecursionError) as exc:
                raise TelegramStateError("Invalid Telegram watchdog state") from exc
        yield _TrackerLease(directory_fd, identity, tracker)
    finally:
        if lock_fd >= 0:
            try:
                if locked:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
        os.close(directory_fd)


def _write_tracker(lease: _TrackerLease) -> None:
    normalized = _normalize_tracker(lease.tracker)
    write_json_atomic_at(
        lease.directory_fd,
        _TRACKER_NAME,
        normalized,
        expected_identity=lease.identity,
    )
    raw, identity = read_text_with_identity_at(
        lease.directory_fd,
        _TRACKER_NAME,
        max_bytes=_MAX_STATE_BYTES,
    )
    try:
        persisted = _normalize_tracker(json.loads(raw))
    except (TypeError, ValueError, RecursionError) as exc:
        raise TelegramStateError("Invalid Telegram watchdog state") from exc
    if persisted != normalized:
        raise TelegramStateError("Telegram watchdog state changed after write")
    lease.tracker.clear()
    lease.tracker.update(persisted)
    lease.identity = identity


def _new_tracker() -> dict[str, Any]:
    return {
        "version": _STATE_VERSION,
        "fingerprint": "",
        "samples": 0,
        "first_seen_at": None,
        "last_seen_at": None,
        "pending_fingerprint": "",
        "pending_generation": None,
        "pending_at": None,
        "last_restart_at": None,
        "restart_times": [],
    }


def _normalize_tracker(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(_new_tracker()):
        raise TelegramStateError("Invalid Telegram watchdog state")
    if type(value.get("version")) is not int or value.get("version") != _STATE_VERSION:
        raise TelegramStateError("Invalid Telegram watchdog state")
    fingerprint, _generation = _normalize_fingerprint(value.get("fingerprint"))
    pending_fingerprint, fingerprint_generation = _normalize_fingerprint(
        value.get("pending_fingerprint")
    )
    samples = value.get("samples")
    restart_times = value.get("restart_times")
    if (
        isinstance(samples, bool)
        or not isinstance(samples, int)
        or not 0 <= samples <= _REQUIRED_SAMPLES
        or not isinstance(restart_times, list)
        or len(restart_times) > _MAX_RESTARTS_PER_WINDOW
    ):
        raise TelegramStateError("Invalid Telegram watchdog state")
    try:
        first_seen_at = _optional_timestamp(value.get("first_seen_at"))
        last_seen_at = _optional_timestamp(value.get("last_seen_at"))
        pending_generation = _optional_timestamp(value.get("pending_generation"))
        pending_at = _optional_timestamp(value.get("pending_at"))
        last_restart_at = _optional_timestamp(value.get("last_restart_at"))
        normalized_restarts = [_timestamp(item) for item in restart_times]
    except ValueError as exc:
        raise TelegramStateError("Invalid Telegram watchdog state") from exc

    if fingerprint:
        if (
            samples < 1
            or first_seen_at is None
            or last_seen_at is None
            or first_seen_at > last_seen_at
        ):
            raise TelegramStateError("Invalid Telegram watchdog state")
    elif samples != 0 or first_seen_at is not None or last_seen_at is not None:
        raise TelegramStateError("Invalid Telegram watchdog state")
    if pending_fingerprint:
        if (
            pending_at is None
            or pending_generation != fingerprint_generation
        ):
            raise TelegramStateError("Invalid Telegram watchdog state")
    elif pending_generation is not None or pending_at is not None:
        raise TelegramStateError("Invalid Telegram watchdog state")
    if normalized_restarts != sorted(normalized_restarts):
        raise TelegramStateError("Invalid Telegram watchdog state")
    if normalized_restarts and (
        last_restart_at is None or last_restart_at < normalized_restarts[-1]
    ):
        raise TelegramStateError("Invalid Telegram watchdog state")
    if pending_at is not None and (
        last_restart_at != pending_at
        or not normalized_restarts
        or normalized_restarts[-1] != pending_at
    ):
        raise TelegramStateError("Invalid Telegram watchdog state")
    return {
        "version": _STATE_VERSION,
        "fingerprint": fingerprint,
        "samples": samples,
        "first_seen_at": first_seen_at,
        "last_seen_at": last_seen_at,
        "pending_fingerprint": pending_fingerprint,
        "pending_generation": pending_generation,
        "pending_at": pending_at,
        "last_restart_at": last_restart_at,
        "restart_times": normalized_restarts,
    }


def _normalize_fingerprint(
    value: Any,
) -> tuple[str, float | None]:
    if value == "":
        return "", None
    if not isinstance(value, str) or value.count(":") != 2:
        raise TelegramStateError("Invalid Telegram watchdog state")
    generation_text, reason, evidence = value.split(":", 2)
    if (
        reason not in _RESTARTABLE_REASONS
        or len(evidence) != 64
        or any(character not in "0123456789abcdef" for character in evidence)
    ):
        raise TelegramStateError("Invalid Telegram watchdog state")
    try:
        generation = _timestamp(float.fromhex(generation_text))
    except (TypeError, ValueError, OverflowError) as exc:
        raise TelegramStateError("Invalid Telegram watchdog state") from exc
    if generation_text != generation.hex():
        raise TelegramStateError("Invalid Telegram watchdog state")
    return value, generation


def _record_sample(
    tracker: dict[str, Any], observation: WatchdogObservation, now: float
) -> None:
    last_seen_at = _optional_timestamp(tracker.get("last_seen_at"))
    spacing = None if last_seen_at is None else now - last_seen_at
    continuous = bool(
        spacing is not None and 0 <= spacing <= _MAX_SAMPLE_SPACING_SECONDS
    )
    if tracker.get("fingerprint") != observation.fingerprint or not continuous:
        tracker["fingerprint"] = observation.fingerprint
        tracker["samples"] = 1
        tracker["first_seen_at"] = now
    else:
        tracker["samples"] = min(
            _REQUIRED_SAMPLES,
            int(tracker.get("samples") or 0) + 1,
        )
    tracker["last_seen_at"] = now


def _debounce_complete(tracker: dict[str, Any], now: float) -> bool:
    first_seen_at = _optional_timestamp(tracker.get("first_seen_at"))
    return bool(
        int(tracker.get("samples") or 0) >= _REQUIRED_SAMPLES
        and first_seen_at is not None
        and now - first_seen_at >= _MIN_SAMPLE_SPACING_SECONDS
    )


def _reset_samples(tracker: dict[str, Any]) -> None:
    tracker["fingerprint"] = ""
    tracker["samples"] = 0
    tracker["first_seen_at"] = None
    tracker["last_seen_at"] = None


def _clear_pending(tracker: dict[str, Any]) -> None:
    tracker["pending_fingerprint"] = ""
    tracker["pending_generation"] = None
    tracker["pending_at"] = None


def _rebase_tracker_clock(tracker: dict[str, Any], now: float) -> None:
    """Bound future durable timers after a backwards wall-clock adjustment."""
    first_seen_at = _optional_timestamp(tracker.get("first_seen_at"))
    last_seen_at = _optional_timestamp(tracker.get("last_seen_at"))
    if (
        (first_seen_at is not None and first_seen_at > now)
        or (last_seen_at is not None and last_seen_at > now)
    ):
        _reset_samples(tracker)

    tracker["restart_times"] = [
        min(_timestamp(value), now) for value in tracker.get("restart_times", [])
    ]
    last_restart_at = _optional_timestamp(tracker.get("last_restart_at"))
    if last_restart_at is not None and last_restart_at > now:
        tracker["last_restart_at"] = now
    pending_at = _optional_timestamp(tracker.get("pending_at"))
    if pending_at is not None and pending_at > now:
        tracker["pending_at"] = now
        tracker["last_restart_at"] = now


def _recent_restarts(tracker: dict[str, Any], now: float) -> list[float]:
    cutoff = now - _RESTART_WINDOW_SECONDS
    return [
        value
        for value in (_timestamp(item) for item in tracker.get("restart_times", []))
        if value >= cutoff
    ][-_MAX_RESTARTS_PER_WINDOW:]


def _report(
    observation: WatchdogObservation,
    *,
    action: str,
    tracker: dict[str, Any],
    liveness_ready: bool,
) -> dict[str, object]:
    if action not in _ACTIONS:
        raise ValueError("invalid watchdog action")
    return {
        "liveness_ready": liveness_ready,
        "action": action,
        "reason": observation.reason,
        "service_loaded": observation.service_loaded,
        "lock_held": observation.lock_held,
        "generation": observation.generation,
        "samples": int(tracker.get("samples") or 0),
        "restart_count_window": len(tracker.get("restart_times", [])),
    }


def _observation_acceptable(observation: WatchdogObservation) -> bool:
    return bool(
        observation.service_loaded
        and observation.lock_held
        and observation.generation is not None
        and observation.reason in {"", "startup-grace"}
    )


def _lane_evidence(lane_name: str, lane: dict[str, Any]) -> str:
    return _evidence(
        lane_name,
        lane.get("state"),
        lane.get("operation"),
        lane.get("progress_at"),
        lane.get("deadline_at"),
        lane.get("resume_at"),
    )


def _evidence(*values: object) -> str:
    payload = json.dumps(
        values,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _timestamp(value: Any = None) -> float:
    candidate = time.time() if value is None else value
    if isinstance(candidate, bool) or not isinstance(candidate, (int, float)):
        raise ValueError("watchdog timestamp must be a non-negative finite number")
    try:
        normalized = float(candidate)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            "watchdog timestamp must be a non-negative finite number"
        ) from exc
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError("watchdog timestamp must be a non-negative finite number")
    return normalized


def _optional_timestamp(value: Any) -> float | None:
    if value is None:
        return None
    return _timestamp(value)
