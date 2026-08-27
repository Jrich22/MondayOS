"""
GrowthService — the coordinating façade behind Monday.growth().

The service holds no policy of its own. Isolation belongs to growth.project, the
approval contract to growth.fingerprint, the lifecycle to growth.content, and the
human-approval gate to agents.gates. This module wires them together and returns
plain dictionaries for the API layer to wrap.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents.gates import GATED_ACTIONS
from growth.content import ContentItem, ContentStatus
from growth.store import GrowthStore, WorkspaceHandle
from monday.project import ProjectRegistry

# The gated action a publishing connector must declare (ADR-012). Registered in
# agents.roles.GATED_ACTIONS; named for content specifically because Monday.publish()
# already means Confluence document publishing.
PUBLISH_ACTION = "publish_content"


class GrowthService:
    """Growth operations for one MondayOS project root."""

    def __init__(
        self, project_root: Path = Path("."), registry: ProjectRegistry | None = None
    ) -> None:
        self._root = Path(project_root)
        self._store = GrowthStore(self._root, registry=registry)

    # ------------------------------------------------------------------
    # Workspace
    # ------------------------------------------------------------------

    def init_workspace(self, project: str) -> dict[str, Any]:
        """Create an empty growth workspace for a registered project."""
        workspace = self._store.init_workspace(project)
        return workspace.to_dict()

    def get_workspace(self, project: str) -> dict[str, Any]:
        """Read one project's workspace."""
        return self._store.open(project).read().to_dict()

    def list_workspaces(self) -> list[str]:
        """Slugs of every initialized workspace."""
        return self._store.list_workspaces()

    def bind(
        self,
        project: str,
        platform: str,
        account_id: str,
        account_handle: str = "",
        secret_name: str = "",
    ) -> dict[str, str]:
        """Bind a publishing account to a workspace, by secret name only."""
        binding = self._store.open(project).bind(
            platform=platform,
            account_id=account_id,
            account_handle=account_handle,
            secret_name=secret_name,
        )
        return binding.redacted()

    def list_bindings(self, project: str) -> list[dict[str, str]]:
        """Every binding in a workspace, redacted."""
        return [b.redacted() for b in self._store.open(project).read().bindings]

    def credential_status(
        self, project: str, environ: dict[str, str] | None = None
    ) -> list[dict[str, Any]]:
        """Per-binding report of whether its credential is present in the environment."""
        rows: list[dict[str, Any]] = []
        for binding in self._store.open(project).read().bindings:
            check = binding.credential_check(environ)
            rows.append(
                {
                    "platform": binding.platform,
                    "secret_name": binding.secret_name,
                    "ready": check.ok,
                    "detail": check.instructions(),
                }
            )
        return rows

    # ------------------------------------------------------------------
    # Content
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _describe(item: ContentItem) -> dict[str, Any]:
        """Item payload plus the derived approval facts callers actually need."""
        payload = item.to_dict()
        payload["current_fingerprint"] = item.current_fingerprint()
        payload["is_approved"] = item.is_approved
        payload["approval_is_stale"] = item.approval_is_stale()
        payload["missing_required_fields"] = item.missing_required_fields()
        payload["publishable"] = False
        payload["publishable_reason"] = (
            "Publishing is not implemented in this increment; no connector exists."
        )
        return payload

    @staticmethod
    def handle_for(project: str, root: Path) -> WorkspaceHandle:
        """Convenience for callers that want direct handle access."""
        return GrowthStore(root).open(project)


def publish_action_is_gated() -> bool:
    """True when the content-publishing action is registered as human-gated."""
    return PUBLISH_ACTION in GATED_ACTIONS


def _as_datetime(value: Any) -> datetime | None:
    """Coerce an ISO string or datetime to a UTC datetime; None passes through."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    parsed = datetime.fromisoformat(str(value).rstrip("Z"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
