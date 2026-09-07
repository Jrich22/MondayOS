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

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from brain.providers.base import AIProvider, ProviderError
from reasoning.models import Assessment, Mode
from workspace.context.snapshot import ContextSnapshot
from workspace.models import Message, MessageRole

# Output budget for one conversational turn. An initial default, configurable per
# responder — not a product rule. Long enough for a substantive answer about a
# project, short enough that a runaway generation is bounded.
DEFAULT_MAX_TOKENS = 2000

# An executive answer carries eight sections, three scores and the alternatives
# that lost. That does not fit in a conversational budget: the first live run of
# the format stopped mid-sentence inside the final heading. Raised deliberately
# rather than by trimming the format, because the sections a truncated answer
# drops are the last ones — evidence strength, confidence and risk — which are
# exactly the ones that make the advice auditable.
#
# Set from measurement, not taste: a benchmark run against a real repository with
# accumulated conversation history reached ~4000 tokens on the longest strategic
# answer, so 4000 was the number that truncated. Both budgets remain hard ceilings
# — an answer that exceeds them is still marked incomplete rather than passed off
# as finished.
EXECUTIVE_MAX_TOKENS = 6000

# How many prior turns to replay. Recent dialogue is high-value context and cheap;
# the whole history is neither. Increment 2 replaces this with summarised history.
DEFAULT_HISTORY_TURNS = 12

# The standing instruction, in two registers.
#
# The original single instruction told the model to say plainly when the context
# did not contain an answer. That is why hallucinations were low — and it is also
# why Monday read as a search engine: asked what to build next, it correctly
# reported which documents it lacked.
#
# The fix is not to loosen grounding, which would trade the property that made it
# trustworthy. It is to let inference happen *labelled*. A conclusion presented as
# a conclusion, with its confidence attached, is safe in a way an unmarked one is
# not, and MondayOS now computes both before the model is called (see `reasoning`).
SYSTEM_INSTRUCTION = (
    "You are MondayOS, an AI operating system, answering about one specific project. "
    "You have been given a context snapshot assembled from that project's registry entry, "
    "documentation, architecture decisions, tasks, knowledge base and git state, and may "
    "also have been given a reasoning assessment computed from the same project.\n\n"
    "Ground your answer in that material. Keep the distinction between what the project "
    "states, what follows from it, and what you are recommending — but do not stop "
    "because the record is incomplete. Where the snapshot has no direct answer, reason "
    "from what is present and mark that reasoning as inference. Never present an "
    "inference as a documented fact about this codebase.\n\n"
    "Do not open by listing what the context lacks. Lead with the most useful thing you "
    "can say, and name a missing document only where it changes what the reader should "
    "do next.\n\n"
    "The context is scoped to this project alone. Do not speculate about other projects."
)

# The executive register. Used when MondayOS has routed the question as strategic
# and has already done the reasoning.
#
# The critical line is the one forbidding the model to invent confidence numbers.
# Every score in the assessment was computed from evidence by `reasoning.confidence`;
# a model that helpfully adds its own would produce something that looks identical
# on screen and means nothing, which would quietly destroy the one signal telling
# the reader how much to trust the rest.
EXECUTIVE_INSTRUCTION = (
    "You are MondayOS, acting as the technical and product lead for one specific "
    "project.\n\n"
    "You have been given a reasoning assessment built deterministically from this "
    "project's capabilities, index, relationship graph, decision log, task store and "
    "git history. Its facts, inferences, capability health, recommendations, "
    "alternatives, confidence scores and execution risks were computed by MondayOS, "
    "not by you. Narrate and explain that assessment: do not recompute it, do not "
    "invent additional confidence or risk numbers, and do not contradict a stated "
    "score.\n\n"
    "When the assessment reports record/reality drift, say so plainly and early. It "
    "means the project record disagrees with the code, so figures derived from the "
    "record are unreliable. MondayOS has not changed any task and you must not imply "
    "it has; report the disagreement and leave the correction to the reader.\n\n"
    "Lead with capabilities, not files. The reader thinks in products — AI Workspace, "
    "Billing, RSVP — and drills into implementation only when it changes a decision. "
    "A capability the assessment marks as needing attention belongs near the top.\n\n"
    "Answer as an experienced CTO would. Take a position, say which thing to do "
    "first, and be specific about what it costs and what you are giving up.\n\n"
    "Structure the answer under these headings, in this order:\n"
    "Facts — what this repository directly supports.\n"
    "Inferences — what follows from those facts, marked as reasoning not record.\n"
    "Alternatives Considered — the options that were weighed and why each lost.\n"
    "Tradeoffs — what the recommended path costs.\n"
    "Recommendation — what you advise, ranked.\n"
    "Evidence Strength — how well-supported the underlying facts are.\n"
    "Recommendation Confidence — how sure the call itself is.\n"
    "Execution Risk — how likely carrying it out is to go wrong.\n\n"
    "The last three are different measures and often disagree. Strong evidence for a "
    "risky change is a normal and important situation; if they diverge, say so "
    "plainly rather than averaging them into one impression.\n\n"
    "Incomplete documentation is normal and is never a reason to decline to advise. "
    "Reason from what exists. The context is scoped to this project alone."
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
    # 0 means "let the responder decide from the register". A concrete value is a
    # caller overriding that, usually because it knows what it can display.
    max_tokens: int = 0
    # A deterministic condensation of turns older than the verbatim window. Empty
    # for a short conversation. Carried separately from ``history`` so a router
    # can see that a thread was compacted rather than inferring it from length.
    history_digest: str = ""
    # What MondayOS concluded before the model was called. Carried as a value
    # rather than folded into the context text for the same reason the snapshot
    # is: a router needs to see that a question was routed as strategic, and
    # flattening it into a prompt string would hide the one field that says so.
    assessment: Assessment | None = None

    @property
    def executive(self) -> bool:
        """Whether this turn is in the strategic register."""
        return self.assessment is not None and self.assessment.mode is Mode.EXECUTIVE

    def instruction(self) -> str:
        """The standing instruction this turn should run under."""
        return EXECUTIVE_INSTRUCTION if self.executive else SYSTEM_INSTRUCTION

    def token_budget(self, default: int = DEFAULT_MAX_TOKENS) -> int:
        """
        Output budget for this turn.

        A strategic answer needs several times a conversational one. An explicit
        ``max_tokens`` on the request still wins — the caller knows more than we
        do about what it can display.
        """
        if self.max_tokens:
            return self.max_tokens
        return EXECUTIVE_MAX_TOKENS if self.executive else default

    def render_context(self, history_turns: int = DEFAULT_HISTORY_TURNS) -> str:
        """
        Flatten snapshot and history into provider context text.

        Called by a responder at the last moment, never before: the structure is
        what makes routing possible, so it is preserved right up to the call.
        """
        blocks: list[str] = []
        if self.assessment is not None and (self.assessment.has_reasoning or self.assessment.facts):
            # Placed before the raw snapshot deliberately. A model reads what comes
            # first as the frame for what follows, and the assessment is the
            # conclusion the snapshot supports — inverting them invites a summary
            # of documents with the reasoning appended as an afterthought.
            blocks.append("# MondayOS assessment\n" + self.assessment.render())
        if self.snapshot is not None:
            blocks.append(self.snapshot.render())
        if self.history_digest:
            blocks.append(self.history_digest)

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
    # True when generation stopped before the model finished — a user pressing
    # stop, or a stream that died mid-answer. The distinction matters: partial
    # text presented as a complete answer is a quiet correctness failure, so the
    # flag travels with the reply and is persisted on the message.
    incomplete: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.content.strip())


@dataclass
class ReplyChunk:
    """
    One increment of a streaming reply, in the workspace's own vocabulary.

    A provider's chunk shape never reaches this far: ``ProviderWorkspaceResponder``
    translates. That is what lets a future router stream from providers with
    completely different wire formats without anything above noticing.
    """

    text: str = ""
    done: bool = False
    reply: WorkspaceReply | None = None


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

    def respond_stream(self, request: WorkspaceRequest) -> Iterator[ReplyChunk]:
        """
        Answer one turn incrementally.

        Yields text chunks, then a final chunk carrying the assembled reply.
        Closing the iterator early is how a caller stops generation: the
        implementation must treat that as a stop, not an error.
        """
        ...

    @property
    def name(self) -> str:
        """Identifier for provenance. Never branched on."""
        ...

    @property
    def streams(self) -> bool:
        """True when this responder emits genuinely incremental chunks."""
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

    @property
    def streams(self) -> bool:
        """Whether the configured provider genuinely emits incremental chunks."""
        return self._provider.supports_streaming

    def respond_stream(self, request: WorkspaceRequest) -> Iterator[ReplyChunk]:
        """
        Stream one turn through the provider.

        Three failures are handled here rather than left to the caller:

        **Stop.** A caller that closes this iterator gets ``GeneratorExit``.
        That is a stop, not an error: whatever text arrived is assembled into a
        reply marked ``incomplete`` and handed back through the final chunk, so
        the partial answer is preserved rather than discarded.

        **Mid-stream failure.** A provider that dies after emitting text has
        still produced something. The reply keeps that text, records the error,
        and is marked incomplete — the alternative is throwing away work the
        user watched arrive.

        **Empty stream.** A stream that yields nothing is a failed turn, not an
        empty answer, for the same reason a blank `ask` response is.
        """
        availability = self._provider.availability()
        if not availability.available:
            yield ReplyChunk(
                done=True,
                reply=WorkspaceReply(
                    content="", provider=self._provider.name, error=availability.instructions()
                ),
            )
            return

        context = request.render_context(self._history_turns)
        prompt = f"{request.instruction()}\n\n{request.message}"

        parts: list[str] = []
        model = ""
        provider_name = self._provider.name
        stop_reason = ""
        tokens = 0
        error = ""

        try:
            for chunk in self._provider.stream(
                prompt, context=context, max_tokens=request.token_budget(self._max_tokens)
            ):
                if chunk.done:
                    model = chunk.model or model
                    provider_name = chunk.provider or provider_name
                    tokens = chunk.tokens_used or tokens
                    stop_reason = chunk.stop_reason or stop_reason
                    continue
                if chunk.text:
                    parts.append(chunk.text)
                    yield ReplyChunk(text=chunk.text)
        except GeneratorExit:
            # The caller stopped us. Preserve what arrived and re-raise so the
            # generator closes cleanly; the caller already holds the text it saw.
            raise
        except ProviderError as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 — a provider bug must surface, not vanish
            error = f"{type(exc).__name__}: {exc}"

        content = "".join(parts).strip()
        if not content and not error:
            error = "The provider returned an empty response."

        # A run cut off at max_tokens produced a partial answer that reads as a
        # finished one. Without this the flag says complete while the text stops
        # mid-sentence — the precise failure `incomplete` exists to prevent, and
        # the reason ProviderChunk carries stop_reason at all.
        truncated = stop_reason == "max_tokens"

        yield ReplyChunk(
            done=True,
            reply=WorkspaceReply(
                content=content,
                provider=provider_name,
                model=model,
                tokens_used=tokens,
                error=error,
                # Text arrived and then something went wrong: partial, not failed.
                incomplete=bool(error and content) or truncated,
                metadata={"stop_reason": stop_reason} if stop_reason else {},
            ),
        )

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
        prompt = f"{request.instruction()}\n\n{request.message}"

        try:
            response = self._provider.ask(
                prompt,
                context=context,
                max_tokens=request.token_budget(self._max_tokens),
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
