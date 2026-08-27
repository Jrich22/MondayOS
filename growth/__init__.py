"""
growth — the MondayOS Growth Bot service.

Plans, drafts, reviews, and approves marketing content on behalf of a MondayOS
project. One service, many projects, and no data shared between them.

This package implements increments 1 and 2 of docs/GROWTH_BOT.md:

    1. The Growth Workspace and the isolation boundary (ADR-011).
    2. The Content Item, its lifecycle, and approval fingerprinting (ADR-013),
       with `publish_content` registered as a human-gated action (ADR-012).

**Nothing here can publish.** There is no connector, no scheduler, and no lifecycle
state beyond Approved. That is the increment boundary, and tests assert it.

Public surface:
    Workspace / Business / Brand / Audience / Marketing — workspace state
    PlatformBinding / SUPPORTED_PLATFORMS               — credential references
    ContentItem / ContentStatus / ContentTransition     — the unit of approval
    compute_fingerprint / FINGERPRINTED_FIELDS          — the approval contract
    GrowthStore / WorkspaceHandle                       — persistence
    GrowthService / PUBLISH_ACTION                      — the façade Monday.growth() uses
    normalize_project_slug / resolve_project            — the isolation gate
"""

from __future__ import annotations

from growth.binding import (
    SUPPORTED_PLATFORMS,
    CredentialCheck,
    InvalidSecretNameError,
    PlatformBinding,
    UnsupportedPlatformError,
    normalize_platform,
)
from growth.content import (
    REQUIRED_FOR_REVIEW,
    ContentItem,
    ContentStatus,
    ContentTransition,
)
from growth.errors import (
    AmbiguousProjectError,
    BindingNotFoundError,
    ContentNotFoundError,
    GrowthError,
    GrowthParseError,
    InvalidProjectSlugError,
    InvalidTransitionError,
    ProjectNotRegisteredError,
    WorkspaceExistsError,
    WorkspaceNotFoundError,
)
from growth.fingerprint import (
    FINGERPRINT_SCHEME,
    FINGERPRINTED_FIELDS,
    compute_fingerprint,
)
from growth.project import (
    ResolvedProject,
    normalize_project_slug,
    resolve_project,
    workspace_path,
)
from growth.service import PUBLISH_ACTION, GrowthService, publish_action_is_gated
from growth.store import GrowthStore, WorkspaceHandle
from growth.workspace import Audience, Brand, Business, Marketing, Workspace

__all__ = [
    "FINGERPRINTED_FIELDS",
    "FINGERPRINT_SCHEME",
    "PUBLISH_ACTION",
    "REQUIRED_FOR_REVIEW",
    "SUPPORTED_PLATFORMS",
    "AmbiguousProjectError",
    "Audience",
    "BindingNotFoundError",
    "Brand",
    "Business",
    "ContentItem",
    "ContentNotFoundError",
    "ContentStatus",
    "ContentTransition",
    "CredentialCheck",
    "GrowthError",
    "GrowthParseError",
    "GrowthService",
    "GrowthStore",
    "InvalidProjectSlugError",
    "InvalidSecretNameError",
    "InvalidTransitionError",
    "Marketing",
    "PlatformBinding",
    "ProjectNotRegisteredError",
    "ResolvedProject",
    "UnsupportedPlatformError",
    "Workspace",
    "WorkspaceExistsError",
    "WorkspaceHandle",
    "WorkspaceNotFoundError",
    "compute_fingerprint",
    "normalize_platform",
    "normalize_project_slug",
    "publish_action_is_gated",
    "resolve_project",
    "workspace_path",
]
