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

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workspace import search
from workspace.context.engine import ContextEngine
from workspace.context.snapshot import ContextSnapshot
from workspace.errors import (
    ConversationArchivedError,
    ConversationNotFoundError,
    MessageNotFoundError,
    ResponderUnavailableError,
)
from workspace.models import (
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
    derive_title,
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
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._root = Path(root)
        self._store = ConversationStore(self._root)
        self._engine = engine
        self._responder = responder
        self._list_projects = list_projects
        self._capture_knowledge = capture_knowledge
        self._now = now or (lambda: datetime.now(tz=UTC))

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

    def _snapshot(self, project: str, query: str = "", allow_reuse: bool = True) -> ContextSnapshot:
        if self._engine is None:
            return ContextSnapshot(id="CTX-none", project=slugify(project), created_at=self._now())
        if allow_reuse:
            snapshot, _reused = self._engine.build_or_reuse(project, query)
            return snapshot
        return self._engine.build(project, query)

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

        snapshot = self._snapshot(project, query=text) if rebuild_context else None
        if snapshot is not None:
            conversation.active_snapshot_id = snapshot.id

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

        reply = self._respond(
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
        )
        conversation.messages.append(assistant_message)
        conversation.updated_at = assistant_message.created_at
        self._store.save(conversation)

        return {
            "conversation": conversation.to_dict(),
            "user_message": user_message.to_dict(),
            "assistant_message": assistant_message.to_dict(),
            "context": snapshot.to_dict() if snapshot else None,
        }

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

        reply = self._respond(
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
    ) -> WorkspaceReply:
        """Ask the responder, turning "nothing configured" into a recorded failure."""
        if self._responder is None:
            return WorkspaceReply(
                content="",
                error=(
                    "No AI provider is configured for this MondayOS instance, so the "
                    "workspace cannot answer. Configure a provider in MondayConfig."
                ),
            )
        return self._responder.respond(
            WorkspaceRequest(
                project=project,
                message=text,
                snapshot=snapshot,
                history=history,
                conversation_id=conversation.id,
            )
        )

    # ---------------------------------------------------------------- search

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
        return {
            "query": query,
            "scope": scope,
            "project": "" if scope == "all" else slugify(project),
            "projects_searched": slugs,
            "hits": [h.to_dict() for h in hits],
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
