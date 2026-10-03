"""OpenAI provider implementation."""
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

_DEFAULT_MODEL = "gpt-4o-mini"
_PROVIDER_NAME = "openai"

_PLAN_SYSTEM = (
    "You are an engineering planning assistant. "
    "Produce clear, numbered, actionable plans. Be specific and prioritise by impact."
)
_REVIEW_SYSTEM = (
    "You are a senior software engineer conducting a code or design review. "
    "Be concise, specific, and constructive."
)


class OpenAIProvider(AIProvider):
    """
    AI provider backed by OpenAI models via the `openai` SDK.

    Requires OPENAI_API_KEY in the environment or explicit api_key in config.
    Compatible with any OpenAI-API-compatible endpoint (e.g. Azure, LM Studio)
    via the base_url config field.
    """

    provider_name = _PROVIDER_NAME
    default_model = _DEFAULT_MODEL
    api_key_env = "OPENAI_API_KEY"
    default_base_url: str | None = None
    display_name = "OpenAI"

    def __init__(self, config: "ProviderConfig") -> None:
        self._model = config.model or self.default_model
        self._api_key = config.api_key or os.environ.get(self.api_key_env, "")
        self._base_url = config.base_url or self.default_base_url
        self._max_tokens = config.max_tokens
        self._connect_timeout = config.connect_timeout
        self._generation_timeout = config.generation_timeout

    @property
    def name(self) -> str:
        return self.provider_name

    @property
    def is_local(self) -> bool:
        return False

    @property
    def cost_tier(self) -> int:
        return 2  # hosted, but defaults to an economical model (gpt-4o-mini)

    def _request_timeout(self, max_tokens: int) -> Any:
        """
        Connect and read deadlines for one request.

        The SDK speaks httpx, so connect and read bind separately and correctly.
        **`read` is an inter-chunk deadline, not a total one**: it bounds silence,
        not the whole answer, so a stream that keeps producing may run longer than
        `read` seconds in total. That is a different guarantee from Ollama's, and
        it is the right one here -- a hosted stream that goes quiet is dead, while
        one still emitting tokens is working.

        Falls back to a single float when httpx is absent, which loses the
        separation but keeps the deadline.
        """
        deadline = self.generation_deadline(max_tokens, self._generation_timeout)
        try:
            import httpx
        except ImportError:
            return deadline
        return httpx.Timeout(
            deadline, connect=self._connect_timeout, write=self._connect_timeout
        )

    @property
    def tokens_per_second(self) -> float:
        """
        Conservative for a hosted model, which is faster in practice.
        """
        return 25.0

    @property
    def capability_tier(self) -> int:
        return 2

    @property
    def reports_stop_reason(self) -> bool:
        """This provider reports why generation stopped, so truncation is detectable."""
        return True

    def availability(self) -> ProviderAvailability:
        """Ready only when the `openai` SDK is importable and a key is set."""
        import importlib

        try:
            importlib.import_module("openai")
        except ImportError:
            return ProviderAvailability(
                available=False, provider=self.name, model=self._model,
                reason="openai SDK not installed",
                env_var=self.api_key_env, install_hint="pip install openai",
            )
        if not self._api_key:
            return ProviderAvailability(
                available=False, provider=self.name, model=self._model,
                reason=f"{self.api_key_env} is not set", env_var=self.api_key_env,
            )
        return ProviderAvailability(
            available=True, provider=self.name, model=self._model,
            reason="ready", env_var=self.api_key_env,
        )

    def ask(
        self,
        prompt: str,
        context: str = "",
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> ProviderResponse:
        messages = _build_messages(prompt, context)
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
        messages = [
            {"role": "system", "content": _PLAN_SYSTEM},
            {"role": "user", "content": user_content},
        ]
        return self._call(messages, max_tokens=max_tokens)

    def summarize(
        self,
        content: str,
        max_words: int = 150,
        **kwargs: Any,
    ) -> ProviderResponse:
        messages = [
            {
                "role": "system",
                "content": (
                    f"Summarize content in {max_words} words or fewer. "
                    "Be direct and factual — no preamble."
                ),
            },
            {"role": "user", "content": content},
        ]
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
        messages = [
            {"role": "system", "content": _REVIEW_SYSTEM},
            {"role": "user", "content": user_content},
        ]
        return self._call(messages, max_tokens=1024)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
    ) -> ProviderResponse:
        try:
            import openai as _openai
        except ImportError as exc:
            raise ProviderError(
                "openai package not installed. Run: pip install openai"
            ) from exc

        try:
            client_kwargs: dict[str, Any] = {"api_key": self._api_key}
            if self._base_url:
                client_kwargs["base_url"] = self._base_url
            client = _openai.OpenAI(**client_kwargs)

            response = client.chat.completions.create(
                model=self._model,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=max_tokens,
                timeout=self._request_timeout(max_tokens),
            )
            choice = response.choices[0] if response.choices else None
            content = choice.message.content or "" if choice else ""
            tokens = 0
            if response.usage:
                tokens = response.usage.total_tokens
            # finish_reason == "length" means the model was cut off mid-answer.
            # Callers need this: a truncated response may be missing its trailing
            # structured verdict, which must never be read as approval.
            finish_reason = str(getattr(choice, "finish_reason", "") or "") if choice else ""
            return ProviderResponse(
                content=content,
                model=response.model,
                provider=self.name,
                tokens_used=tokens,
                metadata={
                    "stop_reason": finish_reason,
                    "truncated": finish_reason == "length",
                },
            )

        except _openai.AuthenticationError as exc:
            raise ProviderAuthError(f"{self.display_name} auth failed: {exc}") from exc
        except _openai.RateLimitError as exc:
            raise ProviderRateLimitError(f"{self.display_name} rate limit: {exc}") from exc
        except _openai.APIConnectionError as exc:
            raise ProviderUnavailableError(f"{self.display_name} unreachable: {exc}") from exc
        except (_openai.APIStatusError, Exception) as exc:
            raise ProviderError(f"{self.display_name} error: {exc}") from exc


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _build_messages(prompt: str, context: str) -> list[dict[str, str]]:
    if context:
        return [{"role": "user", "content": f"Context:\n{context}\n\nQuestion: {prompt}"}]
    return [{"role": "user", "content": prompt}]
