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
from growth.campaign import Campaign, CampaignStatus, CampaignTransition
from growth.content import (
    PUBLISHING_STATES,
    ContentItem,
    ContentStatus,
    ContentTransition,
    ContentType,
)
from growth.errors import (
    BindingNotFoundError,
    CampaignNotFoundError,
    ContentNotFoundError,
    CrossCampaignError,
    GrowthParseError,
    InvalidTransitionError,
    WorkspaceExistsError,
    WorkspaceNotFoundError,
)
from growth.project import ResolvedProject, resolve_project, workspace_path
from growth.workspace import Workspace
from monday.project import ProjectRegistry

if TYPE_CHECKING:
    from growth.events import EventStore
    from growth.pause import PauseController

_CONTENT_PREFIX = "CONTENT"
_CAMPAIGN_PREFIX = "CAMPAIGN"
_SNAPSHOT_PREFIX = "SNAPSHOT"
_SEQUENCES_FILENAME = ".sequences.json"
_WORKSPACE_FILENAME = "workspace.md"
_CONTENT_DIRNAME = "content"
_CAMPAIGN_DIRNAME = "campaigns"
_SNAPSHOT_DIRNAME = "snapshots"
_AGGREGATE_FILENAME = "aggregates.json"

# A CAMPAIGN-shaped value on a content item must resolve to a campaign in THIS
# workspace. Any other string stays a free-text label, which keeps the existing
# descriptive use of ContentItem.campaign working unchanged.
_CAMPAIGN_ID_RE = re.compile(rf"^{_CAMPAIGN_PREFIX}-\d+$")
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
        self._campaign_dir = directory / _CAMPAIGN_DIRNAME
        self._snapshot_dir = directory / _SNAPSHOT_DIRNAME
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
    # Analytics storage (increment 5)
    # ------------------------------------------------------------------

    def event_store(self) -> EventStore:
        """Append-only performance events for THIS workspace."""
        from growth.events import EventStore

        return EventStore(self._dir, self.slug)

    def next_snapshot_id(self) -> EntityId:
        """Allocate the next snapshot id in this workspace."""
        return self._next_id(_SNAPSHOT_PREFIX, self._snapshot_dir)

    def save_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Persist one metric snapshot."""
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)
        path = self._snapshot_dir / f"{snapshot['id']}.json"
        path.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")

    def list_snapshots(self) -> list[dict[str, Any]]:
        """Every snapshot in this workspace, oldest id first."""
        if not self._snapshot_dir.exists():
            return []
        rows: list[dict[str, Any]] = []
        for path in sorted(self._snapshot_dir.glob(f"{_SNAPSHOT_PREFIX}-*.json")):
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(loaded, dict):
                rows.append(loaded)
        return rows

    def write_aggregate(self, aggregate: dict[str, Any]) -> Path:
        """
        Write the portfolio-readable aggregate for this project.

        The ONLY file in a workspace a portfolio view may open. Its contents are
        the caller's responsibility to keep free of project detail; a test
        asserts it carries no copy, media, audience, or account binding.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / _AGGREGATE_FILENAME
        path.write_text(json.dumps(aggregate, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def read_aggregate(self) -> dict[str, Any]:
        """Read this project's aggregate, or an empty dict if never written."""
        path = self._dir / _AGGREGATE_FILENAME
        if not path.exists():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    # ------------------------------------------------------------------
    # Campaigns
    # ------------------------------------------------------------------

    def create_campaign(self, name: str, created_by: str = "human:cli", **fields: Any) -> Campaign:
        """Create a Draft campaign in this workspace."""
        campaign = Campaign(
            id=self._next_id(_CAMPAIGN_PREFIX, self._campaign_dir),
            project=self.slug,
            name=name,
            description=str(fields.get("description", "")),
            objective=str(fields.get("objective", "")),
            target_audience=str(fields.get("target_audience", "")),
            primary_conversion_goal=str(fields.get("primary_conversion_goal", "")),
            start_date=fields.get("start_date"),
            end_date=fields.get("end_date"),
            theme=str(fields.get("theme", "")),
            channels=[normalize_platform(c) for c in (fields.get("channels") or [])],
            cta=str(fields.get("cta", "")),
            destination=str(fields.get("destination", "")),
            kpis=[str(k) for k in (fields.get("kpis") or [])],
        )
        campaign.status_history.append(
            CampaignTransition(
                from_status=None,
                to_status=CampaignStatus.DRAFT,
                changed_by=created_by,
                changed_at=datetime.now(tz=UTC),
                reason="created",
            )
        )
        self._write_campaign(campaign)
        return campaign

    def get_campaign(self, campaign_id: EntityId) -> Campaign:
        """Load one campaign. Raises CampaignNotFoundError."""
        path = self._campaign_dir / f"{campaign_id}.md"
        if not path.exists():
            raise CampaignNotFoundError(campaign_id, self.slug)
        return Campaign.from_dict(_read_frontmatter(path))

    def list_campaigns(self, status: CampaignStatus | None = None) -> list[Campaign]:
        """All campaigns in this workspace, optionally filtered by status."""
        if not self._campaign_dir.exists():
            return []
        campaigns: list[Campaign] = []
        for path in sorted(self._campaign_dir.glob(f"{_CAMPAIGN_PREFIX}-*.md")):
            try:
                campaigns.append(Campaign.from_dict(_read_frontmatter(path)))
            except (GrowthParseError, ValueError, KeyError) as exc:
                _warnings.warn(f"Skipping {path.name}: {exc}", stacklevel=2)
        if status is not None:
            campaigns = [c for c in campaigns if c.status is status]
        return sorted(campaigns, key=lambda c: c.id)

    def save_campaign(self, campaign: Campaign) -> Campaign:
        """Persist a campaign exactly as given."""
        self._write_campaign(campaign)
        return campaign

    def transition_campaign(
        self,
        campaign_id: EntityId,
        new_status: CampaignStatus,
        changed_by: str = "human:cli",
        reason: str = "",
    ) -> Campaign:
        """Move a campaign along its lifecycle. Raises InvalidTransitionError."""
        campaign = self.get_campaign(campaign_id)
        campaign.apply_transition(new_status, changed_by=changed_by, reason=reason)
        self._write_campaign(campaign)
        return campaign

    def assign_campaign(
        self, content_id: EntityId, campaign_id: str, changed_by: str = "human:cli"
    ) -> ContentItem:
        """
        Attach a content item to a campaign in THIS workspace.

        Refuses a campaign that does not exist here, which is what stops content
        drifting between campaigns or across projects. Passing an empty campaign
        id detaches the item from whichever campaign currently holds it.
        """
        item = self.get_content(content_id)
        if item.status in PUBLISHING_STATES:
            raise InvalidTransitionError(item.status.value, "recampaigned")

        previous = item.campaign
        if campaign_id:
            try:
                campaign = self.get_campaign(campaign_id)
            except CampaignNotFoundError as exc:
                raise CrossCampaignError(content_id, campaign_id, self.slug) from exc
            if campaign.project != self.slug:
                raise CrossCampaignError(content_id, campaign_id, self.slug)
            if not campaign.accepts_content():
                raise InvalidTransitionError(campaign.status.value, "accepting content")
            campaign.attach_content(item.id)
            self._write_campaign(campaign)

        if previous and _CAMPAIGN_ID_RE.match(previous) and previous != campaign_id:
            try:
                old = self.get_campaign(previous)
            except CampaignNotFoundError:
                old = None
            if old is not None:
                old.detach_content(item.id)
                self._write_campaign(old)

        item.campaign = campaign_id
        item.updated = datetime.now(tz=UTC)
        self._write_content(item)
        return item

    def _validate_campaign_ref(self, content_id: str, campaign_id: str) -> None:
        """A CAMPAIGN-shaped reference must resolve in this workspace."""
        if not campaign_id or not _CAMPAIGN_ID_RE.match(campaign_id):
            return
        try:
            campaign = self.get_campaign(campaign_id)
        except CampaignNotFoundError as exc:
            raise CrossCampaignError(content_id, campaign_id, self.slug) from exc
        if campaign.project != self.slug:
            raise CrossCampaignError(content_id, campaign_id, self.slug)

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
        content_type: ContentType = ContentType.SOCIAL_POST,
        title: str = "",
        themes: list[str] | None = None,
        audience: str = "",
        variant_group_id: str = "",
        reuse_eligible: bool = False,
        tags: list[str] | None = None,
    ) -> ContentItem:
        """Create a Draft content item in this workspace."""
        if platform:
            platform = normalize_platform(platform)
        self._validate_campaign_ref("(new)", campaign)
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
            content_type=content_type,
            title=title,
            themes=list(themes or []),
            audience=audience,
            variant_group_id=variant_group_id,
            reuse_eligible=reuse_eligible,
            tags=list(tags or []),
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
        if campaign and _CAMPAIGN_ID_RE.match(campaign):
            linked = self.get_campaign(campaign)
            linked.attach_content(item.id)
            self._write_campaign(linked)
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
        content_type: ContentType | _Unset = _UNSET,
        title: str | _Unset = _UNSET,
        themes: list[str] | _Unset = _UNSET,
        audience: str | _Unset = _UNSET,
        variant_group_id: str | _Unset = _UNSET,
        reuse_eligible: bool | _Unset = _UNSET,
        last_reused_at: datetime | None | _Unset = _UNSET,
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
            self._validate_campaign_ref(item.id, campaign)
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
        # Library metadata. None of this is fingerprinted, so none of it can
        # disturb a standing approval.
        if not isinstance(content_type, _Unset):
            item.content_type = content_type
        if not isinstance(title, _Unset):
            item.title = title
        if not isinstance(themes, _Unset):
            item.themes = list(themes)
        if not isinstance(audience, _Unset):
            item.audience = audience
        if not isinstance(variant_group_id, _Unset):
            item.variant_group_id = variant_group_id
        if not isinstance(reuse_eligible, _Unset):
            item.reuse_eligible = reuse_eligible
        if not isinstance(last_reused_at, _Unset):
            item.last_reused_at = last_reused_at

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

    def _write_campaign(self, campaign: Campaign) -> None:
        self._campaign_dir.mkdir(parents=True, exist_ok=True)
        _write_frontmatter(self._campaign_dir / f"{campaign.id}.md", campaign.to_dict())

    def _next_content_id(self) -> EntityId:
        return self._next_id(_CONTENT_PREFIX, self._content_dir)

    def _next_id(self, prefix: str, directory: Path) -> EntityId:
        """
        Allocate the next id for a prefix within THIS workspace.

        Per-workspace, never global: a shared counter would let one project infer
        another's volume from the gaps in its own ids.
        """
        sequences = self._load_sequences()
        counter = max(sequences.get(prefix, 0), self._highest_id_on_disk(prefix, directory))
        next_seq = counter + 1
        sequences[prefix] = next_seq
        self._save_sequences(sequences)
        return f"{prefix}-{next_seq:04d}"

    def _highest_id_on_disk(self, prefix: str, directory: Path) -> int:
        """
        Highest sequence number on disk for a prefix in this workspace.

        Reads filenames rather than contents, so an item whose body is unreadable
        still reserves its id. Guards against a lost or truncated sequence file
        silently reissuing an id that is already taken.
        """
        if not directory.exists():
            return 0
        pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
        highest = 0
        try:
            paths = list(directory.glob(f"{prefix}-*.md"))
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
