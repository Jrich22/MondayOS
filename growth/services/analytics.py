"""
Performance events and the analytics computed from them.

One domain of GrowthService, split out per issue #35. Behaviour is unchanged:
these are the same methods, composed back into the public facade.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from growth.events import EventSource, EventType
from growth.services.base import GrowthServiceBase, _as_datetime


class AnalyticsServiceMixin(GrowthServiceBase):
    """Performance events and the analytics computed from them."""

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
