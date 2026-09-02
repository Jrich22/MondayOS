"""
The Weekly Marketing Package - one week's complete plan, assembled and reviewable.

This is where generation meets the existing approval machinery, and the seam has
one rule: **generation never bypasses approval.** Assets become real ContentItems
in DRAFT and travel the same lifecycle as anything written by hand. There is no
path from this module to a published post that does not run through a human.

Approving a week is not a blanket authorisation. It approves each post
individually, against that post's own fingerprint, and authorises nothing that
was not in the package when the human looked at it. A week-level approval of five
posts produces five per-item approvals — the same five, and no others (ADR-013).

The package is produced by an on-demand command. No scheduler is built here;
recurring execution is left to MondayOS scheduling, and pretending otherwise
would put a background job in a subsystem nobody has asked to run unattended.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from growth.content import ContentType
from growth.generation.copywriter import (
    Copywriter,
    enhanced_review_for,
    gate_for_review,
    summarize_effort,
)
from growth.generation.formatter import make_variants
from growth.generation.models import (
    AssetKind,
    BrandContext,
    GeneratedAsset,
    InvalidPackageTransitionError,
    PackageStatus,
    can_transition,
    package_id,
)
from growth.generation.planner import ContentPlanner, WeeklyPlan
from growth.store import WorkspaceHandle

_PACKAGE_DIRNAME = "packages"

# How an AssetKind maps onto the stored ContentType. Briefs are campaign assets:
# they are work requests, not something that gets published on their own.
_KIND_TO_CONTENT_TYPE: dict[AssetKind, ContentType] = {
    AssetKind.LINKEDIN_POST: ContentType.SOCIAL_POST,
    AssetKind.X_POST: ContentType.SOCIAL_POST,
    AssetKind.X_THREAD: ContentType.SOCIAL_POST,
    AssetKind.FACEBOOK_POST: ContentType.SOCIAL_POST,
    AssetKind.INSTAGRAM_CAPTION: ContentType.SOCIAL_POST,
    AssetKind.TIKTOK_SCRIPT: ContentType.VIDEO_SCRIPT,
    AssetKind.YOUTUBE_SHORT_SCRIPT: ContentType.VIDEO_SCRIPT,
    AssetKind.BLOG_ARTICLE: ContentType.BLOG_ARTICLE,
    AssetKind.NEWSLETTER: ContentType.NEWSLETTER,
    AssetKind.PRODUCT_ANNOUNCEMENT: ContentType.PRODUCT_ANNOUNCEMENT,
    AssetKind.EDUCATIONAL_POST: ContentType.EDUCATIONAL_POST,
    AssetKind.LAUNCH_CAMPAIGN: ContentType.CAMPAIGN_ASSET,
    AssetKind.COMMUNITY_POST: ContentType.COMMUNITY_POST,
    AssetKind.PARTNER_OUTREACH: ContentType.PARTNER_OUTREACH,
    AssetKind.SEO_ARTICLE: ContentType.SEO_ARTICLE,
    AssetKind.CAROUSEL_BRIEF: ContentType.CAROUSEL,
    AssetKind.IMAGE_BRIEF: ContentType.CAMPAIGN_ASSET,
    AssetKind.VIDEO_BRIEF: ContentType.CAMPAIGN_ASSET,
}


@dataclass
class PackagePost:
    """One post in a package, linked to the ContentItem it created."""

    content_id: str
    asset_id: str
    platform: str
    kind: str
    campaign: str
    theme: str
    title: str
    caption: str
    cta: str
    destination_url: str
    scheduled_at: str
    goal: str
    media_refs: list[str] = field(default_factory=list)
    recommendation_ids: list[str] = field(default_factory=list)
    experiment_ids: list[str] = field(default_factory=list)
    variant_group_id: str = ""
    warnings: list[str] = field(default_factory=list)
    escalations: list[str] = field(default_factory=list)
    claim_risks: list[str] = field(default_factory=list)
    requires_enhanced_review: bool = False
    blocked: bool = False
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "content_id": self.content_id,
            "asset_id": self.asset_id,
            "platform": self.platform,
            "kind": self.kind,
            "campaign": self.campaign,
            "theme": self.theme,
            "title": self.title,
            "caption": self.caption,
            "cta": self.cta,
            "destination_url": self.destination_url,
            "scheduled_at": self.scheduled_at,
            "goal": self.goal,
            "media_refs": list(self.media_refs),
            "recommendation_ids": list(self.recommendation_ids),
            "experiment_ids": list(self.experiment_ids),
            "variant_group_id": self.variant_group_id,
            "warnings": list(self.warnings),
            "escalations": list(self.escalations),
            "claim_risks": list(self.claim_risks),
            "requires_enhanced_review": self.requires_enhanced_review,
            "blocked": self.blocked,
            "rationale": self.rationale,
        }


@dataclass
class WeeklyPackage:
    """One week's complete marketing plan."""

    id: str
    project: str
    week_start: str
    week_end: str
    objective: str
    audience: str
    theme: str
    reasoning_summary: str
    posts: list[PackagePost] = field(default_factory=list)
    supporting_recommendations: list[dict[str, Any]] = field(default_factory=list)
    proposed_experiments: list[dict[str, Any]] = field(default_factory=list)
    expected_outcomes: dict[str, Any] = field(default_factory=dict)
    estimated_effort: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    status: PackageStatus = PackageStatus.DRAFT
    history: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""

    @property
    def publishable_posts(self) -> list[PackagePost]:
        """Posts not blocked by a safety finding or an escalation."""
        return [p for p in self.posts if not p.blocked and not p.escalations]

    def apply_transition(self, status: PackageStatus, by: str, reason: str, at: datetime) -> None:
        """Move the package along its lifecycle. Raises on an illegal edge."""
        if not can_transition(self.status, status):
            raise InvalidPackageTransitionError(self.status.value, status.value)
        self.history.append(
            {
                "from_status": self.status.value,
                "to_status": status.value,
                "changed_by": by,
                "reason": reason,
                "at": _fmt(at),
            }
        )
        self.status = status

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project": self.project,
            "week_start": self.week_start,
            "week_end": self.week_end,
            "objective": self.objective,
            "audience": self.audience,
            "theme": self.theme,
            "reasoning_summary": self.reasoning_summary,
            "posts": [p.to_dict() for p in self.posts],
            "supporting_recommendations": list(self.supporting_recommendations),
            "proposed_experiments": list(self.proposed_experiments),
            "expected_outcomes": dict(self.expected_outcomes),
            "estimated_effort": dict(self.estimated_effort),
            "warnings": list(self.warnings),
            "status": self.status.value,
            "history": list(self.history),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WeeklyPackage:
        package = cls(
            id=str(data["id"]),
            project=str(data.get("project", "")),
            week_start=str(data.get("week_start", "")),
            week_end=str(data.get("week_end", "")),
            objective=str(data.get("objective", "")),
            audience=str(data.get("audience", "")),
            theme=str(data.get("theme", "")),
            reasoning_summary=str(data.get("reasoning_summary", "")),
            supporting_recommendations=list(data.get("supporting_recommendations") or []),
            proposed_experiments=list(data.get("proposed_experiments") or []),
            expected_outcomes=dict(data.get("expected_outcomes") or {}),
            estimated_effort=dict(data.get("estimated_effort") or {}),
            warnings=[str(w) for w in (data.get("warnings") or [])],
            status=PackageStatus(str(data.get("status", "draft"))),
            created_at=str(data.get("created_at", "")),
        )
        package.posts = [
            PackagePost(
                content_id=str(p.get("content_id", "")),
                asset_id=str(p.get("asset_id", "")),
                platform=str(p.get("platform", "")),
                kind=str(p.get("kind", "")),
                campaign=str(p.get("campaign", "")),
                theme=str(p.get("theme", "")),
                title=str(p.get("title", "")),
                caption=str(p.get("caption", "")),
                cta=str(p.get("cta", "")),
                destination_url=str(p.get("destination_url", "")),
                scheduled_at=str(p.get("scheduled_at", "")),
                goal=str(p.get("goal", "")),
                media_refs=[str(m) for m in (p.get("media_refs") or [])],
                recommendation_ids=[str(r) for r in (p.get("recommendation_ids") or [])],
                experiment_ids=[str(e) for e in (p.get("experiment_ids") or [])],
                variant_group_id=str(p.get("variant_group_id", "")),
                warnings=[str(w) for w in (p.get("warnings") or [])],
                escalations=[str(e) for e in (p.get("escalations") or [])],
                claim_risks=[str(c) for c in (p.get("claim_risks") or [])],
                requires_enhanced_review=bool(p.get("requires_enhanced_review", False)),
                blocked=bool(p.get("blocked", False)),
                rationale=str(p.get("rationale", "")),
            )
            for p in (data.get("posts") or [])
        ]
        package.history = list(data.get("history") or [])
        return package


class WeeklyPackageBuilder:
    """Assembles one week's package and writes its posts as real ContentItems."""

    def __init__(self, handle: WorkspaceHandle, writer: Copywriter | None = None) -> None:
        self._handle = handle
        self._copywriter = writer or Copywriter()

    def build(
        self,
        brand: BrandContext,
        plan: WeeklyPlan,
        recommendations: list[dict[str, Any]],
        experiments: list[dict[str, Any]],
        now: datetime,
        multi_platform: bool = False,
    ) -> WeeklyPackage:
        """
        Draft every planned slot and create its ContentItem.

        Every item enters as DRAFT. A blocked or escalated asset still becomes a
        ContentItem so the problem is visible in the queue rather than silently
        dropped — it simply cannot proceed to review until a human resolves it.
        """
        brand.validate()
        week_start = _parse(plan.week_start_iso())
        package = WeeklyPackage(
            id=package_id(brand.project, week_start),
            project=brand.project,
            week_start=plan.week_start_iso(),
            week_end=plan.week_end_iso(),
            objective=brand.objective,
            audience=brand.audience or (brand.personas[0] if brand.personas else ""),
            theme=plan.posts[0].theme if plan.posts else "",
            reasoning_summary=plan.rationale,
            supporting_recommendations=[
                {
                    "id": r["id"],
                    "title": r.get("title", ""),
                    "priority": r.get("priority", ""),
                    "falsifier": r.get("falsifier", ""),
                }
                for r in recommendations
                if str(r.get("id", "")) in plan.cited_recommendations
            ],
            proposed_experiments=[
                {
                    "id": e["id"],
                    "hypothesis": e.get("hypothesis", ""),
                    "metric": e.get("metric", ""),
                }
                for e in experiments
            ],
            warnings=list(plan.warnings),
            created_at=_fmt(now),
        )

        assets: list[GeneratedAsset] = []
        for planned in plan.posts:
            drafted = self._copywriter.draft(
                planned, brand, angle=planned.theme, destination_url=brand.website
            )
            targets = (
                make_variants(drafted, [planned.platform], brand)
                if not multi_platform
                else make_variants(drafted, plan.platforms, brand)
            )
            for variant in targets:
                assets.append(variant)
                package.posts.append(self._persist(variant, planned.scheduled_at, brand))

        package.estimated_effort = summarize_effort(assets)
        package.expected_outcomes = self._expected_outcomes(package, recommendations)
        if any(p.blocked for p in package.posts):
            package.warnings.append(
                f"{sum(1 for p in package.posts if p.blocked)} post(s) are blocked by a "
                "brand-safety finding and cannot reach review until edited."
            )
        if any(p.escalations for p in package.posts):
            package.warnings.append(
                f"{sum(1 for p in package.posts if p.escalations)} post(s) touch a "
                "sensitive category and require a human decision."
            )
        flagged = sum(1 for p in package.posts if p.requires_enhanced_review)
        if flagged:
            package.warnings.append(
                f"{flagged} post(s) contain claim-shaped statements and require ENHANCED "
                "review: a human must confirm each claim is true and correctly stated. "
                "The deterministic safety checks finding nothing is not a verification."
            )
        self.save(package)
        return package

    def save(self, package: WeeklyPackage) -> Path:
        """Persist a package inside its own workspace."""
        directory = self._handle.path / _PACKAGE_DIRNAME
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{package.id}.json"
        path.write_text(json.dumps(package.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        return path

    def load(self, package_ref: str) -> WeeklyPackage:
        """Read one package by id."""
        path = self._handle.path / _PACKAGE_DIRNAME / f"{package_ref}.json"
        if not path.exists():
            raise KeyError(f"No weekly package {package_ref!r} in {self._handle.slug!r}.")
        return WeeklyPackage.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_packages(self) -> list[WeeklyPackage]:
        """Every package in this workspace, oldest week first."""
        directory = self._handle.path / _PACKAGE_DIRNAME
        if not directory.exists():
            return []
        found: list[WeeklyPackage] = []
        for path in sorted(directory.glob("WEEK-*.json")):
            try:
                found.append(WeeklyPackage.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, OSError, KeyError, ValueError):
                continue
        return found

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _persist(
        self, asset: GeneratedAsset, scheduled_at: datetime, brand: BrandContext
    ) -> PackagePost:
        """Create the ContentItem for one asset. Always DRAFT, never further."""
        allowed, reasons = gate_for_review(asset, brand)
        needs_review, risks, review_summary = enhanced_review_for(asset)
        item = self._handle.create_content(
            platform=asset.platform,
            copy=asset.draft,
            cta=asset.cta,
            destination_url=asset.destination_url,
            campaign=asset.campaign,
            expected_goal=asset.goal,
            expected_audience=asset.audience,
            scheduled_at=scheduled_at,
            content_type=_KIND_TO_CONTENT_TYPE.get(asset.kind, ContentType.SOCIAL_POST),
            title=asset.title,
            themes=[asset.theme] if asset.theme else [],
            audience=asset.audience,
            variant_group_id=asset.variant_group_id,
            media=list(asset.media_refs),
            created_by="agent:growth-generation",
        )
        # Provenance and any safety problem live on the item, so a reviewer opening
        # it in the library sees why it exists and what is wrong with it.
        item.metadata.update(
            {
                "generated": True,
                "generation_asset_id": asset.id,
                "recommendation_ids": list(asset.recommendation_ids),
                "experiment_ids": list(asset.experiment_ids),
                "campaign_id": asset.campaign,
                "rationale": asset.rationale,
                "generation_method": asset.generation_method,
                "provider": asset.provider,
                "requires_enhanced_review": needs_review,
                "claim_risks": risks,
                "claim_review_summary": review_summary,
                "generated_at": _fmt(scheduled_at),
            }
        )
        item.warnings = reasons
        self._handle.save_content(item)

        return PackagePost(
            content_id=item.id,
            asset_id=asset.id,
            platform=asset.platform,
            kind=asset.kind.value,
            campaign=asset.campaign,
            theme=asset.theme,
            title=asset.title,
            caption=asset.draft,
            cta=asset.cta,
            destination_url=asset.destination_url,
            scheduled_at=_fmt(scheduled_at),
            goal=asset.goal,
            media_refs=list(asset.media_refs),
            recommendation_ids=list(asset.recommendation_ids),
            experiment_ids=list(asset.experiment_ids),
            variant_group_id=asset.variant_group_id,
            warnings=reasons,
            escalations=list(asset.escalations),
            claim_risks=risks,
            requires_enhanced_review=needs_review,
            blocked=not allowed,
            rationale=asset.rationale,
        )

    @staticmethod
    def _expected_outcomes(
        package: WeeklyPackage, recommendations: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        What the week is aiming at.

        Deliberately unquantified. The Brain does not produce effect sizes for its
        recommendations, so predicting a number here would invent one at exactly
        the point a human is most likely to believe it.
        """
        goals = sorted({p.goal for p in package.posts if p.goal})
        return {
            "conversion_goals": goals,
            "post_count": len(package.posts),
            "publishable_count": len(package.publishable_posts),
            "quantified": False,
            "reason": (
                "No effect size is predicted: the Brain produces no measured effect "
                "sizes for its recommendations, so any number here would be invented."
            ),
            "success_metrics": sorted(
                {
                    str(r.get("success_metric", ""))
                    for r in recommendations
                    if r.get("success_metric")
                }
            ),
        }


def build_plan(
    handle: WorkspaceHandle,
    week_start: datetime,
    cadence: int,
    recommendations: list[dict[str, Any]],
    campaigns: list[dict[str, Any]],
    platforms: list[str],
    experiments: list[dict[str, Any]] | None = None,
    pillars: list[str] | None = None,
) -> WeeklyPlan:
    """Convenience wrapper: plan a week for one workspace."""
    return ContentPlanner(handle.slug).plan_week(
        week_start=week_start,
        cadence=cadence,
        recommendations=recommendations,
        campaigns=campaigns,
        platforms=platforms,
        experiments=experiments,
        pillars=pillars,
    )


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.rstrip("Z")).replace(tzinfo=UTC)


def _fmt(value: datetime) -> str:
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.strftime("%Y-%m-%dT%H:%M:%SZ")


def week_bounds(value: datetime) -> tuple[datetime, datetime]:
    """The Monday 00:00 and Sunday 23:59 UTC around ``value``."""
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    monday = (aware - timedelta(days=aware.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday, monday + timedelta(days=6, hours=23, minutes=59)
