"""
Persistence for growth workspaces.

Storage mirrors the convention agents/registry.py and tasks/manager.py already use:
Markdown files with YAML frontmatter, and a .sequences.json for id allocation.

    <root>/growth/workspaces/<slug>/workspace.md
    <root>/growth/workspaces/<slug>/content/CONTENT-NNNN.md
    <root>/growth/workspaces/<slug>/.sequences.json

The sequence file is **per workspace**, not global. A shared counter would let one
project infer another's publishing volume from the gaps in its own ids, which is
exactly the cross-project inference ADR-011 exists to prevent.

A WorkspaceHandle fixes its root at construction. Every content operation goes
through a handle, so no operation is able to name a second project.
"""

from __future__ import annotations

import json
import re
import warnings as _warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from core.types import EntityId
from growth.binding import PlatformBinding, normalize_platform
from growth.content import (
    PUBLISHING_STATES,
    ContentItem,
    ContentStatus,
    ContentTransition,
)
from growth.errors import (
    BindingNotFoundError,
    ContentNotFoundError,
    GrowthParseError,
    InvalidTransitionError,
    WorkspaceExistsError,
    WorkspaceNotFoundError,
)
from growth.project import ResolvedProject, resolve_project, workspace_path
from growth.workspace import Workspace
from monday.project import ProjectRegistry

if TYPE_CHECKING:
    from growth.pause import PauseController

_CONTENT_PREFIX = "CONTENT"
_SEQUENCES_FILENAME = ".sequences.json"
_WORKSPACE_FILENAME = "workspace.md"
_CONTENT_DIRNAME = "content"
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


class _Unset:
    """Sentinel for 'argument not supplied', where None is itself meaningful."""


_UNSET = _Unset()


class WorkspaceHandle:
    """
    Read/write access to exactly one project's growth workspace.

    The handle is the isolation boundary in code: ``_dir`` is fixed at construction
    from an already-resolved project, and no method accepts another project.
    """

    def __init__(self, project: ResolvedProject, directory: Path) -> None:
        self._project = project
        self._dir = directory
        self._content_dir = directory / _CONTENT_DIRNAME
        self._sequences_path = directory / _SEQUENCES_FILENAME

    @property
    def slug(self) -> str:
        return self._project.slug

    @property
    def path(self) -> Path:
        return self._dir

    def exists(self) -> bool:
        return (self._dir / _WORKSPACE_FILENAME).exists()

    # ------------------------------------------------------------------
    # Workspace
    # ------------------------------------------------------------------

    def read(self) -> Workspace:
        """Load this project's workspace. Raises WorkspaceNotFoundError."""
        path = self._dir / _WORKSPACE_FILENAME
        if not path.exists():
            raise WorkspaceNotFoundError(self.slug)
        return Workspace.from_dict(_read_frontmatter(path))

    def write(self, workspace: Workspace) -> Workspace:
        """Persist the workspace, stamping ``updated``."""
        workspace.updated = datetime.now(tz=UTC)
        self._dir.mkdir(parents=True, exist_ok=True)
        _write_frontmatter(self._dir / _WORKSPACE_FILENAME, workspace.to_dict())
        return workspace

    def bind(
        self,
        platform: str,
        account_id: str,
        account_handle: str = "",
        secret_name: str = "",
    ) -> PlatformBinding:
        """Add or replace this workspace's binding for a platform."""
        workspace = self.read()
        binding = PlatformBinding(
            platform=platform,
            account_id=account_id,
            account_handle=account_handle,
            secret_name=secret_name,
        )
        workspace.bindings = [b for b in workspace.bindings if b.platform != binding.platform]
        workspace.bindings.append(binding)
        workspace.bindings.sort(key=lambda b: b.platform)
        self.write(workspace)
        return binding

    def binding(self, platform: str) -> PlatformBinding:
        """Return the active binding for a platform. Raises BindingNotFoundError."""
        found = self.read().binding_for(platform)
        if found is None:
            raise BindingNotFoundError(normalize_platform(platform), self.slug)
        return found

    # ------------------------------------------------------------------
    # Content
    # ------------------------------------------------------------------

    def create_content(
        self,
        platform: str = "",
        account: str = "",
        media: list[str] | None = None,
        copy: str = "",
        cta: str = "",
        destination_url: str = "",
        scheduled_at: datetime | None = None,
        campaign: str = "",
        expected_goal: str = "",
        expected_audience: str = "",
        created_by: str = "human:cli",
    ) -> ContentItem:
        """Create a Draft content item in this workspace."""
        if platform:
            platform = normalize_platform(platform)
        item = ContentItem(
            id=self._next_content_id(),
            project=self.slug,
            platform=platform,
            account=account,
            media=list(media or []),
            copy=copy,
            cta=cta,
            destination_url=destination_url,
            scheduled_at=scheduled_at,
            campaign=campaign,
            expected_goal=expected_goal,
            expected_audience=expected_audience,
        )
        item.status_history.append(
            ContentTransition(
                from_status=None,
                to_status=ContentStatus.DRAFT,
                changed_by=created_by,
                changed_at=datetime.now(tz=UTC),
                reason="created",
            )
        )
        self._write_content(item)
        return item

    def save_content(self, item: ContentItem) -> ContentItem:
        """
        Persist an item exactly as given.

        The publishing dispatcher owns its own transitions and needs to write the
        result without re-running update_content's approval-reset rule, which
        would fight it. Callers editing *fields* must use update_content.
        """
        self._write_content(item)
        return item

    def pause_controller(self, project_root: Path) -> PauseController:
        """Pause controls scoped to this workspace, plus the global stop."""
        from growth.pause import PauseController

        return PauseController(project_root, self._dir)

    def get_content(self, content_id: EntityId) -> ContentItem:
        """Load one content item. Raises ContentNotFoundError."""
        path = self._content_dir / f"{content_id}.md"
        if not path.exists():
            raise ContentNotFoundError(content_id, self.slug)
        return ContentItem.from_dict(_read_frontmatter(path))

    def list_content(self, status: ContentStatus | None = None) -> list[ContentItem]:
        """All content in this workspace, optionally filtered by status."""
        if not self._content_dir.exists():
            return []
        items: list[ContentItem] = []
        for path in sorted(self._content_dir.glob(f"{_CONTENT_PREFIX}-*.md")):
            try:
                items.append(ContentItem.from_dict(_read_frontmatter(path)))
            except (GrowthParseError, ValueError, KeyError) as exc:
                # One malformed file must not hide the rest of the library.
                _warnings.warn(f"Skipping {path.name}: {exc}", stacklevel=2)
        if status is not None:
            items = [i for i in items if i.status is status]
        return sorted(items, key=lambda i: i.id)

    def update_content(
        self,
        content_id: EntityId,
        changed_by: str = "human:cli",
        platform: str | _Unset = _UNSET,
        account: str | _Unset = _UNSET,
        media: list[str] | _Unset = _UNSET,
        copy: str | _Unset = _UNSET,
        cta: str | _Unset = _UNSET,
        destination_url: str | _Unset = _UNSET,
        scheduled_at: datetime | None | _Unset = _UNSET,
        campaign: str | _Unset = _UNSET,
        expected_goal: str | _Unset = _UNSET,
        expected_audience: str | _Unset = _UNSET,
        notes: str | _Unset = _UNSET,
        tags: list[str] | _Unset = _UNSET,
        warnings: list[str] | _Unset = _UNSET,
    ) -> ContentItem:
        """
        Apply field updates, resetting a stale approval automatically (ADR-013).

        If the item was Approved and any *approved* field changes, it transitions back
        to Ready for Review and its recorded fingerprint is cleared. Nothing has to
        remember to call an invalidate function — the reset is a consequence of the
        write, and ContentItem.is_approved independently recomputes the comparison.
        """
        item = self.get_content(content_id)
        if item.status in PUBLISHING_STATES:
            raise InvalidTransitionError(item.status.value, "edited")
        before = item.current_fingerprint()

        if not isinstance(platform, _Unset):
            item.platform = normalize_platform(platform) if platform else ""
        if not isinstance(account, _Unset):
            item.account = account
        if not isinstance(media, _Unset):
            item.media = list(media)
        if not isinstance(copy, _Unset):
            item.copy = copy
        if not isinstance(cta, _Unset):
            item.cta = cta
        if not isinstance(destination_url, _Unset):
            item.destination_url = destination_url
        if not isinstance(scheduled_at, _Unset):
            item.scheduled_at = scheduled_at
        if not isinstance(campaign, _Unset):
            item.campaign = campaign
        if not isinstance(expected_goal, _Unset):
            item.expected_goal = expected_goal
        if not isinstance(expected_audience, _Unset):
            item.expected_audience = expected_audience
        if not isinstance(notes, _Unset):
            item.notes = notes
        if not isinstance(tags, _Unset):
            item.tags = list(tags)
        if not isinstance(warnings, _Unset):
            item.warnings = list(warnings)

        item.updated = datetime.now(tz=UTC)

        if (
            item.status in (ContentStatus.APPROVED, ContentStatus.SCHEDULED)
            and item.current_fingerprint() != before
        ):
            item.apply_transition(
                ContentStatus.READY_FOR_REVIEW,
                changed_by="system",
                reason=(
                    "Approved fields changed; the approval no longer covers this content "
                    f"(edited by {changed_by})."
                ),
            )
            item.approved_fingerprint = ""
            item.approved_by = ""
            item.approved_at = None

        self._write_content(item)
        return item

    def transition_content(
        self,
        content_id: EntityId,
        new_status: ContentStatus,
        changed_by: str = "human:cli",
        reason: str = "",
    ) -> ContentItem:
        """Move a content item along the lifecycle. Raises InvalidTransitionError."""
        item = self.get_content(content_id)
        item.apply_transition(new_status, changed_by=changed_by, reason=reason)
        if new_status in (ContentStatus.CHANGES_REQUESTED, ContentStatus.READY_FOR_REVIEW):
            item.approved_fingerprint = ""
            item.approved_by = ""
            item.approved_at = None
        self._write_content(item)
        return item

    def approve_content(
        self, content_id: EntityId, approved_by: str, reason: str = ""
    ) -> ContentItem:
        """
        Record a human approval of this item's current approved fields.

        The fingerprint is captured at the moment of approval; any later change to a
        covered field invalidates it via update_content and via is_approved.
        """
        item = self.get_content(content_id)
        item.apply_transition(ContentStatus.APPROVED, changed_by=approved_by, reason=reason)
        item.approved_fingerprint = item.current_fingerprint()
        item.approved_by = approved_by
        item.approved_at = datetime.now(tz=UTC)
        self._write_content(item)
        return item

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _write_content(self, item: ContentItem) -> None:
        self._content_dir.mkdir(parents=True, exist_ok=True)
        _write_frontmatter(self._content_dir / f"{item.id}.md", item.to_dict())

    def _next_content_id(self) -> EntityId:
        sequences = self._load_sequences()
        counter = max(sequences.get(_CONTENT_PREFIX, 0), self._highest_id_on_disk())
        next_seq = counter + 1
        sequences[_CONTENT_PREFIX] = next_seq
        self._save_sequences(sequences)
        return f"{_CONTENT_PREFIX}-{next_seq:04d}"

    def _highest_id_on_disk(self) -> int:
        """
        Highest content sequence number on disk in this workspace.

        Reads filenames rather than contents, so an item whose body is unreadable
        still reserves its id. Guards against a lost or truncated sequence file
        silently reissuing an id that is already taken.
        """
        if not self._content_dir.exists():
            return 0
        pattern = re.compile(rf"^{re.escape(_CONTENT_PREFIX)}-(\d+)$")
        highest = 0
        try:
            paths = list(self._content_dir.glob(f"{_CONTENT_PREFIX}-*.md"))
        except OSError:
            return 0
        for path in paths:
            match = pattern.match(path.stem)
            if match:
                highest = max(highest, int(match.group(1)))
        return highest

    def _load_sequences(self) -> dict[str, int]:
        if not self._sequences_path.exists():
            return {}
        try:
            loaded = json.loads(self._sequences_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(loaded, dict):
            return {}
        return {str(k): int(v) for k, v in loaded.items() if isinstance(v, int)}

    def _save_sequences(self, sequences: dict[str, int]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._sequences_path.write_text(
            json.dumps(sequences, indent=2, sort_keys=True), encoding="utf-8"
        )


class GrowthStore:
    """
    Entry point to growth workspaces. Resolves a project name to one handle.

    Every path this class produces comes from growth.project, so the isolation rules
    are enforced in one place rather than at each call site.
    """

    def __init__(
        self, project_root: Path = Path("."), registry: ProjectRegistry | None = None
    ) -> None:
        self._root = Path(project_root)
        self._registry = (
            registry if registry is not None else ProjectRegistry(self._root / "config")
        )

    def open(self, project: str) -> WorkspaceHandle:
        """Return a handle for a registered project. Does not require the workspace to exist."""
        resolved = resolve_project(project, self._registry)
        return WorkspaceHandle(resolved, workspace_path(self._root, resolved.slug))

    def init_workspace(self, project: str) -> Workspace:
        """Create an empty workspace for a registered project. Raises WorkspaceExistsError."""
        resolved = resolve_project(project, self._registry)
        handle = WorkspaceHandle(resolved, workspace_path(self._root, resolved.slug))
        if handle.exists():
            raise WorkspaceExistsError(handle.slug)
        workspace = Workspace(slug=resolved.slug, registered_name=resolved.registered_name)
        workspace.business.name = resolved.registered_name
        return handle.write(workspace)

    def list_workspaces(self) -> list[str]:
        """Slugs of every initialized workspace, sorted."""
        base = self._root / "growth" / "workspaces"
        if not base.exists():
            return []
        return sorted(path.name for path in base.iterdir() if (path / _WORKSPACE_FILENAME).exists())


def _read_frontmatter(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        raise GrowthParseError("No YAML frontmatter block found (expected --- ... ---)", str(path))
    try:
        loaded = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise GrowthParseError(f"Malformed YAML frontmatter: {exc}", str(path)) from exc
    if not isinstance(loaded, dict):
        raise GrowthParseError("Frontmatter is not a mapping", str(path))
    return loaded


def _write_frontmatter(path: Path, payload: dict[str, Any]) -> None:
    body = yaml.dump(
        payload, default_flow_style=False, allow_unicode=True, sort_keys=True, width=100
    )
    path.write_text(f"---\n{body}---\n", encoding="utf-8")
