"""
Project resolution for growth workspaces — the isolation gate (ADR-011).

Every growth workspace path derives from this module and nowhere else. A caller
names a project; this module decides whether that name is safe, whether it is a
project MondayOS actually manages, and which single directory it maps to.

Two rules make the boundary hold:

  1. A slug is validated against a strict pattern before it is ever joined to a
     path, so a name like "../other" is rejected as a name rather than escaping
     as a path.
  2. The project must already exist in the MondayOS project registry. Growth
     does not invent a second notion of "project".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.identity import InvalidSlugError, require_slug
from growth.errors import (
    AmbiguousProjectError,
    InvalidProjectSlugError,
    ProjectNotRegisteredError,
)
from core.project import ProjectRegistry


@dataclass(frozen=True)
class ResolvedProject:
    """A project name that resolved to exactly one growth workspace slug."""

    slug: str
    registered_name: str
    source_path: str


def normalize_project_slug(name: str) -> str:
    """
    Normalize a project name to its Growth workspace slug.

    Delegates the transformation to `core.identity` and keeps Growth's own
    stricter acceptance: ASCII only. That split matters. A subsystem may reject a
    name for its own safety reasons; it may not turn a name into a *different*
    identity than the rest of MondayOS uses, or the same project becomes two
    projects depending on which component asked. Growth accepts fewer names than
    the workspace does, and for every name it accepts the slug is byte-identical.

    Raises InvalidProjectSlugError for anything that cannot be a single safe path
    segment, which is what makes traversal a rejected *name* rather than a path
    to sanitize (ADR-011).
    """
    try:
        return require_slug(name, ascii_only=True)
    except InvalidSlugError as exc:
        raise InvalidProjectSlugError(name, exc.reason) from exc


def resolve_project(name: str, registry: ProjectRegistry) -> ResolvedProject:
    """
    Resolve a project name to its workspace slug via the MondayOS project registry.

    Raises InvalidProjectSlugError for an unsafe name, ProjectNotRegisteredError if
    no registered project normalizes to the slug, and AmbiguousProjectError if two
    registered names share the slug but point at different source paths.
    """
    slug = normalize_project_slug(name)

    matches = [entry for entry in registry.list() if _slug_or_none(entry.name) == slug]
    if not matches:
        raise ProjectNotRegisteredError(name, [entry.name for entry in registry.list()])

    # The registry does not normalize names, so "weatherbot" and "WeatherBot" can both
    # exist. Same source path means one project recorded twice — resolvable. Different
    # source paths mean two projects competing for one workspace, which must not be guessed.
    source_paths = {entry.source_path for entry in matches}
    if len(source_paths) > 1:
        raise AmbiguousProjectError(slug, [entry.name for entry in matches])

    canonical = sorted(matches, key=lambda entry: entry.name)[0]
    return ResolvedProject(
        slug=slug,
        registered_name=canonical.name,
        source_path=canonical.source_path,
    )


def workspace_path(project_root: Path, slug: str) -> Path:
    """
    Return the workspace directory for an already-validated slug.

    Re-validates rather than trusting the caller: this function joins a value to a
    filesystem path, so it verifies the value at the point of use.
    """
    safe = normalize_project_slug(slug)
    return Path(project_root) / "growth" / "workspaces" / safe


def _slug_or_none(name: str) -> str | None:
    """Slug for a registered name, or None if that name cannot be a workspace slug."""
    try:
        return normalize_project_slug(name)
    except InvalidProjectSlugError:
        return None
