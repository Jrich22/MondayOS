"""
Growth analytics - aggregation, time series, trends, funnels, and snapshots.

Everything here is a deterministic projection of two inputs: the events in one
workspace, and the content and campaign records already stored there. Nothing is
inferred, nothing is estimated, and no recommendation is produced - this
increment collects and calculates, and the Growth Brain that will reason over it
does not exist yet.

Scoping is structural. ``GrowthAnalytics`` is constructed from a single
``WorkspaceHandle`` and an ``EventStore`` opened for the same project, so there
is no argument it could be given that would make it read another workspace
(ADR-011).

Two properties are worth stating because later increments will lean on them:

* **Operational rates come from lifecycle records, not counters.** Approval rate
  is counted from each item's own ``status_history``, and publishing success from
  its ``publication`` record. A parallel counter would be a second source of
  truth free to drift from the audit trail; these cannot drift, because they are
  read from the trail itself.

* **Provenance survives aggregation.** If any contributing event is synthetic or
  imported, every metric and every aggregate built from it says so, all the way
  out to the CLI.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from growth.campaign import Campaign
from growth.content import ContentItem, ContentStatus
from growth.events import (
    CONVERSION_TYPES,
    EventStore,
    EventType,
    PerformanceEvent,
    contains_unverified,
)
from growth.metrics import (
    MetricValue,
    approval_rate,
    audience_growth,
    compute_all,
    publishing_success_rate,
)
from growth.store import WorkspaceHandle

# The ordered stages of the conversion funnel. Each stage is a superset of the
# next in intent, though not arithmetically guaranteed to be larger - a project
# can record more clicks than impressions if its data is partial, and the funnel
# reports that honestly rather than clamping it.
FUNNEL_STAGES: tuple[tuple[str, EventType], ...] = (
    ("impressions", EventType.IMPRESSION),
    ("clicks", EventType.CLICK),
    ("website_visits", EventType.WEBSITE_VISIT),
    ("registrations", EventType.SIGNUP),
    ("purchases", EventType.PURCHASE),
)

# Buckets a time series may be grouped into.
GRANULARITIES: tuple[str, ...] = ("day", "week")


@dataclass
class TrendResult:
    """
    Movement in one metric between two periods.

    ``direction`` is "up", "down", "flat", or "unknown". Unknown is not a
    failure: it is what an honest answer looks like when one of the periods has
    no data, and it exists so a caller never reads a missing baseline as a
    decline.
    """

    metric: str
    current: float | None
    previous: float | None
    delta: float | None = None
    percent_change: float | None = None
    direction: str = "unknown"
    synthetic: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "current": self.current,
            "previous": self.previous,
            "delta": self.delta,
            "percent_change": self.percent_change,
            "direction": self.direction,
            "synthetic": self.synthetic,
            "reason": self.reason,
        }


@dataclass
class Snapshot:
    """A point-in-time capture of a project's metrics, kept for trend history."""

    id: str
    project: str
    taken_at: datetime
    metrics: dict[str, Any] = field(default_factory=dict)
    followers: dict[str, float] = field(default_factory=dict)
    synthetic: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project": self.project,
            "taken_at": _fmt(self.taken_at),
            "metrics": dict(self.metrics),
            "followers": dict(self.followers),
            "synthetic": self.synthetic,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Snapshot:
        return cls(
            id=str(data.get("id", "")),
            project=str(data.get("project", "")),
            taken_at=_parse(data.get("taken_at")),
            metrics=dict(data.get("metrics") or {}),
            followers={k: float(v) for k, v in (data.get("followers") or {}).items()},
            synthetic=bool(data.get("synthetic", False)),
            note=str(data.get("note", "")),
        )


class GrowthAnalytics:
    """Deterministic analytics over exactly one Growth workspace."""

    def __init__(self, handle: WorkspaceHandle, events: EventStore | None = None) -> None:
        self._handle = handle
        self._events = events or EventStore(handle.path, handle.slug)

    @property
    def project(self) -> str:
        return self._handle.slug

    @property
    def events(self) -> EventStore:
        return self._events

    # ------------------------------------------------------------------
    # Aggregations
    # ------------------------------------------------------------------

    def content_performance(self, content_id: str) -> dict[str, Any]:
        """Metrics for one content item."""
        item = self._handle.get_content(content_id)
        events = self._events.query(content_id=content_id)
        return {
            "project": self.project,
            "content_id": item.id,
            "title": item.title,
            "platform": item.platform,
            "campaign": item.campaign,
            "status": item.status.value,
            "synthetic": contains_unverified(events),
            "event_count": len(events),
            "metrics": _metrics_dict(events),
        }

    def platform_performance(self) -> list[dict[str, Any]]:
        """Metrics grouped by platform, best-reaching first."""
        by_platform: dict[str, list[PerformanceEvent]] = defaultdict(list)
        for event in self._events.all():
            if event.platform:
                by_platform[event.platform].append(event)

        rows: list[tuple[float, dict[str, Any]]] = []
        for platform, events in by_platform.items():
            metrics = _metrics_dict(events)
            rows.append(
                (
                    _value(metrics, "reach"),
                    {
                        "project": self.project,
                        "platform": platform,
                        "synthetic": contains_unverified(events),
                        "event_count": len(events),
                        "metrics": metrics,
                    },
                )
            )
        rows.sort(key=lambda pair: pair[0], reverse=True)
        return [row for _, row in rows]

    def campaign_performance(self, campaign_id: str) -> dict[str, Any]:
        """
        Metrics and delivery counts for one campaign, plus objective progress.

        Content counts come from the items themselves rather than the campaign's
        own id list, so an item detached from a campaign stops counting toward it
        immediately and no reconciliation step is needed.
        """
        campaign = self._handle.get_campaign(campaign_id)
        items = [i for i in self._handle.list_content() if i.campaign == campaign_id]
        events = self._events.query(campaign=campaign_id)
        metrics = _metrics_dict(events)

        ranked = self._rank_content(items, events)
        return {
            "project": self.project,
            "campaign_id": campaign.id,
            "campaign_name": campaign.name,
            "status": campaign.status.value,
            "objective": campaign.objective,
            "primary_conversion_goal": campaign.primary_conversion_goal,
            "synthetic": contains_unverified(events),
            "content_created": len(items),
            "content_approved": sum(1 for i in items if i.is_approved),
            "content_published": sum(1 for i in items if i.status is ContentStatus.PUBLISHED),
            "metrics": metrics,
            "top_platform": self._top_platform(events),
            "top_content": ranked[0] if ranked else None,
            "worst_content": ranked[-1] if len(ranked) > 1 else None,
            "objective_progress": self._objective_progress(campaign, events),
        }

    def workspace_performance(self) -> dict[str, Any]:
        """Whole-project metrics, including the operational rates."""
        events = self._events.all()
        items = self._handle.list_content()
        reviewed, approved = self._review_counts(items)
        published, failed = self._publish_counts(items)

        metrics = _metrics_dict(events)
        metrics["approval_rate"] = approval_rate(reviewed, approved).to_dict()
        metrics["publishing_success_rate"] = publishing_success_rate(published, failed).to_dict()
        metrics["audience_growth"] = self.audience_growth().to_dict()

        return {
            "project": self.project,
            "synthetic": contains_unverified(events),
            "event_count": len(events),
            "content_total": len(items),
            "content_reviewed": reviewed,
            "content_approved": approved,
            "content_published": published,
            "content_failed": failed,
            "campaigns_total": len(self._handle.list_campaigns()),
            "metrics": metrics,
        }

    # ------------------------------------------------------------------
    # Time series, trends, funnel
    # ------------------------------------------------------------------

    def time_series(
        self,
        metric: str = "impressions",
        granularity: str = "day",
        campaign: str = "",
        platform: str = "",
    ) -> dict[str, Any]:
        """
        One metric bucketed over time, oldest bucket first.

        Buckets are derived from the events themselves, so a period with no
        observations is simply absent rather than reported as a zero that a
        reader could mistake for a measured trough.
        """
        if granularity not in GRANULARITIES:
            raise ValueError(
                f"Unknown granularity {granularity!r}. Valid: {', '.join(GRANULARITIES)}"
            )
        # Validated before the loop: with no events the loop never runs, and an
        # unknown metric would otherwise return an empty series as if it were real.
        if metric not in compute_all([]):
            raise ValueError(
                f"Unknown metric {metric!r}. Valid: {', '.join(sorted(compute_all([])))}"
            )
        events = self._events.query(campaign=campaign, platform=platform)
        buckets: dict[str, list[PerformanceEvent]] = defaultdict(list)
        for event in events:
            buckets[_bucket_key(event.occurred_at, granularity)].append(event)

        points = []
        for key in sorted(buckets):
            value = compute_all(buckets[key])[metric]
            points.append(
                {
                    "bucket": key,
                    "value": value.value,
                    "synthetic": value.synthetic,
                    "sample_size": value.sample_size,
                }
            )
        return {
            "project": self.project,
            "metric": metric,
            "granularity": granularity,
            "synthetic": contains_unverified(events),
            "points": points,
        }

    def trend(
        self,
        metric: str,
        period_days: int,
        now: datetime,
        campaign: str = "",
        platform: str = "",
    ) -> TrendResult:
        """
        Compare the last ``period_days`` against the period immediately before it.

        ``now`` is injected rather than read from the clock, so a trend is exactly
        reproducible and a test does not depend on when it runs.
        """
        current_end = _as_utc(now)
        current_start = current_end - timedelta(days=period_days)
        previous_start = current_start - timedelta(days=period_days)

        current_events = self._events.query(
            campaign=campaign, platform=platform, since=current_start, until=current_end
        )
        previous_events = self._events.query(
            campaign=campaign, platform=platform, since=previous_start, until=current_start
        )

        current = compute_all(current_events).get(metric)
        previous = compute_all(previous_events).get(metric)
        if current is None or previous is None:
            raise ValueError(f"Unknown metric {metric!r}.")

        synthetic = current.synthetic or previous.synthetic
        if current.value is None or previous.value is None:
            return TrendResult(
                metric=metric,
                current=current.value,
                previous=previous.value,
                synthetic=synthetic,
                reason="undefined: one of the two periods has no data to compare",
            )

        delta = current.value - previous.value
        if previous.value == 0:
            percent = None
            reason = "percent change undefined: the previous period was zero"
        else:
            percent = (delta / previous.value) * 100.0
            reason = ""
        return TrendResult(
            metric=metric,
            current=current.value,
            previous=previous.value,
            delta=delta,
            percent_change=percent,
            direction="up" if delta > 0 else ("down" if delta < 0 else "flat"),
            synthetic=synthetic,
            reason=reason,
        )

    def funnel(self, campaign: str = "", platform: str = "") -> dict[str, Any]:
        """
        The conversion funnel, with a step-to-step rate between each stage.

        A stage whose predecessor is empty reports a null rate rather than zero:
        with nothing entering a step, its conversion is unknown, not failing.
        """
        events = self._events.query(campaign=campaign, platform=platform)
        stages: list[dict[str, Any]] = []
        previous_count: float | None = None

        for label, event_type in FUNNEL_STAGES:
            count = float(sum(e.value for e in events if e.event_type is event_type))
            if previous_count is None:
                rate: float | None = None
                reason = "entry stage"
            elif previous_count <= 0:
                rate = None
                reason = f"undefined: no {stages[-1]['stage']} recorded"
            else:
                rate = count / previous_count
                reason = ""
            stages.append(
                {
                    "stage": label,
                    "count": count,
                    "rate_from_previous": rate,
                    "reason": reason,
                }
            )
            previous_count = count

        total_conversions = float(sum(e.value for e in events if e.event_type in CONVERSION_TYPES))
        entry = stages[0]["count"] if stages else 0.0
        return {
            "project": self.project,
            "campaign": campaign,
            "platform": platform,
            "synthetic": contains_unverified(events),
            "stages": stages,
            "total_conversions": total_conversions,
            "overall_rate": (total_conversions / entry) if entry > 0 else None,
            "overall_rate_reason": "" if entry > 0 else "undefined: no impressions recorded",
        }

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    def take_snapshot(
        self, now: datetime, followers: dict[str, float] | None = None, note: str = ""
    ) -> Snapshot:
        """
        Capture the current metrics so later trends have a baseline.

        ``followers`` is supplied by the caller because follower count is a gauge
        a platform reports, and no platform reports anything yet.
        """
        performance = self.workspace_performance()
        snapshot = Snapshot(
            id=self._handle.next_snapshot_id(),
            project=self.project,
            taken_at=_as_utc(now),
            metrics=performance["metrics"],
            followers=dict(followers or {}),
            synthetic=bool(performance["synthetic"]),
            note=note,
        )
        self._handle.save_snapshot(snapshot.to_dict())
        return snapshot

    def snapshots(self) -> list[Snapshot]:
        """Every snapshot for this project, oldest first."""
        return [Snapshot.from_dict(d) for d in self._handle.list_snapshots()]

    def audience_growth(self) -> MetricValue:
        """Follower change between the earliest and latest snapshot."""
        taken = self.snapshots()
        if len(taken) < 2:
            return audience_growth(None, None, synthetic=bool(taken and taken[0].synthetic))
        first, last = taken[0], taken[-1]
        return audience_growth(
            sum(first.followers.values()) if first.followers else None,
            sum(last.followers.values()) if last.followers else None,
            synthetic=first.synthetic or last.synthetic,
        )

    # ------------------------------------------------------------------
    # Portfolio aggregate
    # ------------------------------------------------------------------

    def write_aggregate(self, now: datetime) -> dict[str, Any]:
        """
        Write the per-project aggregate the portfolio view will later read.

        Counts, rates and deltas only. No copy, no media, no audience definition,
        no account binding, no campaign or content names - the portfolio must be
        able to compare projects without learning what any of them said.
        """
        performance = self.workspace_performance()
        metrics = performance["metrics"]
        aggregate = {
            "project": self.project,
            "generated_at": _fmt(_as_utc(now)),
            "synthetic": performance["synthetic"],
            "counts": {
                "events": performance["event_count"],
                "campaigns": performance["campaigns_total"],
                "content_total": performance["content_total"],
                "content_approved": performance["content_approved"],
                "content_published": performance["content_published"],
                "content_failed": performance["content_failed"],
            },
            "totals": {
                key: _value(metrics, key)
                for key in ("reach", "impressions", "engagement", "clicks", "conversions")
            },
            "rates": {
                key: (metrics.get(key) or {}).get("value")
                for key in (
                    "engagement_rate",
                    "ctr",
                    "conversion_rate",
                    "approval_rate",
                    "publishing_success_rate",
                )
            },
            "deltas": {"audience_growth": (metrics.get("audience_growth") or {}).get("value")},
        }
        self._handle.write_aggregate(aggregate)
        return aggregate

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _review_counts(items: list[ContentItem]) -> tuple[int, int]:
        """(reached review, ever approved), counted from each item's own history."""
        reviewed = 0
        approved = 0
        for item in items:
            statuses = {t.to_status for t in item.status_history}
            if ContentStatus.READY_FOR_REVIEW in statuses:
                reviewed += 1
            if ContentStatus.APPROVED in statuses:
                approved += 1
        return reviewed, approved

    @staticmethod
    def _publish_counts(items: list[ContentItem]) -> tuple[int, int]:
        """(published, failed), read from the publication record on each item."""
        published = sum(1 for i in items if i.status is ContentStatus.PUBLISHED)
        failed = sum(1 for i in items if i.status is ContentStatus.FAILED)
        return published, failed

    def _top_platform(self, events: list[PerformanceEvent]) -> str:
        by_platform: dict[str, float] = defaultdict(float)
        for event in events:
            if event.platform and event.event_type in CONVERSION_TYPES:
                by_platform[event.platform] += event.value
        if not by_platform:
            for event in events:
                if event.platform:
                    by_platform[event.platform] += event.value
        if not by_platform:
            return ""
        return max(sorted(by_platform), key=lambda p: by_platform[p])

    def _rank_content(
        self, items: list[ContentItem], events: list[PerformanceEvent]
    ) -> list[dict[str, Any]]:
        """Rank a campaign's content by conversions, then clicks, then engagement."""
        if not items:
            return []
        by_content: dict[str, list[PerformanceEvent]] = defaultdict(list)
        for event in events:
            if event.content_id:
                by_content[event.content_id].append(event)

        ranked = []
        for item in items:
            item_events = by_content.get(item.id, [])
            metrics = _metrics_dict(item_events)
            ranked.append(
                {
                    "content_id": item.id,
                    "title": item.title,
                    "platform": item.platform,
                    "measured": bool(item_events),
                    "conversions": _value(metrics, "conversions"),
                    "clicks": _value(metrics, "clicks"),
                    "engagement": _value(metrics, "engagement"),
                }
            )
        # Unmeasured content sorts last rather than as a worst performer: never
        # observed is not the same as observed and bad.
        ranked.sort(
            key=lambda r: (
                r["measured"],
                r["conversions"] or 0.0,
                r["clicks"] or 0.0,
                r["engagement"] or 0.0,
            ),
            reverse=True,
        )
        return ranked

    @staticmethod
    def _objective_progress(campaign: Campaign, events: list[PerformanceEvent]) -> dict[str, Any]:
        """
        Progress toward a campaign's conversion goal.

        A target is only reported when the campaign's objective contains an
        explicit number. Inventing a denominator would turn a vague objective into
        a false percentage, so an objective without a number reports the achieved
        count and says the target is unstated.
        """
        goal = (campaign.primary_conversion_goal or "").strip().lower()
        if goal:
            # A stated goal counts only conversions that ARE that goal: a
            # custom_conversion carrying the name, or the matching event type for
            # a goal like "signup" or "purchase". Counting an unrelated purchase
            # toward a demo-request goal would overstate progress, and a campaign
            # that reads 106% complete on the wrong conversions is worse than one
            # that reads 0%.
            matching = [
                e
                for e in events
                if e.event_type in CONVERSION_TYPES
                and (e.name.strip().lower() == goal or e.event_type.value == goal)
            ]
        else:
            matching = [e for e in events if e.event_type in CONVERSION_TYPES]
        achieved = float(sum(e.value for e in matching))

        target = _first_number(campaign.objective)
        if target is None:
            return {
                "goal": campaign.primary_conversion_goal,
                "achieved": achieved,
                "target": None,
                "percent": None,
                "reason": "objective states no numeric target",
            }
        return {
            "goal": campaign.primary_conversion_goal,
            "achieved": achieved,
            "target": target,
            "percent": (achieved / target * 100.0) if target > 0 else None,
            "reason": "" if target > 0 else "target is zero",
        }


def _metrics_dict(events: list[PerformanceEvent]) -> dict[str, Any]:
    return {name: value.to_dict() for name, value in compute_all(events).items()}


def _value(metrics: dict[str, Any], key: str) -> float:
    entry = metrics.get(key) or {}
    value = entry.get("value")
    return float(value) if isinstance(value, (int, float)) else 0.0


def _first_number(text: str) -> float | None:
    """First integer appearing in a string, or None."""
    digits = ""
    for char in text or "":
        if char.isdigit():
            digits += char
        elif digits:
            break
    return float(digits) if digits else None


def _bucket_key(when: datetime, granularity: str) -> str:
    moment = _as_utc(when)
    if granularity == "week":
        monday = moment - timedelta(days=moment.weekday())
        return monday.strftime("%Y-%m-%d")
    return moment.strftime("%Y-%m-%d")


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _parse(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.rstrip("Z")).replace(tzinfo=UTC)
    return datetime.now(tz=UTC)


def _fmt(value: datetime) -> str:
    return _as_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")
