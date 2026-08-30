"""
Opportunity detection - explicit rules over measured numbers.

Each detector is a named rule with a stated threshold. Nothing is learned,
nothing is tuned at runtime, and the same workspace state always produces the
same opportunities in the same order.

Two constraints shape every rule here.

**Nothing is quantified without backing data.** An opportunity's ``expected_upside``
is ``None`` with a reason whenever the numbers to compute it do not exist. A
fabricated "+30% engagement" is worse than no estimate: it survives being copied
into a plan long after anyone remembers it was invented.

**A thin sample produces a hypothesis, not a claim.** Detectors record the sample
behind each finding, and the engine downgrades anything below the minimum into a
hypothesis rather than a recommendation. Small samples on opaque platform
distribution will fit any story.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from growth.brain.evidence import Evidence, MetricCitation, standing_assumptions
from growth.brain.models import deterministic_id

# Thresholds. Conventions, not discoveries - stated here so a reader can disagree
# with a number rather than reverse-engineer it from behaviour.
DECLINE_THRESHOLD = -0.20
SURGE_THRESHOLD = 0.30
OUTPERFORM_RATIO = 1.5
UNDERPERFORM_RATIO = 0.5
CAMPAIGN_AHEAD_RATIO = 1.15
CAMPAIGN_BEHIND_RATIO = 0.75

# A comparison needs at least this many observations on each side to be worth
# making at all.
MIN_COMPARISON_SAMPLE = 3


class OpportunityType(Enum):
    """The kinds of finding this engine can produce."""

    CONTENT_GAP = "content-gap"
    CHANNEL = "channel"
    SEO = "seo"
    CAMPAIGN = "campaign"
    REUSE = "reuse"
    CONVERSION = "conversion"
    AUDIENCE = "audience"
    PARTNERSHIP = "partnership"


class Severity(Enum):
    """How much attention a finding warrants."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class Opportunity:
    """One detected finding, with everything needed to judge and act on it."""

    id: str
    project: str
    type: OpportunityType
    severity: Severity
    title: str
    detail: str
    evidence: Evidence
    confidence: str = "low"
    expected_upside: float | None = None
    upside_unit: str = ""
    upside_reason: str = ""
    recommended_action: str = ""
    proposed_experiment: str = ""
    success_metric: str = ""
    sample_size: int = 0
    affected_campaigns: list[str] = field(default_factory=list)
    affected_platforms: list[str] = field(default_factory=list)
    rule: str = ""

    @property
    def synthetic(self) -> bool:
        return self.evidence.synthetic

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project": self.project,
            "type": self.type.value,
            "severity": self.severity.value,
            "title": self.title,
            "detail": self.detail,
            "confidence": self.confidence,
            "expected_upside": self.expected_upside,
            "upside_unit": self.upside_unit,
            "upside_reason": self.upside_reason,
            "recommended_action": self.recommended_action,
            "proposed_experiment": self.proposed_experiment,
            "success_metric": self.success_metric,
            "sample_size": self.sample_size,
            "affected_campaigns": list(self.affected_campaigns),
            "affected_platforms": list(self.affected_platforms),
            "rule": self.rule,
            "synthetic": self.synthetic,
            "evidence": self.evidence.to_dict(),
        }


def _confidence_for(sample: int, synthetic: bool) -> str:
    """Confidence from sample size, capped when the data is not real."""
    if synthetic:
        return "low"
    if sample >= 30:
        return "high"
    if sample >= 10:
        return "medium"
    return "low"


def _citations(metrics: dict[str, Any], names: list[str], scope: str) -> list[MetricCitation]:
    return [
        MetricCitation.from_metric(metrics[name], scope=scope)
        for name in names
        if isinstance(metrics.get(name), dict)
    ]


def _value(metrics: dict[str, Any], key: str) -> float | None:
    entry = metrics.get(key) or {}
    value = entry.get("value")
    return float(value) if isinstance(value, (int, float)) else None


def _sample(metrics: dict[str, Any]) -> int:
    return max(
        (int((m or {}).get("sample_size", 0)) for m in metrics.values() if isinstance(m, dict)),
        default=0,
    )


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------


def detect_engagement_trend(
    project: str, trend: dict[str, Any], metrics: dict[str, Any]
) -> list[Opportunity]:
    """Declining or unusually high engagement, from a period-over-period trend."""
    change = trend.get("percent_change")
    if not isinstance(change, (int, float)):
        return []
    sample = _sample(metrics)
    if sample < MIN_COMPARISON_SAMPLE:
        return []

    synthetic = bool(trend.get("synthetic"))
    fraction = float(change) / 100.0
    citations = _citations(metrics, ["engagement", "engagement_rate", "impressions"], "workspace")
    current, previous = trend.get("current"), trend.get("previous")

    if fraction <= DECLINE_THRESHOLD:
        return [
            Opportunity(
                id=deterministic_id("OPP", project, "engagement-decline"),
                project=project,
                type=OpportunityType.CONVERSION,
                severity=Severity.HIGH if fraction <= -0.4 else Severity.MEDIUM,
                title="Engagement is declining",
                detail=(
                    f"Engagement fell {abs(float(change)):.1f}% versus the previous period "
                    f"({previous} -> {current})."
                ),
                evidence=Evidence(
                    metrics=citations,
                    observations=[
                        f"engagement {previous} -> {current} "
                        f"({float(change):+.1f}%) period over period"
                    ],
                    assumptions=standing_assumptions(
                        ["The two periods are comparable in posting volume and mix."]
                    ),
                    falsifier=(
                        "Engagement returns to the previous level over the next period "
                        "without any change in approach."
                    ),
                    sample_size=sample,
                ),
                confidence=_confidence_for(sample, synthetic),
                expected_upside=(
                    round(float(previous) - float(current), 2)
                    if isinstance(previous, (int, float)) and isinstance(current, (int, float))
                    else None
                ),
                upside_unit="engagement actions recoverable",
                upside_reason=(
                    ""
                    if isinstance(previous, (int, float))
                    else "unknown: no previous-period baseline"
                ),
                recommended_action=(
                    "Compare the mix of formats and posting times between the two periods "
                    "before changing anything."
                ),
                proposed_experiment="Re-test the previous period's best-performing format.",
                success_metric="engagement_rate",
                sample_size=sample,
                rule="engagement-trend/decline",
            )
        ]

    if fraction >= SURGE_THRESHOLD:
        return [
            Opportunity(
                id=deterministic_id("OPP", project, "engagement-surge"),
                project=project,
                type=OpportunityType.AUDIENCE,
                severity=Severity.MEDIUM,
                title="Engagement is unusually high",
                detail=(
                    f"Engagement rose {float(change):.1f}% versus the previous period "
                    f"({previous} -> {current}). Worth identifying what changed."
                ),
                evidence=Evidence(
                    metrics=citations,
                    observations=[f"engagement {previous} -> {current} ({float(change):+.1f}%)"],
                    assumptions=standing_assumptions(
                        ["The rise is attributable to content rather than an external event."]
                    ),
                    falsifier=(
                        "The rise is explained by a one-off external event, or does not "
                        "reproduce when the same approach is repeated."
                    ),
                    sample_size=sample,
                ),
                confidence=_confidence_for(sample, synthetic),
                expected_upside=None,
                upside_reason=(
                    "unknown: a surge's repeatability cannot be estimated from one period"
                ),
                recommended_action="Identify what changed and test whether it reproduces.",
                proposed_experiment="Repeat the highest-engagement format from this period.",
                success_metric="engagement_rate",
                sample_size=sample,
                rule="engagement-trend/surge",
            )
        ]
    return []


def detect_campaign_performance(project: str, campaign: dict[str, Any]) -> list[Opportunity]:
    """Campaigns running ahead of or behind their stated objective."""
    progress = campaign.get("objective_progress", {}) or {}
    percent = progress.get("percent")
    if not isinstance(percent, (int, float)):
        return []

    metrics = campaign.get("metrics", {})
    sample = _sample(metrics)
    if sample < MIN_COMPARISON_SAMPLE:
        return []

    synthetic = bool(campaign.get("synthetic"))
    campaign_id = str(campaign.get("campaign_id", ""))
    citations = _citations(
        metrics, ["conversions", "conversion_rate", "clicks"], f"campaign:{campaign_id}"
    )
    ratio = float(percent) / 100.0
    confidence = _confidence_for(sample, synthetic)

    if ratio >= CAMPAIGN_AHEAD_RATIO:
        return [
            Opportunity(
                id=deterministic_id("OPP", project, "campaign-ahead", campaign_id),
                severity=Severity.LOW,
                title=f"{campaign.get('campaign_name', campaign_id)} is exceeding its objective",
                detail=(
                    f"At {float(percent):.0f}% of its stated target "
                    f"({progress.get('achieved')} of {progress.get('target')})."
                ),
                evidence=Evidence(
                    metrics=citations,
                    observations=[f"objective progress {float(percent):.0f}%"],
                    campaigns=[campaign_id],
                    assumptions=standing_assumptions(
                        ["The objective's stated number is the real target."]
                    ),
                    falsifier=(
                        "The conversions counted are not the goal the campaign was "
                        "actually judged on."
                    ),
                    sample_size=sample,
                ),
                expected_upside=None,
                upside_reason=(
                    "unknown: additional upside from extending a campaign cannot be "
                    "projected from progress alone"
                ),
                recommended_action=(
                    "Consider extending or raising the target, and record what worked "
                    "as marketing memory."
                ),
                proposed_experiment="Extend the campaign window and watch the run rate.",
                rule="campaign/ahead",
                project=project,
                type=OpportunityType.CAMPAIGN,
                sample_size=sample,
                affected_campaigns=[campaign_id],
                confidence=confidence,
                success_metric="conversions",
            )
        ]

    if ratio <= CAMPAIGN_BEHIND_RATIO:
        return [
            Opportunity(
                id=deterministic_id("OPP", project, "campaign-behind", campaign_id),
                severity=Severity.HIGH if ratio <= 0.4 else Severity.MEDIUM,
                title=f"{campaign.get('campaign_name', campaign_id)} is behind its objective",
                detail=(
                    f"At {float(percent):.0f}% of its stated target "
                    f"({progress.get('achieved')} of {progress.get('target')})."
                ),
                evidence=Evidence(
                    metrics=citations,
                    observations=[f"objective progress {float(percent):.0f}%"],
                    campaigns=[campaign_id],
                    assumptions=standing_assumptions(
                        ["The campaign window is far enough along for progress to be meaningful."]
                    ),
                    falsifier=(
                        "The campaign is early in its window, or conversions are being "
                        "recorded under a different name than the stated goal."
                    ),
                    sample_size=sample,
                ),
                expected_upside=(
                    round(float(progress.get("target", 0)) - float(progress.get("achieved", 0)), 2)
                    if isinstance(progress.get("target"), (int, float))
                    else None
                ),
                upside_unit="conversions to close the gap",
                recommended_action=(
                    "Review whether the conversion path is working before adding volume."
                ),
                proposed_experiment="Test a different CTA on the highest-traffic content.",
                rule="campaign/behind",
                project=project,
                type=OpportunityType.CAMPAIGN,
                sample_size=sample,
                affected_campaigns=[campaign_id],
                confidence=confidence,
                success_metric="conversions",
            )
        ]
    return []


def detect_platform_performance(project: str, platforms: list[dict[str, Any]]) -> list[Opportunity]:
    """
    Platforms materially out- or under-performing the others.

    Needs at least two platforms with data: "best platform" out of one is not a
    finding, it is a tautology.
    """
    measured = [
        p
        for p in platforms
        if _value(p.get("metrics", {}), "conversion_rate") is not None
        and _sample(p.get("metrics", {})) >= MIN_COMPARISON_SAMPLE
    ]
    if len(measured) < 2:
        return []

    rates = {
        str(p["platform"]): float(_value(p.get("metrics", {}), "conversion_rate") or 0.0)
        for p in measured
    }
    average = sum(rates.values()) / len(rates)
    if average <= 0:
        return []

    found: list[Opportunity] = []
    for row in sorted(measured, key=lambda r: str(r["platform"])):
        platform = str(row["platform"])
        metrics = row.get("metrics", {})
        sample = _sample(metrics)
        synthetic = bool(row.get("synthetic"))
        ratio = rates[platform] / average
        citations = _citations(
            metrics, ["conversion_rate", "engagement_rate", "ctr"], f"platform:{platform}"
        )

        if ratio >= OUTPERFORM_RATIO:
            found.append(
                Opportunity(
                    id=deterministic_id("OPP", project, "platform-outperform", platform),
                    project=project,
                    type=OpportunityType.CHANNEL,
                    severity=Severity.MEDIUM,
                    title=f"{platform} is converting well above the others",
                    detail=(
                        f"{platform} converts at {rates[platform] * 100:.2f}% against a "
                        f"{average * 100:.2f}% average across {len(rates)} platforms."
                    ),
                    evidence=Evidence(
                        metrics=citations,
                        observations=[
                            f"{platform} conversion_rate {rates[platform]:.4f} "
                            f"vs {average:.4f} average"
                        ],
                        platforms=[platform],
                        assumptions=standing_assumptions(
                            ["Platforms are compared on the same conversion definition."]
                        ),
                        falsifier=(
                            f"{platform}'s advantage disappears once posting volume is "
                            "equalised across platforms."
                        ),
                        sample_size=sample,
                    ),
                    confidence=_confidence_for(sample, synthetic),
                    expected_upside=None,
                    upside_reason=("unknown: reallocating effort has no measured effect size yet"),
                    recommended_action=f"Shift a share of posting effort toward {platform}.",
                    proposed_experiment=(
                        f"Publish the same idea on {platform} and one weaker platform, "
                        "then compare conversion."
                    ),
                    success_metric="conversion_rate",
                    sample_size=sample,
                    affected_platforms=[platform],
                    rule="platform/outperform",
                )
            )
        elif ratio <= UNDERPERFORM_RATIO:
            found.append(
                Opportunity(
                    id=deterministic_id("OPP", project, "platform-underperform", platform),
                    project=project,
                    type=OpportunityType.CHANNEL,
                    severity=Severity.MEDIUM,
                    title=f"{platform} is converting well below the others",
                    detail=(
                        f"{platform} converts at {rates[platform] * 100:.2f}% against a "
                        f"{average * 100:.2f}% average."
                    ),
                    evidence=Evidence(
                        metrics=citations,
                        observations=[
                            f"{platform} conversion_rate {rates[platform]:.4f} "
                            f"vs {average:.4f} average"
                        ],
                        platforms=[platform],
                        assumptions=standing_assumptions(
                            ["The conversion path is equivalent on every platform compared."]
                        ),
                        falsifier=(
                            f"{platform} serves a different funnel stage, so a lower "
                            "conversion rate is expected rather than a problem."
                        ),
                        sample_size=sample,
                    ),
                    confidence=_confidence_for(sample, synthetic),
                    expected_upside=None,
                    upside_reason="unknown: no measured effect size for a fix",
                    recommended_action=(
                        f"Check whether {platform}'s destination and CTA match the platform's "
                        "conventions before reducing investment."
                    ),
                    proposed_experiment=f"Test a platform-native CTA on {platform}.",
                    success_metric="conversion_rate",
                    sample_size=sample,
                    affected_platforms=[platform],
                    rule="platform/underperform",
                )
            )
    return found


def detect_theme_performance(
    project: str, theme_stats: dict[str, dict[str, Any]]
) -> list[Opportunity]:
    """
    Themes performing unusually well or poorly, by mean conversions per item.

    Needs at least two themes with enough measured content to compare; one theme
    has nothing to be unusual against.
    """
    eligible = {
        theme: stats
        for theme, stats in theme_stats.items()
        if stats.get("measured_items", 0) >= MIN_COMPARISON_SAMPLE
    }
    if len(eligible) < 2:
        return []

    means = {t: float(s.get("mean_conversions", 0.0)) for t, s in eligible.items()}
    average = sum(means.values()) / len(means)
    if average <= 0:
        return []

    found: list[Opportunity] = []
    for theme in sorted(eligible):
        stats = eligible[theme]
        sample = int(stats.get("measured_items", 0))
        synthetic = bool(stats.get("synthetic"))
        ratio = means[theme] / average
        evidence = Evidence(
            metrics=[],
            observations=[
                f"theme {theme!r}: {means[theme]:.2f} mean conversions per item "
                f"across {sample} measured items, against a {average:.2f} average"
            ],
            content_ids=list(stats.get("content_ids", []))[:10],
            assumptions=standing_assumptions(
                ["Themes are compared on content published under comparable conditions."]
            ),
            falsifier=(
                f"The advantage for {theme!r} disappears when items are matched on "
                "platform and posting time."
            ),
            sample_size=sample,
        )
        if ratio >= OUTPERFORM_RATIO:
            found.append(
                Opportunity(
                    id=deterministic_id("OPP", project, "theme-strong", theme),
                    project=project,
                    type=OpportunityType.CONTENT_GAP,
                    severity=Severity.MEDIUM,
                    title=f"Theme {theme!r} is outperforming",
                    detail=(
                        f"{means[theme]:.2f} mean conversions per item against a "
                        f"{average:.2f} average across {len(means)} themes."
                    ),
                    evidence=evidence,
                    confidence=_confidence_for(sample, synthetic),
                    expected_upside=None,
                    upside_reason=(
                        "unknown: additional volume on a theme has no measured effect size"
                    ),
                    recommended_action=f"Produce more content on {theme!r}.",
                    proposed_experiment=(f"Publish matched pairs on {theme!r} and a weaker theme."),
                    success_metric="conversions",
                    sample_size=sample,
                    rule="theme/strong",
                )
            )
        elif ratio <= UNDERPERFORM_RATIO:
            found.append(
                Opportunity(
                    id=deterministic_id("OPP", project, "theme-weak", theme),
                    project=project,
                    type=OpportunityType.CONTENT_GAP,
                    severity=Severity.LOW,
                    title=f"Theme {theme!r} is underperforming",
                    detail=(
                        f"{means[theme]:.2f} mean conversions per item against a "
                        f"{average:.2f} average."
                    ),
                    evidence=evidence,
                    confidence=_confidence_for(sample, synthetic),
                    expected_upside=None,
                    upside_reason="unknown: no measured effect size for a fix",
                    recommended_action=(
                        f"Reduce investment in {theme!r} or change how it is framed."
                    ),
                    proposed_experiment=f"Reframe {theme!r} around a concrete customer outcome.",
                    success_metric="conversions",
                    sample_size=sample,
                    rule="theme/weak",
                )
            )
    return found


def detect_reuse_opportunity(project: str, reusable: list[dict[str, Any]]) -> list[Opportunity]:
    """Evergreen content sitting unused."""
    if len(reusable) < MIN_COMPARISON_SAMPLE:
        return []
    ids = [str(r.get("content_id", "")) for r in reusable]
    return [
        Opportunity(
            id=deterministic_id("OPP", project, "reuse-backlog"),
            project=project,
            type=OpportunityType.REUSE,
            severity=Severity.LOW,
            title=f"{len(reusable)} evergreen items have not been reused",
            detail=(
                f"{len(reusable)} items are flagged reusable and have no recent reuse recorded."
            ),
            evidence=Evidence(
                metrics=[],
                observations=[f"{len(reusable)} reusable items with no recent reuse"],
                content_ids=ids[:10],
                assumptions=standing_assumptions(
                    ["Reuse eligibility was set deliberately rather than by default."]
                ),
                falsifier=(
                    "The items were marked reusable in bulk without review, so the "
                    "backlog is a labelling artifact rather than an opportunity."
                ),
                sample_size=len(reusable),
            ),
            confidence=_confidence_for(len(reusable), False),
            expected_upside=None,
            upside_reason="unknown: reuse has no measured effect size in this project yet",
            recommended_action="Schedule the strongest evergreen items into the next cycle.",
            proposed_experiment="Republish one evergreen item and compare it to its original run.",
            success_metric="engagement_rate",
            sample_size=len(reusable),
            rule="reuse/backlog",
        )
    ]


def detect_conversion_gap(
    project: str, funnel: dict[str, Any], metrics: dict[str, Any]
) -> list[Opportunity]:
    """
    The weakest step in the funnel, when there is enough of a funnel to judge.

    Reports the step with the worst measured drop-off rather than asserting a
    cause; why a step leaks is a hypothesis for an experiment, not a finding.
    """
    stages = [
        s for s in funnel.get("stages", []) if isinstance(s.get("rate_from_previous"), (int, float))
    ]
    if len(stages) < 2:
        return []

    worst = min(stages, key=lambda s: float(s["rate_from_previous"]))
    sample = _sample(metrics)
    if sample < MIN_COMPARISON_SAMPLE:
        return []

    synthetic = bool(funnel.get("synthetic"))
    rate = float(worst["rate_from_previous"])
    return [
        Opportunity(
            id=deterministic_id("OPP", project, "conversion-gap", str(worst["stage"])),
            project=project,
            type=OpportunityType.CONVERSION,
            severity=Severity.HIGH if rate < 0.05 else Severity.MEDIUM,
            title=f"Steepest funnel drop-off before {worst['stage']}",
            detail=(
                f"Only {rate * 100:.1f}% of the previous stage reaches "
                f"{worst['stage']} ({worst['count']:g})."
            ),
            evidence=Evidence(
                metrics=_citations(metrics, ["clicks", "conversions", "conversion_rate"], "funnel"),
                observations=[
                    f"{worst['stage']} converts at {rate * 100:.1f}% from the previous stage"
                ],
                assumptions=standing_assumptions(
                    [
                        "Funnel stages are recorded consistently; a missing stage would "
                        "show as a drop-off that is really an instrumentation gap."
                    ]
                ),
                falsifier=(
                    f"The {worst['stage']} stage is under-instrumented, so the drop is a "
                    "measurement artifact rather than a real loss."
                ),
                sample_size=sample,
            ),
            confidence=_confidence_for(sample, synthetic),
            expected_upside=None,
            upside_reason=("unknown: recovering a funnel step has no measured effect size here"),
            recommended_action=(
                f"Verify {worst['stage']} is instrumented correctly before treating this "
                "as a conversion problem."
            ),
            proposed_experiment=f"Test one change to the step feeding {worst['stage']}.",
            success_metric="conversion_rate",
            sample_size=sample,
            rule="conversion/weakest-step",
        )
    ]
