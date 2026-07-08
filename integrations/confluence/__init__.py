"""
Confluence publishing integration for MondayOS.

MondayOS remains the system of record. This package publishes MondayOS
documents (roadmaps, sprint summaries, architecture docs, release notes,
agent workflow reports, research reports) *to* Confluence as a downstream
destination — it never treats Confluence as a source of truth.

Public surface:
    ConfluenceConfig / CredentialCheck   — env-var configuration + checks
    ConfluenceClient / HttpConfluenceClient / FakeConfluenceClient
    ConfluenceError / PageResult / build_client
    markdown_to_storage                  — Markdown → storage-format XHTML
    ConfluencePublisher / PublishDoc / PublishResult
    PublishStore / PublishRecord / PublishEvent
"""
from __future__ import annotations

from integrations.confluence.client import (
    ConfluenceClient,
    ConfluenceError,
    FakeConfluenceClient,
    HttpConfluenceClient,
    PageResult,
    build_client,
)
from integrations.confluence.config import (
    ConfluenceConfig,
    CredentialCheck,
)
from integrations.confluence.converter import markdown_to_storage
from integrations.confluence.mapping import (
    PublishEvent,
    PublishRecord,
    PublishStore,
)
from integrations.confluence.publisher import (
    ConfluencePublisher,
    PublishDoc,
    PublishResult,
)

__all__ = [
    "ConfluenceConfig",
    "CredentialCheck",
    "ConfluenceClient",
    "HttpConfluenceClient",
    "FakeConfluenceClient",
    "ConfluenceError",
    "PageResult",
    "build_client",
    "markdown_to_storage",
    "ConfluencePublisher",
    "PublishDoc",
    "PublishResult",
    "PublishStore",
    "PublishRecord",
    "PublishEvent",
]
