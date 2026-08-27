"""
integrations.publishing — provider-neutral publishing connectors.

A connector executes an approved instruction against a platform. It holds no
policy: approval, isolation, pause state, scheduling, retry, and idempotency all
belong to the Growth dispatcher above it. Nothing here imports the growth package.

Only the deterministic fake exists in this increment; real platform adapters
register in ``factory.REAL_ADAPTERS`` when a credential framework exists.
"""

from __future__ import annotations

from integrations.publishing.connector import (
    PERMANENT_CODES,
    TRANSIENT_CODES,
    PermanentPublishError,
    PublishError,
    PublishingConnector,
    PublishOutcome,
    PublishRequest,
    TransientPublishError,
)
from integrations.publishing.factory import REAL_ADAPTERS, build_connector
from integrations.publishing.fake import FakePublishingConnector, QueuedFailure

__all__ = [
    "PERMANENT_CODES",
    "REAL_ADAPTERS",
    "TRANSIENT_CODES",
    "FakePublishingConnector",
    "PermanentPublishError",
    "PublishError",
    "PublishOutcome",
    "PublishRequest",
    "PublishingConnector",
    "QueuedFailure",
    "TransientPublishError",
    "build_connector",
]
