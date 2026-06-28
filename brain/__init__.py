"""
MondayOS Brain — AI coordination layer.

Brain is the single entry point for all MondayOS AI operation. It wires
subsystems together and exposes a stable surface to callers. The provider
abstraction layer (brain.providers) ensures no provider-specific logic
leaks into the application.

Internal components:
    BrainConfig    — configuration dataclass
    Brain          — the main system coordinator
    Router         — selects the right model for each task
    RoutingDecision — the result of a routing decision (always includes reasoning)
    ModelTier      — available model capability/cost tiers

Provider abstraction:
    AIProvider       — abstract interface all providers implement
    ProviderConfig   — configuration: type, model, api_key, base_url
    create_provider  — factory: ProviderConfig → AIProvider | None
    ProviderResponse — structured return type from all provider methods
    ProviderError and subclasses
"""
from __future__ import annotations

from brain.orchestrator import Brain, BrainConfig
from brain.providers import (
    AIProvider,
    ProviderAuthError,
    ProviderConfig,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponse,
    ProviderUnavailableError,
    create_provider,
)
from brain.reasoner import QuestionIntent, ReasoningEngine, ReasoningResult
from brain.router import ModelTier, Router, RoutingDecision

__all__ = [
    "Brain",
    "BrainConfig",
    # AI provider abstraction
    "AIProvider",
    "ProviderAuthError",
    "ProviderConfig",
    "ProviderError",
    "ProviderRateLimitError",
    "ProviderResponse",
    "ProviderUnavailableError",
    "create_provider",
    # Reasoning
    "QuestionIntent",
    "ReasoningEngine",
    "ReasoningResult",
    # Routing
    "Router",
    "RoutingDecision",
    "ModelTier",
]
