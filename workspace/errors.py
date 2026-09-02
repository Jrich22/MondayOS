"""
Typed errors for the AI Workspace.

One hierarchy so callers can catch the layer they care about: everything here
descends from ``WorkspaceError``, and the API surface turns them into failed
responses rather than letting them escape as tracebacks.
"""

from __future__ import annotations


class WorkspaceError(Exception):
    """Base class for every AI Workspace failure."""


class ConversationNotFoundError(WorkspaceError):
    """Raised when a conversation id does not exist in the named project."""

    def __init__(self, conversation_id: str, project: str) -> None:
        self.conversation_id = conversation_id
        self.project = project
        super().__init__(f"No conversation {conversation_id!r} in project {project!r}.")


class MessageNotFoundError(WorkspaceError):
    """Raised when a message id does not exist in the named conversation."""

    def __init__(self, message_id: str, conversation_id: str) -> None:
        self.message_id = message_id
        self.conversation_id = conversation_id
        super().__init__(f"No message {message_id!r} in conversation {conversation_id!r}.")


class InvalidProjectError(WorkspaceError):
    """
    Raised when a project cannot be resolved to a registered project.

    Deliberately not a "create it for me" path: a conversation attached to an
    unregistered project would have no context to load and no owner.
    """

    def __init__(self, project: str, detail: str = "") -> None:
        self.project = project
        suffix = f" {detail}" if detail else ""
        super().__init__(
            f"{project!r} is not a registered MondayOS project.{suffix} "
            "Register it first so the workspace has a project to load context from."
        )


class ResponderUnavailableError(WorkspaceError):
    """
    Raised when no responder is configured, or the configured one cannot run.

    Distinct from a provider *failure* mid-call: this is the case where there was
    never anything to call, which is a configuration problem with a different fix.
    """


class ConversationArchivedError(WorkspaceError):
    """Raised when a message is sent to a conversation that has been archived."""

    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        super().__init__(
            f"Conversation {conversation_id!r} is archived. Unarchive it before sending, "
            "so the history is not silently extended after someone decided it was done."
        )
