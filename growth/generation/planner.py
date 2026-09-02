"""
The content planner - turning Brain recommendations into a week's worth of slots.

The planner decides how many posts, on which platforms, for which campaigns, in
what mix, at what cadence, and how much of the week goes to experiments. It
decides nothing about *what is worth saying* - that judgement already happened in
the Brain, and the planner's job is to allocate against it.

Every slot cites the recommendations that produced it. A slot with no citation is
a slot nobody can later explain, so the planner does not create one: if the Brain
produced no recommendations, the plan falls back to the project's own content
pillars and campaigns and says so in the rationale.

Planning is deterministic. The week's shape is a function of the recommendations,
the campaigns, the configured cadence and the week start - never of a clock, and
never of a random draw. The same inputs always produce the same plan, which is
what makes a week reviewable against the last one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from growth.generation.models import AssetKind, PlannedPost

# Default posting hour, UTC. Mid-morning is a convention, not a finding; the
# Brain will propose a posting-time experiment if the data ever says otherwise.
DEFAULT_HOUR_UTC = 9

# How the week's slots are distributed across asset kinds when nothing more
# specific applies. Ordered, and cycled through - so a four-post week gets a mix
# rather than four of the same thing.
DEFAULT_MIX: tuple[AssetKind, ...] = (
    AssetKind.LINKEDIN_POST,
    AssetKind.EDUCATIONAL_POST,
    AssetKind.CAROUSEL_BRIEF,
    AssetKind.X_POST,
    AssetKind.COMMUNITY_POST,
    AssetKind.NEWSLETTER,
    AssetKind.BLOG_ARTICLE,
)

# Fraction of a week's slots reserved for testing a Brain-proposed experiment.
EXPERIMENT_ALLOCATION = 0.25

# Days of the week used before a cadence exceeds them, in posting order.
POSTING_DAYS: tuple[int, ...] = (0, 2, 4, 1, 3, 5, 6)


@dataclass
class WeeklyPlan:
    """The shape of one week, before anything is written."""

    project: str
    week_start: datetime
    week_end: datetime
    posts: list[PlannedPost] = field(default_factory=list)
    cadence: int = 0
    platforms: list[str] = field(default_factory=list)
    campaigns: list[str] = field(default_factory=list)
    cited_recommendations: list[str] = field(default_factory=list)
    experiment_slots: int = 0
    rationale: str = ""
    warnings: list[str] = field(default_factory=list)

    def week_start_iso(self) -> str:
        """The week's Monday as a stable UTC string."""
        return _fmt(self.week_start)

    def week_end_iso(self) -> str:
        """The week's Sunday as a stable UTC string."""
        return _fmt(self.week_end)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "week_start": _fmt(self.week_start),
            "week_end": _fmt(self.week_end),
            "posts": [p.to_dict() for p in self.posts],
            "cadence": self.cadence,
            "platforms": list(self.platforms),
            "campaigns": list(self.campaigns),
            "cited_recommendations": list(self.cited_recommendations),
            "experiment_slots": self.experiment_slots,
            "rationale": self.rationale,
            "warnings": list(self.warnings),
        }


class ContentPlanner:
    """Allocates a week of slots against the Brain's conclusions."""

    def __init__(self, project: str) -> None:
        self._project = project

    def plan_week(
        self,
        week_start: datetime,
        cadence: int,
        recommendations: list[dict[str, Any]],
        campaigns: list[dict[str, Any]],
        platforms: list[str],
        experiments: list[dict[str, Any]] | None = None,
        pillars: list[str] | None = None,
    ) -> WeeklyPlan:
        """
        Build one Monday-Sunday plan.

        ``recommendations`` and ``experiments`` come from the Brain; ``campaigns``
        and ``platforms`` from the workspace. Nothing is invented: if there are no
        recommendations and no open campaigns, the plan is empty and says why.
        """
        monday = _monday_of(week_start)
        plan = WeeklyPlan(
            project=self._project,
            week_start=monday,
            week_end=monday + timedelta(days=6, hours=23, minutes=59),
            cadence=max(0, cadence),
            platforms=sorted(set(platforms)),
        )

        open_campaigns = [c for c in campaigns if c.get("accepts_content", True)]
        plan.campaigns = sorted({str(c["id"]) for c in open_campaigns})

        if plan.cadence == 0:
            plan.warnings.append(
                "Cadence is zero: the project has not stated how often it wants to post. "
                "Complete growth onboarding to set one."
            )
            plan.rationale = "No plan produced: posting cadence is not configured."
            return plan
        if not plan.platforms:
            plan.warnings.append(
                "No platforms selected. Record platform intents during onboarding."
            )
            plan.rationale = "No plan produced: no platforms are selected."
            return plan
        if not open_campaigns:
            plan.warnings.append(
                "No campaign is open to accept content. Create or activate a campaign."
            )
            plan.rationale = "No plan produced: every campaign is completed or cancelled."
            return plan

        ranked = self._rank_recommendations(recommendations)
        plan.cited_recommendations = [str(r["id"]) for r in ranked]
        plan.experiment_slots = int(plan.cadence * EXPERIMENT_ALLOCATION) if experiments else 0

        available_pillars = list(pillars or []) or ["general"]
        experiment_ids = [str(e["id"]) for e in (experiments or [])]

        for index in range(plan.cadence):
            campaign = open_campaigns[index % len(open_campaigns)]
            platform = plan.platforms[index % len(plan.platforms)]
            kind = DEFAULT_MIX[index % len(DEFAULT_MIX)]
            day_offset = POSTING_DAYS[index % len(POSTING_DAYS)]
            recommendation = ranked[index % len(ranked)] if ranked else None

            is_experiment_slot = index < plan.experiment_slots
            theme = (
                str(campaign.get("theme") or "")
                or available_pillars[index % len(available_pillars)]
            )
            plan.posts.append(
                PlannedPost(
                    slot=index + 1,
                    platform=platform,
                    kind=kind,
                    campaign=str(campaign["id"]),
                    theme=theme,
                    scheduled_at=monday + timedelta(days=day_offset, hours=DEFAULT_HOUR_UTC),
                    goal=str(campaign.get("primary_conversion_goal") or ""),
                    cta=str(campaign.get("cta") or ""),
                    recommendation_ids=([str(recommendation["id"])] if recommendation else []),
                    experiment_ids=(
                        [experiment_ids[index % len(experiment_ids)]]
                        if is_experiment_slot and experiment_ids
                        else []
                    ),
                    rationale=self._slot_rationale(recommendation, campaign, is_experiment_slot),
                )
            )

        plan.rationale = self._plan_rationale(plan, ranked, experiments or [])
        if not ranked:
            plan.warnings.append(
                "The Growth Brain produced no recommendations for this project, so the "
                "plan is allocated against open campaigns and content pillars rather "
                "than against measured findings."
            )
        return plan

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _rank_recommendations(recommendations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Highest priority first, then by id so ordering is stable.

        Only PROPOSED and ACCEPTED recommendations are planned against. A rejected
        or invalidated one has been ruled out, and planning content for it would
        quietly reverse a human's decision.
        """
        live = [
            r
            for r in recommendations
            if str(r.get("status", "proposed")) in ("proposed", "accepted")
        ]
        return sorted(live, key=lambda r: (str(r.get("priority", "P3")), str(r.get("id", ""))))

    @staticmethod
    def _slot_rationale(
        recommendation: dict[str, Any] | None, campaign: dict[str, Any], experiment: bool
    ) -> str:
        parts: list[str] = []
        if recommendation:
            parts.append(
                f"Addresses {recommendation['id']} "
                f"({recommendation.get('priority', 'P3')}): {recommendation.get('title', '')}."
            )
        else:
            parts.append(
                f"Allocated against open campaign {campaign['id']} "
                f"({campaign.get('name', '')}); no Brain recommendation applied."
            )
        if experiment:
            parts.append("Reserved as an experiment slot.")
        return " ".join(parts)

    @staticmethod
    def _plan_rationale(
        plan: WeeklyPlan, ranked: list[dict[str, Any]], experiments: list[dict[str, Any]]
    ) -> str:
        basis = (
            f"{len(ranked)} Brain recommendation(s)"
            if ranked
            else "open campaigns and content pillars (no Brain recommendations available)"
        )
        return (
            f"{plan.cadence} post(s) across {len(plan.platforms)} platform(s) and "
            f"{len(plan.campaigns)} campaign(s), allocated against {basis}. "
            f"{plan.experiment_slots} slot(s) reserved for testing "
            f"{len(experiments)} proposed experiment(s)."
        )


def _monday_of(value: datetime) -> datetime:
    """The Monday 00:00 UTC of the week containing ``value``."""
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    monday = aware - timedelta(days=aware.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def _fmt(value: datetime) -> str:
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.strftime("%Y-%m-%dT%H:%M:%SZ")
