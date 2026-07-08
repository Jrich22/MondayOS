"""
Document publisher — MondayOS → Confluence.

Takes a resolved :class:`PublishDoc` (content already pulled from the MondayOS
system of record by the caller) and publishes it to Confluence, deciding
between *create* and *update* from the local mapping, honouring dry-run and
overwrite-safety rules, and recording the outcome locally.

Safety model:
  * A page is only ever *updated* (overwritten) when we already know which page
    to write to — either the doc has a stored mapping, or an explicit
    ``update_page`` id was supplied. Otherwise a brand-new page is created.
  * ``dry_run`` never calls the network client; it reports what *would* happen.
  * When the source content is unchanged since the last publish, the update is
    skipped as ``up-to-date`` unless ``force`` is set.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from integrations.confluence.client import ConfluenceClient, ConfluenceError, build_client
from integrations.confluence.config import ConfluenceConfig
from integrations.confluence.converter import markdown_to_storage
from integrations.confluence.mapping import PublishEvent, PublishRecord, PublishStore


@dataclass
class PublishDoc:
    """A MondayOS document resolved to publishable content."""

    doc_id: str
    title: str
    markdown: str
    source: str = ""          # source file path or knowledge entry ID


@dataclass
class PublishResult:
    """Outcome of a publish attempt."""

    success: bool
    action: str               # created | updated | up-to-date | dry-run | failed
    doc_id: str = ""
    page_id: str = ""
    url: str = ""
    space_key: str = ""
    checksum: str = ""
    dry_run: bool = False
    message: str = ""
    summary: dict[str, Any] = field(default_factory=dict)
    storage_preview: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "action": self.action,
            "doc_id": self.doc_id,
            "page_id": self.page_id,
            "url": self.url,
            "space_key": self.space_key,
            "checksum": self.checksum,
            "dry_run": self.dry_run,
            "message": self.message,
            "summary": self.summary,
        }


class ConfluencePublisher:
    """Publishes MondayOS documents to Confluence and records the mapping."""

    def __init__(
        self,
        project_root: Path,
        config: ConfluenceConfig | None = None,
        client: ConfluenceClient | None = None,
    ) -> None:
        self._root = Path(project_root)
        self._config = config or ConfluenceConfig()
        self._client = client  # injected (tests) or built lazily for real runs
        self._store = PublishStore(self._root)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self._store.history(limit=limit)]

    def publish(
        self,
        doc: PublishDoc,
        space_key: str = "",
        parent_id: str = "",
        update_page: str = "",
        dry_run: bool = False,
        force: bool = False,
    ) -> PublishResult:
        checksum = _sha256(doc.markdown)
        storage = markdown_to_storage(doc.markdown)
        existing = self._store.get(doc.doc_id)

        # Decide the target: known mapping or explicit id => update; else create.
        target_page_id = update_page or (existing.page_id if existing else "")
        is_update = bool(target_page_id)
        space = space_key or self._config.space_key or (existing.space_key if existing else "")

        unchanged = bool(existing) and existing.checksum == checksum and not force

        # ---- dry run: preview only, never touch the client ----
        if dry_run:
            action = "update" if is_update else "create"
            summary = {
                "would": action,
                "page_id": target_page_id or "(new)",
                "space_key": space or "(default)",
                "parent_id": parent_id or "",
                "content_changed": (not unchanged) if existing else True,
                "storage_bytes": len(storage),
                "prev_checksum": existing.checksum if existing else "",
                "new_checksum": checksum,
            }
            self._store.log_event(PublishEvent(
                doc_id=doc.doc_id, action="dry-run", page_id=target_page_id,
                checksum=checksum, status="dry-run", at=_now_iso(),
                message=f"Dry run — would {action} in space {space or '(default)'}.",
            ))
            return PublishResult(
                success=True, action="dry-run", doc_id=doc.doc_id,
                page_id=target_page_id, space_key=space, checksum=checksum,
                dry_run=True, summary=summary,
                storage_preview=storage[:400],
                message=(
                    f"Dry run: would {action} page "
                    f"{('#' + target_page_id) if target_page_id else '(new)'} "
                    f"in space {space or '(default)'}."
                ),
            )

        # ---- unchanged content: skip the update ----
        if is_update and unchanged:
            return PublishResult(
                success=True, action="up-to-date", doc_id=doc.doc_id,
                page_id=target_page_id, url=existing.url if existing else "",
                space_key=space, checksum=checksum,
                message=f"No changes since last publish (page #{target_page_id}); skipped.",
                summary={"content_changed": False},
            )

        # ---- real publish: create or update ----
        if not is_update and not space:
            return self._fail(
                doc, checksum,
                "No space key configured. Set CONFLUENCE_SPACE_KEY or pass --space.",
            )

        try:
            client = self._require_client()
        except ConfluenceError as exc:
            return self._fail(doc, checksum, str(exc))

        try:
            if is_update:
                page = client.update_page(target_page_id, doc.title, storage, parent_id)
                action = "updated"
            else:
                page = client.create_page(space, doc.title, storage, parent_id)
                action = "created"
        except ConfluenceError as exc:
            return self._fail(doc, checksum, f"Confluence publish failed: {exc}")

        now = _now_iso()
        record = PublishRecord(
            doc_id=doc.doc_id, source=doc.source or doc.doc_id, title=doc.title,
            page_id=page.id, url=page.url, space_key=space, checksum=checksum,
            last_published=now, status=action, version=page.version,
        )
        self._store.record(record, PublishEvent(
            doc_id=doc.doc_id, action=action, page_id=page.id, url=page.url,
            checksum=checksum, status=action, at=now,
            message=f"{action.title()} page #{page.id} (v{page.version}).",
        ))
        return PublishResult(
            success=True, action=action, doc_id=doc.doc_id, page_id=page.id,
            url=page.url, space_key=space, checksum=checksum,
            summary={"version": page.version, "content_changed": True},
            message=f"{action.title()} “{doc.title}” → {page.url}",
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _require_client(self) -> ConfluenceClient:
        if self._client is not None:
            return self._client
        check = self._config.credential_check(require_space=False)
        if not check.ok:
            raise ConfluenceError(check.instructions())
        self._client = build_client(self._config, self._root)
        return self._client

    def _fail(self, doc: PublishDoc, checksum: str, message: str) -> PublishResult:
        self._store.log_event(PublishEvent(
            doc_id=doc.doc_id, action="failed", checksum=checksum,
            status="failed", at=_now_iso(), message=message,
        ))
        return PublishResult(
            success=False, action="failed", doc_id=doc.doc_id,
            checksum=checksum, message=message,
        )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
