"""
Connector construction.

Mirrors ``integrations.confluence.client.build_client``: one place that decides
which implementation a platform resolves to, so callers never import a concrete
adapter.

Only the fake exists today. Real platform adapters are the next increment, and
each will register here — the dispatcher above this layer does not change when
they arrive.
"""

from __future__ import annotations

from pathlib import Path

from integrations.publishing.connector import PermanentPublishError, PublishingConnector
from integrations.publishing.fake import FakePublishingConnector

# Platforms with a real adapter. Empty by design: MondayOS has no OAuth or
# credential-exchange framework yet, and inventing a second one for social
# publishing is out of scope for this increment.
REAL_ADAPTERS: dict[str, type[PublishingConnector]] = {}


def build_connector(
    platform: str,
    project_root: Path | None = None,
    connector: PublishingConnector | None = None,
) -> PublishingConnector:
    """
    Return the connector for ``platform``.

    An explicitly injected ``connector`` wins, which is how tests and the CLI
    smoke path supply a fake. Otherwise the fake is returned, because no real
    adapter is registered yet. When adapters land, this is where credential
    availability gets checked before returning one.
    """
    if connector is not None:
        return connector

    slug = (platform or "").strip().lower()
    if not slug:
        raise PermanentPublishError("No platform specified.", code="unsupported_platform")

    adapter = REAL_ADAPTERS.get(slug)
    if adapter is not None:  # pragma: no cover — no real adapters registered yet
        return adapter()

    store = (
        Path(project_root) / "logs" / "growth" / f"fake-{slug}.json"
        if project_root is not None
        else None
    )
    return FakePublishingConnector(platform=slug, store_path=store)
