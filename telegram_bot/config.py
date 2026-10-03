"""Environment-only configuration for the MondayOS Telegram bot."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


def _ids(value: str) -> frozenset[int]:
    items: set[int] = set()
    for raw in value.replace(" ", ",").split(","):
        token = raw.strip()
        if not token:
            continue
        try:
            items.add(int(token))
        except ValueError as exc:
            raise ValueError(f"Telegram allowlist contains a non-numeric id: {token!r}") from exc
    return frozenset(items)


@dataclass(frozen=True)
class TelegramConfig:
    """Validated bot settings. The token is excluded from repr and logs."""

    token: str = field(repr=False)
    allowed_user_ids: frozenset[int]
    project_root: Path
    allowed_chat_ids: frozenset[int] = field(default_factory=frozenset)
    provider: str = ""
    poll_timeout: int = 30

    @property
    def state_path(self) -> Path:
        return self.project_root / "logs" / "telegram" / "state.json"

    @property
    def lock_path(self) -> Path:
        return self.project_root / "logs" / "telegram" / "bot.lock"

    @classmethod
    def from_env(
        cls,
        project_root: Path,
        environ: Mapping[str, str] | None = None,
    ) -> TelegramConfig:
        env = environ if environ is not None else os.environ
        token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
        users = _ids(
            env.get("MONDAYOS_TELEGRAM_ALLOWED_USER_IDS")
            or env.get("TELEGRAM_ALLOWED_USER_IDS")
            or ""
        )
        chats = _ids(env.get("MONDAYOS_TELEGRAM_ALLOWED_CHAT_IDS") or "")
        provider = (env.get("MONDAYOS_TELEGRAM_PROVIDER") or "").strip().lower()
        raw_timeout = (env.get("MONDAYOS_TELEGRAM_POLL_TIMEOUT") or "30").strip()
        try:
            timeout = int(raw_timeout)
        except ValueError as exc:
            raise ValueError("MONDAYOS_TELEGRAM_POLL_TIMEOUT must be an integer") from exc

        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        if not users:
            raise ValueError("MONDAYOS_TELEGRAM_ALLOWED_USER_IDS is required")
        if any(user_id <= 0 for user_id in users):
            raise ValueError("MONDAYOS_TELEGRAM_ALLOWED_USER_IDS must contain positive IDs")
        if 0 in chats:
            raise ValueError("MONDAYOS_TELEGRAM_ALLOWED_CHAT_IDS cannot contain zero")
        if not 1 <= timeout <= 50:
            raise ValueError("MONDAYOS_TELEGRAM_POLL_TIMEOUT must be between 1 and 50")

        return cls(
            token=token,
            allowed_user_ids=users,
            allowed_chat_ids=chats,
            project_root=Path(project_root).resolve(),
            provider=provider,
            poll_timeout=timeout,
        )

    def safe_summary(self) -> dict[str, object]:
        """Configuration safe for status output; deliberately excludes token."""
        return {
            "allowed_user_count": len(self.allowed_user_ids),
            "allowed_chat_count": len(self.allowed_chat_ids),
            "project_root": str(self.project_root),
            "provider": self.provider or "role-defaults-with-fallback",
            "poll_timeout": self.poll_timeout,
        }
