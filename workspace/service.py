"""
WorkspaceService — the AI Workspace facade.

Orchestration only. Conversations persist through ``ConversationStore``, context
comes from ``ContextEngine``, answers come from a ``WorkspaceResponder``, and
knowledge capture goes to the existing ``KnowledgeStore``. This module owns no
storage of its own and reimplements no subsystem — that is the whole design
constraint of the AI Workspace.

The subsystem readers arrive as injected callables. That is what keeps project
scoping structural: by the time the service holds a reader, the reader is already
scoped, so there is no call the service could make that widens it (ADR-017).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workspace import briefing as briefing_mod
from workspace import compaction, search
from workspace.activity import ActivityKind, ActivityRecorder, NullRecorder
from workspace.context.engine import ContextEngine
from workspace.context.snapshot import ContextSnapshot
from workspace.errors import (
    ConversationArchivedError,
    ConversationNotFoundError,
    MessageNotFoundError,
    ResponderUnavailableError,
)
from workspace.models import (
    MAX_ALTERNATIVES,
    MAX_EVIDENCE_REFS,
    MAX_INITIATIVES,
    AlternativeRef,
    Conversation,
    ConversationStatus,
    EvidenceRef,
    Message,
    MessageRole,
    Score,
    StrategicState,
    derive_title,
    recommendation_key,
    slugify,
)
from workspace.responder import WorkspaceReply, WorkspaceRequest, WorkspaceResponder
from workspace.store import ConversationStore


class WorkspaceService:
    """
    Conversation, context and response orchestration for one MondayOS root.

    Every method that touches a conversation names its project, so there is no
    unscoped path through this class.
    """

    def __init__(
        self,
        root: Path = Path("."),
        engine: ContextEngine | None = None,
        responder: WorkspaceResponder | None = None,
        list_projects: Callable[[], list[dict[str, Any]]] | None = None,
        capture_knowledge: Callable[[dict[str, Any]], str] | None = None,
        create_task: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        read_tasks: Callable[[str], list[dict[str, Any]]] | None = None,
        read_completed: Callable[[str], list[dict[str, Any]]] | None = None,
        git_lines: Callable[[str], list[str]] | None = None,
        activity: ActivityRecorder | NullRecorder | None = None,
        summarizer: compaction.ConversationSummarizer | None = None,
        # (project, question, subject, thin_retrieval, strategic_state, fingerprint)
        assess: Callable[[str, str, str, bool, Any, str], Any] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._root = Path(root)
        self._store = ConversationStore(self._root)
        self._engine = engine
        self._responder = responder
        self._list_projects = list_projects
        self._capture_knowledge = capture_knowledge
        self._create_task = create_task
        self._read_tasks = read_tasks
        self._read_completed = read_completed
        self._git_lines = git_lines
        # Reasoning over a retrieved snapshot. Optional, and injected rather
        # than constructed: without it every turn is exactly the grounded
        # answer increments 1-3 produced, which is a working workspace and
        # not a degraded one.
        self._assess = assess
        # Defaults to a recorder that drops everything, so nothing in this class
        # is conditional on whether anyone is watching the activity feed.
        self._activity = activity or NullRecorder()
        # Wired but unused: model summarisation is an increment-3 decision about
        # summary quality, not a refactor to bundle with this one.
        self._summarizer = summarizer
        self._now = now or (lambda: datetime.now(tz=UTC))

    @property
    def activity(self) -> ActivityRecorder | NullRecorder:
        return self._activity

    # ------------------------------------------------------------- projects

    def list_projects(self) -> list[dict[str, Any]]:
        """
        Projects the workspace can open, from the existing registry.

        Annotated with each project's conversation count so the selector can show
        where work already exists. The workspace does not maintain its own list of
        projects — there is one project registry in MondayOS.
        """
        if self._list_projects is None:
            return []
        projects = self._list_projects()
        for project in projects:
            slug = slugify(str(project.get("name", "")))
            try:
                project["conversation_count"] = len(self._store.list(slug))
            except (OSError, ValueError):
                project["conversation_count"] = 0
        return projects

    # --------------------------------------------------------- conversations

    def list_conversations(
        self, project: str, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        """Conversation summaries for one project, newest first."""
        return [c.summary_dict() for c in self._store.list(project, include_archived)]

    def create_conversation(self, project: str, title: str = "") -> dict[str, Any]:
        """Start a new conversation. The title may be empty and derived on first message."""
        conversation = self._store.create(
            project, title=title.strip() or "New conversation", now=self._now()
        )
        return conversation.to_dict()

    def get_conversation(self, project: str, conversation_id: str) -> dict[str, Any]:
        return self._store.get(project, conversation_id).to_dict()

    def rename_conversation(self, project: str, conversation_id: str, title: str) -> dict[str, Any]:
        clean = title.strip()
        if not clean:
            raise ValueError("A conversation title cannot be empty.")
        conversation = self._store.get(project, conversation_id)
        conversation.title = clean
        conversation.updated_at = self._now()
        self._store.save(conversation)
        return conversation.to_dict()

    def archive_conversation(self, project: str, conversation_id: str) -> dict[str, Any]:
        """
        Archive a conversation. Reversible on purpose.

        Archiving rather than deleting is the default because a conversation is
        the reasoning behind decisions already made; losing it silently loses the
        explanation for work that still exists.
        """
        conversation = self._store.get(project, conversation_id)
        conversation.status = ConversationStatus.ARCHIVED
        conversation.updated_at = self._now()
        self._store.save(conversation)
        return conversation.to_dict()

    def unarchive_conversation(self, project: str, conversation_id: str) -> dict[str, Any]:
        conversation = self._store.get(project, conversation_id)
        conversation.status = ConversationStatus.ACTIVE
        conversation.updated_at = self._now()
        self._store.save(conversation)
        return conversation.to_dict()

    def delete_conversation(self, project: str, conversation_id: str) -> dict[str, Any]:
        """Permanently remove a conversation. Archiving is the reversible option."""
        self._store.delete(project, conversation_id)
        return {"id": conversation_id, "project": slugify(project), "deleted": True}

    # -------------------------------------------------------------- context

    def build_context(self, project: str, query: str = "") -> dict[str, Any]:
        """Assemble a fresh context snapshot for one project."""
        return self._snapshot(project, query, allow_reuse=False).to_dict()

    def invalidate_context(self, project: str = "") -> None:
        """Drop cached context. Called whenever something a snapshot reads changes."""
        if self._engine is not None:
            self._engine.invalidate(project)

    def _snapshot(
        self,
        project: str,
        query: str = "",
        allow_reuse: bool = True,
        carry: str = "",
    ) -> ContextSnapshot:
        if self._engine is None:
            return ContextSnapshot(id="CTX-none", project=slugify(project), created_at=self._now())
        if allow_reuse:
            snapshot, reused = self._engine.build_or_reuse(project, query, carry)
            self._activity.record(
                ActivityKind.CONTEXT,
                "Reused project context" if reused else "Assembled project context",
                project=snapshot.project,
                detail=snapshot.summary(),
            )
            return snapshot
        snapshot = self._engine.build(project, query, carry)
        self._activity.record(
            ActivityKind.CONTEXT,
            "Assembled project context",
            project=snapshot.project,
            detail=snapshot.summary(),
        )
        return snapshot

    # ------------------------------------------------------------- messaging

    def send_message(
        self,
        project: str,
        conversation_id: str,
        content: str,
        rebuild_context: bool = True,
    ) -> dict[str, Any]:
        """
        Record a user message, answer it, and persist both turns.

        The user message is written before the provider is called, so a provider
        failure never loses what the human said. A failed turn is recorded as an
        assistant message carrying the error rather than being discarded, which is
        what makes retry meaningful: the transcript shows the attempt.
        """
        text = content.strip()
        if not text:
            raise ValueError("A message cannot be empty.")

        conversation = self._store.get(project, conversation_id)
        if conversation.is_archived:
            raise ConversationArchivedError(conversation_id)

        snapshot = (
            self._snapshot(project, query=text, carry=conversation.subject)
            if rebuild_context
            else None
        )
        if snapshot is not None:
            conversation.active_snapshot_id = snapshot.id
            conversation.subject = _subject_of(snapshot) or conversation.subject

        stamp = self._now()
        user_message = Message(
            id=self._store.next_message_id(conversation),
            role=MessageRole.USER,
            content=text,
            created_at=stamp,
            snapshot_id=snapshot.id if snapshot else conversation.active_snapshot_id,
        )
        conversation.messages.append(user_message)

        if conversation.title in ("", "New conversation"):
            conversation.title = derive_title(text)

        conversation.updated_at = stamp
        self._store.save(conversation)

        reply, assessment = self._respond(
            project=conversation.project,
            conversation=conversation,
            snapshot=snapshot,
            text=text,
            history=conversation.messages[:-1],
        )

        assistant_message = Message(
            id=self._store.next_message_id(conversation),
            role=MessageRole.ASSISTANT,
            content=reply.content,
            created_at=self._now(),
            provider=reply.provider,
            model=reply.model,
            snapshot_id=snapshot.id if snapshot else conversation.active_snapshot_id,
            tokens_used=reply.tokens_used,
            error=reply.error,
            incomplete=reply.incomplete,
        )
        conversation.messages.append(assistant_message)
        conversation.updated_at = assistant_message.created_at
        _capture_strategy(conversation, assessment, assistant_message, snapshot, self._now())
        self._store.save(conversation)
        self._activity.record(
            ActivityKind.ERROR if reply.error else ActivityKind.PROVIDER,
            "Provider failed" if reply.error else f"Answered via {reply.provider or 'provider'}",
            project=conversation.project,
            detail=reply.error or f"{reply.tokens_used} tokens",
            ok=not reply.error,
        )

        return {
            "conversation": conversation.to_dict(),
            "user_message": user_message.to_dict(),
            "assistant_message": assistant_message.to_dict(),
            "context": snapshot.to_dict() if snapshot else None,
            # Additive and observational: what the reasoning layer concluded, so
            # the execution path can be inspected rather than reconstructed.
            "assessment": observed_assessment(assessment),
        }

    def stream_message(
        self, project: str, conversation_id: str, content: str
    ) -> Iterator[dict[str, Any]]:
        """
        Record a user message and stream the answer, persisting either way.

        Yields plain dicts the API turns into SSE frames. Three properties make
        this safe to interrupt at any point:

        **The user message is persisted before the provider is called**, so a
        failure or a stop never loses what the human said.

        **Stopping persists the partial.** Closing this generator raises
        GeneratorExit inside it; the ``finally`` block writes whatever text
        arrived as an assistant message marked ``incomplete``. A stopped
        response is preserved and visibly partial — never discarded, and never
        presented as a finished answer.

        **The transcript never claims more than happened.** An empty stream and
        a mid-stream failure both produce a message carrying the error rather
        than a blank bubble.
        """
        text = content.strip()
        if not text:
            raise ValueError("A message cannot be empty.")

        conversation = self._store.get(project, conversation_id)
        if conversation.is_archived:
            raise ConversationArchivedError(conversation_id)

        snapshot = self._snapshot(project, query=text, carry=conversation.subject)
        conversation.active_snapshot_id = snapshot.id
        # Carry the subject forward, so the next question need not restate it.
        conversation.subject = _subject_of(snapshot) or conversation.subject

        stamp = self._now()
        user_message = Message(
            id=self._store.next_message_id(conversation),
            role=MessageRole.USER,
            content=text,
            created_at=stamp,
            snapshot_id=snapshot.id,
        )
        conversation.messages.append(user_message)
        if conversation.title in ("", "New conversation"):
            conversation.title = derive_title(text)
        conversation.updated_at = stamp
        self._store.save(conversation)

        yield {"type": "user", "message": user_message.to_dict()}
        yield {"type": "context", "context": snapshot.to_dict()}

        parts: list[str] = []
        reply: WorkspaceReply | None = None
        finished = False
        # Bound before the try because the finally block reads it: a provider
        # that fails before the request is built would otherwise raise NameError
        # from the cleanup path, turning a recorded failure into a crash.
        assessment: Any = None

        try:
            if self._responder is None:
                reply = WorkspaceReply(
                    content="",
                    error=(
                        "No AI provider is configured for this MondayOS instance, so the "
                        "workspace cannot answer. Configure a provider in MondayConfig."
                    ),
                )
            else:
                self._activity.record(
                    ActivityKind.PROVIDER,
                    f"Calling {self._responder.name}",
                    project=conversation.project,
                )
                request = self._request(conversation, snapshot, text, conversation.messages[:-1])
                assessment = request.assessment
                for chunk in self._responder.respond_stream(request):
                    if chunk.done:
                        reply = chunk.reply
                        continue
                    if chunk.text:
                        parts.append(chunk.text)
                        yield {"type": "delta", "text": chunk.text}
            finished = True
        finally:
            # Runs on normal completion AND on GeneratorExit when the caller
            # stops us. Either way the turn is written down.
            message = self._persist_stream(
                conversation, snapshot, parts, reply, finished, assessment
            )
            if finished:
                yield {
                    "type": "done",
                    "message": message.to_dict(),
                    "conversation": conversation.to_dict(),
                }

    def _persist_stream(
        self,
        conversation: Conversation,
        snapshot: ContextSnapshot,
        parts: list[str],
        reply: WorkspaceReply | None,
        finished: bool,
        assessment: Any = None,
    ) -> Message:
        """Write the assistant turn, however the stream ended."""
        streamed = "".join(parts).strip()

        if not finished:
            # Stopped by the caller. Whatever arrived is a real partial answer.
            content, error, incomplete = streamed, "", True
            provider = self._responder.name if self._responder else ""
            model, tokens = "", 0
            if not content:
                error = "Generation was stopped before any response arrived."
        elif reply is None:
            content, error, incomplete = streamed, "The response stream ended unexpectedly.", True
            provider = self._responder.name if self._responder else ""
            model, tokens = "", 0
        else:
            content = reply.content or streamed
            error, incomplete = reply.error, reply.incomplete
            provider, model, tokens = reply.provider, reply.model, reply.tokens_used

        message = Message(
            id=self._store.next_message_id(conversation),
            role=MessageRole.ASSISTANT,
            content=content,
            created_at=self._now(),
            provider=provider,
            model=model,
            snapshot_id=snapshot.id,
            tokens_used=tokens,
            error=error,
            incomplete=incomplete,
        )
        conversation.messages.append(message)
        conversation.updated_at = message.created_at
        _capture_strategy(conversation, assessment, message, snapshot, self._now())
        self._store.save(conversation)
        self._activity.record(
            ActivityKind.PERSIST,
            "Saved conversation",
            project=conversation.project,
            detail=("stopped — partial saved" if not finished else (error or "complete")),
            ok=not error,
        )
        return message

    def _request(
        self,
        conversation: Conversation,
        snapshot: ContextSnapshot | None,
        text: str,
        history: list[Message],
    ) -> WorkspaceRequest:
        """
        Build the responder request, compacting a long history.

        The stored transcript is untouched: compaction decides only what is
        *sent*. A long thread contributes recent turns verbatim plus a
        deterministic digest of what came before.
        """
        plan = compaction.compact(history, summarizer=self._summarizer)
        return WorkspaceRequest(
            project=conversation.project,
            message=text,
            snapshot=snapshot,
            history=plan.verbatim,
            conversation_id=conversation.id,
            history_digest=plan.digest,
            assessment=self._assessment(conversation, text, snapshot),
        )

    def _assessment(
        self,
        conversation: Conversation,
        text: str,
        snapshot: ContextSnapshot | None,
    ) -> Any:
        """
        What MondayOS concludes about this turn, before the model is called.

        Runs between retrieval and generation, which is the whole point: the
        model receives conclusions to explain rather than documents to summarise.

        Failure here is never fatal. A reasoning layer that can take down a
        conversation is worse than one that occasionally has nothing to add, so
        an exception yields no assessment and the turn proceeds as a grounded
        answer.
        """
        if self._assess is None:
            return None
        try:
            return self._assess(
                conversation.project,
                text,
                conversation.subject,
                _thin(snapshot),
                conversation.strategy,
                snapshot.fingerprint if snapshot is not None else "",
            )
        except Exception:  # noqa: BLE001 — reasoning must never break a turn
            return None

    def retry_message(self, project: str, conversation_id: str) -> dict[str, Any]:
        """
        Re-answer the last user message.

        Drops trailing assistant turns back to the last user message and asks
        again. Used after a provider failure, and equally valid for a poor answer:
        the failed attempt is replaced rather than accumulating dead turns in the
        transcript.
        """
        conversation = self._store.get(project, conversation_id)
        if conversation.is_archived:
            raise ConversationArchivedError(conversation_id)

        last_user = conversation.last_user_message()
        if last_user is None:
            raise MessageNotFoundError("(no user message)", conversation_id)

        index = conversation.messages.index(last_user)
        conversation.messages = conversation.messages[: index + 1]

        snapshot = self._snapshot(project)
        conversation.active_snapshot_id = snapshot.id

        reply, assessment = self._respond(
            project=conversation.project,
            conversation=conversation,
            snapshot=snapshot,
            text=last_user.content,
            history=conversation.messages[:-1],
        )
        assistant_message = Message(
            id=self._store.next_message_id(conversation),
            role=MessageRole.ASSISTANT,
            content=reply.content,
            created_at=self._now(),
            provider=reply.provider,
            model=reply.model,
            snapshot_id=snapshot.id,
            tokens_used=reply.tokens_used,
            error=reply.error,
        )
        conversation.messages.append(assistant_message)
        conversation.updated_at = assistant_message.created_at
        _capture_strategy(conversation, assessment, assistant_message, snapshot, self._now())
        self._store.save(conversation)

        return {
            "conversation": conversation.to_dict(),
            "assistant_message": assistant_message.to_dict(),
            "context": snapshot.to_dict(),
        }

    def _respond(
        self,
        project: str,
        conversation: Conversation,
        snapshot: ContextSnapshot | None,
        text: str,
        history: list[Message],
    ) -> tuple[WorkspaceReply, Any]:
        """
        Ask the responder, turning "nothing configured" into a recorded failure.

        Builds its request through ``_request`` rather than inline. It used to
        construct one directly, which quietly meant this path never received an
        assessment at all: every non-streaming turn ran without the reasoning
        layer while the streaming path had it.

        Returns the assessment alongside the reply so the caller can record what
        was recommended. Rebuilding it at persist time would run the analysis
        twice and could produce a different winner than the one the user read.
        """
        if self._responder is None:
            return (
                WorkspaceReply(
                    content="",
                    error=(
                        "No AI provider is configured for this MondayOS instance, so the "
                        "workspace cannot answer. Configure a provider in MondayConfig."
                    ),
                ),
                None,
            )
        request = self._request(conversation, snapshot, text, history)
        return self._responder.respond(request), request.assessment

    # ---------------------------------------------------------------- search

    def search_conversations(
        self, query: str, project: str = "", scope: str = "project", limit: int = 25
    ) -> dict[str, Any]:
        """
        Search conversations by title and visible content.

        Scoped to one project by default. ``scope="all"`` searches every project
        that has conversations, and every hit names its project — a cross-project
        search that did not would be the same disclosure as a context leak,
        arriving through a different door.
        """
        if scope not in ("project", "all"):
            raise ValueError(f"scope must be 'project' or 'all'; got {scope!r}.")
        if scope == "project" and not project:
            raise ValueError("A project is required unless scope='all'.")

        slugs = self._store.projects_with_conversations() if scope == "all" else [slugify(project)]
        conversations: list[Conversation] = []
        for slug in slugs:
            conversations.extend(self._store.list(slug, include_archived=True))

        hits = search.search(conversations, query, limit=limit)
        self._activity.record(
            ActivityKind.KNOWLEDGE,
            f"Searched conversations for {query!r}",
            project="" if scope == "all" else slugify(project),
            detail=f"{len(hits)} match(es) across {len(slugs)} project(s)",
        )
        return {
            "query": query,
            "scope": scope,
            "project": "" if scope == "all" else slugify(project),
            "projects_searched": slugs,
            "hits": [h.to_dict() for h in hits],
        }

    # -------------------------------------------------------------- briefing

    def briefing(self, project: str = "") -> dict[str, Any]:
        """
        Where work stands, from stored state alone.

        Nothing here is inferred beyond what the task system and git already
        record. With no project given, the most recently updated conversation
        across all projects decides which project to brief on — that is the last
        place work actually happened, not a guess.
        """
        latest: dict[str, Any] | None = None
        slugs = [slugify(project)] if project else self._store.projects_with_conversations()

        candidates: list[Conversation] = []
        for slug in slugs:
            candidates.extend(self._store.list(slug))
        if candidates:
            candidates.sort(key=lambda c: (c.updated_at, c.id), reverse=True)
            latest = candidates[0].summary_dict()

        # An empty project means "brief across everything", not "a project whose
        # name is empty" -- so it never reaches slug validation.
        target = (slugify(project) if project else "") or (str(latest["project"]) if latest else "")
        active = self._read_tasks(target) if (self._read_tasks and target) else []
        completed = self._read_completed(target) if (self._read_completed and target) else []
        git = self._git_lines(target) if (self._git_lines and target) else []

        result = briefing_mod.build_briefing(
            now=self._now(),
            latest=latest,
            active_tasks=active,
            completed_tasks=completed,
            git_lines=git,
        )
        return result.to_dict()

    # ------------------------------------------------------------------ tasks

    def create_task_from_message(
        self,
        project: str,
        conversation_id: str,
        message_id: str,
        title: str = "",
        objective: str = "",
    ) -> dict[str, Any]:
        """
        Create a MondayOS task from an assistant response.

        Uses the existing TaskManager through an injected hook — the workspace
        builds no second task system. Provenance travels with the task: the
        project, conversation and message it came from, so a task created in a
        conversation can be traced back to the reasoning that produced it.
        """
        if self._create_task is None:
            raise ResponderUnavailableError(
                "No task system is wired into this workspace, so a task cannot be created."
            )

        conversation = self._store.get(project, conversation_id)
        message = next((m for m in conversation.messages if m.id == message_id), None)
        if message is None:
            raise MessageNotFoundError(message_id, conversation_id)
        if message.role is not MessageRole.ASSISTANT:
            raise ValueError(
                f"Only an assistant message can become a task; {message_id} is a "
                f"{message.role.value} message."
            )
        if message.failed or not message.content.strip():
            raise ValueError(f"Message {message_id} recorded a failure and has no content.")

        created = self._create_task(
            {
                "project": conversation.project,
                "title": title.strip() or derive_title(message.content),
                "objective": objective.strip() or message.content.strip(),
                "conversation_id": conversation.id,
                "message_id": message.id,
            }
        )

        task_id = str(created.get("id", ""))
        if task_id and task_id not in conversation.task_refs:
            conversation.task_refs.append(task_id)
        event = Message(
            id=self._store.next_message_id(conversation),
            role=MessageRole.EVENT,
            content=f"Created task {task_id}: {created.get('title', '')}",
            created_at=self._now(),
        )
        conversation.messages.append(event)
        conversation.updated_at = event.created_at
        self._store.save(conversation)

        self._activity.record(
            ActivityKind.TASK,
            f"Created task {task_id}",
            project=conversation.project,
            detail=str(created.get("title", "")),
        )
        # A new task changes what the next snapshot should contain.
        self.invalidate_context(conversation.project)
        return {
            "task": created,
            "conversation_id": conversation.id,
            "message_id": message.id,
            "project": conversation.project,
        }

    # ------------------------------------------------------------- knowledge

    def save_message_to_knowledge(
        self,
        project: str,
        conversation_id: str,
        message_id: str,
        title: str = "",
    ) -> dict[str, Any]:
        """
        Capture one assistant message as durable MondayOS knowledge.

        Explicit only. Nothing is captured automatically in increment 1: a
        conversation contains as much exploration as conclusion, and writing all
        of it to knowledge would fill the store with things nobody decided.

        Uses the existing ``KnowledgeStore`` — the workspace creates no second
        knowledge system.
        """
        if self._capture_knowledge is None:
            raise ResponderUnavailableError(
                "No knowledge store is wired into this workspace, so a message cannot be captured."
            )

        conversation = self._store.get(project, conversation_id)
        message = next((m for m in conversation.messages if m.id == message_id), None)
        if message is None:
            raise MessageNotFoundError(message_id, conversation_id)
        if message.role is not MessageRole.ASSISTANT:
            raise ValueError(
                f"Only an assistant message can be captured as knowledge; {message_id} is a "
                f"{message.role.value} message."
            )
        if message.failed or not message.content.strip():
            raise ValueError(
                f"Message {message_id} recorded a failure and has no content to capture."
            )

        entry_title = title.strip() or derive_title(message.content, fallback=conversation.title)
        entry_id = self._capture_knowledge(
            {
                "project": conversation.project,
                "title": entry_title,
                "body": message.content,
                "conversation_id": conversation.id,
                "message_id": message.id,
                "provider": message.provider,
                "model": message.model,
                "snapshot_id": message.snapshot_id,
            }
        )

        # Record the capture in the transcript. Knowledge that came from a
        # conversation should be traceable from both ends.
        event = Message(
            id=self._store.next_message_id(conversation),
            role=MessageRole.EVENT,
            content=f"Saved to project knowledge as {entry_id}: {entry_title}",
            created_at=self._now(),
        )
        conversation.messages.append(event)
        conversation.updated_at = event.created_at
        self._store.save(conversation)

        return {
            "knowledge_id": entry_id,
            "title": entry_title,
            "project": conversation.project,
            "conversation_id": conversation.id,
            "message_id": message.id,
        }


__all__ = [
    "ConversationArchivedError",
    "ConversationNotFoundError",
    "WorkspaceService",
]


# Below this many context items, retrieval counts as thin and reasoning is
# attached to a grounded answer. Tuned to catch the case this layer exists for —
# a question the project barely covers — without firing on every ordinary lookup,
# which would attach a memo to "where is X defined".
THIN_CONTEXT_ITEMS = 8


def observed_assessment(assessment: Any) -> dict[str, Any] | None:
    """
    What the reasoning layer concluded this turn, as plain data.

    **Observational metadata.** Nothing in MondayOS branches on it and no
    existing caller is affected; it is additive, and a client that ignores it
    behaves exactly as before.

    It exists because the register a turn was routed to was not recoverable
    afterwards. `Message` records provider, model, tokens and `incomplete`, and
    strategic state is written only for executive turns -- so a grounded turn
    left no trace of *why* it was grounded. Anything checking that routing
    behaved had to re-run the router and compare its own answer to itself, which
    measures a reconstruction rather than the execution path. A harness that
    cannot see what happened cannot report that something else did.

    Only fields already visible in a completed answer appear here: the register
    and why, whether this continued a prior decision, whether that decision had
    gone stale, and the three scores the user was shown. No prompt, no context
    snapshot, no rejected candidate, no model reasoning -- the same rule
    `StrategicState` holds, for the same reason.
    """
    if assessment is None:
        return None

    top = assessment.recommendations[0] if getattr(assessment, "recommendations", None) else None
    slug = ""
    if top is not None:
        slug = str(getattr(top, "initiative_slug", "") or "")

    def _value(score: Any) -> float:
        return round(float(getattr(score, "value", 0.0) or 0.0), 4)

    return {
        "mode": getattr(assessment.mode, "value", str(assessment.mode)),
        "mode_reason": str(getattr(assessment, "mode_reason", "")),
        "continuation": bool(getattr(assessment, "continuation", False)),
        "stale": bool(getattr(assessment, "stale", False)),
        "stale_because": str(getattr(assessment, "stale_because", "")),
        "obsolete": bool(getattr(assessment, "obsolete", False)),
        "replaced_stale": bool(getattr(assessment, "replaced_stale", False)),
        "has_reasoning": bool(getattr(assessment, "has_reasoning", False)),
        "recommendation": str(getattr(top, "statement", "")) if top is not None else "",
        "recommendation_key": (
            recommendation_key(str(top.statement), slug) if top is not None else ""
        ),
        "initiative_slug": slug,
        "initiative_slugs": [
            str(getattr(i, "slug", "")) for i in getattr(assessment, "initiatives", []) or []
        ],
        "alternatives": [
            str(getattr(a, "statement", "")) for a in getattr(top, "alternatives", ()) or ()
        ]
        if top is not None
        else [],
        "evidence_strength": _value(getattr(top, "evidence_strength", None)) if top else 0.0,
        "confidence": _value(getattr(top, "confidence", None)) if top else 0.0,
        "execution_risk": _value(getattr(top, "execution_risk", None)) if top else 0.0,
    }


def _capture_strategy(
    conversation: Conversation,
    assessment: Any,
    message: Message,
    snapshot: ContextSnapshot | None,
    now: datetime,
) -> None:
    """
    Record what Monday recommended, when this turn actually recommended something.

    Three conditions, each a correctness rule rather than a filter.

    **It must be a fresh executive assessment.** A continuation answers about a
    decision already stored; letting it overwrite the record with a re-derivation
    of itself would let the decision drift turn by turn while appearing stable.

    **The turn must have completed.** A truncated narration may have stopped
    before the recommendation was shown, and the guarantee this state rests on is
    that everything in it was visible in a completed answer.

    **There must be a recommendation.** An executive turn that ranked nothing has
    nothing for a follow-up to refer to.
    """
    if assessment is None or message.incomplete or message.failed:
        return
    if getattr(assessment, "continuation", False):
        return
    mode = getattr(assessment, "mode", None)
    if mode is None or getattr(mode, "value", "") != "executive":
        return
    recommendations = list(getattr(assessment, "recommendations", []) or [])
    if not recommendations:
        return

    top = recommendations[0]
    slug = _slug_for(assessment, getattr(top, "initiative", ""))
    risk = getattr(top, "execution_risk", None)

    conversation.strategy = StrategicState(
        question=assessment.question,
        topic=str(getattr(assessment, "mode_reason", "")).replace("matched ", "").strip(),
        source_message_id=message.id,
        snapshot_id=snapshot.id if snapshot is not None else "",
        fingerprint=snapshot.fingerprint if snapshot is not None else "",
        project=conversation.project,
        created_at=now,
        recommendation_key=recommendation_key(top.statement, slug),
        recommendation=top.statement,
        rationale=top.rationale,
        initiative_slug=slug,
        effort=getattr(top, "effort", ""),
        alternatives=[
            AlternativeRef(statement=a.statement, why_not=a.why_not)
            for a in list(getattr(top, "alternatives", []) or [])[:MAX_ALTERNATIVES]
        ],
        evidence_refs=[
            EvidenceRef(
                kind=c.kind.value,
                reference=c.reference,
                path=c.path,
                line=c.line,
                because=c.because,
            )
            for c in list(top.evidence.citations)[:MAX_EVIDENCE_REFS]
        ],
        initiative_slugs=[
            str(i.slug)
            for i in list(getattr(assessment, "initiatives", []) or [])[:MAX_INITIATIVES]
        ],
        evidence_strength=Score(top.strength.score, top.strength.band.value),
        confidence=Score(top.confidence.score, top.confidence.band.value),
        execution_risk=Score(risk.score, risk.band.value) if risk is not None else Score(),
    )


def _slug_for(assessment: Any, initiative_name: str) -> str:
    """The capability slug behind a recommendation's display name."""
    for initiative in list(getattr(assessment, "initiatives", []) or []):
        if initiative.name == initiative_name:
            return str(initiative.slug)
    return slugify(initiative_name) if initiative_name else ""


def _thin(snapshot: ContextSnapshot | None) -> bool:
    """
    Whether retrieval came back with too little to answer from directly.

    An absent snapshot is thin by definition. So is one where the deterministic
    project index found nothing: the other sources describe the project in
    general, and a question they alone can reach is one nothing specific was
    retrieved for.
    """
    if snapshot is None:
        return True
    total = sum(len(source.items) for source in snapshot.sources)
    intelligence = next((s for s in snapshot.sources if s.name == "intelligence"), None)
    return total < THIN_CONTEXT_ITEMS or intelligence is None or not intelligence.items


def _subject_of(snapshot: ContextSnapshot) -> str:
    """
    What the intelligence source decided this exchange was about.

    Read back off the snapshot rather than recomputed: the question engine
    already resolved the subject (including its own carry-over), and deriving it
    a second time here would be a second implementation that could disagree.
    """
    source = snapshot.source("intelligence")
    if source is None:
        return ""
    for item in source.items:
        if item.startswith("Subject: "):
            return item.removeprefix("Subject: ").strip()
    return ""
