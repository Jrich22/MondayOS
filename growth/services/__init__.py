"""
growth.services — the Growth service split into focused domain facades.

``GrowthService`` remains the single public composition facade that
``Monday.growth()`` talks to; this package holds the domains it composes. The
split is behaviour-preserving: the methods are the same methods, and the public
API, the CLI and the existing tests are unchanged (issue #35).

The split exists because the service had accumulated one domain per increment —
twelve of them behind one class — and increment 7 was about to add three more.
"""

from __future__ import annotations

from growth.services.analytics import AnalyticsServiceMixin
from growth.services.base import GrowthServiceBase
from growth.services.brain import BrainServiceMixin
from growth.services.campaign import CampaignServiceMixin
from growth.services.content import ContentServiceMixin
from growth.services.generation import GenerationServiceMixin
from growth.services.publishing import PublishingServiceMixin
from growth.services.workspace import WorkspaceServiceMixin

__all__ = [
    "AnalyticsServiceMixin",
    "BrainServiceMixin",
    "CampaignServiceMixin",
    "ContentServiceMixin",
    "GenerationServiceMixin",
    "GrowthServiceBase",
    "PublishingServiceMixin",
    "WorkspaceServiceMixin",
]
