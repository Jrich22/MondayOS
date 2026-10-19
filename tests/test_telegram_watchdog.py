"""Focused liveness and safety tests for the Telegram launchd watchdog."""

from __future__ import annotations

import json
import os
import plistlib
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from monday.cli import _cmd_telegram
from telegram_bot.state import InstanceLock, TelegramState, TelegramStateError
from telegram_bot.watchdog import TelegramWatchdog

UID = 501
SERVICE_TARGET = f"gui/{UID}/com.mondayos.telegram"
PRINT_COMMAND = ("/bin/launchctl", "print", SERVICE_TARGET)
KICKSTART_COMMAND = ("/bin/launchctl", "kickstart", "-k", SERVICE_TARGET)


class CommandRecorder:
    """Deterministic launchctl substitute with an optional race hook."""

    def __init__(
        self,
        *,
        print_status: int = 0,
        kickstart_status: int = 0,
        on_call: Callable[[tuple[str, ...], int], None] | None = None,
    ) -> None:
        self.print_status = print_status
        self.kickstart_status = kickstart_status
        self.on_call = on_call
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: tuple[str, ...]) -> int:
        assert isinstance(argv, tuple)
        self.calls.append(argv)
        if self.on_call is not None:
            self.on_call(argv, len(self.calls))
        if argv == PRINT_COMMAND:
            return self.print_status
        if argv == KICKSTART_COMMAND:
            return self.kickstart_status
        raise AssertionError(f"unexpected watchdog command: {argv!r}")


def _state_path(project_root: Path) -> Path:
    return project_root / "logs" / "telegram" / "state.json"


def _lock_path(project_root: Path) -> Path:
    return project_root / "logs" / "telegram" / "bot.lock"


def _tracker_path(project_root: Path) -> Path:
    return project_root / "logs" / "telegram" / "watchdog.json"


def _tracker_lock_path(project_root: Path) -> Path:
    return project_root / "logs" / "telegram" / ".watchdog.lock"


def _watchdog(
    project_root: Path,
    commands: CommandRecorder,
) -> TelegramWatchdog:
    return TelegramWatchdog(
        project_root=project_root,
        command_runner=commands,
        uid=UID,
    )


@contextmanager
def _locked_state(
    project_root: Path,
    *,
    sentinel_at: float = 900.0,
) -> Iterator[TelegramState]:
    """Hold only the daemon lock; the watchdog always owns its own lock."""
    state = TelegramState(_state_path(project_root))
    with mock.patch("telegram_bot.state.time.time", return_value=sentinel_at):
        with InstanceLock(_lock_path(project_root)):
            yield state


def _make_healthy(
    state: TelegramState,
    *,
    now: float,
    started_at: float | None = None,
) -> None:
    if started_at is not None:
        state.mark_worker_started(now=started_at)
    state.mark_runtime_lane(
        "intake",
        "active",
        "poll",
        timeout_seconds=80.0,
        now=now,
    )
    state.mark_runtime_lane("worker", "idle", "idle", now=now)
    state.mark_worker_heartbeat(now=now)


def _make_overdue(
    state: TelegramState,
    *,
    pulse_at: float,
    started_at: float | None = None,
) -> None:
    if started_at is not None:
        state.mark_worker_started(now=started_at)
    progress_at = pulse_at - 50.0
    state.mark_runtime_lane(
        "intake",
        "active",
        "poll",
        timeout_seconds=10.0,
        now=progress_at,
    )
    state.mark_runtime_lane("worker", "idle", "idle", now=progress_at)
    state.mark_worker_heartbeat(now=pulse_at)


def _request_restart(
    watchdog: TelegramWatchdog,
    state: TelegramState,
    *,
    generation: float,
    first_sample_at: float,
) -> dict[str, object]:
    _make_overdue(
        state,
        started_at=generation,
        pulse_at=first_sample_at,
    )
    first = watchdog.run(now=first_sample_at)
    assert first["action"] == "sampled"
    assert first["reason"] == "intake-overdue"
    return watchdog.run(now=first_sample_at + 45.0)


def test_healthy_active_and_idle_lanes_do_not_restart(tmp_path: Path) -> None:
    commands = CommandRecorder()
    with _locked_state(tmp_path) as state:
        _make_healthy(state, started_at=900.0, now=1_000.0)

        report = _watchdog(tmp_path, commands).run(now=1_000.0)

    assert report["action"] == "healthy"
    assert report["reason"] == ""
    assert report["liveness_ready"] is True
    assert report["service_loaded"] is True
    assert report["lock_held"] is True
    assert report["generation"] == 900.0
    assert commands.calls == [PRINT_COMMAND]


def test_initial_stopped_sentinel_is_healthy_during_startup_grace(
    tmp_path: Path,
) -> None:
    commands = CommandRecorder()
    with _locked_state(tmp_path, sentinel_at=1_000.0):
        report = _watchdog(tmp_path, commands).run(now=1_100.0)

    assert report["action"] == "healthy"
    assert report["reason"] == "startup-grace"
    assert report["liveness_ready"] is True
    assert report["generation"] == 1_000.0
    assert commands.calls == [PRINT_COMMAND]


def test_stopped_sentinel_becomes_restartable_after_startup_grace(
    tmp_path: Path,
) -> None:
    commands = CommandRecorder()
    watchdog = _watchdog(tmp_path, commands)
    with _locked_state(tmp_path, sentinel_at=1_000.0):
        first = watchdog.run(now=1_181.0)
        second = watchdog.run(now=1_226.0)

    assert first["action"] == "sampled"
    assert first["reason"] == "daemon-stopped"
    assert first["liveness_ready"] is False
    assert second["action"] == "restart-requested"
    assert second["reason"] == "daemon-stopped"
    assert commands.calls == [
        PRINT_COMMAND,
        PRINT_COMMAND,
        PRINT_COMMAND,
        PRINT_COMMAND,
        KICKSTART_COMMAND,
    ]


def test_overdue_lane_needs_two_matching_samples_45_seconds_apart(
    tmp_path: Path,
) -> None:
    commands = CommandRecorder()
    watchdog = _watchdog(tmp_path, commands)
    with _locked_state(tmp_path) as state:
        _make_overdue(state, started_at=900.0, pulse_at=1_000.0)

        first = watchdog.run(now=1_000.0)
        second = watchdog.run(now=1_045.0)

    assert first["action"] == "sampled"
    assert first["reason"] == "intake-overdue"
    assert first["samples"] == 1
    assert first["liveness_ready"] is False
    assert second["action"] == "restart-requested"
    assert second["reason"] == "intake-overdue"
    assert second["liveness_ready"] is False
    assert commands.calls == [
        PRINT_COMMAND,
        PRINT_COMMAND,
        PRINT_COMMAND,
        PRINT_COMMAND,
        KICKSTART_COMMAND,
    ]


def test_recovery_during_confirmation_race_blocks_restart(tmp_path: Path) -> None:
    state_ref: TelegramState | None = None

    def recover_before_confirmation(argv: tuple[str, ...], call: int) -> None:
        if argv == PRINT_COMMAND and call == 3:
            assert state_ref is not None
            _make_healthy(state_ref, now=1_045.0)

    commands = CommandRecorder(on_call=recover_before_confirmation)
    watchdog = _watchdog(tmp_path, commands)
    with _locked_state(tmp_path) as state:
        state_ref = state
        _make_overdue(state, started_at=900.0, pulse_at=1_000.0)
        assert watchdog.run(now=1_000.0)["action"] == "sampled"

        report = watchdog.run(now=1_045.0)

    assert report["action"] == "healthy"
    assert report["reason"] == ""
    assert report["liveness_ready"] is True
    assert commands.calls == [PRINT_COMMAND, PRINT_COMMAND, PRINT_COMMAND]


def test_progress_during_confirmation_starts_a_new_evidence_window(
    tmp_path: Path,
) -> None:
    state_ref: TelegramState | None = None

    def progress_before_confirmation(argv: tuple[str, ...], call: int) -> None:
        if argv == PRINT_COMMAND and call == 3:
            assert state_ref is not None
            state_ref.mark_runtime_lane(
                "intake",
                "active",
                "poll",
                timeout_seconds=10.0,
                now=1_030.0,
            )
            state_ref.mark_runtime_lane("worker", "idle", "idle", now=1_030.0)
            state_ref.mark_worker_heartbeat(now=1_045.0)

    commands = CommandRecorder(on_call=progress_before_confirmation)
    watchdog = _watchdog(tmp_path, commands)
    with _locked_state(tmp_path) as state:
        state_ref = state
        _make_overdue(state, started_at=900.0, pulse_at=1_000.0)
        assert watchdog.run(now=1_000.0)["action"] == "sampled"

        progressed = watchdog.run(now=1_045.0)
        restarted = watchdog.run(now=1_090.0)

    assert progressed["action"] == "sampled"
    assert progressed["samples"] == 1
    assert restarted["action"] == "restart-requested"
    assert commands.calls.count(KICKSTART_COMMAND) == 1


def test_progress_between_overdue_samples_resets_the_evidence_window(
    tmp_path: Path,
) -> None:
    commands = CommandRecorder()
    watchdog = _watchdog(tmp_path, commands)
    with _locked_state(tmp_path) as state:
        _make_overdue(state, started_at=900.0, pulse_at=1_000.0)
        assert watchdog.run(now=1_000.0)["action"] == "sampled"

        state.mark_runtime_lane(
            "intake",
            "active",
            "poll",
            timeout_seconds=10.0,
            now=1_030.0,
        )
        state.mark_runtime_lane("worker", "idle", "idle", now=1_030.0)
        state.mark_worker_heartbeat(now=1_045.0)
        progressed = watchdog.run(now=1_045.0)
        restarted = watchdog.run(now=1_090.0)

    assert progressed["action"] == "sampled"
    assert progressed["samples"] == 1
    assert restarted["action"] == "restart-requested"
    assert commands.calls.count(KICKSTART_COMMAND) == 1


def test_old_sample_is_not_reused_after_a_long_observation_gap(
    tmp_path: Path,
) -> None:
    commands = CommandRecorder()
    watchdog = _watchdog(tmp_path, commands)
    with _locked_state(tmp_path) as state:
        _make_overdue(state, started_at=900.0, pulse_at=1_000.0)
        assert watchdog.run(now=1_000.0)["action"] == "sampled"

        late = watchdog.run(now=1_300.0)
        restarted = watchdog.run(now=1_345.0)

    assert late["action"] == "sampled"
    assert late["samples"] == 1
    assert restarted["action"] == "restart-requested"
    assert commands.calls.count(KICKSTART_COMMAND) == 1


def test_replacement_after_checkpoint_cancels_the_kickstart(
    tmp_path: Path,
) -> None:
    state_ref: TelegramState | None = None

    def replace_after_checkpoint(argv: tuple[str, ...], call: int) -> None:
        if argv == PRINT_COMMAND and call == 4:
            assert state_ref is not None
            _make_healthy(state_ref, started_at=1_045.0, now=1_045.0)

    commands = CommandRecorder(on_call=replace_after_checkpoint)
    watchdog = _watchdog(tmp_path, commands)
    with _locked_state(tmp_path) as state:
        state_ref = state
        _make_overdue(state, started_at=900.0, pulse_at=1_000.0)
        assert watchdog.run(now=1_000.0)["action"] == "sampled"

        report = watchdog.run(now=1_045.0)

    tracker = json.loads(_tracker_path(tmp_path).read_text(encoding="utf-8"))
    assert report["action"] == "healthy"
    assert report["generation"] == 1_045.0
    assert report["restart_count_window"] == 0
    assert KICKSTART_COMMAND not in commands.calls
    assert tracker["pending_fingerprint"] == ""
    assert tracker["restart_times"] == []


def test_new_started_at_verifies_a_pending_restart(tmp_path: Path) -> None:
    commands = CommandRecorder()
    watchdog = _watchdog(tmp_path, commands)
    with _locked_state(tmp_path) as state:
        requested = _request_restart(
            watchdog,
            state,
            generation=900.0,
            first_sample_at=1_000.0,
        )
        _make_healthy(state, started_at=1_046.0, now=1_046.0)

        verified = watchdog.run(now=1_046.0)

    assert requested["action"] == "restart-requested"
    assert verified["action"] == "restart-verified"
    assert verified["reason"] == ""
    assert verified["liveness_ready"] is True
    assert verified["generation"] == 1_046.0
    assert commands.calls.count(KICKSTART_COMMAND) == 1


def test_same_generation_recovery_clears_pending_restart(tmp_path: Path) -> None:
    commands = CommandRecorder()
    watchdog = _watchdog(tmp_path, commands)
    with _locked_state(tmp_path) as state:
        requested = _request_restart(
            watchdog,
            state,
            generation=900.0,
            first_sample_at=1_000.0,
        )
        _make_healthy(state, now=1_046.0)
        recovered = watchdog.run(now=1_046.0)

        _make_overdue(state, pulse_at=1_100.0)
        later_failure = watchdog.run(now=1_100.0)

    assert requested["action"] == "restart-requested"
    assert recovered["action"] == "healthy"
    assert recovered["reason"] == ""
    assert recovered["liveness_ready"] is True
    assert recovered["generation"] == 900.0
    assert later_failure["action"] == "sampled"
    assert later_failure["samples"] == 1
    assert commands.calls.count(KICKSTART_COMMAND) == 1


def test_unloaded_service_during_pending_is_never_reported_healthy(
    tmp_path: Path,
) -> None:
    commands = CommandRecorder()
    watchdog = _watchdog(tmp_path, commands)
    with _locked_state(tmp_path) as state:
        assert _request_restart(
            watchdog,
            state,
            generation=900.0,
            first_sample_at=1_000.0,
        )["action"] == "restart-requested"

        commands.print_status = 1
        unloaded = watchdog.run(now=1_046.0)
        commands.print_status = 0
        still_pending = watchdog.run(now=1_047.0)

    assert unloaded["action"] == "service-not-loaded"
    assert unloaded["service_loaded"] is False
    assert unloaded["liveness_ready"] is False
    assert still_pending["action"] == "restart-pending"
    assert still_pending["liveness_ready"] is False


def test_service_unloaded_during_confirmation_is_never_reported_healthy(
    tmp_path: Path,
) -> None:
    commands: CommandRecorder

    def unload_before_confirmation(argv: tuple[str, ...], call: int) -> None:
        if argv == PRINT_COMMAND and call == 3:
            commands.print_status = 1

    commands = CommandRecorder(on_call=unload_before_confirmation)
    watchdog = _watchdog(tmp_path, commands)
    with _locked_state(tmp_path) as state:
        _make_overdue(state, started_at=900.0, pulse_at=1_000.0)
        assert watchdog.run(now=1_000.0)["action"] == "sampled"

        report = watchdog.run(now=1_045.0)

    assert report["action"] == "service-not-loaded"
    assert report["service_loaded"] is False
    assert report["liveness_ready"] is False
    assert KICKSTART_COMMAND not in commands.calls


def test_restart_cooldown_throttles_a_confirmed_failure(tmp_path: Path) -> None:
    commands = CommandRecorder()
    watchdog = _watchdog(tmp_path, commands)
    with _locked_state(tmp_path) as state:
        assert _request_restart(
            watchdog,
            state,
            generation=900.0,
            first_sample_at=1_000.0,
        )["action"] == "restart-requested"
        _make_healthy(state, started_at=1_046.0, now=1_046.0)
        assert watchdog.run(now=1_046.0)["action"] == "restart-verified"

        _make_overdue(state, pulse_at=1_200.0)
        assert watchdog.run(now=1_200.0)["action"] == "sampled"
        throttled = watchdog.run(now=1_245.0)

    assert throttled["action"] == "restart-throttled"
    assert throttled["reason"] == "intake-overdue"
    assert throttled["liveness_ready"] is False
    assert commands.calls.count(KICKSTART_COMMAND) == 1


def test_clock_rollback_rebases_pending_timers_to_a_bounded_delay(
    tmp_path: Path,
) -> None:
    commands = CommandRecorder()
    watchdog = _watchdog(tmp_path, commands)
    with _locked_state(tmp_path) as state:
        assert _request_restart(
            watchdog,
            state,
            generation=900.0,
            first_sample_at=1_000.0,
        )["action"] == "restart-requested"

        rolled_back = watchdog.run(now=500.0)
        next_sample = watchdog.run(now=501.0)

    tracker = json.loads(_tracker_path(tmp_path).read_text(encoding="utf-8"))
    assert rolled_back["action"] == "restart-pending"
    assert next_sample["action"] == "restart-pending"
    assert tracker["pending_at"] == 500.0
    assert tracker["last_restart_at"] == 500.0
    assert tracker["restart_times"] == [500.0]


def test_restart_budget_is_limited_to_three_per_hour(tmp_path: Path) -> None:
    commands = CommandRecorder()
    watchdog = _watchdog(tmp_path, commands)
    with _locked_state(tmp_path) as state:
        assert _request_restart(
            watchdog,
            state,
            generation=900.0,
            first_sample_at=1_000.0,
        )["action"] == "restart-requested"

        for generation, first_sample_at in (
            (1_046.0, 1_355.0),
            (1_401.0, 1_710.0),
        ):
            _make_healthy(state, started_at=generation, now=generation)
            assert watchdog.run(now=generation)["action"] == "restart-verified"
            _make_overdue(state, pulse_at=first_sample_at)
            assert watchdog.run(now=first_sample_at)["action"] == "sampled"
            assert watchdog.run(now=first_sample_at + 45.0)["action"] == (
                "restart-requested"
            )

        _make_healthy(state, started_at=1_756.0, now=1_756.0)
        assert watchdog.run(now=1_756.0)["action"] == "restart-verified"
        _make_overdue(state, pulse_at=2_065.0)
        assert watchdog.run(now=2_065.0)["action"] == "sampled"
        exhausted = watchdog.run(now=2_110.0)

    assert exhausted["action"] == "restart-budget-exhausted"
    assert exhausted["reason"] == "intake-overdue"
    assert exhausted["restart_count_window"] == 3
    assert exhausted["liveness_ready"] is False
    assert commands.calls.count(KICKSTART_COMMAND) == 3


def test_service_not_loaded_never_restarts(tmp_path: Path) -> None:
    commands = CommandRecorder(print_status=1)
    with _locked_state(tmp_path) as state:
        _make_healthy(state, started_at=900.0, now=1_000.0)
        report = _watchdog(tmp_path, commands).run(now=1_000.0)

    assert report["action"] == "service-not-loaded"
    assert report["liveness_ready"] is False
    assert report["service_loaded"] is False
    assert commands.calls == [PRINT_COMMAND]


def test_missing_daemon_lock_is_unreadable_and_never_restartable(
    tmp_path: Path,
) -> None:
    commands = CommandRecorder()
    state = TelegramState(_state_path(tmp_path))
    _make_healthy(state, started_at=900.0, now=1_000.0)
    watchdog = _watchdog(tmp_path, commands)

    first = watchdog.run(now=1_000.0)
    second = watchdog.run(now=1_045.0)

    for report in (first, second):
        assert report["action"] == "state-unreadable"
        assert report["reason"] == "lock-missing"
        assert report["generation"] is None
        assert report["liveness_ready"] is False
        assert report["samples"] == 0
    assert commands.calls == [PRINT_COMMAND, PRINT_COMMAND]


@pytest.mark.parametrize("attack", ["symlink", "hardlink"])
def test_untrusted_daemon_lock_is_unreadable_and_never_modified(
    tmp_path: Path,
    attack: str,
) -> None:
    commands = CommandRecorder()
    runtime_dir = tmp_path / "logs" / "telegram"
    runtime_dir.mkdir(parents=True)
    victim = tmp_path / f"outside-lock-{attack}"
    victim.write_text("outside must remain unchanged", encoding="utf-8")
    lock = _lock_path(tmp_path)
    if attack == "symlink":
        lock.symlink_to(victim)
    else:
        os.link(victim, lock)
    before = victim.read_bytes()

    report = _watchdog(tmp_path, commands).run(now=1_000.0)

    assert report["action"] == "state-unreadable"
    assert report["reason"] == "state-unreadable"
    assert report["liveness_ready"] is False
    assert commands.calls == [PRINT_COMMAND]
    assert victim.read_bytes() == before
    if attack == "symlink":
        assert lock.is_symlink()
    else:
        assert lock.stat().st_ino == victim.stat().st_ino
        assert lock.stat().st_nlink == 2


@pytest.mark.parametrize(
    ("heartbeat", "reason"),
    [("missing", "heartbeat-missing"), ("invalid", "heartbeat-invalid")],
)
def test_generationless_heartbeat_failure_is_never_restartable(
    tmp_path: Path,
    heartbeat: str,
    reason: str,
) -> None:
    commands = CommandRecorder()
    watchdog = _watchdog(tmp_path, commands)
    with _locked_state(tmp_path) as state:
        if heartbeat == "missing":
            state.heartbeat_path.unlink()
        else:
            state.heartbeat_path.write_text("{not-json", encoding="utf-8")

        first = watchdog.run(now=1_000.0)
        second = watchdog.run(now=1_045.0)

    for report in (first, second):
        assert report["action"] == "state-unreadable"
        assert report["reason"] == reason
        assert report["generation"] is None
        assert report["liveness_ready"] is False
        assert report["samples"] == 0
    assert commands.calls == [PRINT_COMMAND, PRINT_COMMAND]


def test_kickstart_failure_is_reported(tmp_path: Path) -> None:
    commands = CommandRecorder(kickstart_status=1)
    watchdog = _watchdog(tmp_path, commands)
    with _locked_state(tmp_path) as state:
        failed = _request_restart(
            watchdog,
            state,
            generation=900.0,
            first_sample_at=1_000.0,
        )

    assert failed["action"] == "restart-failed"
    assert failed["reason"] == "intake-overdue"
    assert failed["liveness_ready"] is False
    assert commands.calls == [
        PRINT_COMMAND,
        PRINT_COMMAND,
        PRINT_COMMAND,
        PRINT_COMMAND,
        KICKSTART_COMMAND,
    ]


@pytest.mark.parametrize("attack", ["corrupt", "symlink", "hardlink"])
def test_untrusted_tracker_files_fail_closed(
    tmp_path: Path,
    attack: str,
) -> None:
    commands = CommandRecorder()
    watchdog = _watchdog(tmp_path, commands)
    victim = tmp_path / f"outside-{attack}.json"

    with _locked_state(tmp_path) as state:
        _make_healthy(state, started_at=900.0, now=1_000.0)
        assert watchdog.run(now=1_000.0)["action"] == "healthy"
        tracker = _tracker_path(tmp_path)

        if attack == "corrupt":
            tracker.write_text("{not-json", encoding="utf-8")
            before = tracker.read_bytes()
        elif attack == "symlink":
            tracker.replace(victim)
            before = victim.read_bytes()
            tracker.symlink_to(victim)
        else:
            os.link(tracker, victim)
            before = victim.read_bytes()

        prior_calls = list(commands.calls)
        with pytest.raises((OSError, TelegramStateError)):
            watchdog.run(now=1_001.0)

        assert commands.calls == prior_calls
        if attack == "corrupt":
            assert tracker.read_bytes() == before
        else:
            assert victim.read_bytes() == before
        if attack == "symlink":
            assert tracker.is_symlink()
        if attack == "hardlink":
            assert tracker.stat().st_ino == victim.stat().st_ino
            assert tracker.stat().st_nlink == 2


def test_created_tracker_files_have_exact_private_permissions(
    tmp_path: Path,
) -> None:
    commands = CommandRecorder(print_status=1)
    prior_umask = os.umask(0)
    try:
        with _locked_state(tmp_path, sentinel_at=1_000.0):
            report = _watchdog(tmp_path, commands).run(now=1_000.0)
    finally:
        os.umask(prior_umask)

    runtime_dir = tmp_path / "logs" / "telegram"
    assert report["action"] == "service-not-loaded"
    assert stat.S_IMODE(runtime_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(_tracker_path(tmp_path).stat().st_mode) == 0o600
    assert stat.S_IMODE(_tracker_lock_path(tmp_path).stat().st_mode) == 0o600


def test_watchdog_cli_json_never_constructs_a_telegram_client(
    tmp_path: Path,
) -> None:
    expected = {
        "liveness_ready": True,
        "action": "healthy",
        "reason": "",
        "service_loaded": True,
        "lock_held": True,
        "generation": 900.0,
        "samples": 0,
        "restart_count_window": 0,
    }
    args = SimpleNamespace(
        project_root=str(tmp_path),
        once=False,
        identify=False,
        health=False,
        watchdog=True,
        json=True,
        provider="",
    )
    output = StringIO()
    with mock.patch.object(
        TelegramWatchdog,
        "run",
        return_value=expected,
    ), mock.patch("telegram_bot.client.HttpTelegramClient") as client_type, mock.patch(
        "monday.provider_env.load_env_file"
    ) as load_env:
        with redirect_stdout(output):
            result = _cmd_telegram(args)

    assert result == 0
    assert json.loads(output.getvalue()) == expected
    client_type.assert_not_called()
    load_env.assert_not_called()


def test_watchdog_launchd_template_is_fixed_and_secret_free() -> None:
    path = (
        Path(__file__).resolve().parent.parent
        / "deploy"
        / "launchd"
        / "com.mondayos.telegram-watchdog.plist.example"
    )
    raw = path.read_bytes()
    payload = plistlib.loads(raw)

    assert payload["Label"] == "com.mondayos.telegram-watchdog"
    assert payload["ProgramArguments"] == [
        "REPLACE_WITH_MONDAYOS_ROOT/.venv/bin/monday",
        "--project-root",
        "REPLACE_WITH_MONDAYOS_ROOT",
        "telegram",
        "--watchdog",
        "--json",
    ]
    assert payload["RunAtLoad"] is True
    assert payload["StartInterval"] == 60
    assert "KeepAlive" not in payload
    assert set(payload["EnvironmentVariables"]) == {"PATH"}
    assert raw.count(b"REPLACE_WITH_MONDAYOS_ROOT") == 5
    assert all(
        marker not in raw.upper()
        for marker in (b"TOKEN", b"PASSWORD", b"API_KEY")
    )
