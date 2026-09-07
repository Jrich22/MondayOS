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

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from core.identity import require_slug


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


# --------------------------------------------------------------------------- #
# strategic continuity
# --------------------------------------------------------------------------- #


def recommendation_key(statement: str, initiative_slug: str = "") -> str:
    """
    A stable identifier for a recommendation, derived from what it says.

    Recommendations are produced fresh on every assessment and have no id of
    their own. Matching them by raw statement text is brittle -- a rewording
    between builds would silently look like a different recommendation -- so the
    key is a digest of the normalised statement plus the capability it lands in.

    Derived rather than allocated on purpose. A counter would need a sequence
    file, and two identical recommendations computed in different conversations
    would get different ids, which is exactly backwards: the same advice about
    the same capability is the same advice.
    """
    normalised = " ".join(statement.lower().split())
    return hashlib.sha256(f"{normalised}|{initiative_slug.lower()}".encode()).hexdigest()[:16]


@dataclass(frozen=True)
class EvidenceRef:
    """One citation, in the shape a stored recommendation needs to point at it."""

    kind: str
    reference: str
    path: str = ""
    line: int = 0
    because: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "reference": self.reference,
            "path": self.path,
            "line": self.line,
            "because": self.because,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceRef:
        return cls(
            kind=str(data.get("kind", "")),
            reference=str(data.get("reference", "")),
            path=str(data.get("path", "")),
            line=int(data.get("line", 0) or 0),
            because=str(data.get("because", "")),
        )


@dataclass(frozen=True)
class AlternativeRef:
    """An option that was shown to the user and not chosen."""

    statement: str
    why_not: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"statement": self.statement, "why_not": self.why_not}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AlternativeRef:
        return cls(
            statement=str(data.get("statement", "")),
            why_not=str(data.get("why_not", "")),
        )


@dataclass(frozen=True)
class Score:
    """One of the three measures, as it was displayed."""

    score: float = 0.0
    band: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"score": round(self.score, 3), "band": self.band}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Score:
        if not isinstance(data, dict):
            return cls()
        return cls(score=float(data.get("score", 0.0) or 0.0), band=str(data.get("band", "")))


# Caps. Strategic state is a continuity record, not a second transcript: it holds
# what a follow-up needs to refer to and nothing more.
MAX_ALTERNATIVES = 4
MAX_EVIDENCE_REFS = 12
MAX_INITIATIVES = 8


@dataclass
class StrategicState:
    """
    What Monday recommended, kept so a follow-up can refer to it.

    Every field here was **visible in a completed answer**. That is the rule the
    type exists to enforce: no prompts, no rendered assessment text, no model
    reasoning, no rejected candidates that never reached the user, no retrieval
    intermediates, no context snapshot. If a reader could not have seen it, it is
    not continuity state -- it is hidden reasoning wearing a struct.

    One per conversation and replaced wholesale, never appended. A list would
    become a second transcript, and the conversation already is one.

    Deliberately plain data: strings, floats and lists. It is a persistence
    record read back by `ConversationStore`, and keeping reasoning types out of
    it means a change to `Recommendation` cannot break loading a conversation
    written last week.
    """

    question: str = ""
    topic: str = ""
    # The assistant turn this came from, and the context it was computed against.
    source_message_id: str = ""
    snapshot_id: str = ""
    # The world-state digest at assessment time. A difference means the project
    # moved, which is what makes stale detection possible without storing a copy
    # of the project.
    fingerprint: str = ""
    # Redundant with Conversation.project by construction. Stored anyway and
    # asserted on load: a strategic recommendation attributed to the wrong
    # project would be worse than having none.
    project: str = ""
    created_at: datetime | None = None

    recommendation_key: str = ""
    recommendation: str = ""
    rationale: str = ""
    initiative_slug: str = ""
    effort: str = ""

    alternatives: list[AlternativeRef] = field(default_factory=list)
    evidence_refs: list[EvidenceRef] = field(default_factory=list)
    initiative_slugs: list[str] = field(default_factory=list)

    evidence_strength: Score = field(default_factory=Score)
    confidence: Score = field(default_factory=Score)
    execution_risk: Score = field(default_factory=Score)

    @property
    def empty(self) -> bool:
        return not self.recommendation

    def alternative_at(self, ordinal: int) -> AlternativeRef | None:
        """
        The nth alternative as the user saw it, 1-based.

        Order is the rendered order. "the second option" is only answerable if
        storage preserves what was on screen, so nothing here re-sorts.
        """
        index = ordinal - 1
        if 0 <= index < len(self.alternatives):
            return self.alternatives[index]
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "topic": self.topic,
            "source_message_id": self.source_message_id,
            "snapshot_id": self.snapshot_id,
            "fingerprint": self.fingerprint,
            "project": self.project,
            "created_at": iso(self.created_at) if self.created_at else "",
            "recommendation_key": self.recommendation_key,
            "recommendation": self.recommendation,
            "rationale": self.rationale,
            "initiative_slug": self.initiative_slug,
            "effort": self.effort,
            "alternatives": [a.to_dict() for a in self.alternatives],
            "evidence_refs": [e.to_dict() for e in self.evidence_refs],
            "initiative_slugs": list(self.initiative_slugs),
            "evidence_strength": self.evidence_strength.to_dict(),
            "confidence": self.confidence.to_dict(),
            "execution_risk": self.execution_risk.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StrategicState:
        if not isinstance(data, dict):
            return cls()
        return cls(
            question=str(data.get("question", "")),
            topic=str(data.get("topic", "")),
            source_message_id=str(data.get("source_message_id", "")),
            snapshot_id=str(data.get("snapshot_id", "")),
            fingerprint=str(data.get("fingerprint", "")),
            project=str(data.get("project", "")),
            # Absent means absent. parse_iso falls back to the epoch, which
            # would read as "recorded in 1970" rather than "not recorded".
            created_at=(
                parse_iso(str(data["created_at"]))
                if str(data.get("created_at", "")).strip()
                else None
            ),
            recommendation_key=str(data.get("recommendation_key", "")),
            recommendation=str(data.get("recommendation", "")),
            rationale=str(data.get("rationale", "")),
            initiative_slug=str(data.get("initiative_slug", "")),
            effort=str(data.get("effort", "")),
            alternatives=[
                AlternativeRef.from_dict(a)
                for a in (data.get("alternatives") or [])
                if isinstance(a, dict)
            ][:MAX_ALTERNATIVES],
            evidence_refs=[
                EvidenceRef.from_dict(e)
                for e in (data.get("evidence_refs") or [])
                if isinstance(e, dict)
            ][:MAX_EVIDENCE_REFS],
            initiative_slugs=[str(s) for s in (data.get("initiative_slugs") or [])][
                :MAX_INITIATIVES
            ],
            evidence_strength=Score.from_dict(data.get("evidence_strength") or {}),
            confidence=Score.from_dict(data.get("confidence") or {}),
            execution_risk=Score.from_dict(data.get("execution_risk") or {}),
        )

    def render(self) -> str:
        """
        The stored decision, as material for a continuation answer.

        Reads as a record of what was said rather than as a fresh conclusion,
        because that is what it is -- and a follow-up that presented it as newly
        computed would be claiming work it did not do.
        """
        lines = [
            "# Prior recommendation (already given to the user in this conversation)",
            f"Asked: {self.question}",
            f"Recommended: {self.recommendation}",
        ]
        if self.rationale:
            lines.append(f"Because: {self.rationale}")
        if self.initiative_slug:
            lines.append(f"Capability: {self.initiative_slug}")
        if self.effort:
            lines.append(f"Effort: {self.effort}")
        if self.alternatives:
            lines.append("Alternatives shown, in the order presented:")
            for index, alternative in enumerate(self.alternatives, 1):
                lines.append(f"  {index}. {alternative.statement}")
                if alternative.why_not:
                    lines.append(f"     not chosen because: {alternative.why_not}")
        lines.append(
            f"Scores as given: evidence strength {self.evidence_strength.band}"
            f" ({int(round(self.evidence_strength.score * 100))}%),"
            f" recommendation confidence {self.confidence.band}"
            f" ({int(round(self.confidence.score * 100))}%),"
            f" execution risk {self.execution_risk.band}"
            f" ({int(round(self.execution_risk.score * 100))}%)"
        )
        if self.evidence_refs:
            lines.append("Evidence cited:")
            for ref in self.evidence_refs:
                where = f"{ref.path}:{ref.line}" if ref.path and ref.line else ref.reference
                lines.append(f"  {ref.kind}: {where} — {ref.because}")
        return "\n".join(lines)


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
    # The strategic decision currently under discussion, when one is. Replaced
    # wholesale by each new executive assessment rather than accumulated: a list
    # would become a second transcript, and this file already is one.
    #
    # Separate from ``subject`` on purpose. ``subject`` is a short lexical term
    # string the project index ranks against; this is a structured record of a
    # judgement. Forcing one to carry the other would break whichever lost.
    strategy: StrategicState | None = None
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


def slugify(value: str) -> str:
    """
    The project slug used as a conversation directory name.

    Delegates to the canonical transformation and then validates it as a
    filesystem identity, because that is exactly what it becomes: a path segment
    under ``workspace/conversations/``. This used to be a local regex that
    stripped non-ASCII, which meant a project named in a non-Latin script slugged
    to the empty string and its conversations resolved to the parent directory.

    Raises ``InvalidSlugError`` for a name that cannot be a directory. That is a
    behaviour change from silent degradation, and the right one: a project whose
    name cannot be stored is a problem to report at registration, not to discover
    as a missing folder.
    """
    return require_slug(value)


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
