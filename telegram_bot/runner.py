"""Long-polling lifecycle for the MondayOS Telegram control plane."""
from __future__ import annotations

import sys
import threading
from collections.abc import Callable

from telegram_bot.client import TelegramAPIError, TelegramClient, TelegramRateLimitError
from telegram_bot.service import TelegramBotService
from telegram_bot.state import TelegramState

_MAX_HANDLER_ATTEMPTS = 3


class TelegramUpdateRetryError(RuntimeError):
    """An update handler failed but its durable checkpoint remains retryable."""


class TelegramRunner:
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

    def run_once(self) -> int:
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
                self._service.handle_update(update)
            except TelegramAPIError as exc:
                if exc.retryable:
                    raise
                # A permanent send failure (for example, the user blocked the
                # bot) must not wedge every later update in the queue.
                self._state.dead_letter(update_id)
                handled += 1
                continue
            except Exception:
                attempts = self._state.record_failure(update_id)
                will_retry = attempts < _MAX_HANDLER_ATTEMPTS
                if will_retry:
                    # A failure to send the courtesy notice must not bypass the
                    # handler's own bounded retry lifecycle. Transient Telegram
                    # failures still bubble to the polling backoff; a permanent
                    # send failure is ignored while the update stays retryable.
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
                # Preserve and advance first. A user who blocked the bot must
                # not prevent this poison update from ever reaching its bound.
                self._state.dead_letter(update_id)
                try:
                    self._service.report_error(update, will_retry=False)
                except TelegramAPIError:
                    pass
                handled += 1
                continue
            self._state.acknowledge(update_id)
            handled += 1
        return handled

    def run_forever(self) -> None:
        backoff = 1
        while not self._stop.is_set():
            try:
                self.run_once()
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

    def stop(self) -> None:
        self._stop.set()
