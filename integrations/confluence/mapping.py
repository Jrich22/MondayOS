"""
Local publish mapping + history.

MondayOS is the system of record, so the link between a MondayOS document and
the Confluence page it was published to lives *here*, on the MondayOS side —
not in Confluence. This module persists two things under
``logs/publish/confluence.json``:

* ``pages`` — the current mapping, one :class:`PublishRecord` per MondayOS
  doc ID (source, page ID, URL, checksum, timestamp, status).
* ``history`` — an append-only log of :class:`PublishEvent` entries, one per
  publish attempt (including dry runs), most-recent-last.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class PublishRecord:
    """Current published state of one MondayOS document."""

    doc_id: str
    source: str = ""          # source file path or knowledge entry ID
    title: str = ""
    page_id: str = ""
    url: str = ""
    space_key: str = ""
    checksum: str = ""        # sha256 of the source content last published
    last_published: str = ""
    status: str = ""          # created | updated | up-to-date | dry-run | failed
    version: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PublishRecord":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class PublishEvent:
    """One entry in the append-only publish history."""

    doc_id: str
    action: str = ""          # created | updated | up-to-date | dry-run | failed
    page_id: str = ""
    url: str = ""
    checksum: str = ""
    status: str = ""
    at: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PublishEvent":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


class PublishStore:
    """JSON-backed store for the Confluence publish mapping and history."""

    def __init__(self, project_root: Path) -> None:
        self._path = Path(project_root) / "logs" / "publish" / "confluence.json"
        self._pages: dict[str, PublishRecord] = {}
        self._history: list[PublishEvent] = []
        self._load()

    # -- reads ----------------------------------------------------------

    def get(self, doc_id: str) -> PublishRecord | None:
        return self._pages.get(doc_id)

    def records(self) -> list[PublishRecord]:
        return list(self._pages.values())

    def history(self, limit: int = 20) -> list[PublishEvent]:
        events = list(reversed(self._history))  # most recent first
        return events[: max(0, limit)] if limit else events

    # -- writes ---------------------------------------------------------

    def record(self, record: PublishRecord, event: PublishEvent) -> None:
        """Upsert the mapping for a doc and append the history event."""
        self._pages[record.doc_id] = record
        self._history.append(event)
        self._save()

    def log_event(self, event: PublishEvent) -> None:
        """Append a history event without changing the mapping (e.g. dry runs)."""
        self._history.append(event)
        self._save()

    # -- persistence ----------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return
        self._pages = {
            doc_id: PublishRecord.from_dict(rec)
            for doc_id, rec in (data.get("pages") or {}).items()
        }
        self._history = [PublishEvent.from_dict(e) for e in (data.get("history") or [])]

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "pages": {doc_id: rec.to_dict() for doc_id, rec in self._pages.items()},
                "history": [e.to_dict() for e in self._history],
            }
            self._path.write_text(
                json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
            )
        except Exception:
            pass  # persistence failure must not mask a successful publish
