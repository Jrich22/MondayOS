"""
ProviderConfig and factory for constructing AIProvider instances.

Usage:
    from brain.providers.factory import ProviderConfig, create_provider

    config = ProviderConfig(type="anthropic", model="claude-sonnet-4-6")
    provider = create_provider(config)   # AnthropicProvider | None

    # Changing provider: just change the config, no other code changes:
    config = ProviderConfig(type="openai", model="gpt-4o-mini")
    provider = create_provider(config)   # OpenAIProvider
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from brain.providers.base import AIProvider

_VALID_TYPES = frozenset({"anthropic", "openai", "ollama"})


@dataclass
class ProviderConfig:
    """
    Configuration for an AI provider.

    Attributes:
        type:       Provider name: "anthropic" | "openai" | "ollama" | "".
                    Empty string disables the provider.
        model:      Model identifier. Provider default used when empty.
        api_key:    API key. Read from the standard env var when empty.
                    Never stored in files — set via environment only.
        base_url:   Override API endpoint (useful for self-hosted / Azure).
        connect_timeout:
                    Seconds to wait to *reach* the provider. Never scales with
                    the request: asking for more tokens does not make a socket
                    take longer to open, and a provider that is down should fail
                    fast.
        generation_timeout:
                    Seconds to allow for *producing* the answer, or 0.0 to derive
                    it from the token budget and the provider's own throughput.
        timeout:    Deprecated. See below.
        max_tokens: Default max output tokens for generated responses.
        extra:      Provider-specific options passed through unchanged.

    **Two values, because they fail for different reasons.** A single number
    governing both is a category error, and it produced a real defect: a fixed
    30-second request timeout applied to Executive Mode's 6,000-token budget made
    the register fail 19 times in 20 on a supported local provider, while
    2,000-token grounded answers succeeded 30 times in 32. Connecting does not
    take longer because more tokens were asked for; generating three times as
    much text necessarily does.

    ``timeout`` is retained only for compatibility and maps to
    ``generation_timeout`` **and nothing else**. It deliberately cannot affect
    ``connect_timeout``: letting it do so would restore the exact ambiguity --
    one number meaning two things -- that this design exists to remove. An
    explicit ``generation_timeout`` always wins over it.
    """

    type: str = ""                              # "" → disabled
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    connect_timeout: float = 10.0
    generation_timeout: float = 0.0
    timeout: int | None = None
    max_tokens: int = 1024
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timeout is None:
            return
        warnings.warn(
            "ProviderConfig.timeout is deprecated. It maps to generation_timeout "
            "only and never affects connect_timeout; set generation_timeout "
            "directly.",
            DeprecationWarning,
            stacklevel=3,
        )
        if not self.generation_timeout:
            self.generation_timeout = float(self.timeout)

    def is_enabled(self) -> bool:
        """Return True if this config specifies an active provider."""
        return bool(self.type)


def create_provider(config: ProviderConfig | None) -> "AIProvider | None":
    """
    Construct an AIProvider from a ProviderConfig.

    Returns:
        A configured AIProvider instance, or None if config is None /
        config.type is empty.

    Raises:
        ProviderError: If config.type is not one of the known provider names.
    """
    if config is None or not config.type:
        return None

    provider_type = config.type.lower().strip()

    if provider_type == "anthropic":
        from brain.providers.anthropic import AnthropicProvider
        return AnthropicProvider(config)

    if provider_type in ("openai", "open_ai", "open-ai"):
        from brain.providers.openai import OpenAIProvider
        return OpenAIProvider(config)

    if provider_type == "ollama":
        from brain.providers.ollama import OllamaProvider
        return OllamaProvider(config)

    from brain.providers.base import ProviderError
    raise ProviderError(
        f"Unknown provider type {config.type!r}. "
        f"Valid types: {sorted(_VALID_TYPES)}"
    )
