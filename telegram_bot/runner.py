"""Durable Telegram intake and sequential MondayOS job execution."""
from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from typing import Any

from telegram_bot.client import TelegramAPIError, TelegramClient, TelegramRateLimitError
from telegram_bot.service import TelegramBotService, TelegramUpdatePendingError
from telegram_bot.state import TelegramState

_MAX_HANDLER_ATTEMPTS = 3
_HEARTBEAT_INTERVAL_SECONDS = 30.0


class TelegramUpdateRetryError(RuntimeError):
    """A queued handler failed but its durable checkpoint remains retryable."""


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

            created, position = self._state.enqueue(update_id, normalized)
            if created:
                self._announce_queued(normalized, position)
                self._wake.set()
            handled += 1
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

    def _announce_queued(self, update: dict[str, Any], position: int) -> None:
        announcer = getattr(self._service, "announce_queued", None)
        if not callable(announcer):
            return
        try:
            announcer(update, position)
        except TelegramAPIError:
            # The request and offset are already durable. Execution will retry
            # its own messages, so a best-effort receipt cannot discard work.
            pass
        except Exception:
            print("Could not send Telegram queue acknowledgement.", file=sys.stderr)
