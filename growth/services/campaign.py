"""
Campaigns: the planning object between a project and its content.

One domain of GrowthService, split out per issue #35. Behaviour is unchanged:
these are the same methods, composed back into the public facade.
"""

from __future__ import annotations

from typing import Any

from growth.campaign import CampaignStatus
from growth.services.base import GrowthServiceBase


class CampaignServiceMixin(GrowthServiceBase):
    """Campaigns: the planning object between a project and its content."""

    def create_campaign(self, project: str, name: str, **fields: Any) -> dict[str, Any]:
        """Create a Draft campaign in a project's workspace."""
        return self._store.open(project).create_campaign(name=name, **fields).to_dict()

    def get_campaign(self, project: str, campaign_id: str) -> dict[str, Any]:
        """Read one campaign with its derived content counts."""
        handle = self._store.open(project)
        campaign = handle.get_campaign(campaign_id)
        return self._describe_campaign(handle, campaign)

    def list_campaigns(self, project: str, status: str = "") -> list[dict[str, Any]]:
        """Campaigns in a project, optionally filtered by status."""
        handle = self._store.open(project)
        parsed = CampaignStatus(status) if status else None
        return [self._describe_campaign(handle, c) for c in handle.list_campaigns(parsed)]

    def transition_campaign(
        self,
        project: str,
        campaign_id: str,
        status: str,
        changed_by: str = "human:cli",
        reason: str = "",
    ) -> dict[str, Any]:
        """Move a campaign along its lifecycle."""
        handle = self._store.open(project)
        campaign = handle.transition_campaign(
            campaign_id, CampaignStatus(status), changed_by=changed_by, reason=reason
        )
        return self._describe_campaign(handle, campaign)

    def assign_campaign(
        self, project: str, content_id: str, campaign_id: str, changed_by: str = "human:cli"
    ) -> dict[str, Any]:
        """Attach content to a campaign in the same workspace, or detach it."""
        handle = self._store.open(project)
        return self._describe(handle.assign_campaign(content_id, campaign_id, changed_by))
