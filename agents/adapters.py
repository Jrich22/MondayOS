"""
Adapters that turn a registered Agent into a concrete AIProvider.

The runtime holds no provider-specific code: it asks this module for a provider
and then talks only to the AIProvider abstraction. The ChatGPT (CPO) and Claude
Code (Lead Engineer) "adapters" are simply the existing openai / anthropic
provider implementations selected by role — no new SDK code is introduced here.

FakeAgentProvider is the offline **fake-agent test harness**: a deterministic,
network-free AIProvider used by the tests and available as the ``fake`` provider
so `monday agent run` works with no API keys configured.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterable
from typing import Any

from brain.providers.base import AIProvider, ProviderAvailability, ProviderResponse
from brain.providers.factory import ProviderConfig, create_provider

__all__ = [
    "FakeAgentProvider",
    "build_provider_for",
    "build_provider_pool",
    "availability_for",
    "FAKE_PROVIDER",
]

FAKE_PROVIDER = "fake"
_FALLBACK_PROVIDER_ORDER = ("openai", "anthropic", "deepseek", "ollama")
_SUPPORTED_PROVIDER_NAMES = frozenset((*_FALLBACK_PROVIDER_ORDER, FAKE_PROVIDER))
_MODEL_ENV = {
    "openai": "MONDAYOS_OPENAI_MODEL",
    "anthropic": "MONDAYOS_ANTHROPIC_MODEL",
    "deepseek": "MONDAYOS_DEEPSEEK_MODEL",
    "ollama": "MONDAYOS_OLLAMA_MODEL",
}


class FakeAgentProvider(AIProvider):
    """
    A deterministic, offline AIProvider. Returns role-aware canned text that is
    substantial enough to pass the orchestrator's ResultValidator, so the full
    review-required pipeline can be exercised without any network or API key.
    """

    def __init__(self, name: str = FAKE_PROVIDER, *, role: str = "", verdict: str = "pass") -> None:
        self._name = name
        self._role = role
        # The structured verdict this fake emits: "pass" (default), "block", or
        # "needs_changes". It is expressed as a real JSON verdict object in the
        # response body (see _body), so the team workflow stops the pipeline from
        # the structured signal — not from prose.
        #
        # Three additional modes exist for testing the verdict-integrity path.
        # They emit NO usable verdict and must therefore never read as a pass:
        #   "no_verdict" — reassuring prose only, no JSON at all
        #   "malformed"  — a JSON block that does not parse
        #   "truncated"  — a JSON block cut off mid-object
        self._verdict = verdict
        self.calls: list[tuple[str, str]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_local(self) -> bool:
        return True

    @property
    def cost_tier(self) -> int:
        return 0

    @property
    def capability_tier(self) -> int:
        return 1

    def availability(self) -> ProviderAvailability:
        """The fake provider is always available — no key or network needed."""
        return ProviderAvailability(
            available=True, provider=self._name, model=f"{self._name}-1", reason="ready",
        )

    def _body(self, verb: str, subject: str) -> str:
        role = f"the {self._role} agent" if self._role else "an agent"

        # ── Verdict-integrity modes: convincing prose, no usable verdict ──
        # Each deliberately reads as approval to a human skimming it. None may
        # be accepted as one.
        if self._verdict in ("no_verdict", "malformed", "truncated"):
            prose = (
                f"[{self._name}] Acting as {role}, I {verb} a full review of the work. "
                "Everything looks good to me. Checkpoint 1: PASS. Checkpoint 2: PASS. "
                "I see no blocker and no reason to reject. LGTM — approved from my side. "
                f"Objective reviewed: {subject.strip()[:160]}"
            )
            if self._verdict == "no_verdict":
                return prose
            if self._verdict == "malformed":
                return f"{prose}\n\n```json\n{{\"verdict\": \"pass\", \"summary\": oops,,}}\n```"
            # Truncated: the response is cut off partway through the JSON object,
            # exactly as a provider hitting its output-token ceiling would be.
            return f"{prose}\n\n```json\n{{\n  \"verdict\": \"pass\",\n  \"confidence\": \"hi"

        # Deliberately include the words "blocker"/"blocking" in ordinary prose to
        # prove the structured path is what decides: the verdict comes from the
        # JSON object below, never from these words.
        if self._verdict == "block":
            prose = (
                f"[{self._name}] Acting as {role}, I reviewed the work. I identified "
                f"a blocking issue that must be resolved before this can proceed. "
                f"Objective reviewed: {subject.strip()[:160]}"
            )
            payload = {
                "verdict": "block",
                "confidence": "high",
                "summary": "A blocking issue was found; the stage cannot pass.",
                "findings": ["Blocking issue identified during review."],
                "recommendations": ["Resolve the blocking issue and re-run the stage."],
            }
        elif self._verdict == "needs_changes":
            prose = (
                f"[{self._name}] Acting as {role}, I reviewed the work. It is close, "
                f"but changes are needed. Objective reviewed: {subject.strip()[:160]}"
            )
            payload = {
                "verdict": "needs_changes",
                "confidence": "medium",
                "summary": "Changes are requested before this can pass.",
                "findings": ["Minor issues found during review."],
                "recommendations": ["Address the noted changes and re-submit."],
            }
        else:
            prose = (
                f"[{self._name}] Acting as {role}, {verb} the requested work. "
                f"Objective addressed: {subject.strip()[:180]} "
                "A concrete result was produced and is ready for human review. "
                "There is no blocker here."
            )
            payload = {
                "verdict": "pass",
                "confidence": "high",
                "summary": "Work completed and ready for human review.",
                "findings": [],
                "recommendations": [],
            }
        return f"{prose}\n\n```json\n{json.dumps(payload, indent=2)}\n```"

    def ask(
        self,
        prompt: str,
        context: str = "",
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> ProviderResponse:
        self.calls.append(("ask", prompt))
        return ProviderResponse(
            content=self._body("completed", prompt),
            model=f"{self._name}-1",
            provider=self._name,
            tokens_used=42,
        )

    def plan(
        self,
        objective: str,
        context: str = "",
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> ProviderResponse:
        self.calls.append(("plan", objective))
        return ProviderResponse(content=self._body("planned", objective), provider=self._name)

    def summarize(self, content: str, max_words: int = 150, **kwargs: Any) -> ProviderResponse:
        self.calls.append(("summarize", content))
        return ProviderResponse(content=self._body("summarized", content), provider=self._name)

    def review(
        self,
        content: str,
        criteria: list[str] | None = None,
        **kwargs: Any,
    ) -> ProviderResponse:
        self.calls.append(("review", content))
        return ProviderResponse(content=self._body("reviewed", content), provider=self._name)


def build_provider_for(
    agent: Any,
    *,
    role: str = "",
    configured_providers: Iterable[AIProvider] | None = None,
) -> AIProvider | None:
    """
    Construct the AIProvider for an agent (or a bare provider name).

    ``agent`` may be an Agent (uses .provider / .role) or a provider-name string.
    Returns a FakeAgentProvider for the "fake" provider, a real provider via the
    factory otherwise, or None if construction fails (e.g. missing credentials) —
    in which case the orchestrator reports the run as "skipped" rather than
    crashing.
    """
    provider_name = getattr(agent, "provider", agent)
    role_slug = getattr(agent, "role", role)
    name = str(provider_name or "").strip().lower()

    if not name:
        return None
    if name == FAKE_PROVIDER:
        return FakeAgentProvider(role=role_slug)

    configured = _provider_named(configured_providers, name)
    if configured is not None:
        return configured
    if configured_providers is not None:
        # An explicit configured pool is an allowlist. Do not reconstruct a
        # provider from ambient environment credentials when the operator
        # intentionally excluded it (for example, a DeepSeek-only deployment).
        return None

    try:
        model = (os.environ.get(_MODEL_ENV.get(name, ""), "") or "").strip()
        base_url = ""
        if name == "ollama":
            base_url = (os.environ.get("OLLAMA_HOST") or "").strip()
            if base_url and not base_url.startswith(("http://", "https://")):
                base_url = f"http://{base_url}"
        return create_provider(
            ProviderConfig(type=name, model=model, base_url=base_url)
        )
    except Exception:
        # Unknown type or provider that can't be constructed without credentials.
        return None


def build_provider_pool(
    agent: Any,
    *,
    role: str = "",
    configured_providers: Iterable[AIProvider] | None = None,
) -> list[AIProvider]:
    """
    Build a deterministic role-first provider pool for automatic failover.

    The agent's configured provider remains the primary. The other supported
    providers follow in a stable order so a rate limit, missing key, or service
    failure can move the same role to another model without changing routing.
    An invalid primary is not silently hidden by fallback providers, and the
    offline ``fake`` provider always remains a single-provider pool so tests are
    deterministic.
    """
    role_slug = str(getattr(agent, "role", role) or role)
    primary_name = str(getattr(agent, "provider", agent) or "").strip().lower()
    if role_slug == "reviewer":
        # Product invariant: the terminal independent review is ChatGPT/OpenAI.
        # Registry customization and automatic failover may not weaken it.
        primary_name = "openai"
    elif primary_name not in _SUPPORTED_PROVIDER_NAMES:
        # Fail closed on configuration mistakes. A supported provider that is
        # temporarily unavailable may use the ordered fallback pool below, but
        # an unknown name is not an outage: it is almost certainly a typo. If
        # we silently selected another model, the recorded operator intent and
        # the provider that actually handled the work would disagree.
        return []
    configured = (
        None if configured_providers is None else list(configured_providers)
    )
    if configured is None:
        primary = build_provider_for(primary_name, role=role_slug)
        if primary is None:
            return []
    else:
        # A supplied pool is an allowlist as well as configuration. Never
        # rebuild a role's preferred hosted provider outside it: that could
        # bypass MONDAYOS_PROVIDER or a local-only operator policy.
        primary = _provider_named(configured, primary_name)
        if primary is None and role_slug == "reviewer":
            return []  # mandatory OpenAI reviewer is unavailable: fail closed

    # The terminal reviewer is the mandatory ChatGPT/OpenAI quality gate. A
    # different model may not silently stand in for it during an outage.
    if primary is not None and (
        primary.name == FAKE_PROVIDER or role_slug == "reviewer"
    ):
        return [primary]

    providers = [primary] if primary is not None else []
    seen = {primary.name} if primary is not None else set()
    for name in _FALLBACK_PROVIDER_ORDER:
        if name in seen:
            continue
        # A Monday instance passes the providers it already validated and
        # configured at startup. When that explicit pool is present, do not add
        # bare providers outside it (especially an unverified local Ollama).
        if configured is not None:
            candidate = _provider_named(configured, name)
        else:
            candidate = build_provider_for(name, role=role_slug)
        if candidate is not None:
            providers.append(candidate)
            seen.add(candidate.name)
    return providers


def availability_for(
    agent: Any,
    *,
    role: str = "",
    configured_providers: Iterable[AIProvider] | None = None,
) -> ProviderAvailability:
    """
    Report provider availability for an agent (or a bare provider name).

    Never raises: an unknown/unconstructable provider is reported unavailable
    with a clear reason. Used by `monday agent list` and by the runtime's
    pre-run gate so missing keys fail gracefully.
    """
    role_slug = str(getattr(agent, "role", role) or role)
    name = str(getattr(agent, "provider", agent) or "").strip().lower()
    if role_slug == "reviewer":
        name = "openai"
    pool = build_provider_pool(
        agent,
        role=role_slug,
        configured_providers=configured_providers,
    )
    if not pool:
        return ProviderAvailability(
            available=False, provider=name or "(none)",
            reason=f"unknown or unconstructable provider {name!r}",
        )

    # Match execution semantics: the runtime tries the ordered pool until one
    # provider is usable.  Reporting only the primary here made ``agent list``
    # claim a role was unavailable even though the same role would immediately
    # run on a healthy fallback.
    first_failure: ProviderAvailability | None = None
    for provider in pool:
        try:
            availability = provider.availability()
        except Exception as exc:
            availability = ProviderAvailability(
                available=False,
                provider=provider.name,
                reason=(
                    "availability check failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )
        if availability.available:
            return availability
        if first_failure is None:
            first_failure = availability

    # Preserve the primary provider's diagnostic when every candidate is down;
    # it is the provider the registry asked for and therefore the most useful
    # setup error to show.
    assert first_failure is not None
    return first_failure


def _provider_named(
    providers: Iterable[AIProvider] | None,
    name: str,
) -> AIProvider | None:
    if providers is None:
        return None
    wanted = name.strip().lower()
    for provider in providers:
        if provider.name.strip().lower() == wanted:
            return provider
    return None
