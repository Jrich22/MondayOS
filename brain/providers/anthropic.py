"""Anthropic (Claude) provider implementation."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from brain.providers.base import (
    AIProvider,
    ProviderAuthError,
    ProviderAvailability,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponse,
    ProviderUnavailableError,
)

if TYPE_CHECKING:
    from brain.providers.factory import ProviderConfig

_DEFAULT_MODEL = "claude-sonnet-4-6"
_PROVIDER_NAME = "anthropic"

_PLAN_SYSTEM = (
    "You are an engineering planning assistant. "
    "Produce clear, numbered, actionable plans. "
    "Be specific and prioritise by impact."
)
_REVIEW_SYSTEM = (
    "You are a senior software engineer conducting a code or design review. "
    "Be concise, specific, and constructive."
)


class AnthropicProvider(AIProvider):
    """
    AI provider backed by Anthropic's Claude models via the `anthropic` SDK.

    Requires ANTHROPIC_API_KEY in the environment or explicit api_key in config.
    """

    def __init__(self, config: "ProviderConfig") -> None:
        self._model = config.model or _DEFAULT_MODEL
        self._api_key = config.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._max_tokens = config.max_tokens
        self._timeout = config.timeout

    @property
    def name(self) -> str:
        return _PROVIDER_NAME

    @property
    def is_local(self) -> bool:
        return False

    @property
    def cost_tier(self) -> int:
        return 3  # frontier hosted model — highest cost tier

    @property
    def capability_tier(self) -> int:
        return 3  # highest capability tier

    def availability(self) -> ProviderAvailability:
        """Ready only when the `anthropic` SDK is importable and a key is set."""
        import importlib

        try:
            importlib.import_module("anthropic")
        except ImportError:
            return ProviderAvailability(
                available=False, provider=_PROVIDER_NAME, model=self._model,
                reason="anthropic SDK not installed",
                env_var="ANTHROPIC_API_KEY", install_hint="pip install anthropic",
            )
        if not self._api_key:
            return ProviderAvailability(
                available=False, provider=_PROVIDER_NAME, model=self._model,
                reason="ANTHROPIC_API_KEY is not set", env_var="ANTHROPIC_API_KEY",
            )
        return ProviderAvailability(
            available=True, provider=_PROVIDER_NAME, model=self._model,
            reason="ready", env_var="ANTHROPIC_API_KEY",
        )

    def ask(
        self,
        prompt: str,
        context: str = "",
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> ProviderResponse:
        messages = _user_messages(prompt, context)
        return self._call(messages, max_tokens=max_tokens)

    def plan(
        self,
        objective: str,
        context: str = "",
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> ProviderResponse:
        user_content = f"Create a step-by-step plan for: {objective}"
        if context:
            user_content = f"Context:\n{context}\n\n{user_content}"
        messages = [{"role": "user", "content": user_content}]
        return self._call(messages, system=_PLAN_SYSTEM, max_tokens=max_tokens)

    def summarize(
        self,
        content: str,
        max_words: int = 150,
        **kwargs: Any,
    ) -> ProviderResponse:
        user_content = (
            f"Summarize the following in {max_words} words or fewer. "
            f"Be direct and factual — no preamble.\n\n{content}"
        )
        messages = [{"role": "user", "content": user_content}]
        return self._call(messages, max_tokens=max_words * 2)

    def review(
        self,
        content: str,
        criteria: list[str] | None = None,
        **kwargs: Any,
    ) -> ProviderResponse:
        if criteria:
            criteria_text = "\n".join(f"- {c}" for c in criteria)
            user_content = (
                f"Review the following against these criteria:\n"
                f"{criteria_text}\n\nContent:\n{content}"
            )
        else:
            user_content = (
                f"Review the following for quality, correctness, and completeness:\n\n{content}"
            )
        messages = [{"role": "user", "content": user_content}]
        return self._call(messages, system=_REVIEW_SYSTEM, max_tokens=1024)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call(
        self,
        messages: list[dict[str, str]],
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> ProviderResponse:
        try:
            import anthropic as _anthropic
        except ImportError as exc:
            raise ProviderError(
                "anthropic package not installed. Run: pip install anthropic"
            ) from exc

        try:
            client = _anthropic.Anthropic(api_key=self._api_key)
            call_kwargs: dict[str, Any] = {
                "model": self._model,
                "max_tokens": max_tokens,
                "messages": messages,
            }
            if system:
                call_kwargs["system"] = system

            response = client.messages.create(**call_kwargs)
            content = response.content[0].text if response.content else ""
            tokens = 0
            if response.usage:
                tokens = response.usage.input_tokens + response.usage.output_tokens
            return ProviderResponse(
                content=content,
                model=response.model,
                provider=_PROVIDER_NAME,
                tokens_used=tokens,
            )

        except _anthropic.AuthenticationError as exc:
            raise ProviderAuthError(f"Anthropic auth failed: {exc}") from exc
        except _anthropic.RateLimitError as exc:
            raise ProviderRateLimitError(f"Anthropic rate limit: {exc}") from exc
        except _anthropic.APIConnectionError as exc:
            raise ProviderUnavailableError(f"Anthropic unreachable: {exc}") from exc
        except (_anthropic.APIStatusError, Exception) as exc:
            raise ProviderError(f"Anthropic error: {exc}") from exc


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _user_messages(prompt: str, context: str) -> list[dict[str, str]]:
    if context:
        content = f"Context:\n{context}\n\nQuestion: {prompt}"
    else:
        content = prompt
    return [{"role": "user", "content": content}]
