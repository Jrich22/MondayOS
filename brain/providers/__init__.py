"""
brain.providers — AI provider abstraction layer.

All AI provider interaction flows through this package.
No provider-specific logic appears outside of the implementation modules.

Public surface:
    AIProvider       — abstract interface every provider implements
    ProviderResponse — structured return type from all provider methods
    ProviderError    — base error class
    ProviderAuthError
    ProviderRateLimitError
    ProviderUnavailableError
    ProviderConfig   — configuration dataclass
    create_provider  — factory: ProviderConfig → AIProvider | None

Provider implementations (not imported by default — lazy-loaded by factory):
    AnthropicProvider — Claude via anthropic SDK
    OpenAIProvider    — GPT-* via openai SDK
    OllamaProvider    — local models via Ollama REST API
"""
from __future__ import annotations

from brain.providers.base import (
    AIProvider,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponse,
    ProviderUnavailableError,
)
from brain.providers.factory import ProviderConfig, create_provider

__all__ = [
    "AIProvider",
    "ProviderAuthError",
    "ProviderError",
    "ProviderRateLimitError",
    "ProviderResponse",
    "ProviderUnavailableError",
    "ProviderConfig",
    "create_provider",
]
