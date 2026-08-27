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

import re
from dataclasses import dataclass
from pathlib import Path

from growth.errors import (
    AmbiguousProjectError,
    InvalidProjectSlugError,
    ProjectNotRegisteredError,
)
from monday.project import ProjectRegistry

# A slug is the name of exactly one directory. Anchored, no dots, no separators —
# which is what makes path traversal a rejected *name* rather than a path to sanitize.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

_MAX_SLUG_LENGTH = 64


@dataclass(frozen=True)
class ResolvedProject:
    """A project name that resolved to exactly one growth workspace slug."""

    slug: str
    registered_name: str
    source_path: str


def normalize_project_slug(name: str) -> str:
    """
    Normalize a project name to its workspace slug.

    Lower-cases, and folds underscores and spaces to hyphens. Raises
    InvalidProjectSlugError if the result is not a single safe path segment.
    """
    candidate = (name or "").strip().lower().replace("_", "-").replace(" ", "-")

    if not candidate:
        raise InvalidProjectSlugError(name, "name is empty")
    if len(candidate) > _MAX_SLUG_LENGTH:
        raise InvalidProjectSlugError(name, f"longer than {_MAX_SLUG_LENGTH} characters")
    if not _SLUG_RE.match(candidate):
        raise InvalidProjectSlugError(
            name,
            "must be lower-case letters, digits, and hyphens only, starting with a "
            "letter or digit (path separators and '.' are not permitted)",
        )
    return candidate


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
