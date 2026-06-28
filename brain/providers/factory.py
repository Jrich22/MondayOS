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
        timeout:    Request timeout in seconds.
        max_tokens: Default max output tokens for generated responses.
        extra:      Provider-specific options passed through unchanged.
    """

    type: str = ""                              # "" → disabled
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    timeout: int = 30
    max_tokens: int = 1024
    extra: dict[str, Any] = field(default_factory=dict)

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
