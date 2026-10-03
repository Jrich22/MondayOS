"""DeepSeek provider using its OpenAI-compatible API."""
from __future__ import annotations

from brain.providers.openai import OpenAIProvider


class DeepSeekProvider(OpenAIProvider):
    """DeepSeek chat and reasoning models behind the shared provider interface."""

    provider_name = "deepseek"
    default_model = "deepseek-chat"
    api_key_env = "DEEPSEEK_API_KEY"
    default_base_url = "https://api.deepseek.com"
    display_name = "DeepSeek"

    @property
    def cost_tier(self) -> int:
        return 1

    @property
    def capability_tier(self) -> int:
        return 2
