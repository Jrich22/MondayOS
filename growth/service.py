"""
GrowthService — the public composition facade behind Monday.growth().

The service holds no domain logic of its own. Isolation belongs to
growth.project, the approval contract to growth.fingerprint, the lifecycle to
growth.content, the human-approval gate to agents.gates and reasoning to
growth.brain. This module wires those together and returns plain dictionaries
for the API layer to wrap.

Each domain lives in growth/services/ and is composed here (issue #35). The
public surface is unchanged: every method callers used before is still a method
on this class, with the same name, signature and behaviour.
"""

from __future__ import annotations

from agents.gates import GATED_ACTIONS
from growth.services import (
    AnalyticsServiceMixin,
    BrainServiceMixin,
    CampaignServiceMixin,
    ContentServiceMixin,
    PublishingServiceMixin,
    WorkspaceServiceMixin,
)
from growth.services.base import _as_datetime

# The gated action a publishing connector must declare (ADR-012). Registered in
# agents.roles.GATED_ACTIONS; named for content specifically because
# Monday.publish() already means Confluence document publishing.
PUBLISH_ACTION = "publish_content"

# Re-exported: monday/api.py coerces incoming datetimes through the same helper
# the service uses, so the API and the service never disagree about a timestamp.
__all__ = ["PUBLISH_ACTION", "GrowthService", "publish_action_is_gated", "_as_datetime"]


class GrowthService(
    WorkspaceServiceMixin,
    ContentServiceMixin,
    CampaignServiceMixin,
    PublishingServiceMixin,
    AnalyticsServiceMixin,
    BrainServiceMixin,
):
    """
    Growth operations for one MondayOS project root.

    A composition of the domain facades in growth/services/. Construction,
    shared state and the cross-domain helpers live in GrowthServiceBase.
    """


def publish_action_is_gated() -> bool:
    """True when the content-publishing action is registered as human-gated."""
    return PUBLISH_ACTION in GATED_ACTIONS
