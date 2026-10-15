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
_MAX_ACCEPTANCE_RECEIPT_PAUSE_SECONDS = 86_400


class TelegramUpdateRetryError(RuntimeError):
    """A queued handler failed but its durable checkpoint remains retryable."""


def _acceptance_receipt_rate_limit_delay(value: Any) -> float:
    """Bound an untrusted Bot API retry hint before float/time arithmetic."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 1.0
    return float(min(max(value, 1), _MAX_ACCEPTANCE_RECEIPT_PAUSE_SECONDS))


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

    def run_once(self) -> bool:
        """Process one claimed request, returning false when the inbox is empty."""
        claimed = self._state.claim_next()
        if claimed is None:
            return False
        update_id, update = claimed
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
        except TelegramRateLimitError as exc:
            self._state.defer_rate_limited_follow_up(
                update_id,
                retry_after=exc.retry_after,
            )
        except TelegramAPIError as exc:
            if exc.retryable:
                self._state.defer_follow_up(update_id, delay_seconds=60)
            else:
                # A blocked bot or deleted chat will never recover by polling.
                # Stop this watcher without retaining Telegram's diagnostic.
                self._state.mark_follow_up_notification_failed(update_id)
        except Exception:
            # Observation failures are deliberately isolated from accepted
            # work. The tombstone remains restart-safe and retains no raw
            # remote diagnostic text.
            self._state.defer_follow_up(update_id, delay_seconds=60)
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
                delay_seconds=exc.retry_after,
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
                pending_delivery = False
                try:
                    worked = self.run_once()
                except TelegramUpdatePendingError:
                    # One long-running FIFO delivery must not starve durable
                    # retry notifications or PR lifecycle observation.
                    worked = False
                    pending_delivery = True
                retried = self.run_delivery_retry_once()
                followed_up = self.run_follow_up_once()
                if worked or retried or followed_up:
                    backoff = 1
                    continue
                if pending_delivery:
                    self._wait(backoff)
                    backoff = min(60, backoff * 2)
                    continue
                delay = self._state.next_follow_up_delay()
                self._wake.wait(timeout=delay)
                self._wake.clear()
            except TelegramRateLimitError as exc:
                print(str(exc), file=sys.stderr)
                self._wait(max(1, exc.retry_after))
            except TelegramAPIError as exc:
                if not exc.retryable:
                    raise
                print(str(exc), file=sys.stderr)
                self._wait(backoff)
                backoff = min(60, backoff * 2)
            except TelegramUpdateRetryError as exc:
                print(str(exc), file=sys.stderr)
                self._wait(backoff)
                backoff = min(60, backoff * 2)


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
        self._drain_queue_full_notices()
        # Accepted-job receipts use their own request-free outbox. Failures are
        # isolated here so polling can still accept /queue and /cancel controls.
        self._drain_acceptance_receipts()
        updates = self._client.get_updates(
            offset=self._state.next_offset,
            timeout=self._poll_timeout,
        )
        handled = 0
        for update in sorted(updates, key=lambda item: int(item.get("update_id", -1))):
            update_id = int(update.get("update_id", -1))
            if update_id < 0:
                continue
            if self._state.is_terminal(update_id):
                if self._state.queue_full_notice(update_id):
                    self._deliver_queue_full_notice(update_id, update)
                else:
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
                if tombstone.get("phase") == "queue-full":
                    self._deliver_queue_full_notice(update_id, normalized)
            handled += 1
        self._drain_acceptance_receipts()
        return handled

    def run_once(self) -> int:
        """Ingest one batch and drain it synchronously for CLI setup/tests."""
        handled = self.ingest_once()
        while self._worker.run_once():
            pass
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
        self._wake.set()  # Recover jobs already durable before this process.
        worker_thread.start()
        heartbeat_thread.start()
        backoff = 1
        try:
            while not self._stop.is_set():
                try:
                    self.ingest_once()
                    backoff = 1
                except TelegramRateLimitError as exc:
                    print(str(exc), file=sys.stderr)
                    self._wait(max(1, exc.retry_after))
                except TelegramAPIError as exc:
                    if not exc.retryable:
                        raise
                    print(str(exc), file=sys.stderr)
                    self._wait(backoff)
                    backoff = min(60, backoff * 2)
                except TelegramUpdateRetryError as exc:
                    print(str(exc), file=sys.stderr)
                    self._wait(backoff)
                    backoff = min(60, backoff * 2)
        finally:
            self.stop()
            worker_thread.join()
            heartbeat_thread.join()
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
            self._stop.set()

    def _heartbeat_main(self) -> None:
        while not self._stop.wait(_HEARTBEAT_INTERVAL_SECONDS):
            try:
                self._state.mark_worker_heartbeat()
            except BaseException as exc:
                self._worker_error = exc
                self._stop.set()
                self._wake.set()
                return

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
            delay = _acceptance_receipt_rate_limit_delay(exc.retry_after)
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
    ) -> None:
        """Deliver and checkpoint one request-free capacity refusal notice."""
        try:
            notice = self._state.queue_full_notice(update_id)
            if not notice:
                self._state.reaffirm_terminal(update_id)
                return
            announcer = getattr(self._service, "announce_queue_full_notice", None)
            legacy_announcer = getattr(self._service, "announce_queue_full", None)
            if callable(announcer):
                delivered = announcer(notice)
                if delivered is False:
                    self._state.complete_queue_full_notice(
                        update_id,
                        delivered=False,
                    )
                    return
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
                return
        except TelegramAPIError as exc:
            if exc.retryable:
                # The durable tombstone deliberately leaves the Telegram
                # offset behind this update so a restart/redelivery retries
                # the same request-free notice without re-admitting the job.
                raise
            self._state.complete_queue_full_notice(update_id, delivered=False)
        except Exception:
            print("Could not send Telegram queue-full notice.", file=sys.stderr)
            self._state.complete_queue_full_notice(update_id, delivered=False)
        else:
            self._state.complete_queue_full_notice(update_id, delivered=True)

    def _drain_queue_full_notices(self) -> None:
        """Retry durable refusal notices without relying on Telegram replay."""
        for update_id in self._state.pending_queue_full_notice_ids():
            self._deliver_queue_full_notice(update_id)
