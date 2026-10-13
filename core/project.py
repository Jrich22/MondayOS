"""
Project Registry — tracks external repositories managed by MondayOS.

The registry maps project names to their source paths and metadata.
It is stored in {mondayos_root}/config/projects.json and is always
accessed through the main MondayOS instance (not through external project
instances).

Lives in `core` rather than `monday` because project identity is a foundational
concept, not a facade concern. It sat under `monday/` for historical reasons —
the Architecture Freeze discouraged new packages — and that placement created
the only import cycle in the system: `growth` needs the registry, `monday`
imports `growth.service`, so the two packages depended on each other. Nothing in
this module imports anything from MondayOS, so it was always a leaf wearing the
wrong address.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.identity import InvalidSlugError, require_slug, slug


class ProjectNotFoundError(Exception):
    """Raised when a project name is not found in the registry."""


class ProjectAlreadyExistsError(Exception):
    """Raised when registering a name that already exists (use overwrite=True to replace)."""


class ProjectRegistryError(ValueError):
    """Raised when persisted project identity or filesystem binding is unsafe."""


# A delivery target is an identity, not a remote URL. This deliberately excludes
# hosts, credentials, query strings, refs, and transport suffixes.
_GITHUB_OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
_GITHUB_REPOSITORY_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9_-])?\Z")


def normalize_delivery_repository(value: str) -> str:
    """Validate and canonicalize an explicit GitHub ``owner/repository`` binding."""
    if not isinstance(value, str):
        raise ValueError("delivery_repository must be a GitHub owner/repository slug")
    candidate = value.strip()
    if candidate != value or candidate.count("/") != 1:
        raise ValueError("delivery_repository must be exactly 'owner/repository'")
    owner, repository = candidate.split("/", 1)
    if not _GITHUB_OWNER_RE.fullmatch(owner):
        raise ValueError("delivery_repository has an invalid GitHub owner")
    if "--" in owner:
        raise ValueError("delivery_repository has an invalid GitHub owner")
    if not _GITHUB_REPOSITORY_RE.fullmatch(repository) or repository in {".", ".."}:
        raise ValueError("delivery_repository has an invalid GitHub repository")
    if repository.casefold().endswith(".git"):
        raise ValueError("delivery_repository must not include a .git transport suffix")
    # GitHub resolves both portions case-insensitively, so store one identity.
    return f"{owner.casefold()}/{repository.casefold()}"


def _optional_snapshot_value(data: dict[str, Any], field: str) -> int | None:
    value = data.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer or null")
    return value


@dataclass
class ProjectEntry:
    """A single registered project."""

    name: str
    source_path: str  # absolute path to the external project directory
    description: str
    registered_at: str  # ISO 8601 UTC
    # Empty means ordinary registration without autonomous delivery enrollment.
    delivery_repository: str = ""
    # Stable filesystem identity at registration. Legacy entries omit both.
    source_device: int | None = None
    source_inode: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectEntry:
        if not isinstance(data, dict):
            raise ValueError("project entry must be a JSON object")

        name = data.get("name")
        source_path = data.get("source_path")
        description = data.get("description", "")
        registered_at = data.get("registered_at", "")
        delivery_repository = data.get("delivery_repository", "")
        if not isinstance(name, str) or not name:
            raise ValueError("project entry name must be a non-empty string")
        if not isinstance(source_path, str) or not source_path:
            raise ValueError("project entry source_path must be a non-empty string")
        if not Path(source_path).is_absolute():
            raise ValueError("project entry source_path must be absolute")
        if not isinstance(description, str):
            raise ValueError("project entry description must be a string")
        if not isinstance(registered_at, str):
            raise ValueError("project entry registered_at must be a string")
        if not isinstance(delivery_repository, str):
            raise ValueError("project entry delivery_repository must be a string")

        source_device = _optional_snapshot_value(data, "source_device")
        source_inode = _optional_snapshot_value(data, "source_inode")
        if (source_device is None) != (source_inode is None):
            raise ValueError("source_device and source_inode must be recorded together")

        return cls(
            name=name,
            source_path=source_path,
            description=description,
            registered_at=registered_at,
            delivery_repository=(
                normalize_delivery_repository(delivery_repository)
                if delivery_repository
                else ""
            ),
            source_device=source_device,
            source_inode=source_inode,
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
    replace an analysis-only entry or refresh an identical delivery binding.
    """

    def __init__(self, config_dir: Path) -> None:
        self._config_dir = Path(config_dir).absolute()
        self._path = self._config_dir / "projects.json"
        self._lock_path = self._config_dir / "projects.json.lock"

    def register(
        self,
        name: str,
        source_path: str | Path,
        description: str = "",
        overwrite: bool = False,
        delivery_repository: str | None = None,
    ) -> ProjectEntry:
        """
        Register a new project.

        Args:
            name:        Unique project name (slug; letters, digits, hyphens).
            source_path: Existing, non-symlink source directory. Its canonical
                         path and filesystem identity are pinned at registration.
            description: Optional human-readable description.
            overwrite:   If True, replace an analysis-only entry. A delivery-enabled
                         entry can only be refreshed with the identical target.
            delivery_repository: Optional GitHub owner/repository. Setting it is
                         explicit enrollment for repository delivery.

        Raises:
            ProjectAlreadyExistsError: If name already registered and overwrite=False.
            ValueError: If the name, source directory, or repository binding is invalid.
        """
        if not isinstance(name, str):
            raise ValueError("project name must be a string")
        if not isinstance(description, str):
            raise ValueError("project description must be a string")
        if delivery_repository is not None and not isinstance(delivery_repository, str):
            raise ValueError("delivery_repository must be a GitHub owner/repository slug")
        incoming = require_slug(name, ascii_only=bool(delivery_repository))
        if incoming == "mondayos":
            raise ValueError(
                "project identity 'mondayos' is reserved for the controller repository"
            )
        canonical_path, source_device, source_inode = self._inspect_registration_path(
            source_path
        )
        repository = (
            normalize_delivery_repository(delivery_repository)
            if delivery_repository
            else ""
        )

        with self._mutation_lock():
            projects = self._load()
            if name in projects and not overwrite:
                raise ProjectAlreadyExistsError(
                    f"Project {name!r} is already registered. Use overwrite=True to replace it."
                )
            if name in projects:
                existing_binding = ProjectEntry.from_dict(projects[name])
                if existing_binding.delivery_repository:
                    # A delivery-enabled name is a security boundary, not a mutable
                    # bookmark. Tasks persist the target fingerprint they were
                    # created against, but refusing an in-place retarget also keeps
                    # operators from accidentally changing what future tasks mean.
                    # An identical overwrite remains useful for description updates
                    # and for backfilling snapshots on legacy delivery entries.
                    same_path = existing_binding.source_path == canonical_path
                    same_repository = existing_binding.delivery_repository == repository
                    same_snapshot = (
                        existing_binding.source_device is None
                        or existing_binding.source_inode is None
                        or (
                            existing_binding.source_device == source_device
                            and existing_binding.source_inode == source_inode
                        )
                    )
                    if not (same_path and same_repository and same_snapshot):
                        raise ProjectAlreadyExistsError(
                            f"Delivery-enabled project {name!r} has an immutable target "
                            "binding; overwrite may only repeat the same source path, "
                            "repository, and filesystem identity."
                        )

            # Collision protection. Project names are stored verbatim but *addressed*
            # by their canonical slug, so two different names can reach the same
            # storage. That is fine when they describe the same project -- WeatherBot
            # and weatherbot are one project under two spellings -- and is a silent
            # data-sharing bug when they do not.
            #
            # Rejected at registration rather than detected later, because by the
            # time two projects are writing to one directory the damage is done.
            for existing_name, raw_existing in projects.items():
                if existing_name == name:
                    continue
                existing = ProjectEntry.from_dict(raw_existing)
                if slug(existing_name) == incoming:
                    if existing.source_path != canonical_path:
                        raise ProjectAlreadyExistsError(
                            f"Project {name!r} resolves to the slug {incoming!r}, which is "
                            f"already used by {existing_name!r} at a different path "
                            f"({existing.source_path!r}). Two projects cannot share "
                            "one identity; rename one of them."
                        )
                    if repository or existing.delivery_repository:
                        raise ProjectAlreadyExistsError(
                            f"Delivery project identity {incoming!r} already has the "
                            f"registry alias {existing_name!r}; delivery identities must "
                            "have exactly one name."
                        )

                # Autonomous delivery has a one-to-one registry binding. Historical
                # aliases remain supported for projects that are not delivery-enabled.
                if repository and existing.delivery_repository:
                    self._verify_current_path(existing)
                    if existing.source_path == canonical_path:
                        raise ProjectAlreadyExistsError(
                            f"Delivery-enabled project {existing_name!r} already pins "
                            f"source path {canonical_path!r}."
                        )
                    if existing.delivery_repository.casefold() == repository.casefold():
                        raise ProjectAlreadyExistsError(
                            f"Delivery repository {repository!r} is already pinned by "
                            f"project {existing_name!r}."
                        )

            entry = ProjectEntry(
                name=name,
                source_path=canonical_path,
                description=description,
                registered_at=datetime.now(tz=UTC).isoformat(),
                delivery_repository=repository,
                source_device=source_device,
                source_inode=source_inode,
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
        if name in projects:
            return ProjectEntry.from_dict(projects[name])
        return self._canonical_entry(name, projects)

    def resolve(self, name: str) -> ProjectEntry:
        """
        Resolve a name through its canonical slug and verify its filesystem binding.

        Unlike ``get``, this is a security-sensitive lookup for delivery. It
        rejects ambiguous or unsafe hand-edited aliases and a source directory
        that vanished, became a symlink, or was replaced after registration.
        """
        projects = self._load()
        entry = self._canonical_entry(name, projects)
        self._verify_current_path(entry)
        return entry

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
        with self._mutation_lock():
            projects = self._load()
            stored_name = name
            if stored_name not in projects:
                stored_name = self._canonical_entry(name, projects).name
            del projects[stored_name]
            self._save(projects)

    def exists(self, name: str) -> bool:
        """Return True if the project name is registered."""
        return name in self._load()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @contextmanager
    def _mutation_lock(self) -> Iterator[None]:
        """Serialize each complete registry read-modify-write transaction."""
        self._validate_config_dir(create=True)
        directory_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        directory_flags |= getattr(os, "O_DIRECTORY", 0)
        directory_flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            directory_descriptor = os.open(self._config_dir, directory_flags)
        except OSError as exc:
            raise ProjectRegistryError(
                f"Cannot open project registry directory safely: {exc}"
            ) from exc
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(
                self._lock_path.name,
                flags,
                0o600,
                dir_fd=directory_descriptor,
            )
        except OSError as exc:
            os.close(directory_descriptor)
            raise ProjectRegistryError(
                f"Cannot open project registry lock safely: {exc}"
            ) from exc

        locked = False
        try:
            opened_directory = os.fstat(directory_descriptor)
            visible_directory = self._config_dir.lstat()
            opened = os.fstat(descriptor)
            try:
                visible = os.stat(
                    self._lock_path.name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise ProjectRegistryError(
                    f"Cannot inspect project registry lock safely: {exc}"
                ) from exc
            if (
                not stat.S_ISDIR(opened_directory.st_mode)
                or not stat.S_ISDIR(visible_directory.st_mode)
                or (opened_directory.st_dev, opened_directory.st_ino)
                != (visible_directory.st_dev, visible_directory.st_ino)
                or opened_directory.st_uid != os.geteuid()
                or stat.S_IMODE(opened_directory.st_mode) & 0o022
                or not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(visible.st_mode)
                or opened.st_nlink != 1
                or visible.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
                or opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) & 0o077
            ):
                raise ProjectRegistryError(
                    f"Project registry lock must be one controller-owned regular file: "
                    f"{self._lock_path}"
                )
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            except OSError as exc:
                raise ProjectRegistryError(f"Cannot acquire project registry lock: {exc}") from exc
            locked = True
            current_directory = self._config_dir.lstat()
            current_lock = os.stat(
                self._lock_path.name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if (
                (current_directory.st_dev, current_directory.st_ino)
                != (opened_directory.st_dev, opened_directory.st_ino)
                or not stat.S_ISDIR(current_directory.st_mode)
                or current_directory.st_uid != os.geteuid()
                or stat.S_IMODE(current_directory.st_mode) & 0o022
                or (current_lock.st_dev, current_lock.st_ino)
                != (opened.st_dev, opened.st_ino)
                or not stat.S_ISREG(current_lock.st_mode)
                or current_lock.st_nlink != 1
                or current_lock.st_uid != os.geteuid()
                or stat.S_IMODE(current_lock.st_mode) & 0o077
            ):
                raise ProjectRegistryError(
                    "Project registry lock identity changed while waiting"
                )
            yield
        finally:
            if locked:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                os.close(directory_descriptor)
            except OSError:
                pass

    @staticmethod
    def _canonical_entry(
        name: str,
        projects: dict[str, dict[str, Any]],
    ) -> ProjectEntry:
        canonical_name = require_slug(name)
        matches: list[ProjectEntry] = []
        for stored_name, raw_entry in projects.items():
            if slug(stored_name) != canonical_name:
                continue
            try:
                stored_canonical = require_slug(stored_name)
            except InvalidSlugError as exc:
                raise ProjectRegistryError(
                    f"Project registry contains an unsafe canonical alias {stored_name!r}"
                ) from exc
            if stored_canonical != canonical_name:
                raise ProjectRegistryError(
                    f"Project registry contains a corrupt canonical alias {stored_name!r}"
                )
            matches.append(ProjectEntry.from_dict(raw_entry))

        if not matches:
            registered = list(projects.keys())
            raise ProjectNotFoundError(
                f"Project {name!r} is not registered. "
                + (
                    f"Registered projects: {registered}"
                    if registered
                    else "No projects registered yet."
                )
            )
        if len(matches) != 1:
            aliases = sorted(entry.name for entry in matches)
            raise ProjectRegistryError(
                f"Project {name!r} is ambiguous: canonical identity {canonical_name!r} "
                f"has multiple registry aliases {aliases}."
            )
        return matches[0]

    @staticmethod
    def _inspect_registration_path(source_path: str | Path) -> tuple[str, int, int]:
        supplied = Path(source_path).expanduser()
        try:
            if supplied.is_symlink():
                raise ValueError(f"source_path must not be a symlink: {supplied}")
            resolved = supplied.resolve(strict=True)
            path_stat = resolved.stat(follow_symlinks=False)
        except FileNotFoundError as exc:
            raise ValueError(f"source_path does not exist: {supplied}") from exc
        except OSError as exc:
            raise ValueError(f"source_path cannot be inspected safely: {supplied}: {exc}") from exc
        if not stat.S_ISDIR(path_stat.st_mode):
            raise ValueError(f"source_path is not a directory: {supplied}")
        return str(resolved), int(path_stat.st_dev), int(path_stat.st_ino)

    @staticmethod
    def _verify_current_path(entry: ProjectEntry) -> None:
        path = entry.path
        try:
            if path.is_symlink():
                raise ProjectRegistryError(
                    f"Registered source path became a symlink: {entry.source_path}"
                )
            resolved = path.resolve(strict=True)
            path_stat = resolved.stat(follow_symlinks=False)
        except FileNotFoundError as exc:
            raise ProjectRegistryError(
                f"Registered source path no longer exists: {entry.source_path}"
            ) from exc
        except OSError as exc:
            raise ProjectRegistryError(
                f"Registered source path cannot be inspected safely: {entry.source_path}: {exc}"
            ) from exc

        if resolved != path or not stat.S_ISDIR(path_stat.st_mode):
            raise ProjectRegistryError(
                "Registered source path is no longer the pinned canonical directory: "
                f"{entry.source_path}"
            )
        if entry.source_device is not None and entry.source_inode is not None:
            current_identity = (int(path_stat.st_dev), int(path_stat.st_ino))
            pinned_identity = (entry.source_device, entry.source_inode)
            if current_identity != pinned_identity:
                raise ProjectRegistryError(
                    f"Registered source path identity changed for project {entry.name!r}"
                )

    def _load(self) -> dict[str, dict[str, Any]]:
        self._validate_config_dir(create=False)
        try:
            path_stat = self._path.lstat()
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise ProjectRegistryError(f"Cannot inspect project registry: {exc}") from exc

        if stat.S_ISLNK(path_stat.st_mode):
            raise ProjectRegistryError(f"Project registry must not be a symlink: {self._path}")
        if not stat.S_ISREG(path_stat.st_mode):
            raise ProjectRegistryError(f"Project registry must be a regular file: {self._path}")

        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self._path, flags)
            with os.fdopen(descriptor, encoding="utf-8") as handle:
                raw_projects = json.load(handle)
        except (json.JSONDecodeError, OSError, UnicodeError) as exc:
            raise ProjectRegistryError(
                f"Project registry is unreadable or malformed: {exc}"
            ) from exc

        if not isinstance(raw_projects, dict):
            raise ProjectRegistryError("Project registry root must be a JSON object")

        projects: dict[str, dict[str, Any]] = {}
        for key, raw_entry in raw_projects.items():
            if not isinstance(key, str) or not isinstance(raw_entry, dict):
                raise ProjectRegistryError("Project registry entries must be named JSON objects")
            try:
                entry = ProjectEntry.from_dict(raw_entry)
            except (KeyError, TypeError, ValueError) as exc:
                raise ProjectRegistryError(
                    f"Project registry entry {key!r} is malformed: {exc}"
                ) from exc
            if entry.name != key:
                raise ProjectRegistryError(
                    f"Project registry key {key!r} does not match entry name {entry.name!r}"
                )
            projects[key] = raw_entry
        return projects

    def _save(self, projects: dict[str, dict[str, Any]]) -> None:
        self._validate_config_dir(create=True)
        if self._path.is_symlink():
            raise ProjectRegistryError(f"Project registry must not be a symlink: {self._path}")

        descriptor, temporary_name = tempfile.mkstemp(
            dir=self._path.parent,
            prefix=f".{self._path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(projects, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self._path)
            directory_descriptor = os.open(self._path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    def _validate_config_dir(self, *, create: bool) -> None:
        try:
            metadata = self._config_dir.lstat()
        except FileNotFoundError:
            if not create:
                return
            self._config_dir.mkdir(parents=True, exist_ok=True)
            metadata = self._config_dir.lstat()
        except OSError as exc:
            raise ProjectRegistryError(
                f"Cannot inspect project registry directory: {exc}"
            ) from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise ProjectRegistryError(
                "Project registry directory must be a real directory owned by the "
                f"controller and not group/world writable: {self._config_dir}"
            )
