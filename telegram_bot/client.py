"""Small dependency-free client for the Telegram Bot HTTP API."""
from __future__ import annotations

import http.client
import json
import re
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any, Protocol

_DEFAULT_RATE_LIMIT_DELAY = 5


class TelegramAPIError(RuntimeError):
    """A sanitized Telegram API failure that never contains the bot token."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int = 0,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class TelegramRateLimitError(TelegramAPIError):
    def __init__(self, retry_after: int) -> None:
        super().__init__(
            f"Telegram rate limit; retry after {retry_after} seconds",
            retryable=True,
            status_code=429,
        )
        self.retry_after = retry_after


class TelegramClient(Protocol):
    def get_me(self) -> dict[str, Any]: ...
    def delete_webhook(self, *, drop_pending_updates: bool = False) -> bool: ...
    def get_updates(self, *, offset: int | None, timeout: int) -> list[dict[str, Any]]: ...
    def send_message(self, chat_id: int, text: str) -> list[dict[str, Any]]: ...


class HttpTelegramClient:
    """Telegram Bot API client using only the Python standard library."""

    def __init__(self, token: str, *, api_root: str = "https://api.telegram.org") -> None:
        clean_token = token.strip()
        if not clean_token:
            raise ValueError("Telegram bot token is required")
        # BotFather tokens use digits, letters, colon, underscore, and hyphen.
        # Reject URL-breaking characters before the token is ever interpolated
        # into a Request, where urllib's exception can include the full URL.
        if len(clean_token) > 256 or not re.fullmatch(r"[A-Za-z0-9:_-]+", clean_token):
            raise ValueError("Telegram bot token contains invalid characters")
        self._token = clean_token
        self._base = f"{api_root.rstrip('/')}/bot{self._token}"

    def get_me(self) -> dict[str, Any]:
        result = self._request("getMe", {})
        return result if isinstance(result, dict) else {}

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> bool:
        return bool(self._request("deleteWebhook", {
            "drop_pending_updates": drop_pending_updates,
        }))

    def get_updates(self, *, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = self._request("getUpdates", payload, timeout=timeout + 10)
        if not isinstance(result, list):
            return []
        return [item for item in result if isinstance(item, dict)]

    def send_message(self, chat_id: int, text: str) -> list[dict[str, Any]]:
        sent: list[dict[str, Any]] = []
        for chunk in split_message(text):
            result = self._request("sendMessage", {"chat_id": chat_id, "text": chunk})
            sent.append(result if isinstance(result, dict) else {})
        return sent

    def _request(
        self,
        method: str,
        payload: Mapping[str, Any],
        *,
        timeout: int = 30,
    ) -> Any:
        body = json.dumps(dict(payload)).encode("utf-8")
        try:
            request = urllib.request.Request(
                f"{self._base}/{method}",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            parsed = _read_error(exc)
            retry = _retry_after(parsed)
            if retry or exc.code == 429:
                raise TelegramRateLimitError(
                    retry or _DEFAULT_RATE_LIMIT_DELAY
                ) from None
            raise TelegramAPIError(
                f"Telegram {method} failed with HTTP {exc.code}",
                retryable=exc.code in {408, 425} or exc.code >= 500,
                status_code=exc.code,
            ) from None
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            TimeoutError,
            OSError,
            ValueError,
        ) as exc:
            raise TelegramAPIError(
                f"Telegram {method} failed: {type(exc).__name__}",
                retryable=True,
            ) from None

        if not isinstance(parsed, dict) or not parsed.get("ok"):
            retry = _retry_after(parsed)
            status_code = _error_code(parsed)
            if retry or status_code == 429:
                raise TelegramRateLimitError(retry or _DEFAULT_RATE_LIMIT_DELAY)
            description = (
                str(parsed.get("description", "unknown error"))
                if isinstance(parsed, dict)
                else "invalid response"
            ).replace(self._token, "[REDACTED]")
            raise TelegramAPIError(
                f"Telegram {method} failed: {description[:200]}",
                retryable=status_code in {408, 425} or status_code >= 500,
                status_code=status_code,
            )
        return parsed.get("result")


def split_message(text: str, limit: int = 4096) -> list[str]:
    """Split Telegram text without dropping characters or exceeding its limit."""
    remaining = str(text or "")
    if not remaining:
        return [" "]
    chunks: list[str] = []
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit + 1)
        if cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit + 1)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        chunks.append(remaining)
    return chunks


def _read_error(exc: urllib.error.HTTPError) -> Any:
    try:
        return json.loads(exc.read().decode("utf-8"))
    except (OSError, ValueError):
        return {}


def _retry_after(payload: Any) -> int:
    if not isinstance(payload, dict):
        return 0
    parameters = payload.get("parameters")
    if not isinstance(parameters, dict):
        return 0
    try:
        return max(0, int(parameters.get("retry_after", 0)))
    except (TypeError, ValueError):
        return 0


def _error_code(payload: Any) -> int:
    if not isinstance(payload, dict):
        return 0
    try:
        return max(0, int(payload.get("error_code", 0)))
    except (TypeError, ValueError):
        return 0
