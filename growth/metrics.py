"""
Metric formulas - one module, pure functions, formula in the docstring.

Every function here is a deterministic function of the events handed to it. No
clock, no filesystem, no randomness: the same events always produce the same
number, which is what makes a metric checkable and what lets the Growth Brain
treat this layer as ground truth.

Two rules run through the whole module.

**A rate with no denominator is not zero.** Every rate returns a
:class:`MetricValue` whose ``value`` is ``None`` and whose ``reason`` says why.
Zero is a measurement - it means "we tried and nothing happened" - and reporting
it for "we have no data" would let a project look like it is failing when it has
simply not been measured, or let the Growth Brain draw a conclusion from an
absence.

**A metric is only as verified as its worst input.** If any contributing event is
synthetic or imported, ``synthetic`` is True on the result and stays True all the
way out to the CLI. There is no code path that launders an unverified event into
a metric that looks platform-measured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from growth.events import (
    CONVERSION_TYPES,
    ENGAGEMENT_TYPES,
    LEAD_CONVERSION_NAME,
    EventType,
    PerformanceEvent,
    contains_unverified,
    sources_of,
)


@dataclass
class MetricValue:
    """
    One computed metric, with everything needed to judge how much to trust it.

    ``value`` is None when the metric is undefined - an empty denominator, or no
    observations at all - and ``reason`` explains which. A caller that wants a
    number for arithmetic should use ``or_zero()`` and accept that it is choosing
    to treat "unknown" as "none observed".
    """

    name: str
    value: float | None
    unit: str = "count"
    synthetic: bool = False
    sample_size: int = 0
    reason: str = ""
    formula: str = ""
    sources: list[str] = field(default_factory=list)

    @property
    def is_defined(self) -> bool:
        return self.value is not None

    def or_zero(self) -> float:
        """The value, treating undefined as 0.0. Use deliberately."""
        return self.value if self.value is not None else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "synthetic": self.synthetic,
            "sample_size": self.sample_size,
            "reason": self.reason,
            "formula": self.formula,
            "sources": list(self.sources),
        }


def _provenance(events: list[PerformanceEvent]) -> tuple[bool, list[str]]:
    """(is_synthetic, sorted source names) for a set of events."""
    return contains_unverified(events), sorted(s.value for s in sources_of(events))


def _sum(events: list[PerformanceEvent], types: frozenset[EventType] | EventType) -> float:
    wanted = {types} if isinstance(types, EventType) else types
    return float(sum(e.value for e in events if e.event_type in wanted))


def _count_metric(
    name: str,
    events: list[PerformanceEvent],
    types: frozenset[EventType] | EventType,
    formula: str,
) -> MetricValue:
    """Build a count metric from a subset of events."""
    wanted = {types} if isinstance(types, EventType) else types
    contributing = [e for e in events if e.event_type in wanted]
    synthetic, sources = _provenance(contributing)
    return MetricValue(
        name=name,
        value=float(sum(e.value for e in contributing)),
        unit="count",
        synthetic=synthetic,
        sample_size=len(contributing),
        formula=formula,
        sources=sources,
    )


def _rate_metric(
    name: str,
    numerator: float,
    denominator: float,
    contributing: list[PerformanceEvent],
    formula: str,
    denominator_label: str,
) -> MetricValue:
    """Build a rate, refusing to invent a value when the denominator is empty."""
    synthetic, sources = _provenance(contributing)
    if denominator <= 0:
        return MetricValue(
            name=name,
            value=None,
            unit="ratio",
            synthetic=synthetic,
            sample_size=len(contributing),
            reason=f"undefined: no {denominator_label} recorded",
            formula=formula,
            sources=sources,
        )
    return MetricValue(
        name=name,
        value=numerator / denominator,
        unit="ratio",
        synthetic=synthetic,
        sample_size=len(contributing),
        formula=formula,
        sources=sources,
    )


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------


def impressions(events: list[PerformanceEvent]) -> MetricValue:
    """impressions = sum(value of every impression event)"""
    return _count_metric("impressions", events, EventType.IMPRESSION, "sum(impression.value)")


def reach(events: list[PerformanceEvent]) -> MetricValue:
    """reach = sum(value of every reach event)"""
    return _count_metric("reach", events, EventType.REACH, "sum(reach.value)")


def engagement(events: list[PerformanceEvent]) -> MetricValue:
    """engagement = sum(engagement + reaction + comment + share)"""
    return _count_metric(
        "engagement",
        events,
        ENGAGEMENT_TYPES,
        "sum(engagement + reaction + comment + share)",
    )


def clicks(events: list[PerformanceEvent]) -> MetricValue:
    """clicks = sum(value of every click event)"""
    return _count_metric("clicks", events, EventType.CLICK, "sum(click.value)")


def website_visits(events: list[PerformanceEvent]) -> MetricValue:
    """website_visits = sum(value of every website_visit event)"""
    return _count_metric(
        "website_visits", events, EventType.WEBSITE_VISIT, "sum(website_visit.value)"
    )


def registrations(events: list[PerformanceEvent]) -> MetricValue:
    """registrations = sum(value of every signup event)"""
    return _count_metric("registrations", events, EventType.SIGNUP, "sum(signup.value)")


def purchases(events: list[PerformanceEvent]) -> MetricValue:
    """purchases = sum(value of every purchase event)"""
    return _count_metric("purchases", events, EventType.PURCHASE, "sum(purchase.value)")


def conversions(events: list[PerformanceEvent]) -> MetricValue:
    """conversions = sum(signup + purchase + custom_conversion)"""
    return _count_metric(
        "conversions",
        events,
        CONVERSION_TYPES,
        "sum(signup + purchase + custom_conversion)",
    )


def leads(events: list[PerformanceEvent]) -> MetricValue:
    """
    leads = sum(custom_conversion events named "lead")

    A lead is a custom conversion rather than its own event type because what
    counts as one differs per project, and hard-coding a definition here would
    make the number incomparable between projects that mean different things.
    """
    contributing = [
        e
        for e in events
        if e.event_type is EventType.CUSTOM_CONVERSION
        and e.name.strip().lower() == LEAD_CONVERSION_NAME
    ]
    synthetic, sources = _provenance(contributing)
    return MetricValue(
        name="leads",
        value=float(sum(e.value for e in contributing)),
        unit="count",
        synthetic=synthetic,
        sample_size=len(contributing),
        formula='sum(custom_conversion where name == "lead")',
        sources=sources,
    )


def custom_conversions(events: list[PerformanceEvent], name: str) -> MetricValue:
    """custom_conversions(name) = sum(custom_conversion events with that name)"""
    target = name.strip().lower()
    contributing = [
        e
        for e in events
        if e.event_type is EventType.CUSTOM_CONVERSION and e.name.strip().lower() == target
    ]
    synthetic, sources = _provenance(contributing)
    return MetricValue(
        name=f"custom_conversion:{target}",
        value=float(sum(e.value for e in contributing)),
        unit="count",
        synthetic=synthetic,
        sample_size=len(contributing),
        formula=f'sum(custom_conversion where name == "{target}")',
        sources=sources,
    )


# ---------------------------------------------------------------------------
# Rates
# ---------------------------------------------------------------------------


def engagement_rate(events: list[PerformanceEvent]) -> MetricValue:
    """engagement_rate = engagement / impressions"""
    contributing = [
        e
        for e in events
        if e.event_type in ENGAGEMENT_TYPES or e.event_type is EventType.IMPRESSION
    ]
    return _rate_metric(
        "engagement_rate",
        _sum(events, ENGAGEMENT_TYPES),
        _sum(events, EventType.IMPRESSION),
        contributing,
        "engagement / impressions",
        "impressions",
    )


def ctr(events: list[PerformanceEvent]) -> MetricValue:
    """ctr = clicks / impressions"""
    contributing = [e for e in events if e.event_type in (EventType.CLICK, EventType.IMPRESSION)]
    return _rate_metric(
        "ctr",
        _sum(events, EventType.CLICK),
        _sum(events, EventType.IMPRESSION),
        contributing,
        "clicks / impressions",
        "impressions",
    )


def conversion_rate(events: list[PerformanceEvent]) -> MetricValue:
    """conversion_rate = conversions / clicks"""
    contributing = [
        e for e in events if e.event_type in CONVERSION_TYPES or e.event_type is EventType.CLICK
    ]
    return _rate_metric(
        "conversion_rate",
        _sum(events, CONVERSION_TYPES),
        _sum(events, EventType.CLICK),
        contributing,
        "conversions / clicks",
        "clicks",
    )


def visit_conversion_rate(events: list[PerformanceEvent]) -> MetricValue:
    """visit_conversion_rate = conversions / website_visits"""
    contributing = [
        e
        for e in events
        if e.event_type in CONVERSION_TYPES or e.event_type is EventType.WEBSITE_VISIT
    ]
    return _rate_metric(
        "visit_conversion_rate",
        _sum(events, CONVERSION_TYPES),
        _sum(events, EventType.WEBSITE_VISIT),
        contributing,
        "conversions / website_visits",
        "website visits",
    )


def audience_growth(
    earlier_followers: float | None, later_followers: float | None, synthetic: bool = False
) -> MetricValue:
    """
    audience_growth = later_followers - earlier_followers

    Follower count is a gauge a platform reports, not something that arrives as a
    stream of events, so growth is a difference between two snapshots rather than
    a sum over a period. With fewer than two snapshots it is undefined - and
    saying so is more useful than reporting 0 for a project nobody has measured.
    """
    if earlier_followers is None or later_followers is None:
        return MetricValue(
            name="audience_growth",
            value=None,
            unit="count",
            synthetic=synthetic,
            reason="undefined: needs two snapshots carrying a follower count",
            formula="later_followers - earlier_followers",
        )
    return MetricValue(
        name="audience_growth",
        value=float(later_followers - earlier_followers),
        unit="count",
        synthetic=synthetic,
        sample_size=2,
        formula="later_followers - earlier_followers",
    )


# ---------------------------------------------------------------------------
# Operational rates, derived from lifecycle records rather than events
# ---------------------------------------------------------------------------


def approval_rate(reviewed: int, approved: int) -> MetricValue:
    """
    approval_rate = approved items / items that reached review

    Both inputs are counted from each item's own status history, never from a
    separate counter. A counter would be a second source of truth that could
    drift from the audit trail; the history cannot, because it *is* the record.
    """
    if reviewed <= 0:
        return MetricValue(
            name="approval_rate",
            value=None,
            unit="ratio",
            reason="undefined: no content has reached review",
            formula="approved / reviewed",
        )
    return MetricValue(
        name="approval_rate",
        value=approved / reviewed,
        unit="ratio",
        sample_size=reviewed,
        formula="approved / reviewed",
    )


def publishing_success_rate(published: int, failed: int) -> MetricValue:
    """
    publishing_success_rate = published / (published + failed)

    Derived from publication records on the content items themselves, so it
    agrees with the audit trail by construction.
    """
    attempted = published + failed
    if attempted <= 0:
        return MetricValue(
            name="publishing_success_rate",
            value=None,
            unit="ratio",
            reason="undefined: nothing has been published or failed yet",
            formula="published / (published + failed)",
        )
    return MetricValue(
        name="publishing_success_rate",
        value=published / attempted,
        unit="ratio",
        sample_size=attempted,
        formula="published / (published + failed)",
    )


# Every event-derived metric, so callers and tests can iterate the real set
# rather than restating it.
EVENT_METRICS: tuple[str, ...] = (
    "impressions",
    "reach",
    "engagement",
    "engagement_rate",
    "clicks",
    "ctr",
    "website_visits",
    "registrations",
    "leads",
    "purchases",
    "conversions",
    "conversion_rate",
)

RATE_METRICS: tuple[str, ...] = (
    "engagement_rate",
    "ctr",
    "conversion_rate",
    "visit_conversion_rate",
)


def compute_all(events: list[PerformanceEvent]) -> dict[str, MetricValue]:
    """Every event-derived metric for one set of events."""
    return {
        "impressions": impressions(events),
        "reach": reach(events),
        "engagement": engagement(events),
        "engagement_rate": engagement_rate(events),
        "clicks": clicks(events),
        "ctr": ctr(events),
        "website_visits": website_visits(events),
        "registrations": registrations(events),
        "leads": leads(events),
        "purchases": purchases(events),
        "conversions": conversions(events),
        "conversion_rate": conversion_rate(events),
    }
