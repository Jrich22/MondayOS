"""Ollama local model provider implementation.

Uses the Ollama HTTP REST API directly (no SDK). Requires a running Ollama
service at the configured base_url (default: http://localhost:11434).
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from brain.providers.base import (
    AIProvider,
    ProviderError,
    ProviderResponse,
    ProviderUnavailableError,
)

if TYPE_CHECKING:
    from brain.providers.factory import ProviderConfig

_DEFAULT_MODEL = "llama3"
_DEFAULT_BASE_URL = "http://localhost:11434"
_PROVIDER_NAME = "ollama"

_PLAN_PREFIX = (
    "You are an engineering planning assistant. "
    "Produce clear, numbered, actionable plans.\n\n"
)
_REVIEW_PREFIX = (
    "You are a senior software engineer. "
    "Be concise, specific, and constructive.\n\n"
)


class OllamaProvider(AIProvider):
    """
    AI provider backed by a locally-running Ollama service.

    No API key required. Requires Ollama running at base_url
    (default: http://localhost:11434). Uses the /api/chat endpoint
    which supports structured message roles.

    Configure via ProviderConfig(type="ollama", model="llama3",
    base_url="http://localhost:11434").
    """

    def __init__(self, config: "ProviderConfig") -> None:
        self._model = config.model or _DEFAULT_MODEL
        self._base_url = (config.base_url or _DEFAULT_BASE_URL).rstrip("/")
        self._timeout = config.timeout
        self._options = config.extra.get("options", {})

    @property
    def name(self) -> str:
        return _PROVIDER_NAME

    def ask(
        self,
        prompt: str,
        context: str = "",
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> ProviderResponse:
        messages = _user_messages(prompt, context)
        return self._chat(messages, max_tokens=max_tokens)

    def plan(
        self,
        objective: str,
        context: str = "",
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> ProviderResponse:
        user_content = _PLAN_PREFIX + f"Create a step-by-step plan for: {objective}"
        if context:
            user_content = f"Context:\n{context}\n\n{user_content}"
        messages = [{"role": "user", "content": user_content}]
        return self._chat(messages, max_tokens=max_tokens)

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
        return self._chat(messages, max_tokens=max_words * 2)

    def review(
        self,
        content: str,
        criteria: list[str] | None = None,
        **kwargs: Any,
    ) -> ProviderResponse:
        if criteria:
            criteria_text = "\n".join(f"- {c}" for c in criteria)
            user_content = (
                f"{_REVIEW_PREFIX}Review the following against these criteria:\n"
                f"{criteria_text}\n\nContent:\n{content}"
            )
        else:
            user_content = (
                f"{_REVIEW_PREFIX}Review the following for quality, "
                f"correctness, and completeness:\n\n{content}"
            )
        messages = [{"role": "user", "content": user_content}]
        return self._chat(messages, max_tokens=1024)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
    ) -> ProviderResponse:
        """POST to /api/chat and return a ProviderResponse."""
        url = f"{self._base_url}/api/chat"
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "options": {**self._options, "num_predict": max_tokens},
        }
        body = json.dumps(payload).encode("utf-8")
        req = Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=self._timeout) as resp:
                data: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            raise ProviderError(f"Ollama HTTP {exc.code}: {exc.reason}") from exc
        except URLError as exc:
            raise ProviderUnavailableError(
                f"Ollama unreachable at {self._base_url}: {exc.reason}"
            ) from exc
        except Exception as exc:
            raise ProviderError(f"Ollama error: {exc}") from exc

        message = data.get("message", {})
        content = message.get("content", "")
        eval_count = data.get("eval_count", 0)
        prompt_eval_count = data.get("prompt_eval_count", 0)
        return ProviderResponse(
            content=content,
            model=data.get("model", self._model),
            provider=_PROVIDER_NAME,
            tokens_used=eval_count + prompt_eval_count,
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _user_messages(prompt: str, context: str) -> list[dict[str, str]]:
    if context:
        content = f"Context:\n{context}\n\nQuestion: {prompt}"
    else:
        content = prompt
    return [{"role": "user", "content": content}]
