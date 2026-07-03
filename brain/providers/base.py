"""
AIProvider — the abstract interface all AI provider implementations must satisfy.

No provider-specific logic appears anywhere outside this package.
Swap the provider by changing ProviderConfig — zero application code changes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Response type
# ---------------------------------------------------------------------------

@dataclass
class ProviderResponse:
    """
    Structured return value from every AIProvider method.

    All fields except `content` have defaults so providers can return
    only what they know. Callers must check `content` for results.
    """

    content: str                          # The generated text
    model: str = ""                       # Specific model ID used
    provider: str = ""                    # Provider name (e.g. "anthropic")
    tokens_used: int = 0                  # Input + output tokens (0 if unknown)
    cached: bool = False                  # True if response came from a cache
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderAvailability:
    """
    Whether a provider is ready to run, and if not, how to fix it.

    Attributes:
        available:    True if the provider can execute a call right now.
        provider:     Provider name (e.g. "openai").
        model:        The model that would be used.
        reason:       Human-readable status ("ready" or why it is not).
        env_var:      Env var that must be set ("" if none required).
        install_hint: Shell hint to install a missing SDK ("" if not needed).
    """

    available: bool
    provider: str = ""
    model: str = ""
    reason: str = ""
    env_var: str = ""
    install_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "provider": self.provider,
            "model": self.model,
            "reason": self.reason,
            "env_var": self.env_var,
            "install_hint": self.install_hint,
        }

    def instructions(self) -> str:
        """A single clear line telling the user how to make this provider ready."""
        if self.available:
            return f"{self.provider} is ready (model {self.model})."
        parts = [self.reason or f"{self.provider} is unavailable"]
        if self.install_hint:
            parts.append(f"install: {self.install_hint}")
        if self.env_var:
            parts.append(f"set {self.env_var}")
        return "; ".join(parts) + "."


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------

class ProviderError(Exception):
    """Raised when a provider encounters an unrecoverable error."""


class ProviderAuthError(ProviderError):
    """Raised when API key is missing, invalid, or revoked."""


class ProviderRateLimitError(ProviderError):
    """Raised when the provider rejects the request due to rate limiting."""


class ProviderUnavailableError(ProviderError):
    """Raised when the provider service cannot be reached."""


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------

class AIProvider(ABC):
    """
    Abstract interface for all AI model providers.

    Every method returns a `ProviderResponse`. The four methods represent
    semantically distinct operations — each implementation may use a different
    system prompt, model parameter, or temperature per method.

    The contract: swap `ProviderConfig.type` and every caller works unchanged.
    No SDK or service name should appear outside this package.

    Thread safety: Implementations are not required to be thread-safe.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier: "anthropic" | "openai" | "ollama"."""

    # ------------------------------------------------------------------
    # Selection metadata
    #
    # Used by policy-based provider selection (e.g. the Execution
    # Orchestrator). Concrete, non-abstract properties with sensible
    # defaults describing a generic remote provider; implementations
    # override to expose their own profile. Keeping this on the provider
    # ensures all provider-specific knowledge stays inside provider files.
    # ------------------------------------------------------------------

    @property
    def is_local(self) -> bool:
        """True if the provider runs locally (no network, no per-token cost)."""
        return False

    @property
    def cost_tier(self) -> int:
        """Relative monetary cost: 0 (free/local) … higher is pricier."""
        return 2

    @property
    def capability_tier(self) -> int:
        """Relative capability: higher is more capable."""
        return 2

    def availability(self) -> "ProviderAvailability":
        """
        Report whether this provider can run right now.

        The default assumes a provider with no external requirements (e.g. a
        local or in-process provider) is always available. Providers that need a
        network SDK or API key override this to check for them so callers can
        fail gracefully with clear, actionable instructions before any call.
        """
        return ProviderAvailability(available=True, provider=self.name, reason="ready")

    @abstractmethod
    def ask(
        self,
        prompt: str,
        context: str = "",
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> ProviderResponse:
        """
        Answer a question or respond to a conversational prompt.

        Args:
            prompt:     The question or request.
            context:    Optional background text prepended to the prompt.
            max_tokens: Maximum tokens in the response.
        """

    @abstractmethod
    def plan(
        self,
        objective: str,
        context: str = "",
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> ProviderResponse:
        """
        Produce a structured, actionable plan for an objective.

        Args:
            objective:  The goal to plan for.
            context:    Background information (project state, constraints).
            max_tokens: Maximum tokens in the response.
        """

    @abstractmethod
    def summarize(
        self,
        content: str,
        max_words: int = 150,
        **kwargs: Any,
    ) -> ProviderResponse:
        """
        Produce a concise summary of the given content.

        Args:
            content:   The text to summarize.
            max_words: Target word count ceiling for the summary.
        """

    @abstractmethod
    def review(
        self,
        content: str,
        criteria: list[str] | None = None,
        **kwargs: Any,
    ) -> ProviderResponse:
        """
        Review content against specified quality criteria.

        Args:
            content:  The content to evaluate.
            criteria: Evaluation criteria. Provider defaults apply when None.
        """
