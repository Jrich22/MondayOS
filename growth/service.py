"""
GrowthService — the coordinating façade behind Monday.growth().

The service holds no policy of its own. Isolation belongs to growth.project, the
approval contract to growth.fingerprint, the lifecycle to growth.content, and the
human-approval gate to agents.gates. This module wires them together and returns
plain dictionaries for the API layer to wrap.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents.gates import GATED_ACTIONS
from growth.analytics import GrowthAnalytics
from growth.brain.engine import GrowthBrain
from growth.brain.memory import MemoryCategory
from growth.brain.models import RecommendationStatus
from growth.campaign import Campaign, CampaignStatus
from growth.content import ContentItem, ContentStatus, ContentType
from growth.dispatch import PublishDispatcher
from growth.events import EventSource, EventType
from growth.library import ContentLibrary
from growth.onboarding import (
    PlatformIntent,
    WeeklyReview,
    evaluate_readiness,
    supported_platform_names,
)
from growth.store import GrowthStore, WorkspaceHandle
from integrations.publishing.connector import PublishingConnector
from monday.project import ProjectRegistry

# The gated action a publishing connector must declare (ADR-012). Registered in
# agents.roles.GATED_ACTIONS; named for content specifically because Monday.publish()
# already means Confluence document publishing.
PUBLISH_ACTION = "publish_content"


class GrowthService:
    """Growth operations for one MondayOS project root."""

    def __init__(
        self,
        project_root: Path = Path("."),
        registry: ProjectRegistry | None = None,
        connector: PublishingConnector | None = None,
        now: Callable[[], datetime] | None = None,
        jitter: float = 0.0,
    ) -> None:
        self._root = Path(project_root)
        self._store = GrowthStore(self._root, registry=registry)
        # Injected for tests and the CLI smoke path; None resolves through the
        # connector factory, which returns the fake until real adapters exist.
        self._connector = connector
        self._now = now
        self._jitter = jitter

    def _dispatcher(self, project: str, actor: str = "human:cli") -> PublishDispatcher:
        """A dispatcher bound to exactly one workspace."""
        return PublishDispatcher(
            handle=self._store.open(project),
            project_root=self._root,
            connector=self._connector,
            now=self._now,
            jitter=self._jitter,
            actor=actor,
        )

    # ------------------------------------------------------------------
    # Workspace
    # ------------------------------------------------------------------

    def init_workspace(self, project: str) -> dict[str, Any]:
        """Create an empty growth workspace for a registered project."""
        workspace = self._store.init_workspace(project)
        return workspace.to_dict()

    def get_workspace(self, project: str) -> dict[str, Any]:
        """Read one project's workspace."""
        return self._store.open(project).read().to_dict()

    def list_workspaces(self) -> list[str]:
        """Slugs of every initialized workspace."""
        return self._store.list_workspaces()

    def bind(
        self,
        project: str,
        platform: str,
        account_id: str,
        account_handle: str = "",
        secret_name: str = "",
    ) -> dict[str, str]:
        """Bind a publishing account to a workspace, by secret name only."""
        binding = self._store.open(project).bind(
            platform=platform,
            account_id=account_id,
            account_handle=account_handle,
            secret_name=secret_name,
        )
        return binding.redacted()

    def list_bindings(self, project: str) -> list[dict[str, str]]:
        """Every binding in a workspace, redacted."""
        return [b.redacted() for b in self._store.open(project).read().bindings]

    def credential_status(
        self, project: str, environ: dict[str, str] | None = None
    ) -> list[dict[str, Any]]:
        """Per-binding report of whether its credential is present in the environment."""
        rows: list[dict[str, Any]] = []
        for binding in self._store.open(project).read().bindings:
            check = binding.credential_check(environ)
            rows.append(
                {
                    "platform": binding.platform,
                    "secret_name": binding.secret_name,
                    "ready": check.ok,
                    "detail": check.instructions(),
                }
            )
        return rows

    # ------------------------------------------------------------------
    # Content
    # ------------------------------------------------------------------

    def create_content(self, project: str, **fields: Any) -> dict[str, Any]:
        """Create a Draft content item."""
        handle = self._store.open(project)
        item = handle.create_content(
            platform=fields.get("platform", ""),
            account=fields.get("account", ""),
            media=fields.get("media"),
            copy=fields.get("copy", ""),
            cta=fields.get("cta", ""),
            destination_url=fields.get("destination_url", ""),
            scheduled_at=_as_datetime(fields.get("scheduled_at")),
            campaign=fields.get("campaign", ""),
            expected_goal=fields.get("expected_goal", ""),
            expected_audience=fields.get("expected_audience", ""),
            created_by=fields.get("created_by", "human:cli"),
        )
        return self._describe(item)

    def get_content(self, project: str, content_id: str) -> dict[str, Any]:
        """Read one content item, with its computed approval state."""
        return self._describe(self._store.open(project).get_content(content_id))

    def list_content(self, project: str, status: str = "") -> list[dict[str, Any]]:
        """Every content item in a workspace, optionally filtered by status."""
        handle = self._store.open(project)
        parsed = ContentStatus(status) if status else None
        return [self._describe(i) for i in handle.list_content(parsed)]

    def update_content(self, project: str, content_id: str, **fields: Any) -> dict[str, Any]:
        """Update a content item, resetting a stale approval automatically."""
        handle = self._store.open(project)
        supplied: dict[str, Any] = {
            key: value
            for key, value in fields.items()
            if key
            in {
                "platform",
                "account",
                "media",
                "copy",
                "cta",
                "destination_url",
                "campaign",
                "expected_goal",
                "expected_audience",
                "notes",
                "tags",
                "warnings",
            }
        }
        if "scheduled_at" in fields:
            supplied["scheduled_at"] = _as_datetime(fields["scheduled_at"])
        item = handle.update_content(
            content_id, changed_by=fields.get("changed_by", "human:cli"), **supplied
        )
        return self._describe(item)

    def submit_for_review(
        self, project: str, content_id: str, changed_by: str = "human:cli"
    ) -> dict[str, Any]:
        """
        Move Draft -> AI Review -> Ready for Review.

        Refuses an item missing any REQUIRED_FOR_REVIEW field: an unreviewable item
        must not reach a human as though it were ready.
        """
        handle = self._store.open(project)
        item = handle.get_content(content_id)
        missing = item.missing_required_fields()
        if missing:
            raise ValueError(f"{content_id} is not reviewable — missing: {', '.join(missing)}.")
        if item.status is ContentStatus.DRAFT:
            handle.transition_content(
                content_id, ContentStatus.AI_REVIEW, changed_by=changed_by, reason="submitted"
            )
        updated = handle.transition_content(
            content_id,
            ContentStatus.READY_FOR_REVIEW,
            changed_by=changed_by,
            reason="passed automated review",
        )
        return self._describe(updated)

    def approve_content(
        self, project: str, content_id: str, approved_by: str, reason: str = ""
    ) -> dict[str, Any]:
        """Record a human approval of this item's exact approved fields."""
        handle = self._store.open(project)
        item = handle.approve_content(content_id, approved_by=approved_by, reason=reason)
        return self._describe(item)

    def request_changes(
        self, project: str, content_id: str, changed_by: str, reason: str = ""
    ) -> dict[str, Any]:
        """Return an item to the author with notes."""
        handle = self._store.open(project)
        item = handle.transition_content(
            content_id, ContentStatus.CHANGES_REQUESTED, changed_by=changed_by, reason=reason
        )
        return self._describe(item)

    def cancel_content(
        self, project: str, content_id: str, changed_by: str, reason: str = ""
    ) -> dict[str, Any]:
        """Terminally cancel an item."""
        handle = self._store.open(project)
        item = handle.transition_content(
            content_id, ContentStatus.CANCELLED, changed_by=changed_by, reason=reason
        )
        return self._describe(item)

    # ------------------------------------------------------------------
    # Growth Brain (increment 6)
    # ------------------------------------------------------------------

    def _brain(self, project: str) -> GrowthBrain:
        """A Brain bound to exactly one workspace."""
        handle = self._store.open(project)
        return GrowthBrain(handle, self._root, GrowthAnalytics(handle, handle.event_store()))

    def brain_analyze(self, project: str, now: datetime | None = None) -> dict[str, Any]:
        """The full deterministic pass over one project's measurements."""
        return self._brain(project).analyze(now or datetime.now(tz=UTC))

    def brain_recommendations(
        self, project: str, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Findings that cleared the sample threshold."""
        return [
            r.to_dict() for r in self._brain(project).recommendations(now or datetime.now(tz=UTC))
        ]

    def brain_hypotheses(self, project: str, now: datetime | None = None) -> list[dict[str, Any]]:
        """Candidate explanations that did not clear the threshold."""
        return [h.to_dict() for h in self._brain(project).hypotheses(now or datetime.now(tz=UTC))]

    def brain_opportunities(
        self, project: str, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Every detected finding."""
        return [
            o.to_dict() for o in self._brain(project).opportunities(now or datetime.now(tz=UTC))
        ]

    def brain_scores(self, project: str) -> dict[str, Any]:
        """Workspace, campaign and channel health scores."""
        return self._brain(project).scores()

    def brain_forecasts(self, project: str, now: datetime | None = None) -> dict[str, Any]:
        """Rule-based projections."""
        return self._brain(project).forecasts(now or datetime.now(tz=UTC))

    def brain_experiments(self, project: str, now: datetime | None = None) -> list[dict[str, Any]]:
        """Experiments the Brain proposes, all requiring human approval to run."""
        from growth.brain.experiments import suggest_experiments

        moment = now or datetime.now(tz=UTC)
        brain = self._brain(project)
        return [
            e.to_dict() for e in suggest_experiments(project, brain.opportunities(moment), moment)
        ]

    def memory_record(self, project: str, **fields: Any) -> dict[str, Any]:
        """Record a tentative claim in this project's marketing memory."""
        brain = self._brain(project)
        entry = brain.remember(
            category=MemoryCategory(str(fields.get("category", "recurring-pattern"))),
            statement=str(fields.get("statement", "")),
            sample_size=int(fields.get("sample_size", 0)),
            now=_as_datetime(fields.get("now")) or datetime.now(tz=UTC),
            confidence=str(fields.get("confidence", "low")),
            metric_affected=str(fields.get("metric_affected", "")),
            date_range=str(fields.get("date_range", "")),
            synthetic=bool(fields.get("synthetic", False)),
        )
        return entry.to_dict()

    def memory_list(self, project: str, status: str = "") -> list[dict[str, Any]]:
        """Memory entries, optionally filtered by status."""
        from growth.brain.memory import MemoryStatus

        memory = self._brain(project).memory
        entries = memory.query(status=MemoryStatus(status)) if status else memory.all()
        return [e.to_dict() for e in entries]

    def memory_validate(self, project: str, entry_id: str, by: str, reason: str) -> dict[str, Any]:
        """Promote a tentative claim to validated."""
        return (
            self._brain(project)
            .memory.validate(entry_id, datetime.now(tz=UTC), by, reason)
            .to_dict()
        )

    def memory_invalidate(
        self, project: str, entry_id: str, by: str, reason: str
    ) -> dict[str, Any]:
        """Mark a claim contradicted by later evidence."""
        return (
            self._brain(project)
            .memory.invalidate(entry_id, datetime.now(tz=UTC), by, reason)
            .to_dict()
        )

    def review_recommendation(
        self, project: str, recommendation: dict[str, Any], status: str, by: str, reason: str
    ) -> dict[str, Any]:
        """Record a human decision on a recommendation."""
        from growth.brain.models import Recommendation

        record = Recommendation.from_dict(recommendation)
        return (
            self._brain(project)
            .review_recommendation(
                record, RecommendationStatus(status), by, reason, datetime.now(tz=UTC)
            )
            .to_dict()
        )

    # ------------------------------------------------------------------
    # Analytics (increment 5)
    # ------------------------------------------------------------------

    def _analytics(self, project: str) -> GrowthAnalytics:
        """Analytics bound to exactly one workspace."""
        handle = self._store.open(project)
        return GrowthAnalytics(handle, handle.event_store())

    def record_event(self, project: str, **fields: Any) -> dict[str, Any]:
        """
        Record one performance observation.

        source defaults to imported: an operator supplying data by hand is the
        only real source available, and defaulting to synthetic would mislabel it.
        """
        store = self._store.open(project).event_store()
        event = store.record(
            event_type=EventType(str(fields.get("event_type", ""))),
            source=EventSource(str(fields.get("source", EventSource.IMPORTED.value))),
            occurred_at=_as_datetime(fields.get("occurred_at")) or datetime.now(tz=UTC),
            content_id=str(fields.get("content_id", "")),
            campaign=str(fields.get("campaign", "")),
            platform=str(fields.get("platform", "")),
            value=float(fields.get("value", 1.0)),
            name=str(fields.get("name", "")),
            metadata=dict(fields.get("metadata") or {}),
        )
        return event.to_dict()

    def import_events(
        self, project: str, events: list[dict[str, Any]], source: str = "imported"
    ) -> dict[str, Any]:
        """Bulk-record observations, defaulting every row to one source."""
        rows = [{**row, "source": row.get("source", source)} for row in events]
        written = self._store.open(project).event_store().record_many(rows)
        return {"project": project, "recorded": written, "source": source}

    def list_events(self, project: str, **filters: Any) -> list[dict[str, Any]]:
        """Query this project's events."""
        store = self._store.open(project).event_store()
        event_type = filters.get("event_type")
        return [
            e.to_dict()
            for e in store.query(
                content_id=str(filters.get("content_id", "")),
                campaign=str(filters.get("campaign", "")),
                platform=str(filters.get("platform", "")),
                event_type=EventType(event_type) if event_type else None,
                since=_as_datetime(filters.get("since")),
                until=_as_datetime(filters.get("until")),
            )
        ]

    def workspace_analytics(self, project: str) -> dict[str, Any]:
        """Whole-project metrics including approval and publishing rates."""
        return self._analytics(project).workspace_performance()

    def campaign_analytics(self, project: str, campaign_id: str) -> dict[str, Any]:
        """Metrics, delivery counts and objective progress for one campaign."""
        return self._analytics(project).campaign_performance(campaign_id)

    def content_analytics(self, project: str, content_id: str) -> dict[str, Any]:
        """Metrics for one content item."""
        return self._analytics(project).content_performance(content_id)

    def platform_analytics(self, project: str) -> list[dict[str, Any]]:
        """Metrics grouped by platform."""
        return self._analytics(project).platform_performance()

    def time_series(self, project: str, **fields: Any) -> dict[str, Any]:
        """One metric bucketed over time."""
        return self._analytics(project).time_series(
            metric=str(fields.get("metric", "impressions")),
            granularity=str(fields.get("granularity", "day")),
            campaign=str(fields.get("campaign", "")),
            platform=str(fields.get("platform", "")),
        )

    def trend(
        self, project: str, metric: str, period_days: int = 7, **fields: Any
    ) -> dict[str, Any]:
        """Compare the last period against the one before it."""
        now = _as_datetime(fields.get("now")) or datetime.now(tz=UTC)
        return (
            self._analytics(project)
            .trend(
                metric=metric,
                period_days=period_days,
                now=now,
                campaign=str(fields.get("campaign", "")),
                platform=str(fields.get("platform", "")),
            )
            .to_dict()
        )

    def funnel(self, project: str, campaign: str = "", platform: str = "") -> dict[str, Any]:
        """The conversion funnel for a project, campaign, or platform."""
        return self._analytics(project).funnel(campaign=campaign, platform=platform)

    def take_snapshot(self, project: str, **fields: Any) -> dict[str, Any]:
        """Capture current metrics so later trends have a baseline."""
        now = _as_datetime(fields.get("now")) or datetime.now(tz=UTC)
        followers = {k: float(v) for k, v in (fields.get("followers") or {}).items()}
        return (
            self._analytics(project)
            .take_snapshot(now=now, followers=followers, note=str(fields.get("note", "")))
            .to_dict()
        )

    def list_snapshots(self, project: str) -> list[dict[str, Any]]:
        """Every snapshot for this project."""
        return [s.to_dict() for s in self._analytics(project).snapshots()]

    def write_aggregate(self, project: str, now: datetime | None = None) -> dict[str, Any]:
        """Write the portfolio-readable aggregate for this project."""
        return self._analytics(project).write_aggregate(now or datetime.now(tz=UTC))

    # ------------------------------------------------------------------
    # Campaigns (increment 4)
    # ------------------------------------------------------------------

    def create_campaign(self, project: str, name: str, **fields: Any) -> dict[str, Any]:
        """Create a Draft campaign in a project's workspace."""
        return self._store.open(project).create_campaign(name=name, **fields).to_dict()

    def get_campaign(self, project: str, campaign_id: str) -> dict[str, Any]:
        """Read one campaign with its derived content counts."""
        handle = self._store.open(project)
        campaign = handle.get_campaign(campaign_id)
        return self._describe_campaign(handle, campaign)

    def list_campaigns(self, project: str, status: str = "") -> list[dict[str, Any]]:
        """Campaigns in a project, optionally filtered by status."""
        handle = self._store.open(project)
        parsed = CampaignStatus(status) if status else None
        return [self._describe_campaign(handle, c) for c in handle.list_campaigns(parsed)]

    def transition_campaign(
        self,
        project: str,
        campaign_id: str,
        status: str,
        changed_by: str = "human:cli",
        reason: str = "",
    ) -> dict[str, Any]:
        """Move a campaign along its lifecycle."""
        handle = self._store.open(project)
        campaign = handle.transition_campaign(
            campaign_id, CampaignStatus(status), changed_by=changed_by, reason=reason
        )
        return self._describe_campaign(handle, campaign)

    def assign_campaign(
        self, project: str, content_id: str, campaign_id: str, changed_by: str = "human:cli"
    ) -> dict[str, Any]:
        """Attach content to a campaign in the same workspace, or detach it."""
        handle = self._store.open(project)
        return self._describe(handle.assign_campaign(content_id, campaign_id, changed_by))

    # ------------------------------------------------------------------
    # Content library (increment 4)
    # ------------------------------------------------------------------

    def library_search(self, project: str, **filters: Any) -> list[dict[str, Any]]:
        """Query the content library for one project."""
        library = ContentLibrary(self._store.open(project))
        content_type = filters.get("content_type")
        status = filters.get("status")
        entries = library.search(
            text=str(filters.get("text", "")),
            theme=str(filters.get("theme", "")),
            campaign=str(filters.get("campaign", "")),
            platform=str(filters.get("platform", "")),
            content_type=ContentType(content_type) if content_type else None,
            status=ContentStatus(status) if status else None,
            tag=str(filters.get("tag", "")),
            reusable_only=bool(filters.get("reusable_only", False)),
        )
        return [e.to_dict() for e in entries]

    def library_summary(self, project: str) -> dict[str, Any]:
        """Counts by type, platform, status and theme for one project."""
        return ContentLibrary(self._store.open(project)).summary()

    def library_top(self, project: str, limit: int = 10) -> dict[str, Any]:
        """Highest-performing content, with the basis for the ranking."""
        entries, basis = ContentLibrary(self._store.open(project)).highest_performing(limit)
        return {"entries": [e.to_dict() for e in entries], "basis": basis}

    def library_reusable(self, project: str, days: int = 0) -> list[dict[str, Any]]:
        """Reusable content, optionally only what has not been reused recently."""
        library = ContentLibrary(self._store.open(project))
        entries = library.not_reused_since(days) if days else library.reusable()
        return [e.to_dict() for e in entries]

    def library_variants(self, project: str, variant_group_id: str) -> list[dict[str, Any]]:
        """Every per-platform variant of one idea."""
        return [
            e.to_dict()
            for e in ContentLibrary(self._store.open(project)).variants(variant_group_id)
        ]

    # ------------------------------------------------------------------
    # Onboarding (increment 4)
    # ------------------------------------------------------------------

    def onboard(self, project: str, **fields: Any) -> dict[str, Any]:
        """
        Record growth onboarding for a project.

        Accepts platform intents as {platform, account_label} pairs. It records no
        credential of any kind: account connection is a later increment.
        """
        handle = self._store.open(project)
        workspace = handle.read()
        onboarding = workspace.onboarding

        if "platforms" in fields:
            onboarding.platform_intents = [
                PlatformIntent(
                    platform=str(p.get("platform", "")),
                    account_label=str(p.get("account_label", "")),
                )
                for p in (fields.get("platforms") or [])
            ]
        if "cadence_per_week" in fields:
            onboarding.cadence_per_week = int(fields["cadence_per_week"])
        if "prohibited_content" in fields:
            onboarding.prohibited_content = [str(x) for x in (fields["prohibited_content"] or [])]
        if "weekly_review_day" in fields or "weekly_review_hour_utc" in fields:
            onboarding.weekly_review = WeeklyReview(
                weekday=str(fields.get("weekly_review_day", "sunday")),
                hour_utc=int(fields.get("weekly_review_hour_utc", 17)),
            )
        if "objectives" in fields:
            workspace.marketing.objectives = [str(o) for o in (fields["objectives"] or [])]
        if "brand_voice" in fields:
            workspace.brand.voice = str(fields["brand_voice"])
        if "brand_assets" in fields:
            workspace.brand.approved_imagery = [str(a) for a in (fields["brand_assets"] or [])]
        if "audience_personas" in fields:
            workspace.audience.personas = [str(a) for a in (fields["audience_personas"] or [])]
        if "audience_icps" in fields:
            workspace.audience.icps = [str(a) for a in (fields["audience_icps"] or [])]

        satisfied, missing = evaluate_readiness(workspace)
        onboarding.completed_steps = satisfied
        onboarding.growth_ready_for_planning = not missing
        if not missing and onboarding.completed_at is None:
            onboarding.completed_at = datetime.now(tz=UTC)
        # Never set here. Account connection is a later increment and nothing in
        # growth/ has the authority to declare real publishing ready.
        onboarding.growth_ready_for_real_publishing = False

        handle.write(workspace)
        return self.onboarding_status(project)

    def onboarding_status(self, project: str) -> dict[str, Any]:
        """Readiness, satisfied steps, and what is still missing."""
        workspace = self._store.open(project).read()
        satisfied, missing = evaluate_readiness(workspace)
        return {
            "project": workspace.slug,
            "growth_ready_for_planning": workspace.onboarding.growth_ready_for_planning,
            "growth_ready_for_real_publishing": (
                workspace.onboarding.growth_ready_for_real_publishing
            ),
            "completed_steps": satisfied,
            "missing_steps": missing,
            "platform_intents": [p.to_dict() for p in workspace.onboarding.platform_intents],
            "supported_platforms": list(supported_platform_names()),
            "weekly_review": (
                workspace.onboarding.weekly_review.to_dict()
                if workspace.onboarding.weekly_review
                else None
            ),
            "note": (
                "Real publishing readiness requires an account connection, which does "
                "not exist yet. Publishing runs against the fake connector only."
            ),
        }

    def seed_demo(self, project: str) -> dict[str, Any]:
        """Populate a workspace with clearly-marked synthetic demo data."""
        from growth.demo import seed_workspace

        return seed_workspace(self._store.open(project))

    # ------------------------------------------------------------------
    # Publishing (increment 3)
    # ------------------------------------------------------------------

    def schedule_content(
        self, project: str, content_id: str, actor: str = "human:cli"
    ) -> dict[str, Any]:
        """Move an approved, future-dated item to Scheduled."""
        return self._dispatcher(project, actor).schedule(content_id).to_dict()

    def publish_content_now(
        self, project: str, content_id: str, actor: str = "human:cli", force: bool = False
    ) -> dict[str, Any]:
        """Publish an approved item, or reconcile if this version already landed."""
        return self._dispatcher(project, actor).publish(content_id, force_due=force).to_dict()

    def retry_publication(
        self, project: str, content_id: str, actor: str = "human:cli"
    ) -> dict[str, Any]:
        """Re-attempt a failed item once its backoff window has elapsed."""
        return self._dispatcher(project, actor).retry(content_id).to_dict()

    def publication_status(self, project: str, content_id: str) -> dict[str, Any]:
        """Publication state, attempt history, pause state, and audit trail."""
        return self._dispatcher(project).publication_status(content_id)

    def set_pause(
        self,
        project: str,
        scope: str,
        active: bool,
        target: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        """Engage or clear one pause scope."""
        controller = self._store.open(project).pause_controller(self._root)
        return controller.set_pause(scope, active, target=target, reason=reason).to_dict()

    def list_pauses(self, project: str) -> dict[str, Any]:
        """Active pauses visible to this workspace, including the global stop."""
        return self._store.open(project).pause_controller(self._root).list_pauses()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _describe_campaign(handle: WorkspaceHandle, campaign: Campaign) -> dict[str, Any]:
        """Campaign payload plus counts derived from its attached content."""
        payload = campaign.to_dict()
        items = [i for i in handle.list_content() if i.campaign == campaign.id]
        payload["content_count"] = len(items)
        payload["approved_count"] = sum(1 for i in items if i.is_approved)
        payload["published_count"] = sum(1 for i in items if i.status is ContentStatus.PUBLISHED)
        payload["accepts_content"] = campaign.accepts_content()
        return payload

    @staticmethod
    def _describe(item: ContentItem) -> dict[str, Any]:
        """Item payload plus the derived approval facts callers actually need."""
        payload = item.to_dict()
        payload["current_fingerprint"] = item.current_fingerprint()
        payload["is_approved"] = item.is_approved
        payload["approval_is_stale"] = item.approval_is_stale()
        payload["missing_required_fields"] = item.missing_required_fields()
        payload["publishable"] = item.is_approved and item.status in (
            ContentStatus.APPROVED,
            ContentStatus.SCHEDULED,
        )
        payload["publishable_reason"] = (
            "Approved and admissible for publishing."
            if payload["publishable"]
            else f"Not publishable from {item.status.value} with is_approved={item.is_approved}."
        )
        return payload

    @staticmethod
    def handle_for(project: str, root: Path) -> WorkspaceHandle:
        """Convenience for callers that want direct handle access."""
        return GrowthStore(root).open(project)


def publish_action_is_gated() -> bool:
    """True when the content-publishing action is registered as human-gated."""
    return PUBLISH_ACTION in GATED_ACTIONS


def _as_datetime(value: Any) -> datetime | None:
    """Coerce an ISO string or datetime to a UTC datetime; None passes through."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    parsed = datetime.fromisoformat(str(value).rstrip("Z"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
