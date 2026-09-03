"""
The Conversation domain — MondayOS's first-class representation of a dialogue.

This is the one genuinely new domain the AI Workspace introduces. Everything
else it needs (projects, tasks, knowledge, providers) already exists and is
reused; MondayOS simply had no durable notion of "a conversation about a
project".

Two rules are enforced by the types rather than by review.

**Only visible content is persisted.** A message holds the text a human could
read on screen, plus provenance about which provider produced it. Provider-private
reasoning is not requested, not stored, and has no field to live in. This is not
an oversight to be corrected later — it is the contract (ADR-015).

**A conversation belongs to exactly one project.** The project is set at
construction and is part of the storage path, so scoping is structural. There is
no method that moves a conversation between projects, because the context that
produced its answers would no longer apply (ADR-017).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


# The visible roles. `EVENT` records something that happened *to* the
# conversation (a project switch, a failure, a knowledge capture) so the
# transcript stays honest about gaps rather than presenting an unbroken dialogue.
class MessageRole(Enum):
    """Who produced a message."""

    USER = "user"
    ASSISTANT = "assistant"
    EVENT = "event"


class ConversationStatus(Enum):
    """Lifecycle of a conversation."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class ArtifactKind(Enum):
    """
    What a referenced artifact is.

    Increment 1 stores references only — nothing here is created or edited. The
    vocabulary is defined now so that references recorded today remain readable
    when the artifact system arrives, rather than being migrated from free text.
    """

    DOCUMENT = "document"
    FILE = "file"
    TASK = "task"
    PULL_REQUEST = "pull-request"
    IMAGE = "image"
    REPORT = "report"
    OTHER = "other"


@dataclass
class ArtifactRef:
    """A pointer to something that exists outside the conversation."""

    kind: ArtifactKind
    reference: str
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "reference": self.reference, "label": self.label}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArtifactRef:
        return cls(
            kind=ArtifactKind(str(data.get("kind", "other"))),
            reference=str(data.get("reference", "")),
            label=str(data.get("label", "")),
        )


@dataclass
class Message:
    """
    One turn in a conversation.

    ``provider``/``model`` are provenance for assistant messages: recorded so a
    reader knows what wrote this, never branched on. ``snapshot_id`` records the
    context this turn was answered against, which is what makes "why did Monday
    know this?" answerable months later against the context that actually applied
    rather than against the project as it looks now.
    """

    id: str
    role: MessageRole
    content: str
    created_at: datetime
    provider: str = ""
    model: str = ""
    snapshot_id: str = ""
    tokens_used: int = 0
    # Set when generation failed. The turn is kept rather than discarded: a user
    # message that got no answer is part of what happened, and hiding it makes the
    # transcript lie about the conversation.
    error: str = ""
    # True when generation stopped before the model finished — the operator
    # pressed stop, or the stream died mid-answer. Partial text shown as a
    # complete answer is a quiet correctness failure, so this is persisted and
    # rendered, never inferred at read time.
    incomplete: bool = False
    artifact_refs: list[ArtifactRef] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        """True when this turn recorded a generation failure."""
        return bool(self.error)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role.value,
            "content": self.content,
            "created_at": iso(self.created_at),
            "provider": self.provider,
            "model": self.model,
            "snapshot_id": self.snapshot_id,
            "tokens_used": self.tokens_used,
            "error": self.error,
            "incomplete": self.incomplete,
            "artifact_refs": [a.to_dict() for a in self.artifact_refs],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        return cls(
            id=str(data.get("id", "")),
            role=MessageRole(str(data.get("role", "user"))),
            content=str(data.get("content", "")),
            created_at=parse_iso(str(data.get("created_at", ""))),
            provider=str(data.get("provider", "")),
            model=str(data.get("model", "")),
            snapshot_id=str(data.get("snapshot_id", "")),
            tokens_used=int(data.get("tokens_used", 0) or 0),
            error=str(data.get("error", "")),
            incomplete=bool(data.get("incomplete", False)),
            artifact_refs=[ArtifactRef.from_dict(a) for a in data.get("artifact_refs") or []],
        )


@dataclass
class Conversation:
    """
    A durable dialogue about one project.

    The project is fixed at construction and is a path segment in storage, so a
    read cannot accidentally span projects.
    """

    id: str
    project: str
    title: str
    created_at: datetime
    updated_at: datetime
    status: ConversationStatus = ConversationStatus.ACTIVE
    active_snapshot_id: str = ""
    # What this conversation is currently about, carried forward from the last
    # question that named a subject. Twenty minutes into discussing the
    # ContextEngine, "where is that implemented?" should mean the ContextEngine —
    # requiring the operator to restate it is the difference between a tool and a
    # search box. Persisted so it survives a reload, like everything else here.
    subject: str = ""
    messages: list[Message] = field(default_factory=list)
    artifact_refs: list[ArtifactRef] = field(default_factory=list)
    task_refs: list[str] = field(default_factory=list)

    @property
    def is_archived(self) -> bool:
        return self.status is ConversationStatus.ARCHIVED

    @property
    def message_count(self) -> int:
        return len(self.messages)

    def visible_turns(self) -> list[Message]:
        """User and assistant messages only — what a model should see as dialogue."""
        return [m for m in self.messages if m.role in (MessageRole.USER, MessageRole.ASSISTANT)]

    def last_user_message(self) -> Message | None:
        for message in reversed(self.messages):
            if message.role is MessageRole.USER:
                return message
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project": self.project,
            "title": self.title,
            "status": self.status.value,
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
            "active_snapshot_id": self.active_snapshot_id,
            "subject": self.subject,
            "message_count": self.message_count,
            "messages": [m.to_dict() for m in self.messages],
            "artifact_refs": [a.to_dict() for a in self.artifact_refs],
            "task_refs": list(self.task_refs),
        }

    def summary_dict(self) -> dict[str, Any]:
        """The listing shape — no message bodies, so a sidebar load stays cheap."""
        return {
            "id": self.id,
            "project": self.project,
            "title": self.title,
            "status": self.status.value,
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
            "message_count": self.message_count,
        }


# Titles are derived, not invented: the first user message is what the operator
# actually said, which makes a conversation findable later by what it was about.
_TITLE_MAX = 60


def derive_title(text: str, fallback: str = "New conversation") -> str:
    """
    A short, human-recognisable title from the first thing the user said.

    Truncates on a word boundary so the sidebar never shows a title cut
    mid-word.
    """
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return fallback
    if len(cleaned) <= _TITLE_MAX:
        return cleaned
    return cleaned[: _TITLE_MAX - 1].rsplit(" ", 1)[0] + "…"


_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def slugify(value: str) -> str:
    """
    Normalise a project name to a filesystem-safe slug.

    This is the isolation primitive: a slug can contain no path separators and
    no traversal, so a project name can never escape its own directory.
    """
    slug = _SLUG_RE.sub("-", (value or "").strip().lower().replace(" ", "-"))
    return slug.strip("-")


def iso(value: datetime) -> str:
    """Serialize a datetime as ISO-8601 UTC with an explicit Z."""
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    """
    Parse an ISO-8601 timestamp, tolerating the Z suffix.

    Falls back to epoch rather than raising: a conversation with one unreadable
    timestamp is still worth showing, and a hard failure here would make the
    whole file unreadable over a formatting detail.
    """
    text = (value or "").strip()
    if not text:
        return datetime(1970, 1, 1, tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return datetime(1970, 1, 1, tzinfo=UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
