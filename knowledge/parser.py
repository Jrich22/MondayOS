"""Markdown + YAML frontmatter parser for knowledge entries."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import yaml

from knowledge.entry import (
    KnowledgeEntry,
    KnowledgeType,
    LifecycleStatus,
    Relationship,
    RelationType,
)
from knowledge.errors import KnowledgeParseError

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

_REQUIRED_FIELDS = ("id", "entry_type", "title", "status", "created_at")

# Fields owned by the CKO schema; everything else goes to metadata.
_KNOWN_FIELDS = frozenset({
    "id", "entry_type", "version", "status", "title", "summary", "body",
    "created_at", "created_by", "updated_at", "updated_by", "authored_by",
    "confidence", "tags", "components", "relationships", "type_fields",
    "superseded_by",
})


class KnowledgeParser:
    """
    Parses raw Markdown (with YAML frontmatter) into KnowledgeEntry and back.

    File format:
        ---
        id: BUG-0001
        entry_type: bug
        title: Short description
        status: active
        created_at: "2026-06-27T14:00:00Z"
        ... (other frontmatter fields)
        ---

        ## Body heading
        Body content in Markdown.

    The parser is strict on required fields and tolerant on optional ones.
    Unknown frontmatter fields are stored in entry.metadata (forward compat).
    serialize(parse(raw)) round-trips cleanly for all well-formed files.
    """

    def parse(self, raw: str, source_path: str = "<string>") -> KnowledgeEntry:
        """
        Parse a raw Markdown string into a KnowledgeEntry.

        Raises KnowledgeParseError if frontmatter is missing or a required
        field is absent or has an invalid value.
        """
        match = _FRONTMATTER_RE.match(raw)
        if not match:
            raise KnowledgeParseError(
                "No YAML frontmatter block found (expected --- ... ---)",
                source_path=source_path,
            )

        fm_raw = match.group(1)
        body = raw[match.end():].strip()

        try:
            fm: dict[str, Any] = yaml.safe_load(fm_raw) or {}
        except yaml.YAMLError as exc:
            raise KnowledgeParseError(
                f"YAML parse error: {exc}", source_path=source_path
            ) from exc

        for required in _REQUIRED_FIELDS:
            if required not in fm:
                raise KnowledgeParseError(
                    f"Missing required field: {required!r}",
                    source_path=source_path,
                    field=required,
                )

        try:
            entry_type = KnowledgeType(str(fm["entry_type"]))
        except ValueError as exc:
            raise KnowledgeParseError(
                f"Unknown entry_type: {fm['entry_type']!r}",
                source_path=source_path,
                field="entry_type",
            ) from exc

        try:
            status = LifecycleStatus(str(fm["status"]))
        except ValueError as exc:
            raise KnowledgeParseError(
                f"Unknown status: {fm['status']!r}",
                source_path=source_path,
                field="status",
            ) from exc

        relationships: list[Relationship] = []
        for r in (fm.get("relationships") or []):
            try:
                rel = Relationship(
                    relation=RelationType(r["relation"]),
                    target_id=str(r["target_id"]),
                    created_at=_parse_dt(r.get("created_at", fm["created_at"])),
                    created_by=str(r.get("created_by", fm.get("created_by", "human"))),
                    note=str(r.get("note", "")),
                )
                relationships.append(rel)
            except (KeyError, ValueError) as exc:
                raise KnowledgeParseError(
                    f"Invalid relationship entry: {exc}",
                    source_path=source_path,
                    field="relationships",
                ) from exc

        extra_metadata = {k: v for k, v in fm.items() if k not in _KNOWN_FIELDS}

        return KnowledgeEntry(
            id=str(fm["id"]),
            entry_type=entry_type,
            title=str(fm["title"]),
            status=status,
            created_at=_parse_dt(fm["created_at"]),
            components=list(fm.get("components") or []),
            tags=list(fm.get("tags") or []),
            body=body,
            version=int(fm.get("version", 1)),
            summary=str(fm.get("summary", "")),
            created_by=str(fm.get("created_by", "human")),
            updated_at=_parse_dt(fm["updated_at"]) if fm.get("updated_at") else None,
            updated_by=str(fm.get("updated_by", "")),
            authored_by=str(fm.get("authored_by", "human")),
            confidence=float(fm.get("confidence", 1.0)),
            relationships=relationships,
            type_fields=dict(fm.get("type_fields") or {}),
            metadata=extra_metadata,
            superseded_by=str(fm["superseded_by"]) if fm.get("superseded_by") else None,
        )

    def serialize(self, entry: KnowledgeEntry) -> str:
        """
        Serialize a KnowledgeEntry to the Markdown + YAML frontmatter format.

        The output of serialize(parse(raw)) round-trips cleanly for all
        well-formed entries.
        """
        fm: dict[str, Any] = {
            "authored_by": entry.authored_by,
            "components": list(entry.components),
            "confidence": entry.confidence,
            "created_at": _fmt_dt(entry.created_at),
            "created_by": entry.created_by,
            "entry_type": entry.entry_type.value,
            "id": entry.id,
            "relationships": [
                {
                    "created_at": _fmt_dt(r.created_at),
                    "created_by": r.created_by,
                    "note": r.note,
                    "relation": r.relation.value,
                    "target_id": r.target_id,
                }
                for r in entry.relationships
            ],
            "status": entry.status.value,
            "summary": entry.summary,
            "tags": list(entry.tags),
            "title": entry.title,
            "type_fields": dict(entry.type_fields),
            "updated_at": _fmt_dt(entry.updated_at) if entry.updated_at else None,
            "updated_by": entry.updated_by,
            "version": entry.version,
        }
        if entry.superseded_by is not None:
            fm["superseded_by"] = entry.superseded_by

        # Extra metadata fields go back into the frontmatter
        fm.update(entry.metadata)

        fm_yaml = yaml.dump(
            fm,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=True,
            width=120,
        )
        return f"---\n{fm_yaml}---\n\n{entry.body}\n"


def _parse_dt(value: Any) -> datetime:
    """Parse a datetime from a YAML value (may be str or already a datetime)."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        # Strip trailing Z before fromisoformat (Python <3.11 doesn't accept it)
        s = value.rstrip("Z")
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    raise ValueError(f"Cannot parse datetime from {type(value).__name__}: {value!r}")


def _fmt_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
