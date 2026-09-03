"""
Conversation persistence — MondayOS owns the record, the browser caches it.

Layout:

    <root>/workspace/conversations/<project>/.sequences.json
    <root>/workspace/conversations/<project>/CONV-0001.md

Markdown with YAML frontmatter, per ADR-003, following the same shape as
``agents/registry.py`` and ``growth/store.py`` so there is one persistence idiom
in MondayOS rather than three.

**The project is a directory, not a field.** A read is scoped by the path it
opens, so a query cannot span projects even if a caller passes the wrong filter —
there is no filter to pass. This is the same reasoning that made the Growth
Workspace a directory (ADR-011), applied at OS level (ADR-015).

**Sequence counters are per project.** A shared counter would let one project
infer another's conversation volume from the gaps in its own ids. Cheap to avoid,
impossible to retrofit once ids are allocated.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from workspace.errors import ConversationNotFoundError
from workspace.models import (
    ArtifactRef,
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
    iso,
    parse_iso,
    slugify,
)

_SEQUENCES_FILENAME = ".sequences.json"
_CONVERSATION_PREFIX = "CONV"
_MESSAGE_PREFIX = "MSG"
_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)

# `ConversationStore.list` mirrors `ProjectRegistry.list`, which means the name
# `list` is shadowed inside the class body and a bare `list[str]` annotation
# there resolves to the method instead of the builtin. Aliased once here rather
# than renaming the method away from the convention it follows.
_StrList = list[str]


class ConversationStore:
    """
    Reads and writes conversations under one MondayOS root.

    Every method takes a project and resolves it to a directory first, so there
    is no code path that touches a conversation without naming its project.
    """

    def __init__(self, root: Path = Path(".")) -> None:
        self._root = Path(root)
        self._base = self._root / "workspace" / "conversations"

    # ------------------------------------------------------------------ paths

    def project_dir(self, project: str) -> Path:
        """
        The directory owning one project's conversations.

        The slug is normalised and validated before it becomes a path segment.
        A name that does not survive slugification cannot address a directory,
        which is what stops `../other` from ever being a project.
        """
        slug = slugify(project)
        if not slug:
            raise ValueError(
                f"{project!r} does not normalise to a usable project slug. A conversation "
                "must belong to a nameable project."
            )
        return self._base / slug

    def _path(self, project: str, conversation_id: str) -> Path:
        return self.project_dir(project) / f"{conversation_id}.md"

    # ------------------------------------------------------------------- ids

    def _next_id(self, project: str, prefix: str) -> str:
        """
        Allocate the next id from this project's own counter.

        Recovers from a drifted counter by consulting what is actually on disk,
        mirroring ``tasks/manager.py``: a counter that reads lower than reality
        would otherwise reissue a live id and overwrite a real conversation.
        """
        directory = self.project_dir(project)
        directory.mkdir(parents=True, exist_ok=True)
        sequences_path = directory / _SEQUENCES_FILENAME

        sequences: dict[str, int] = {}
        if sequences_path.is_file():
            try:
                loaded = json.loads(sequences_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    sequences = {str(k): int(v) for k, v in loaded.items()}
            except (OSError, ValueError):
                sequences = {}

        counter = max(sequences.get(prefix, 0), self._highest_on_disk(directory, prefix))
        counter += 1
        sequences[prefix] = counter
        sequences_path.write_text(json.dumps(sequences, indent=2) + "\n", encoding="utf-8")
        return f"{prefix}-{counter:04d}"

    @staticmethod
    def _highest_on_disk(directory: Path, prefix: str) -> int:
        highest = 0
        if not directory.is_dir():
            return highest
        for path in directory.glob(f"{prefix}-*.md"):
            try:
                highest = max(highest, int(path.stem.split("-")[-1]))
            except ValueError:
                continue
        return highest

    def next_message_id(self, conversation: Conversation) -> str:
        """
        The next message id within one conversation.

        Derived from the conversation's own messages rather than a global
        counter: message ids are only ever meaningful inside their conversation,
        and a shared counter would be a second thing to keep consistent.
        """
        highest = 0
        for message in conversation.messages:
            try:
                highest = max(highest, int(message.id.split("-")[-1]))
            except ValueError:
                continue
        return f"{_MESSAGE_PREFIX}-{highest + 1:04d}"

    # ---------------------------------------------------------------- writes

    def create(self, project: str, title: str, now: datetime | None = None) -> Conversation:
        """Create and persist an empty conversation for one project."""
        stamp = now or datetime.now(tz=UTC)
        conversation = Conversation(
            id=self._next_id(project, _CONVERSATION_PREFIX),
            project=slugify(project),
            title=title,
            created_at=stamp,
            updated_at=stamp,
        )
        self.save(conversation)
        return conversation

    def save(self, conversation: Conversation) -> Path:
        """
        Write a conversation to disk, replacing what was there.

        Last-write-wins. Acceptable for a single-operator workstation and an
        explicit constraint recorded in ADR-015 — not an oversight.
        """
        path = self._path(conversation.project, conversation.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_serialize(conversation), encoding="utf-8")
        return path

    def delete(self, project: str, conversation_id: str) -> None:
        """Remove a conversation permanently. Archiving is the reversible option."""
        path = self._path(project, conversation_id)
        if not path.is_file():
            raise ConversationNotFoundError(conversation_id, project)
        path.unlink()

    # ----------------------------------------------------------------- reads

    def get(self, project: str, conversation_id: str) -> Conversation:
        path = self._path(project, conversation_id)
        if not path.is_file():
            raise ConversationNotFoundError(conversation_id, project)
        return _parse(path.read_text(encoding="utf-8"), fallback_project=slugify(project))

    def exists(self, project: str, conversation_id: str) -> bool:
        return self._path(project, conversation_id).is_file()

    def list(self, project: str, include_archived: bool = False) -> list[Conversation]:
        """
        Every conversation in one project, newest first.

        Reads only this project's directory, so the listing is scoped by the
        filesystem rather than by a filter that could be forgotten.
        """
        directory = self.project_dir(project)
        if not directory.is_dir():
            return []

        conversations: list[Conversation] = []
        for path in sorted(directory.glob(f"{_CONVERSATION_PREFIX}-*.md")):
            try:
                conversation = _parse(
                    path.read_text(encoding="utf-8"), fallback_project=slugify(project)
                )
            except (OSError, ValueError):
                # One malformed file must not make the sidebar unusable.
                continue
            if conversation.is_archived and not include_archived:
                continue
            conversations.append(conversation)

        conversations.sort(key=lambda c: (c.updated_at, c.id), reverse=True)
        return conversations

    def projects_with_conversations(self) -> _StrList:
        """Project slugs that have at least one conversation on disk."""
        if not self._base.is_dir():
            return []
        return sorted(p.name for p in self._base.iterdir() if p.is_dir())


# --------------------------------------------------------------------------- #
# serialization
# --------------------------------------------------------------------------- #


def _serialize(conversation: Conversation) -> str:
    """
    Render a conversation as Markdown with YAML frontmatter.

    Messages live in the frontmatter rather than the body: they are structured
    data with roles and provenance, and round-tripping them through prose would
    lose that. The body is a human-readable transcript — a convenience for
    reading the file directly, never the source of truth on load.
    """
    frontmatter: dict[str, Any] = {
        "id": conversation.id,
        "project": conversation.project,
        "title": conversation.title,
        "status": conversation.status.value,
        "created_at": iso(conversation.created_at),
        "updated_at": iso(conversation.updated_at),
        "active_snapshot_id": conversation.active_snapshot_id,
        "subject": conversation.subject,
        "artifact_refs": [a.to_dict() for a in conversation.artifact_refs],
        "task_refs": list(conversation.task_refs),
        "messages": [m.to_dict() for m in conversation.messages],
    }
    header = yaml.dump(frontmatter, sort_keys=False, allow_unicode=True, width=1000)

    lines = [f"# {conversation.title}", ""]
    for message in conversation.messages:
        who = message.role.value
        if message.role is MessageRole.ASSISTANT and message.provider:
            who = f"assistant ({message.provider})"
        lines.append(f"## {who} — {iso(message.created_at)}")
        lines.append("")
        lines.append(message.error if message.failed else message.content)
        lines.append("")
    return f"---\n{header}---\n\n" + "\n".join(lines)


def _parse(text: str, fallback_project: str) -> Conversation:
    """Load a conversation from its file. Frontmatter is authoritative."""
    match = _FRONTMATTER.match(text)
    if not match:
        raise ValueError("Conversation file has no YAML frontmatter.")

    try:
        loaded = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise ValueError(f"Conversation frontmatter is not valid YAML: {exc}") from exc

    if not isinstance(loaded, dict):
        raise ValueError("Conversation frontmatter is not a mapping.")

    return Conversation(
        id=str(loaded.get("id", "")),
        project=str(loaded.get("project", "") or fallback_project),
        title=str(loaded.get("title", "")),
        created_at=parse_iso(str(loaded.get("created_at", ""))),
        updated_at=parse_iso(str(loaded.get("updated_at", ""))),
        status=ConversationStatus(str(loaded.get("status", "active"))),
        active_snapshot_id=str(loaded.get("active_snapshot_id", "")),
        subject=str(loaded.get("subject", "")),
        messages=[Message.from_dict(m) for m in loaded.get("messages") or []],
        artifact_refs=[ArtifactRef.from_dict(a) for a in loaded.get("artifact_refs") or []],
        task_refs=[str(t) for t in loaded.get("task_refs") or []],
    )
