"""
Publishing actions and pause controls.

One domain of GrowthService, split out per issue #35. Behaviour is unchanged:
these are the same methods, composed back into the public facade.
"""

from __future__ import annotations

from typing import Any

from growth.services.base import GrowthServiceBase


class PublishingServiceMixin(GrowthServiceBase):
    """Publishing actions and pause controls."""

    def schedule_content(
        self, project: str, content_id: str, actor: str = "human:cli"
    ) -> dict[str, Any]:
        """Move an approved, future-dated item to Scheduled."""
        return self._dispatcher(project, actor).schedule(content_id).to_dict()

    def publish_content_now(
        self, project: str, content_id: str, actor: str = "human:cli", force: bool = False
    ) -> dict[str, Any]:
        """Publish an approved item, or reconcile if this version already landed."""
        return self._dispatcher(project, actor).publish(content_id, force_due=force).to_dict()

    def retry_publication(
        self, project: str, content_id: str, actor: str = "human:cli"
    ) -> dict[str, Any]:
        """Re-attempt a failed item once its backoff window has elapsed."""
        return self._dispatcher(project, actor).retry(content_id).to_dict()

    def publication_status(self, project: str, content_id: str) -> dict[str, Any]:
        """Publication state, attempt history, pause state, and audit trail."""
        return self._dispatcher(project).publication_status(content_id)

    def set_pause(
        self,
        project: str,
        scope: str,
        active: bool,
        target: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        """Engage or clear one pause scope."""
        controller = self._store.open(project).pause_controller(self._root)
        return controller.set_pause(scope, active, target=target, reason=reason).to_dict()

    def list_pauses(self, project: str) -> dict[str, Any]:
        """Active pauses visible to this workspace, including the global stop."""
        return self._store.open(project).pause_controller(self._root).list_pauses()
