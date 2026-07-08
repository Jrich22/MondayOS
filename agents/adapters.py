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
from typing import Any

from brain.providers.base import AIProvider, ProviderAvailability, ProviderResponse
from brain.providers.factory import ProviderConfig, create_provider

__all__ = ["FakeAgentProvider", "build_provider_for", "availability_for", "FAKE_PROVIDER"]

FAKE_PROVIDER = "fake"


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

    def ask(self, prompt: str, context: str = "", max_tokens: int = 1024, **kwargs: Any) -> ProviderResponse:
        self.calls.append(("ask", prompt))
        return ProviderResponse(
            content=self._body("completed", prompt),
            model=f"{self._name}-1",
            provider=self._name,
            tokens_used=42,
        )

    def plan(self, objective: str, context: str = "", max_tokens: int = 2048, **kwargs: Any) -> ProviderResponse:
        self.calls.append(("plan", objective))
        return ProviderResponse(content=self._body("planned", objective), provider=self._name)

    def summarize(self, content: str, max_words: int = 150, **kwargs: Any) -> ProviderResponse:
        self.calls.append(("summarize", content))
        return ProviderResponse(content=self._body("summarized", content), provider=self._name)

    def review(self, content: str, criteria: list[str] | None = None, **kwargs: Any) -> ProviderResponse:
        self.calls.append(("review", content))
        return ProviderResponse(content=self._body("reviewed", content), provider=self._name)


def build_provider_for(agent: Any, *, role: str = "") -> AIProvider | None:
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

    try:
        return create_provider(ProviderConfig(type=name))
    except Exception:
        # Unknown type or provider that can't be constructed without credentials.
        return None


def availability_for(agent: Any, *, role: str = "") -> ProviderAvailability:
    """
    Report provider availability for an agent (or a bare provider name).

    Never raises: an unknown/unconstructable provider is reported unavailable
    with a clear reason. Used by `monday agent list` and by the runtime's
    pre-run gate so missing keys fail gracefully.
    """
    name = str(getattr(agent, "provider", agent) or "").strip().lower()
    prov = build_provider_for(agent, role=role)
    if prov is None:
        return ProviderAvailability(
            available=False, provider=name or "(none)",
            reason=f"unknown or unconstructable provider {name!r}",
        )
    return prov.availability()
