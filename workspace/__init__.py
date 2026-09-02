"""
workspace — the MondayOS AI Workspace.

The conversational surface where project work happens: a durable Conversation
domain, an OS-level Context Engine that assembles project reality, and a
responder seam that will later become the model router.

This package **orchestrates existing MondayOS capabilities and duplicates none
of them**. Projects come from the registry, tasks from the TaskManager, knowledge
from the KnowledgeStore, model access from the provider abstraction. The only new
domain is the Conversation, because MondayOS had no first-class representation of
a durable dialogue.

External callers use ``Monday.workspace(...)``; this surface exists for the API
layer and for tests.
"""

from workspace.context import ContextEngine, ContextSnapshot, ContextSource
from workspace.errors import (
    ConversationArchivedError,
    ConversationNotFoundError,
    InvalidProjectError,
    MessageNotFoundError,
    ResponderUnavailableError,
    WorkspaceError,
)
from workspace.models import (
    ArtifactKind,
    ArtifactRef,
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
    derive_title,
    slugify,
)
from workspace.responder import (
    ProviderWorkspaceResponder,
    WorkspaceReply,
    WorkspaceRequest,
    WorkspaceResponder,
)
from workspace.service import WorkspaceService
from workspace.store import ConversationStore

__all__ = [
    "ArtifactKind",
    "ArtifactRef",
    "ContextEngine",
    "ContextSnapshot",
    "ContextSource",
    "Conversation",
    "ConversationArchivedError",
    "ConversationNotFoundError",
    "ConversationStatus",
    "ConversationStore",
    "InvalidProjectError",
    "Message",
    "MessageNotFoundError",
    "MessageRole",
    "ProviderWorkspaceResponder",
    "ResponderUnavailableError",
    "WorkspaceError",
    "WorkspaceReply",
    "WorkspaceRequest",
    "WorkspaceResponder",
    "WorkspaceService",
    "derive_title",
    "slugify",
]
