"""Durable Telegram intake and sequential MondayOS job execution."""
from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from telegram_bot.client import TelegramAPIError, TelegramClient, TelegramRateLimitError
from telegram_bot.service import TelegramBotService, TelegramUpdatePendingError
from telegram_bot.state import TelegramState

_MAX_HANDLER_ATTEMPTS = 3
_HEARTBEAT_INTERVAL_SECONDS = 30.0
_MAX_TELEGRAM_NOTICE_PAUSE_SECONDS = 86_400
_RUNTIME_INTAKE_DISPATCH_TIMEOUT_SECONDS = 300.0
_RUNTIME_WORKER_TIMEOUT_SECONDS = 300.0
_RUNTIME_WORKER_OPERATION_TIMEOUTS = {
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
}


class TelegramUpdateRetryError(RuntimeError):
    """A queued handler failed but its durable checkpoint remains retryable."""


def _telegram_notice_rate_limit_delay(value: Any) -> float:
    """Bound an untrusted Bot API retry hint before float/time arithmetic."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 1.0
    return float(min(max(value, 1), _MAX_TELEGRAM_NOTICE_PAUSE_SECONDS))


class TelegramJobWorker:
    """Run one durable Telegram request at a time in FIFO order."""

    def __init__(
        self,
        service: TelegramBotService,
        state: TelegramState,
        *,
        stop_event: threading.Event,
        wake_event: threading.Event,
        wait: Callable[[float], bool] | None = None,
    ) -> None:
        self._service = service
        self._state = state
        self._stop = stop_event
        self._wake = wake_event
        self._wait = wait or self._stop.wait
        self._runtime_leases_enabled = False

    def enable_runtime_leases(self, enabled: bool) -> None:
        """Enable daemon-only lane updates without affecting one-shot callers."""
        self._runtime_leases_enabled = enabled

    def run_once(self) -> bool:
        """Process one claimed request, returning false when the inbox is empty."""
        claimed = self._state.claim_next()
        if claimed is None:
            return False
        update_id, update = claimed
        self._mark_runtime_lane(
            "active",
            "job",
            timeout_seconds=_RUNTIME_WORKER_TIMEOUT_SECONDS,
        )
        try:
            self._service.handle_update(update)
        except TelegramUpdatePendingError:
            # A lease-owning delivery may still be running. Keep the same
            # request and stable delivery id without consuming poison retries.
            self._state.mark_waiting(update_id)
            raise
        except TelegramAPIError as exc:
            if exc.retryable:
                self._state.mark_waiting(update_id)
                raise
            # A permanent send failure (for example, the user blocked the bot)
            # must not wedge every later request in the durable FIFO.
            self._state.dead_letter(update_id)
            return True
        except Exception:
            attempts = self._state.record_failure(update_id)
            will_retry = attempts < _MAX_HANDLER_ATTEMPTS
            if will_retry:
                # A courtesy-notice failure cannot bypass the handler's own
                # bounded retry lifecycle. Retryable Telegram errors still
                # control the worker backoff; permanent ones are ignored here.
                try:
                    self._service.report_error(update, will_retry=True)
                except TelegramAPIError as notice_error:
                    if notice_error.retryable:
                        raise
                except Exception:
                    pass
                raise TelegramUpdateRetryError(
                    f"Telegram update {update_id} will be retried"
                ) from None

            # Preserve the checkpoint before attempting the final notice. A
            # blocked bot must not prevent a poison request reaching its bound.
            self._state.dead_letter(update_id)
            try:
                self._service.report_error(update, will_retry=False)
            except TelegramAPIError:
                pass
            return True

        self._state.acknowledge(update_id)
        return True

    def run_follow_up_once(self) -> bool:
        """Observe one due PR lifecycle without rehydrating request text."""
        claimed = self._state.claim_due_follow_up()
        if claimed is None:
            return False
        update_id, follow_up = claimed
        poller = getattr(self._service, "poll_follow_up", None)
        if not callable(poller):
            self._state.defer_follow_up(update_id, delay_seconds=300)
            return True
        try:
            poller(update_id, follow_up)
        except Exception:
            # Observation failures are deliberately isolated from accepted
            # work. The tombstone remains restart-safe and retains no raw
            # remote diagnostic text.
            self._state.defer_follow_up(update_id, delay_seconds=60)
        return True

    def run_delivery_notice_once(self) -> bool:
        """Send one due request-free terminal notice without blocking work."""
        due = self._state.due_delivery_notice_ids()
        if not due:
            return False
        update_id = due[0]
        notice = self._state.delivery_notice(update_id)
        if not notice:
            return False
        binding = str(notice.get("source_binding") or "")
        deliverer = getattr(self._service, "deliver_delivery_notice", None)
        if not callable(deliverer):
            self._state.complete_delivery_notice(
                update_id,
                source_binding=binding,
                delivered=False,
            )
            return True
        try:
            delivered = deliverer(notice)
        except TelegramRateLimitError as exc:
            self._state.defer_delivery_notices(
                due,
                delay_seconds=_telegram_notice_rate_limit_delay(exc.retry_after),
            )
        except TelegramAPIError as exc:
            if exc.retryable:
                self._state.defer_delivery_notice(
                    update_id,
                    source_binding=binding,
                    delay_seconds=30,
                )
            else:
                self._state.complete_delivery_notice(
                    update_id,
                    source_binding=binding,
                    delivered=False,
                )
        except Exception:
            # Never retain provider, local, or Telegram diagnostic text.
            self._state.defer_delivery_notice(
                update_id,
                source_binding=binding,
                delay_seconds=60,
            )
        else:
            self._state.complete_delivery_notice(
                update_id,
                source_binding=binding,
                delivered=delivered is not False,
            )
        return True

    def run_lifecycle_notice_once(self) -> bool:
        """Send one due PR notice without delaying lifecycle observation."""
        due = self._state.due_lifecycle_notice_ids()
        if not due:
            return False
        update_id = due[0]
        notice = self._state.lifecycle_notice(update_id)
        if not notice:
            return False
        binding = str(notice.get("source_binding") or "")
        deliverer = getattr(self._service, "deliver_lifecycle_notice", None)
        if not callable(deliverer):
            self._state.complete_lifecycle_notice(
                update_id,
                source_binding=binding,
                delivered=False,
            )
            return True
        try:
            delivered = deliverer(notice)
        except TelegramRateLimitError as exc:
            self._state.defer_lifecycle_notices(
                due,
                delay_seconds=_telegram_notice_rate_limit_delay(exc.retry_after),
            )
        except TelegramAPIError as exc:
            if exc.retryable:
                self._state.defer_lifecycle_notice(
                    update_id,
                    source_binding=binding,
                    delay_seconds=30,
                )
            else:
                self._state.complete_lifecycle_notice(
                    update_id,
                    source_binding=binding,
                    delivered=False,
                )
        except Exception:
            # Never retain GitHub or Telegram diagnostic text. The semantic
            # notice remains source-bound and independently retryable.
            self._state.defer_lifecycle_notice(
                update_id,
                source_binding=binding,
                delay_seconds=60,
            )
        else:
            self._state.complete_lifecycle_notice(
                update_id,
                source_binding=binding,
                delivered=delivered is not False,
            )
        return True

    def run_delivery_retry_once(self) -> bool:
        """Run one due request-free retry without blocking the main FIFO."""
        claimed = self._state.claim_due_delivery_retry()
        if claimed is None:
            return False
        update_id, retry = claimed
        retrier = getattr(self._service, "retry_delivery", None)
        if not callable(retrier):
            self._state.defer_delivery_retry(update_id, delay_seconds=300)
            return True
        try:
            retrier(update_id, retry)
        except TelegramRateLimitError as exc:
            self._state.defer_delivery_retry(
                update_id,
                delay_seconds=_telegram_notice_rate_limit_delay(exc.retry_after),
            )
        except TelegramAPIError as exc:
            if exc.retryable:
                self._state.defer_delivery_retry(update_id, delay_seconds=60)
            else:
                self._state.stop_delivery_retry(
                    update_id,
                    phase="notification-failed",
                )
        except Exception:
            # Never persist model, CLI, GitHub, or Telegram diagnostics. The
            # exact retry identity is safe to reconcile after a short delay.
            self._state.defer_delivery_retry(update_id, delay_seconds=60)
        return True

    def run_forever(self) -> None:
        backoff = 1
        while not self._stop.is_set():
            try:
                # A due delivery retry supersedes its stale "will retry"
                # notice atomically when claimed, so retry work must win this
                # tie before terminal notices are selected for delivery.
                self._mark_background_active()
                retried = self.run_delivery_retry_once()
                self._mark_background_active()
                noticed = self.run_delivery_notice_once()
                pending_delivery = False
                try:
                    self._mark_background_active()
                    worked = self.run_once()
                except TelegramUpdatePendingError:
                    # One long-running FIFO delivery must not starve durable
                    # retry notifications or PR lifecycle observation.
                    worked = False
                    pending_delivery = True
                self._mark_background_active()
                followed_up = self.run_follow_up_once()
                # Observe first so a newer GitHub transition can atomically
                # replace a stale unsent notice before this lane selects it.
                self._mark_background_active()
                lifecycle_noticed = self.run_lifecycle_notice_once()
                if noticed or lifecycle_noticed or worked or retried or followed_up:
                    backoff = 1
                    continue
                if pending_delivery:
                    self._mark_runtime_lane(
                        "backoff",
                        "retry",
                        resume_after_seconds=backoff,
                    )
                    self._wait(backoff)
                    backoff = min(60, backoff * 2)
                    continue
                delay = self._state.next_follow_up_delay()
                self._mark_runtime_lane("idle", "idle")
                self._wake.wait(timeout=delay)
                self._wake.clear()
            except TelegramRateLimitError as exc:
                print(str(exc), file=sys.stderr)
                delay = _telegram_notice_rate_limit_delay(exc.retry_after)
                self._mark_runtime_lane(
                    "backoff",
                    "rate-limit",
                    resume_after_seconds=delay,
                )
                self._wait(delay)
            except TelegramAPIError as exc:
                if not exc.retryable:
                    raise
                print(str(exc), file=sys.stderr)
                self._mark_runtime_lane(
                    "backoff",
                    "retry",
                    resume_after_seconds=backoff,
                )
                self._wait(backoff)
                backoff = min(60, backoff * 2)
            except TelegramUpdateRetryError as exc:
                print(str(exc), file=sys.stderr)
                self._mark_runtime_lane(
                    "backoff",
                    "retry",
                    resume_after_seconds=backoff,
                )
                self._wait(backoff)
                backoff = min(60, backoff * 2)

    def _mark_background_active(self) -> None:
        self._mark_runtime_lane(
            "active",
            "background",
            timeout_seconds=_RUNTIME_WORKER_TIMEOUT_SECONDS,
        )

    def _mark_runtime_lane(
        self,
        state: str,
        operation: str,
        *,
        timeout_seconds: float = 0.0,
        resume_after_seconds: float = 0.0,
    ) -> None:
        if not self._runtime_leases_enabled:
            return
        marker = getattr(self._state, "mark_runtime_lane", None)
        if callable(marker):
            marker(
                "worker",
                state,
                operation,
                timeout_seconds=timeout_seconds,
                resume_after_seconds=resume_after_seconds,
            )


class TelegramRunner:
    """Persist Telegram updates promptly while a single worker runs MondayOS."""

    def __init__(
        self,
        client: TelegramClient,
        service: TelegramBotService,
        state: TelegramState,
        *,
        poll_timeout: int = 30,
        stop_event: threading.Event | None = None,
        wait: Callable[[float], bool] | None = None,
    ) -> None:
        self._client = client
        self._service = service
        self._state = state
        self._poll_timeout = poll_timeout
        self._stop = stop_event or threading.Event()
        self._wait = wait or self._stop.wait
        self._wake = threading.Event()
        self._worker = TelegramJobWorker(
            service,
            state,
            stop_event=self._stop,
            wake_event=self._wake,
        )
        self._worker_error: BaseException | None = None
        self._acceptance_receipts_paused_until = 0.0
        self._capacity_notices_paused_until = 0.0
        self._runtime_leases_enabled = False

    def start(self) -> dict[str, object]:
        identity = self._client.get_me()
        self._state.bind_bot_identity(
            identity.get("id"),
            str(identity.get("username") or ""),
        )
        self._service.bind_bot_identity(identity)
        self._client.delete_webhook(drop_pending_updates=False)
        return {
            "id": identity.get("id"),
            "username": identity.get("username", ""),
        }

    def ingest_once(self) -> int:
        """Persist one Telegram batch without running any Monday/model work."""
        # Telegram retains updates for a limited period. Retry request-free
        # refusal notices from our durable state before polling so a long
        # outage cannot strand a notice merely because its source update aged
        # out of Telegram's queue.
        self._mark_intake_dispatch()
        self._drain_queue_full_notices()
        # Accepted-job receipts use their own request-free outbox. Failures are
        # isolated here so polling can still accept /queue and /cancel controls.
        self._mark_intake_dispatch()
        self._drain_acceptance_receipts()
        self._mark_runtime_lane(
            "intake",
            "active",
            "poll",
            timeout_seconds=min(80, max(1, self._poll_timeout + 30)),
        )
        updates = self._client.get_updates(
            offset=self._state.next_offset,
            timeout=self._poll_timeout,
        )
        handled = 0
        for update in sorted(updates, key=lambda item: int(item.get("update_id", -1))):
            self._mark_intake_dispatch()
            update_id = int(update.get("update_id", -1))
            if update_id < 0:
                continue
            if self._state.is_terminal(update_id):
                if not self._state.queue_full_notice(update_id):
                    self._state.reaffirm_terminal(update_id)
                handled += 1
                continue

            try:
                normalized = self._normalize(update)
            except Exception:
                attempts = self._state.record_failure(update_id)
                will_retry = attempts < _MAX_HANDLER_ATTEMPTS
                if will_retry:
                    try:
                        self._service.report_error(update, will_retry=True)
                    except TelegramAPIError as notice_error:
                        if notice_error.retryable:
                            raise
                    except Exception:
                        pass
                    raise TelegramUpdateRetryError(
                        f"Telegram update {update_id} intake will be retried"
                    ) from None
                self._state.dead_letter(update_id)
                try:
                    self._service.report_error(update, will_retry=False)
                except TelegramAPIError:
                    pass
                handled += 1
                continue

            if normalized is None:
                # Store only a tombstone for ignored/unauthorized input. Its
                # sender metadata and message body never enter the durable FIFO.
                self._state.acknowledge(update_id)
                handled += 1
                continue

            try:
                controlled = self._handle_intake_control(normalized)
            except TelegramAPIError as exc:
                if exc.retryable:
                    # The state-only control mutation is idempotent. Leave this
                    # update unacknowledged so Telegram can redeliver it and the
                    # user can receive the same safe result after recovery.
                    raise
                # A permanently unreachable chat must not wedge intake after a
                # control mutation has already reached durable state.
                self._state.acknowledge(update_id)
                handled += 1
                continue
            if controlled:
                self._state.acknowledge(update_id)
                # Cancelling an unresolved FIFO head may expose the next job.
                self._wake.set()
                handled += 1
                continue

            created, position = self._state.enqueue(update_id, normalized)
            if created:
                self._deliver_acceptance_receipt(
                    update_id,
                    rate_limit_scope=self._state.due_acceptance_receipt_ids(),
                )
                self._wake.set()
            elif position == 0:
                tombstone = self._state.completed_job(update_id)
                if tombstone.get("phase") != "queue-full":
                    self._state.reaffirm_terminal(update_id)
            handled += 1
        # Process state-only controls before attempting a newly-created
        # capacity notice, so a slow send cannot hold a later /cancel behind it.
        self._mark_intake_dispatch()
        self._drain_queue_full_notices()
        self._mark_intake_dispatch()
        self._drain_acceptance_receipts()
        return handled

    def run_once(self) -> int:
        """Ingest one batch and drain it synchronously for CLI setup/tests."""
        handled = self.ingest_once()
        while True:
            delivery_noticed = self._worker.run_delivery_notice_once()
            worked = self._worker.run_once()
            if not delivery_noticed and not worked:
                break
        return handled

    def run_forever(self) -> None:
        worker_thread = threading.Thread(
            target=self._worker_main,
            name="mondayos-telegram-worker",
            daemon=False,
        )
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_main,
            name="mondayos-telegram-heartbeat",
            daemon=False,
        )
        self._state.mark_worker_started()
        self._runtime_leases_enabled = True
        self._worker.enable_runtime_leases(True)
        try:
            self._bind_runtime_progress(self._renew_worker_runtime)
            self._wake.set()  # Recover jobs already durable before this process.
            worker_thread.start()
            heartbeat_thread.start()
            backoff = 1
            while not self._stop.is_set():
                try:
                    self.ingest_once()
                    backoff = 1
                except TelegramRateLimitError as exc:
                    print(str(exc), file=sys.stderr)
                    delay = _telegram_notice_rate_limit_delay(exc.retry_after)
                    self._mark_runtime_lane(
                        "intake",
                        "backoff",
                        "rate-limit",
                        resume_after_seconds=delay,
                    )
                    self._wait(delay)
                except TelegramAPIError as exc:
                    if not exc.retryable:
                        raise
                    print(str(exc), file=sys.stderr)
                    self._mark_runtime_lane(
                        "intake",
                        "backoff",
                        "retry",
                        resume_after_seconds=backoff,
                    )
                    self._wait(backoff)
                    backoff = min(60, backoff * 2)
                except TelegramUpdateRetryError as exc:
                    print(str(exc), file=sys.stderr)
                    self._mark_runtime_lane(
                        "intake",
                        "backoff",
                        "retry",
                        resume_after_seconds=backoff,
                    )
                    self._wait(backoff)
                    backoff = min(60, backoff * 2)
        finally:
            self.stop()
            if worker_thread.ident is not None:
                worker_thread.join()
            if heartbeat_thread.ident is not None:
                heartbeat_thread.join()
            try:
                self._bind_runtime_progress(None)
            finally:
                self._worker.enable_runtime_leases(False)
                self._runtime_leases_enabled = False
                self._state.mark_worker_stopped()
        if self._worker_error is not None:
            raise self._worker_error

    def stop(self) -> None:
        """Stop intake and let the active job finish at its durable checkpoint."""
        self._stop.set()
        self._wake.set()

    def _worker_main(self) -> None:
        try:
            self._worker.run_forever()
        except BaseException as exc:
            self._worker_error = exc
            try:
                # The supervisor can still pulse while intake unwinds from its
                # current long poll. Publish the known worker failure first so
                # health cannot remain falsely ready during that window.
                self._mark_runtime_lane("worker", "stopped", "stopped")
            except BaseException:
                # Preserve the original worker failure. A heartbeat write
                # failure is independently visible as unreadable/stale health.
                pass
            self._stop.set()
            self._wake.set()

    def _heartbeat_main(self) -> None:
        while not self._stop.wait(_HEARTBEAT_INTERVAL_SECONDS):
            try:
                self._state.mark_worker_heartbeat()
            except BaseException as exc:
                self._worker_error = exc
                self._stop.set()
                self._wake.set()
                return

    def _bind_runtime_progress(
        self,
        reporter: Callable[[str], None] | None,
    ) -> None:
        binder = getattr(self._service, "bind_runtime_progress", None)
        if callable(binder):
            binder(reporter)

    def _renew_worker_runtime(self, operation: str) -> None:
        timeout = _RUNTIME_WORKER_OPERATION_TIMEOUTS.get(operation)
        if timeout is None:
            operation = "job"
            timeout = _RUNTIME_WORKER_OPERATION_TIMEOUTS[operation]
        self._mark_runtime_lane(
            "worker",
            "active",
            operation,
            timeout_seconds=timeout,
        )

    def _mark_intake_dispatch(self) -> None:
        self._mark_runtime_lane(
            "intake",
            "active",
            "dispatch",
            timeout_seconds=_RUNTIME_INTAKE_DISPATCH_TIMEOUT_SECONDS,
        )

    def _mark_runtime_lane(
        self,
        lane: str,
        state: str,
        operation: str,
        *,
        timeout_seconds: float = 0.0,
        resume_after_seconds: float = 0.0,
    ) -> None:
        if not self._runtime_leases_enabled:
            return
        marker = getattr(self._state, "mark_runtime_lane", None)
        if callable(marker):
            marker(
                lane,
                state,
                operation,
                timeout_seconds=timeout_seconds,
                resume_after_seconds=resume_after_seconds,
            )

    def _normalize(self, update: dict[str, Any]) -> dict[str, Any] | None:
        normalizer = getattr(self._service, "normalize_update", None)
        if callable(normalizer):
            value = normalizer(update)
            return value if isinstance(value, dict) else None
        # Compatibility for small embedders that implement only handle_update.
        return update

    def _handle_intake_control(self, update: dict[str, Any]) -> bool:
        """Run one state-only control without entering the model/build FIFO."""
        handler = getattr(self._service, "handle_intake_control", None)
        return bool(handler(update)) if callable(handler) else False

    def _deliver_acceptance_receipt(
        self,
        update_id: int,
        *,
        rate_limit_scope: list[int] | None = None,
    ) -> float | None:
        """Resolve one due receipt without exposing or rehydrating its request."""
        defer_scope = rate_limit_scope or [update_id]
        paused_for = self._acceptance_receipts_paused_until - time.time()
        if paused_for > 0:
            self._state.defer_acceptance_receipts(
                defer_scope,
                delay_seconds=paused_for,
            )
            return paused_for
        notice = self._state.acceptance_receipt_notice(update_id)
        if not notice:
            return None
        announcer = getattr(self._service, "announce_acceptance_receipt", None)
        if not callable(announcer):
            self._state.complete_acceptance_receipt(
                update_id,
                delivered=False,
            )
            self._wake.set()
            return None
        try:
            delivered = announcer(notice)
        except TelegramRateLimitError as exc:
            delay = _telegram_notice_rate_limit_delay(exc.retry_after)
            self._acceptance_receipts_paused_until = time.time() + delay
            self._state.defer_acceptance_receipts(
                defer_scope,
                delay_seconds=delay,
            )
            return delay
        except TelegramAPIError as exc:
            if exc.retryable:
                self._state.defer_acceptance_receipt(
                    update_id,
                    delay_seconds=30,
                )
            else:
                self._state.complete_acceptance_receipt(
                    update_id,
                    delivered=False,
                )
                self._wake.set()
        except Exception:
            # Persist neither request text nor raw API/internal diagnostics.
            print("Could not send Telegram acceptance receipt.", file=sys.stderr)
            self._state.defer_acceptance_receipt(
                update_id,
                delay_seconds=60,
            )
        else:
            self._state.complete_acceptance_receipt(
                update_id,
                delivered=delivered is not False,
            )
            self._wake.set()
        return None

    def _drain_acceptance_receipts(self) -> None:
        """Retry due receipts from state without relying on Telegram replay."""
        due = self._state.due_acceptance_receipt_ids()
        for index, update_id in enumerate(due):
            # Each send can consume the full HTTP timeout. Renew between
            # independent receipts so a progressing bounded drain never looks
            # like one stalled dispatch operation.
            self._mark_intake_dispatch()
            paused_for = self._deliver_acceptance_receipt(
                update_id,
                rate_limit_scope=due[index:],
            )
            if paused_for is None:
                continue
            break

    def _deliver_queue_full_notice(
        self,
        update_id: int,
        update: dict[str, Any] | None = None,
        *,
        rate_limit_scope: list[int] | None = None,
    ) -> float | None:
        """Deliver and checkpoint one request-free capacity refusal notice."""
        due = self._state.due_queue_full_notice_ids()
        if update_id not in due:
            return None
        defer_scope = rate_limit_scope or [update_id]
        paused_for = self._capacity_notices_paused_until - time.time()
        if paused_for > 0:
            self._state.defer_queue_full_notices(
                defer_scope,
                delay_seconds=paused_for,
            )
            return paused_for
        try:
            notice = self._state.queue_full_notice(update_id)
            if not notice:
                return None
            announcer = getattr(self._service, "announce_queue_full_notice", None)
            legacy_announcer = getattr(self._service, "announce_queue_full", None)
            if callable(announcer):
                delivered = announcer(notice)
                if delivered is False:
                    self._state.complete_queue_full_notice(
                        update_id,
                        delivered=False,
                    )
                    return None
            elif callable(legacy_announcer):
                legacy_announcer(update or {
                    "update_id": update_id,
                    "message": {
                        "from": {
                            "id": notice["user_id"],
                            "is_bot": False,
                        },
                        "chat": {
                            "id": notice["chat_id"],
                            "type": notice["chat_type"],
                        },
                    },
                })
            else:
                self._state.complete_queue_full_notice(update_id, delivered=False)
                return None
        except TelegramRateLimitError as exc:
            delay = _telegram_notice_rate_limit_delay(exc.retry_after)
            self._capacity_notices_paused_until = time.time() + delay
            self._state.defer_queue_full_notices(
                defer_scope,
                delay_seconds=delay,
            )
            return delay
        except TelegramAPIError as exc:
            if exc.retryable:
                self._state.defer_queue_full_notice(
                    update_id,
                    delay_seconds=30,
                )
            else:
                self._state.complete_queue_full_notice(update_id, delivered=False)
        except Exception:
            print("Could not send Telegram queue-full notice.", file=sys.stderr)
            self._state.defer_queue_full_notice(
                update_id,
                delay_seconds=60,
            )
        else:
            self._state.complete_queue_full_notice(update_id, delivered=True)
        return None

    def _drain_queue_full_notices(self) -> None:
        """Retry durable refusal notices without relying on Telegram replay."""
        due = self._state.due_queue_full_notice_ids()
        for index, update_id in enumerate(due):
            self._deliver_queue_full_notice(
                update_id,
                rate_limit_scope=due[index:],
            )
            # Bound pre-poll work even when Telegram is healthy. Remaining
            # notices stay durable and another loop will drain the next one.
            break
