"""
Content items and the content library.

One domain of GrowthService, split out per issue #35. Behaviour is unchanged:
these are the same methods, composed back into the public facade.
"""

from __future__ import annotations

from typing import Any

from growth.content import ContentStatus, ContentType
from growth.library import ContentLibrary
from growth.services.base import GrowthServiceBase, _as_datetime


class ContentServiceMixin(GrowthServiceBase):
    """Content items and the content library."""

    def create_content(self, project: str, **fields: Any) -> dict[str, Any]:
        """Create a Draft content item."""
        handle = self._store.open(project)
        item = handle.create_content(
            platform=fields.get("platform", ""),
            account=fields.get("account", ""),
            media=fields.get("media"),
            copy=fields.get("copy", ""),
            cta=fields.get("cta", ""),
            destination_url=fields.get("destination_url", ""),
            scheduled_at=_as_datetime(fields.get("scheduled_at")),
            campaign=fields.get("campaign", ""),
            expected_goal=fields.get("expected_goal", ""),
            expected_audience=fields.get("expected_audience", ""),
            created_by=fields.get("created_by", "human:cli"),
        )
        return self._describe(item)

    def get_content(self, project: str, content_id: str) -> dict[str, Any]:
        """Read one content item, with its computed approval state."""
        return self._describe(self._store.open(project).get_content(content_id))

    def list_content(self, project: str, status: str = "") -> list[dict[str, Any]]:
        """Every content item in a workspace, optionally filtered by status."""
        handle = self._store.open(project)
        parsed = ContentStatus(status) if status else None
        return [self._describe(i) for i in handle.list_content(parsed)]

    def update_content(self, project: str, content_id: str, **fields: Any) -> dict[str, Any]:
        """Update a content item, resetting a stale approval automatically."""
        handle = self._store.open(project)
        supplied: dict[str, Any] = {
            key: value
            for key, value in fields.items()
            if key
            in {
                "platform",
                "account",
                "media",
                "copy",
                "cta",
                "destination_url",
                "campaign",
                "expected_goal",
                "expected_audience",
                "notes",
                "tags",
                "warnings",
            }
        }
        if "scheduled_at" in fields:
            supplied["scheduled_at"] = _as_datetime(fields["scheduled_at"])
        item = handle.update_content(
            content_id, changed_by=fields.get("changed_by", "human:cli"), **supplied
        )
        return self._describe(item)

    def submit_for_review(
        self, project: str, content_id: str, changed_by: str = "human:cli"
    ) -> dict[str, Any]:
        """
        Move Draft -> AI Review -> Ready for Review.

        Refuses an item missing any REQUIRED_FOR_REVIEW field: an unreviewable item
        must not reach a human as though it were ready.
        """
        handle = self._store.open(project)
        item = handle.get_content(content_id)
        missing = item.missing_required_fields()
        if missing:
            raise ValueError(f"{content_id} is not reviewable — missing: {', '.join(missing)}.")
        if item.status is ContentStatus.DRAFT:
            handle.transition_content(
                content_id, ContentStatus.AI_REVIEW, changed_by=changed_by, reason="submitted"
            )
        updated = handle.transition_content(
            content_id,
            ContentStatus.READY_FOR_REVIEW,
            changed_by=changed_by,
            reason="passed automated review",
        )
        return self._describe(updated)

    def approve_content(
        self, project: str, content_id: str, approved_by: str, reason: str = ""
    ) -> dict[str, Any]:
        """Record a human approval of this item's exact approved fields."""
        handle = self._store.open(project)
        item = handle.approve_content(content_id, approved_by=approved_by, reason=reason)
        return self._describe(item)

    def request_changes(
        self, project: str, content_id: str, changed_by: str, reason: str = ""
    ) -> dict[str, Any]:
        """Return an item to the author with notes."""
        handle = self._store.open(project)
        item = handle.transition_content(
            content_id, ContentStatus.CHANGES_REQUESTED, changed_by=changed_by, reason=reason
        )
        return self._describe(item)

    def cancel_content(
        self, project: str, content_id: str, changed_by: str, reason: str = ""
    ) -> dict[str, Any]:
        """Terminally cancel an item."""
        handle = self._store.open(project)
        item = handle.transition_content(
            content_id, ContentStatus.CANCELLED, changed_by=changed_by, reason=reason
        )
        return self._describe(item)

    def library_search(self, project: str, **filters: Any) -> list[dict[str, Any]]:
        """Query the content library for one project."""
        library = ContentLibrary(self._store.open(project))
        content_type = filters.get("content_type")
        status = filters.get("status")
        entries = library.search(
            text=str(filters.get("text", "")),
            theme=str(filters.get("theme", "")),
            campaign=str(filters.get("campaign", "")),
            platform=str(filters.get("platform", "")),
            content_type=ContentType(content_type) if content_type else None,
            status=ContentStatus(status) if status else None,
            tag=str(filters.get("tag", "")),
            reusable_only=bool(filters.get("reusable_only", False)),
        )
        return [e.to_dict() for e in entries]

    def library_summary(self, project: str) -> dict[str, Any]:
        """Counts by type, platform, status and theme for one project."""
        return ContentLibrary(self._store.open(project)).summary()

    def library_top(self, project: str, limit: int = 10) -> dict[str, Any]:
        """Highest-performing content, with the basis for the ranking."""
        entries, basis = ContentLibrary(self._store.open(project)).highest_performing(limit)
        return {"entries": [e.to_dict() for e in entries], "basis": basis}

    def library_reusable(self, project: str, days: int = 0) -> list[dict[str, Any]]:
        """Reusable content, optionally only what has not been reused recently."""
        library = ContentLibrary(self._store.open(project))
        entries = library.not_reused_since(days) if days else library.reusable()
        return [e.to_dict() for e in entries]

    def library_variants(self, project: str, variant_group_id: str) -> list[dict[str, Any]]:
        """Every per-platform variant of one idea."""
        return [
            e.to_dict()
            for e in ContentLibrary(self._store.open(project)).variants(variant_group_id)
        ]
