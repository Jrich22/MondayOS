"""
The responder seam — where model routing will attach.

The workspace never depends on a provider. It depends on ``WorkspaceResponder``,
a protocol with one method. Increment 1 ships ``ProviderWorkspaceResponder``,
which wraps a single MondayOS ``AIProvider``; increment 4 will add a
``RoutingWorkspaceResponder`` that picks per request. Neither the Conversation
model, the Context Engine, nor the service changes when that happens (ADR-018).

Two details make the seam real rather than decorative.

**The request carries structure, not a rendered prompt.** A responder receives
the project, the snapshot, and the conversation history as separate fields. Hand
a router a finished prompt string and it has nothing left to route on — "which
model suits this request" becomes unanswerable once context is flattened.

**No vendor SDK is imported here or anywhere else in this package.** The provider
arrives injected. Which model runs is MondayOS configuration, and this module
works unchanged when that changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from brain.providers.base import AIProvider, ProviderError
from workspace.context.snapshot import ContextSnapshot
from workspace.models import Message, MessageRole

# Output budget for one conversational turn. An initial default, configurable per
# responder — not a product rule. Long enough for a substantive answer about a
# project, short enough that a runaway generation is bounded.
DEFAULT_MAX_TOKENS = 1500

# How many prior turns to replay. Recent dialogue is high-value context and cheap;
# the whole history is neither. Increment 2 replaces this with summarised history.
DEFAULT_HISTORY_TURNS = 12

# The standing instruction. It states what MondayOS is and, more importantly, what
# to do when the context does not contain the answer — because the failure mode
# that matters here is a confident answer assembled from nothing.
SYSTEM_INSTRUCTION = (
    "You are MondayOS, an AI operating system, answering about one specific project. "
    "You have been given a context snapshot assembled from that project's registry entry, "
    "documentation, architecture decisions, tasks, knowledge base and git state.\n\n"
    "Ground your answer in that context. When the context does not contain what is needed, "
    "say so plainly and name what is missing — do not infer project details that are not "
    "there, and do not present a guess as a fact about this project. General knowledge is "
    "fine when it is clearly general rather than claimed about this codebase.\n\n"
    "The context is scoped to this project alone. Do not speculate about other projects."
)


@dataclass
class WorkspaceRequest:
    """
    One turn's worth of everything a responder could route on.

    Deliberately structured: a future router reads ``snapshot`` size, ``project``,
    and message shape to choose a model. A pre-rendered string would hide all of it.
    """

    project: str
    message: str
    snapshot: ContextSnapshot | None = None
    history: list[Message] = field(default_factory=list)
    conversation_id: str = ""
    max_tokens: int = DEFAULT_MAX_TOKENS

    def render_context(self, history_turns: int = DEFAULT_HISTORY_TURNS) -> str:
        """
        Flatten snapshot and history into provider context text.

        Called by a responder at the last moment, never before: the structure is
        what makes routing possible, so it is preserved right up to the call.
        """
        blocks: list[str] = []
        if self.snapshot is not None:
            blocks.append(self.snapshot.render())

        turns = [m for m in self.history if m.role in (MessageRole.USER, MessageRole.ASSISTANT)]
        recent = turns[-history_turns:] if history_turns > 0 else []
        if recent:
            lines = ["# Conversation so far"]
            for message in recent:
                speaker = "User" if message.role is MessageRole.USER else "MondayOS"
                lines.append(f"{speaker}: {message.content}")
            blocks.append("\n".join(lines))

        return "\n\n".join(b for b in blocks if b)


@dataclass
class WorkspaceReply:
    """
    What a responder produced.

    ``content`` is the visible answer and the only thing persisted as message
    text. ``provider``/``model`` are provenance. There is deliberately no field
    for provider reasoning: it is not requested, and there is nowhere to put it
    if it arrived (ADR-015).
    """

    content: str
    provider: str = ""
    model: str = ""
    tokens_used: int = 0
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.content.strip())


class WorkspaceResponder(Protocol):
    """
    The routing seam.

    An implementation turns a request into a reply. It makes no decision about
    whether the conversation should continue, never persists anything, and never
    reaches into the store.
    """

    def respond(self, request: WorkspaceRequest) -> WorkspaceReply:
        """Answer one turn."""
        ...

    @property
    def name(self) -> str:
        """Identifier for provenance. Never branched on."""
        ...


class ProviderWorkspaceResponder:
    """
    Increment 1's responder: one configured MondayOS ``AIProvider``.

    The provider is injected — this class never constructs one, never names a
    vendor, and never reads provider configuration. Replacing it with a router is
    a new class implementing the same protocol, not a change here.
    """

    def __init__(
        self,
        provider: AIProvider,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        history_turns: int = DEFAULT_HISTORY_TURNS,
    ) -> None:
        self._provider = provider
        self._max_tokens = max_tokens
        self._history_turns = history_turns

    @property
    def name(self) -> str:
        return self._provider.name

    def respond(self, request: WorkspaceRequest) -> WorkspaceReply:
        """
        Answer one turn through the provider.

        Returns a reply carrying ``error`` rather than raising: a provider outage
        is a normal thing that happens mid-conversation, and the turn should be
        recorded and retryable rather than lost to an exception.
        """
        availability = self._provider.availability()
        if not availability.available:
            return WorkspaceReply(
                content="",
                provider=self._provider.name,
                error=availability.instructions(),
            )

        context = request.render_context(self._history_turns)
        prompt = f"{SYSTEM_INSTRUCTION}\n\n{request.message}"

        try:
            response = self._provider.ask(
                prompt,
                context=context,
                max_tokens=request.max_tokens or self._max_tokens,
            )
        except ProviderError as exc:
            return WorkspaceReply(content="", provider=self._provider.name, error=str(exc))
        except Exception as exc:  # noqa: BLE001 — a provider bug must surface, not vanish
            return WorkspaceReply(
                content="",
                provider=self._provider.name,
                error=f"{type(exc).__name__}: {exc}",
            )

        content = (response.content or "").strip()
        if not content:
            # An empty body is a failed turn, not an answer. Recording it as an
            # assistant message would put a blank bubble in the transcript and
            # let the conversation continue as though something was said.
            return WorkspaceReply(
                content="",
                provider=response.provider or self._provider.name,
                model=response.model,
                error="The provider returned an empty response.",
            )

        return WorkspaceReply(
            content=content,
            provider=response.provider or self._provider.name,
            model=response.model,
            tokens_used=response.tokens_used,
        )
