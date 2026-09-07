"""
Project Registry — tracks external repositories managed by MondayOS.

The registry maps project names to their source paths and metadata.
It is stored in {mondayos_root}/config/projects.json and is always
accessed through the main MondayOS instance (not through external project
instances).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.identity import slug


class ProjectNotFoundError(Exception):
    """Raised when a project name is not found in the registry."""


class ProjectAlreadyExistsError(Exception):
    """Raised when registering a name that already exists (use overwrite=True to replace)."""


@dataclass
class ProjectEntry:
    """A single registered project."""

    name: str
    source_path: str  # absolute path to the external project directory
    description: str
    registered_at: str  # ISO 8601 UTC

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectEntry:
        return cls(
            name=data["name"],
            source_path=data["source_path"],
            description=data.get("description", ""),
            registered_at=data.get("registered_at", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def path(self) -> Path:
        return Path(self.source_path)


class ProjectRegistry:
    """
    Persistent registry of external projects managed by MondayOS.

    Registry file: {config_dir}/projects.json

    Operations are idempotent where sensible: registering an existing name
    raises `ProjectAlreadyExistsError` by default; pass overwrite=True to
    replace the entry.
    """

    def __init__(self, config_dir: Path) -> None:
        self._path = config_dir / "projects.json"

    def register(
        self,
        name: str,
        source_path: str | Path,
        description: str = "",
        overwrite: bool = False,
    ) -> ProjectEntry:
        """
        Register a new project.

        Args:
            name:        Unique project name (slug; letters, digits, hyphens).
            source_path: Absolute path to the project's source directory.
            description: Optional human-readable description.
            overwrite:   If True, replace an existing entry with the same name.

        Raises:
            ProjectAlreadyExistsError: If name already registered and overwrite=False.
            ValueError: If source_path does not exist.
        """
        source_path = str(Path(source_path).expanduser().resolve())
        if not Path(source_path).exists():
            raise ValueError(f"source_path does not exist: {source_path}")

        projects = self._load()
        if name in projects and not overwrite:
            raise ProjectAlreadyExistsError(
                f"Project {name!r} is already registered. Use overwrite=True to replace it."
            )

        # Collision protection. Project names are stored verbatim but *addressed*
        # by their canonical slug, so two different names can reach the same
        # storage. That is fine when they describe the same project -- WeatherBot
        # and weatherbot are one project under two spellings -- and is a silent
        # data-sharing bug when they do not.
        #
        # Rejected at registration rather than detected later, because by the
        # time two projects are writing to one directory the damage is done.
        incoming = slug(name)
        for existing_name, existing in projects.items():
            if existing_name == name or slug(existing_name) != incoming:
                continue
            if str(existing.get("source_path", "")) != source_path:
                raise ProjectAlreadyExistsError(
                    f"Project {name!r} resolves to the slug {incoming!r}, which is "
                    f"already used by {existing_name!r} at a different path "
                    f"({existing.get('source_path')!r}). Two projects cannot share "
                    "one identity; rename one of them."
                )

        entry = ProjectEntry(
            name=name,
            source_path=source_path,
            description=description,
            registered_at=datetime.now(tz=UTC).isoformat(),
        )
        projects[name] = entry.to_dict()
        self._save(projects)
        return entry

    def get(self, name: str) -> ProjectEntry:
        """
        Return the ProjectEntry for the given name.

        Raises:
            ProjectNotFoundError: If the project is not registered.
        """
        projects = self._load()
        if name not in projects:
            registered = list(projects.keys())
            raise ProjectNotFoundError(
                f"Project {name!r} is not registered. "
                + (
                    f"Registered projects: {registered}"
                    if registered
                    else "No projects registered yet."
                )
            )
        return ProjectEntry.from_dict(projects[name])

    def list(self) -> list[ProjectEntry]:
        """Return all registered projects, sorted by registration time."""
        projects = self._load()
        entries = [ProjectEntry.from_dict(d) for d in projects.values()]
        entries.sort(key=lambda e: e.registered_at)
        return entries

    def remove(self, name: str) -> None:
        """
        Remove a project from the registry.

        Raises:
            ProjectNotFoundError: If the project is not registered.
        """
        projects = self._load()
        if name not in projects:
            raise ProjectNotFoundError(f"Project {name!r} is not registered.")
        del projects[name]
        self._save(projects)

    def exists(self, name: str) -> bool:
        """Return True if the project name is registered."""
        return name in self._load()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, projects: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(projects, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
