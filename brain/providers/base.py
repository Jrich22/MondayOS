"""
AIProvider — the abstract interface all AI provider implementations must satisfy.

No provider-specific logic appears anywhere outside this package.
Swap the provider by changing ProviderConfig — zero application code changes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
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
class ProviderChunk:
    """
    One piece of a streamed response.

    Deliberately not a vendor's SSE frame. Every provider streams in its own
    shape — Anthropic emits `content_block_delta`, OpenAI emits `choices[].delta`
    — and translating those into this type inside each provider file is what
    keeps the streaming format from leaking into callers. Nothing above this
    package should ever see a provider's wire format.

    The final chunk carries `done=True` and the response metadata; text chunks
    before it carry only `text`.
    """

    text: str = ""
    done: bool = False
    model: str = ""
    provider: str = ""
    tokens_used: int = 0
    # Provider-reported termination, on the final chunk. A run cut off at
    # max_tokens is truncated, not finished, and a caller that cannot tell the
    # difference will read a half-answer as a whole one.
    stop_reason: str = ""


@dataclass
class ProviderAvailability:
    """
    Whether a provider is ready to run, and if not, how to fix it.

    Attributes:
        available:    True if the provider can execute a call right now.
        provider:     Provider name (e.g. "openai").
        model:        The model that would be used.
        reason:       Legacy status field. Sanitized consumers expose a fixed
                      readiness code here, never provider-controlled prose.
        env_var:      Env var that must be set ("" if none required).
        install_hint: Shell hint to install a missing SDK ("" if not needed).
        code:         Fixed readiness code for safe cross-layer diagnostics.
    """

    available: bool
    provider: str = ""
    model: str = ""
    reason: str = ""
    env_var: str = ""
    install_hint: str = ""
    code: str = ""

    def to_dict(self) -> dict[str, Any]:
        provider = self.provider if type(self) is ProviderAvailability else ""
        safe = safe_provider_availability(self, provider=provider)
        return {
            "available": safe.available,
            "provider": safe.provider,
            "model": safe.model,
            "reason": safe.reason,
            "env_var": safe.env_var,
            "install_hint": "",
            "code": safe.code,
        }

    def instructions(self) -> str:
        """Return fixed, actionable guidance without free-form provider prose."""
        provider = self.provider if type(self) is ProviderAvailability else ""
        return provider_availability_message(
            safe_provider_availability(self, provider=provider)
        )


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


_CANONICAL_PROVIDER_NAMES = frozenset({
    "anthropic",
    "deepseek",
    "fake",
    "ollama",
    "openai",
})
_PROVIDER_ALIASES = {
    "deep-seek": "deepseek",
    "deep_seek": "deepseek",
    "open-ai": "openai",
    "open_ai": "openai",
}
_PROVIDER_ENV_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
}
_PROVIDER_MODEL_LABELS = {
    "anthropic": "anthropic-model",
    "deepseek": "deepseek-model",
    "fake": "fake-1",
    "ollama": "ollama-model",
    "openai": "openai-model",
}
_READINESS_CODES = frozenset({
    "authentication-failed",
    "missing-credential",
    "missing-sdk",
    "model-missing",
    "provider-error",
    "provider-not-configured",
    "provider-status-invalid",
    "rate-limited",
    "ready",
    "service-unreachable",
    "unavailable",
})
_STOP_REASONS = frozenset({
    "content_filter",
    "end_turn",
    "length",
    "max_tokens",
    "stop",
    "stop_sequence",
    "tool_use",
})


def provider_failure_code(error: BaseException) -> str:
    """Reduce an untrusted provider exception to one fixed diagnostic code."""
    if isinstance(error, ProviderAuthError):
        return "authentication-failed"
    if isinstance(error, ProviderRateLimitError):
        return "rate-limited"
    if isinstance(error, ProviderUnavailableError):
        return "unavailable"
    return "provider-error"


def provider_failure_message(error: BaseException | str) -> str:
    """Render a fixed user-facing message for a provider failure."""
    if type(error) is str:
        code = error
    elif isinstance(error, BaseException):
        code = provider_failure_code(error)
    else:
        code = "provider-error"
    messages = {
        "authentication-failed": (
            "Provider authentication failed. Check the configured credential "
            "and retry."
        ),
        "rate-limited": "Provider request was rate-limited. Retry later.",
        "unavailable": "Provider service is unavailable. Retry later.",
    }
    return messages.get(code, "Provider request failed. Retry later.")


def safe_provider_identifier(value: Any, *, fallback: str = "unknown") -> str:
    """Return only a fixed, supported provider identity.

    Provider implementations are extension points, so even a syntactically
    ordinary ``name`` may contain copied credentials or prompt fragments.  A
    known provider name is useful provenance; every other value becomes the
    fixed ``custom`` label instead of crossing the boundary verbatim.
    """
    try:
        candidate = value if type(value) is str else getattr(value, "name", "")
    except Exception:  # noqa: BLE001 - hostile provider properties are untrusted
        candidate = ""
    normalized = candidate.strip().lower() if type(candidate) is str else ""
    normalized = _PROVIDER_ALIASES.get(normalized, normalized)
    if normalized in _CANONICAL_PROVIDER_NAMES:
        return normalized
    if normalized == "custom":
        return "custom"
    return fallback if fallback in {"custom", "unknown"} else "unknown"


def safe_provider_model(provider: Any) -> str:
    """Return a fixed model-family label, never a provider-reported model ID."""
    return _PROVIDER_MODEL_LABELS.get(
        safe_provider_identifier(provider),
        "",
    )


def safe_provider_stop_reason(value: Any) -> str:
    """Allow only stop reasons whose exact values MondayOS understands."""
    if type(value) is not str:
        return ""
    candidate = value.strip().lower()
    return candidate if candidate in _STOP_REASONS else ""


def safe_provider_token_count(value: Any) -> int:
    """Return a bounded token count without coercing provider-controlled data."""
    if type(value) is not int:
        return 0
    return value if 0 <= value <= 1_000_000_000 else 0


def safe_provider_availability(
    value: Any,
    *,
    provider: str = "",
    error: BaseException | None = None,
) -> ProviderAvailability:
    """Return a bounded readiness record without provider-controlled prose.

    Provider status is copied into API responses, agent checkpoints, and team
    output.  Reasons and install hints can originate in an SDK or remote error,
    so only structural booleans and tightly validated identifiers may cross
    that boundary.
    """
    provider_name = safe_provider_identifier(provider, fallback="custom")
    if error is not None:
        code = provider_failure_code(error)
        return ProviderAvailability(
            available=False,
            provider=provider_name,
            reason=code,
            code=code,
        )
    if type(value) is not ProviderAvailability or type(value.available) is not bool:
        return ProviderAvailability(
            available=False,
            provider=provider_name,
            reason="provider-status-invalid",
            code="provider-status-invalid",
        )

    reported_provider = safe_provider_identifier(value.provider, fallback="custom")
    if (
        provider_name not in {"custom", "unknown"}
        and reported_provider not in {"custom", "unknown", provider_name}
    ):
        return ProviderAvailability(
            available=False,
            provider=provider_name,
            reason="provider-status-invalid",
            code="provider-status-invalid",
        )

    if value.available:
        code = "ready"
    else:
        raw_code = value.code if type(value.code) is str else ""
        if not raw_code and type(value.reason) is str:
            raw_code = value.reason
        code = raw_code if raw_code in _READINESS_CODES else ""
        if (
            not code
            and type(value.install_hint) is str
            and bool(value.install_hint)
        ):
            code = "missing-sdk"
        elif not code and type(value.env_var) is str and bool(value.env_var):
            code = "missing-credential"
        if not code or code == "ready":
            code = "unavailable"

    env_var = (
        _PROVIDER_ENV_VARS.get(provider_name, "")
        if code == "missing-credential"
        else ""
    )
    return ProviderAvailability(
        available=value.available,
        provider=provider_name,
        model=safe_provider_model(provider_name),
        reason=code,
        env_var=env_var,
        install_hint="",
        code=code,
    )


def provider_availability_message(value: ProviderAvailability) -> str:
    """Render a fixed readiness message from an already bounded status."""
    provider = value.provider if type(value) is ProviderAvailability else ""
    safe = safe_provider_availability(value, provider=provider)
    provider = safe.provider
    if safe.available:
        return f"Provider is ready ({provider})."
    if safe.code == "missing-sdk":
        return f"Provider component is not installed ({provider})."
    if safe.code == "missing-credential" and safe.env_var:
        return f"Provider credential is missing ({provider}); set {safe.env_var} and retry."
    if safe.code == "service-unreachable":
        return f"Provider service is unreachable ({provider}). Retry later."
    if safe.code == "model-missing":
        return f"Configured provider model is not installed ({provider})."
    if safe.code == "provider-status-invalid":
        return f"Provider readiness response was invalid ({provider})."
    if safe.code == "provider-not-configured":
        return "Requested provider is unsupported or not configured."
    if safe.code in {"authentication-failed", "rate-limited", "provider-error"}:
        return provider_failure_message(safe.code)
    return f"Provider is unavailable ({provider}). Check its configuration and retry."


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------

# A generation deadline is never shorter than this, so a small request does not
# get less headroom than it has today, and never longer than this, so a
# pathological budget cannot hang a caller indefinitely.
MIN_GENERATION_SECONDS = 30.0
MAX_GENERATION_SECONDS = 900.0


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

    @property
    def tokens_per_second(self) -> float:
        """
        A conservative floor on how fast this provider generates.

        Used to turn a token budget into a deadline. Deliberately a static,
        pessimistic constant rather than a measurement: a predictable deadline
        can be explained and asserted, and an adaptive one would make every
        timeout a function of whatever the machine was doing last week.

        Too low merely wastes patience on a hung provider; too high fails a
        healthy one mid-answer. So the numbers err low.
        """
        return 20.0

    def generation_deadline(self, max_tokens: int, override: float = 0.0) -> float:
        """
        How long this provider may take to produce ``max_tokens``.

        Scales with the budget, because that is what determines the duration. A
        fixed wall-clock limit applied to a variable-size request is what made
        Executive Mode's 6,000-token budget fail against the same 30 seconds that
        comfortably fits a 2,000-token answer.
        """
        if override > 0:
            return override
        rate = self.tokens_per_second or 1.0
        return min(MAX_GENERATION_SECONDS, max(MIN_GENERATION_SECONDS, max_tokens / rate))

    @property
    def reports_stop_reason(self) -> bool:
        """
        True when this provider says *why* generation stopped.

        Without it, an answer cut off at the token limit is indistinguishable
        from one that finished: the text simply ends, and it reads as complete.
        MondayOS marks such an answer `incomplete` only when the provider says
        so, which means on a silent provider the distinction cannot be made at
        all.

        Declared as a capability rather than inferred from the provider's name,
        so a caller can ask what is knowable here instead of keeping its own
        table of which vendors report what. A checker that cannot verify a
        property should say so -- asserting it against a provider that cannot
        report it would be measuring nothing and calling it a pass.
        """
        return False

    @property
    def supports_streaming(self) -> bool:
        """
        True when this provider emits incremental chunks.

        False by default, and the default is honest rather than pessimistic: a
        provider that has not implemented `stream` still works through the
        fallback below, but it delivers the whole answer at once. Callers show
        that difference rather than animating a single chunk to look like
        streaming, because a fake progressive reveal misrepresents latency the
        user is actually waiting through.
        """
        return False

    def stream(
        self,
        prompt: str,
        context: str = "",
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> Iterator[ProviderChunk]:
        """
        Stream a response as incremental chunks.

        The default implementation calls `ask` and yields the whole answer as one
        chunk, so every existing provider satisfies this interface without
        change and no caller needs to branch on capability. A provider that can
        genuinely stream overrides this and sets `supports_streaming`.
        """
        response = self.ask(prompt, context=context, max_tokens=max_tokens, **kwargs)
        if response.content:
            yield ProviderChunk(text=response.content)
        yield ProviderChunk(
            done=True,
            model=response.model,
            provider=response.provider or self.name,
            tokens_used=response.tokens_used,
            stop_reason=str(response.metadata.get("stop_reason", "")),
        )

    def availability(self) -> ProviderAvailability:
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
