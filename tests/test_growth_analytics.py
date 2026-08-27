"""
Tests for Growth performance events and analytics (increment 5).

This layer becomes the sole source of truth for every future Growth Brain
recommendation, so the tests lean hardest on the properties that would make it
untrustworthy: workspace leakage, non-determinism, fabricated values where data
is absent, and unverified data that stops looking unverified.
"""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from growth import GrowthStore
from growth.analytics import GrowthAnalytics
from growth.content import ContentStatus
from growth.events import (
    EventSource,
    EventType,
    PlatformSourceUnavailableError,
)
from growth.metrics import (
    RATE_METRICS,
    approval_rate,
    compute_all,
    conversion_rate,
    ctr,
    engagement_rate,
    publishing_success_rate,
    visit_conversion_rate,
)
from growth.service import GrowthService
from monday import Monday, MondayConfig
from monday.project import ProjectRegistry

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


def _root(tmp: str, projects: dict[str, str] | None = None) -> Path:
    root = Path(tmp)
    (root / "config").mkdir(parents=True, exist_ok=True)
    registry = ProjectRegistry(root / "config")
    for name, source in (projects or {"acme": "acme-src"}).items():
        (root / source).mkdir(parents=True, exist_ok=True)
        registry.register(name, root / source, overwrite=True)
    return root


class AnalyticsCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = _root(self._tmp.name)
        self.store = GrowthStore(self.root)
        self.store.init_workspace("acme")
        self.handle = self.store.open("acme")
        self.events = self.handle.event_store()
        self.analytics = GrowthAnalytics(self.handle, self.events)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _emit(self, event_type: EventType, value: float = 1.0, **kw):
        return self.events.record(
            event_type=event_type,
            source=kw.pop("source", EventSource.SYNTHETIC),
            occurred_at=kw.pop("occurred_at", T0),
            value=value,
            **kw,
        )

    def _standard_funnel(self, **kw) -> None:
        self._emit(EventType.IMPRESSION, 1000, **kw)
        self._emit(EventType.CLICK, 100, **kw)
        self._emit(EventType.WEBSITE_VISIT, 80, **kw)
        self._emit(EventType.SIGNUP, 20, **kw)
        self._emit(EventType.PURCHASE, 5, **kw)


# ---------------------------------------------------------------------------
# Event model and source integrity
# ---------------------------------------------------------------------------


class TestEventSourceIntegrity(AnalyticsCase):
    def test_platform_sourced_events_are_refused(self):
        """No adapter exists, so nothing can honestly claim a platform reported it."""
        with self.assertRaises(PlatformSourceUnavailableError):
            self.events.record(
                event_type=EventType.IMPRESSION,
                source=EventSource.PLATFORM,
                occurred_at=T0,
            )

    def test_no_production_path_writes_a_platform_event(self):
        service = GrowthService(self.root)
        with self.assertRaises(PlatformSourceUnavailableError):
            service.record_event("acme", event_type="impression", source="platform")

    def test_real_adapters_is_still_empty(self):
        from integrations.publishing.factory import REAL_ADAPTERS

        self.assertEqual(REAL_ADAPTERS, {})

    def test_source_is_recorded_at_rest(self):
        self._emit(EventType.CLICK, source=EventSource.SYNTHETIC)
        raw = self.events.path.read_text(encoding="utf-8")
        self.assertIn('"source": "synthetic"', raw)

    def test_service_defaults_to_imported_not_synthetic(self):
        """Operator-supplied data is imported; mislabelling it synthetic would lie."""
        service = GrowthService(self.root)
        event = service.record_event("acme", event_type="click")
        self.assertEqual(event["source"], "imported")

    def test_a_metric_from_synthetic_events_is_flagged_synthetic(self):
        self._emit(EventType.IMPRESSION, 100, source=EventSource.SYNTHETIC)
        metrics = compute_all(self.events.all())
        self.assertTrue(metrics["impressions"].synthetic)
        self.assertEqual(metrics["impressions"].sources, ["synthetic"])

    def test_synthetic_flag_survives_every_aggregation_level(self):
        campaign = self.handle.create_campaign(name="Launch")
        item = self.handle.create_content(platform="linkedin", copy="x")
        self._standard_funnel(content_id=item.id, campaign=campaign.id, platform="linkedin")
        self.assertTrue(self.analytics.workspace_performance()["synthetic"])
        self.assertTrue(self.analytics.campaign_performance(campaign.id)["synthetic"])
        self.assertTrue(self.analytics.content_performance(item.id)["synthetic"])
        self.assertTrue(self.analytics.platform_performance()[0]["synthetic"])
        self.assertTrue(self.analytics.funnel()["synthetic"])

    def test_imported_events_are_also_treated_as_unverified(self):
        self._emit(EventType.IMPRESSION, 50, source=EventSource.IMPORTED)
        self.assertTrue(compute_all(self.events.all())["impressions"].synthetic)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism(AnalyticsCase):
    def test_every_metric_is_identical_across_repeated_computation(self):
        self._standard_funnel()
        self._emit(EventType.REACTION, 30)
        events = self.events.all()

        first = {k: v.to_dict() for k, v in compute_all(events).items()}
        second = {k: v.to_dict() for k, v in compute_all(events).items()}
        self.assertEqual(first, second)

    def test_aggregations_are_identical_across_repeated_calls(self):
        campaign = self.handle.create_campaign(name="Launch")
        self._standard_funnel(campaign=campaign.id, platform="linkedin")
        for call in (
            lambda: self.analytics.workspace_performance(),
            lambda: self.analytics.campaign_performance(campaign.id),
            lambda: self.analytics.platform_performance(),
            lambda: self.analytics.funnel(),
            lambda: self.analytics.time_series("impressions"),
        ):
            with self.subTest(call=call):
                self.assertEqual(call(), call())

    def test_trend_uses_an_injected_clock_not_the_wall_clock(self):
        self._emit(EventType.CLICK, 10, occurred_at=T0)
        a = self.analytics.trend("clicks", period_days=7, now=T0 + timedelta(days=1))
        b = self.analytics.trend("clicks", period_days=7, now=T0 + timedelta(days=1))
        self.assertEqual(a.to_dict(), b.to_dict())


# ---------------------------------------------------------------------------
# Undefined rather than fabricated
# ---------------------------------------------------------------------------


class TestNoFabricatedValues(AnalyticsCase):
    def test_every_rate_metric_is_undefined_with_no_data(self):
        for func in (engagement_rate, ctr, conversion_rate, visit_conversion_rate):
            with self.subTest(metric=func.__name__):
                result = func([])
                self.assertIsNone(result.value)
                self.assertIn("undefined", result.reason)

    def test_rate_metrics_constant_matches_the_functions(self):
        self.assertEqual(
            set(RATE_METRICS),
            {"engagement_rate", "ctr", "conversion_rate", "visit_conversion_rate"},
        )

    def test_a_rate_with_an_empty_denominator_is_none_not_zero(self):
        self._emit(EventType.CLICK, 5)  # clicks but no impressions
        result = ctr(self.events.all())
        self.assertIsNone(result.value)
        self.assertNotEqual(result.value, 0.0)

    def test_a_genuine_zero_is_reported_as_zero(self):
        """Zero measured clicks is a finding; it must not read as missing data."""
        self._emit(EventType.IMPRESSION, 1000)
        result = ctr(self.events.all())
        self.assertEqual(result.value, 0.0)
        self.assertEqual(result.reason, "")

    def test_operational_rates_are_undefined_before_anything_happens(self):
        self.assertIsNone(approval_rate(0, 0).value)
        self.assertIsNone(publishing_success_rate(0, 0).value)

    def test_audience_growth_needs_two_snapshots(self):
        result = self.analytics.audience_growth()
        self.assertIsNone(result.value)
        self.assertIn("two snapshots", result.reason)

    def test_objective_progress_without_a_number_reports_no_target(self):
        campaign = self.handle.create_campaign(
            name="Vague", objective="build authority", primary_conversion_goal="signup"
        )
        progress = self.analytics.campaign_performance(campaign.id)["objective_progress"]
        self.assertIsNone(progress["target"])
        self.assertIsNone(progress["percent"])
        self.assertIn("no numeric target", progress["reason"])

    def test_funnel_stage_after_an_empty_stage_is_undefined(self):
        self._emit(EventType.SIGNUP, 3)  # signups with no upstream funnel
        stages = {s["stage"]: s for s in self.analytics.funnel()["stages"]}
        self.assertIsNone(stages["clicks"]["rate_from_previous"])
        self.assertIn("undefined", stages["clicks"]["reason"])

    def test_trend_with_no_baseline_is_unknown_not_a_decline(self):
        self._emit(EventType.CLICK, 10, occurred_at=T0)
        result = self.analytics.trend("clicks", period_days=7, now=T0 + timedelta(days=1))
        self.assertEqual(result.direction, "up")
        self.assertIsNone(result.percent_change)
        self.assertIn("previous period was zero", result.reason)

    def test_time_series_omits_empty_buckets_rather_than_zero_filling(self):
        self._emit(EventType.IMPRESSION, 10, occurred_at=T0)
        self._emit(EventType.IMPRESSION, 10, occurred_at=T0 + timedelta(days=5))
        points = self.analytics.time_series("impressions")["points"]
        self.assertEqual(len(points), 2)


# ---------------------------------------------------------------------------
# Metric correctness
# ---------------------------------------------------------------------------


class TestMetricFormulas(AnalyticsCase):
    def test_counts(self):
        self._standard_funnel()
        self._emit(EventType.REACTION, 30)
        self._emit(EventType.COMMENT, 12)
        self._emit(EventType.SHARE, 8)
        self._emit(EventType.REACH, 700)
        m = compute_all(self.events.all())

        self.assertEqual(m["impressions"].value, 1000)
        self.assertEqual(m["reach"].value, 700)
        self.assertEqual(m["engagement"].value, 50)
        self.assertEqual(m["clicks"].value, 100)
        self.assertEqual(m["website_visits"].value, 80)
        self.assertEqual(m["registrations"].value, 20)
        self.assertEqual(m["purchases"].value, 5)
        self.assertEqual(m["conversions"].value, 25)

    def test_rates(self):
        self._standard_funnel()
        self._emit(EventType.REACTION, 50)
        m = compute_all(self.events.all())
        self.assertAlmostEqual(m["ctr"].value, 0.1)
        self.assertAlmostEqual(m["engagement_rate"].value, 0.05)
        self.assertAlmostEqual(m["conversion_rate"].value, 0.25)

    def test_leads_are_named_custom_conversions(self):
        self._emit(EventType.CUSTOM_CONVERSION, 7, name="lead")
        self._emit(EventType.CUSTOM_CONVERSION, 3, name="demo_request")
        m = compute_all(self.events.all())
        self.assertEqual(m["leads"].value, 7)
        self.assertEqual(m["conversions"].value, 10)

    def test_every_metric_carries_its_formula(self):
        self._standard_funnel()
        for name, value in compute_all(self.events.all()).items():
            with self.subTest(metric=name):
                self.assertTrue(value.formula, f"{name} has no formula")

    def test_approval_rate_derives_from_lifecycle_history(self):
        """Counted from each item's own status_history, never a separate counter."""
        approved = self.handle.create_content(
            platform="linkedin",
            account="a",
            copy="c",
            cta="x",
            destination_url="https://example.com",
            expected_goal="g",
            expected_audience="a",
            scheduled_at=T0,
        )
        self.handle.transition_content(approved.id, ContentStatus.AI_REVIEW)
        self.handle.transition_content(approved.id, ContentStatus.READY_FOR_REVIEW)
        self.handle.approve_content(approved.id, approved_by="human:j")

        rejected = self.handle.create_content(platform="linkedin", copy="c2")
        self.handle.transition_content(rejected.id, ContentStatus.AI_REVIEW)
        self.handle.transition_content(rejected.id, ContentStatus.READY_FOR_REVIEW)
        self.handle.transition_content(rejected.id, ContentStatus.CHANGES_REQUESTED)

        metrics = self.analytics.workspace_performance()["metrics"]
        self.assertEqual(metrics["approval_rate"]["value"], 0.5)
        self.assertEqual(metrics["approval_rate"]["sample_size"], 2)

    def test_publishing_success_rate_derives_from_item_state(self):
        self.assertEqual(publishing_success_rate(3, 1).value, 0.75)
        self.assertEqual(publishing_success_rate(1, 0).value, 1.0)


# ---------------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------------


class TestWorkspaceIsolation(unittest.TestCase):
    def test_analytics_never_read_another_projects_events(self):
        with TemporaryDirectory() as tmp:
            root = _root(tmp, {"alpha": "a", "beta": "b"})
            store = GrowthStore(root)
            store.init_workspace("alpha")
            store.init_workspace("beta")

            alpha = store.open("alpha")
            for _ in range(5):
                alpha.event_store().record(
                    event_type=EventType.IMPRESSION,
                    source=EventSource.SYNTHETIC,
                    occurred_at=T0,
                    value=100,
                )

            beta_analytics = GrowthAnalytics(store.open("beta"))
            self.assertEqual(beta_analytics.workspace_performance()["event_count"], 0)
            self.assertEqual(
                beta_analytics.workspace_performance()["metrics"]["impressions"]["value"], 0.0
            )

    def test_event_files_are_stored_per_workspace(self):
        with TemporaryDirectory() as tmp:
            root = _root(tmp, {"alpha": "a", "beta": "b"})
            store = GrowthStore(root)
            store.init_workspace("alpha")
            store.init_workspace("beta")
            store.open("alpha").event_store().record(
                event_type=EventType.CLICK, source=EventSource.SYNTHETIC, occurred_at=T0
            )
            self.assertTrue(store.open("alpha").event_store().path.exists())
            self.assertFalse(store.open("beta").event_store().path.exists())

    def test_event_ordinals_do_not_leak_volume_between_projects(self):
        with TemporaryDirectory() as tmp:
            root = _root(tmp, {"alpha": "a", "beta": "b"})
            store = GrowthStore(root)
            store.init_workspace("alpha")
            store.init_workspace("beta")
            for _ in range(6):
                store.open("alpha").event_store().record(
                    event_type=EventType.CLICK, source=EventSource.SYNTHETIC, occurred_at=T0
                )
            first_beta = (
                store.open("beta")
                .event_store()
                .record(event_type=EventType.CLICK, source=EventSource.SYNTHETIC, occurred_at=T0)
            )
            self.assertEqual(first_beta.id, 1)

    def test_snapshots_are_project_scoped(self):
        with TemporaryDirectory() as tmp:
            root = _root(tmp, {"alpha": "a", "beta": "b"})
            store = GrowthStore(root)
            store.init_workspace("alpha")
            store.init_workspace("beta")
            GrowthAnalytics(store.open("alpha")).take_snapshot(now=T0)
            self.assertEqual(len(GrowthAnalytics(store.open("alpha")).snapshots()), 1)
            self.assertEqual(GrowthAnalytics(store.open("beta")).snapshots(), [])


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------


class TestAggregations(AnalyticsCase):
    def setUp(self) -> None:
        super().setUp()
        self.campaign = self.handle.create_campaign(
            name="Launch",
            objective="Generate 50 demo requests",
            primary_conversion_goal="demo_request",
        )
        self.good = self.handle.create_content(
            platform="linkedin",
            account="a",
            copy="good",
            cta="x",
            destination_url="https://example.com",
            campaign=self.campaign.id,
            expected_goal="g",
            expected_audience="a",
            scheduled_at=T0,
        )
        self.poor = self.handle.create_content(
            platform="x",
            account="a",
            copy="poor",
            cta="x",
            destination_url="https://example.com",
            campaign=self.campaign.id,
            expected_goal="g",
            expected_audience="a",
            scheduled_at=T0,
        )
        for _ in range(20):
            self._emit(
                EventType.CUSTOM_CONVERSION,
                1,
                content_id=self.good.id,
                campaign=self.campaign.id,
                platform="linkedin",
                name="demo_request",
            )
        self._emit(
            EventType.CLICK,
            200,
            content_id=self.good.id,
            campaign=self.campaign.id,
            platform="linkedin",
        )
        self._emit(
            EventType.CLICK, 10, content_id=self.poor.id, campaign=self.campaign.id, platform="x"
        )

    def test_campaign_aggregation_reports_delivery_and_performance(self):
        result = self.analytics.campaign_performance(self.campaign.id)
        self.assertEqual(result["content_created"], 2)
        self.assertEqual(result["content_approved"], 0)
        self.assertEqual(result["content_published"], 0)
        self.assertEqual(result["metrics"]["clicks"]["value"], 210)
        self.assertEqual(result["metrics"]["conversions"]["value"], 20)

    def test_campaign_reports_top_and_worst_content(self):
        result = self.analytics.campaign_performance(self.campaign.id)
        self.assertEqual(result["top_content"]["content_id"], self.good.id)
        self.assertEqual(result["worst_content"]["content_id"], self.poor.id)

    def test_top_platform_prefers_conversions(self):
        self.assertEqual(
            self.analytics.campaign_performance(self.campaign.id)["top_platform"], "linkedin"
        )

    def test_objective_progress_uses_the_number_in_the_objective(self):
        progress = self.analytics.campaign_performance(self.campaign.id)["objective_progress"]
        self.assertEqual(progress["target"], 50.0)
        self.assertEqual(progress["achieved"], 20.0)
        self.assertAlmostEqual(progress["percent"], 40.0)

    def test_progress_counts_only_the_stated_goal(self):
        """A purchase must not count toward a demo_request goal."""
        self._emit(EventType.PURCHASE, 500, campaign=self.campaign.id)
        progress = self.analytics.campaign_performance(self.campaign.id)["objective_progress"]
        self.assertEqual(progress["achieved"], 20.0)

    def test_progress_matches_a_goal_named_after_an_event_type(self):
        campaign = self.handle.create_campaign(
            name="Signups", objective="Get 100 signups", primary_conversion_goal="signup"
        )
        self._emit(EventType.SIGNUP, 30, campaign=campaign.id)
        self._emit(EventType.PURCHASE, 99, campaign=campaign.id)
        progress = self.analytics.campaign_performance(campaign.id)["objective_progress"]
        self.assertEqual(progress["achieved"], 30.0)

    def test_content_aggregation(self):
        result = self.analytics.content_performance(self.good.id)
        self.assertEqual(result["metrics"]["clicks"]["value"], 200)
        self.assertEqual(result["platform"], "linkedin")

    def test_platform_aggregation_sorts_by_reach(self):
        rows = self.analytics.platform_performance()
        self.assertEqual({r["platform"] for r in rows}, {"linkedin", "x"})

    def test_workspace_aggregation_includes_operational_rates(self):
        metrics = self.analytics.workspace_performance()["metrics"]
        for key in ("approval_rate", "publishing_success_rate", "audience_growth"):
            self.assertIn(key, metrics)

    def test_unmeasured_content_ranks_last_not_worst(self):
        """Never observed is not the same as observed and bad."""
        unmeasured = self.handle.create_content(
            platform="linkedin", copy="never measured", campaign=self.campaign.id
        )
        result = self.analytics.campaign_performance(self.campaign.id)
        self.assertEqual(result["worst_content"]["content_id"], unmeasured.id)
        self.assertFalse(result["worst_content"]["measured"])

    def test_detached_content_stops_counting_immediately(self):
        self.handle.assign_campaign(self.poor.id, "")
        self.assertEqual(
            self.analytics.campaign_performance(self.campaign.id)["content_created"], 1
        )


# ---------------------------------------------------------------------------
# Time series, trends, funnel
# ---------------------------------------------------------------------------


class TestTimeSeriesAndTrends(AnalyticsCase):
    def test_daily_buckets(self):
        for day in range(3):
            self._emit(EventType.IMPRESSION, 100, occurred_at=T0 + timedelta(days=day))
        points = self.analytics.time_series("impressions", "day")["points"]
        self.assertEqual(len(points), 3)
        self.assertEqual([p["value"] for p in points], [100, 100, 100])

    def test_weekly_buckets_collapse_a_week(self):
        for day in range(5):
            self._emit(EventType.IMPRESSION, 100, occurred_at=T0 + timedelta(days=day))
        points = self.analytics.time_series("impressions", "week")["points"]
        self.assertLess(len(points), 5)

    def test_unknown_granularity_is_refused(self):
        with self.assertRaises(ValueError):
            self.analytics.time_series("impressions", "fortnight")

    def test_unknown_metric_is_refused(self):
        with self.assertRaises(ValueError):
            self.analytics.time_series("vibes")

    def test_trend_up(self):
        self._emit(EventType.CLICK, 10, occurred_at=T0 - timedelta(days=10))
        self._emit(EventType.CLICK, 30, occurred_at=T0 - timedelta(days=2))
        result = self.analytics.trend("clicks", period_days=7, now=T0)
        self.assertEqual(result.direction, "up")
        self.assertEqual(result.delta, 20.0)
        self.assertAlmostEqual(result.percent_change, 200.0)

    def test_trend_down(self):
        self._emit(EventType.CLICK, 40, occurred_at=T0 - timedelta(days=10))
        self._emit(EventType.CLICK, 10, occurred_at=T0 - timedelta(days=2))
        result = self.analytics.trend("clicks", period_days=7, now=T0)
        self.assertEqual(result.direction, "down")
        self.assertEqual(result.delta, -30.0)

    def test_trend_flat(self):
        self._emit(EventType.CLICK, 20, occurred_at=T0 - timedelta(days=10))
        self._emit(EventType.CLICK, 20, occurred_at=T0 - timedelta(days=2))
        self.assertEqual(self.analytics.trend("clicks", 7, T0).direction, "flat")

    def test_funnel_stages_and_rates(self):
        self._standard_funnel()
        funnel = self.analytics.funnel()
        stages = {s["stage"]: s for s in funnel["stages"]}
        self.assertEqual(stages["impressions"]["count"], 1000)
        self.assertAlmostEqual(stages["clicks"]["rate_from_previous"], 0.1)
        self.assertAlmostEqual(stages["website_visits"]["rate_from_previous"], 0.8)
        self.assertAlmostEqual(stages["registrations"]["rate_from_previous"], 0.25)
        self.assertAlmostEqual(funnel["overall_rate"], 0.025)

    def test_funnel_can_be_scoped_to_a_campaign(self):
        campaign = self.handle.create_campaign(name="C")
        self._standard_funnel(campaign=campaign.id)
        self._emit(EventType.IMPRESSION, 9999)  # outside the campaign
        stages = {s["stage"]: s for s in self.analytics.funnel(campaign=campaign.id)["stages"]}
        self.assertEqual(stages["impressions"]["count"], 1000)


# ---------------------------------------------------------------------------
# Snapshots and the portfolio aggregate
# ---------------------------------------------------------------------------


class TestSnapshotsAndAggregate(AnalyticsCase):
    def test_snapshot_captures_current_metrics(self):
        self._standard_funnel()
        snapshot = self.analytics.take_snapshot(now=T0, followers={"linkedin": 1000})
        self.assertEqual(snapshot.metrics["impressions"]["value"], 1000)
        self.assertTrue(snapshot.synthetic)

    def test_audience_growth_from_two_snapshots(self):
        self.analytics.take_snapshot(now=T0, followers={"linkedin": 1000})
        self.analytics.take_snapshot(now=T0 + timedelta(days=7), followers={"linkedin": 1250})
        result = self.analytics.audience_growth()
        self.assertEqual(result.value, 250.0)

    def test_aggregate_contains_only_counts_rates_and_deltas(self):
        campaign = self.handle.create_campaign(name="Secret campaign name")
        self.handle.create_content(
            platform="linkedin",
            copy="CONFIDENTIAL BODY TEXT",
            campaign=campaign.id,
            media=["assets/private.png"],
            audience="VP Talent",
        )
        self.handle.bind("linkedin", "acct-1", "acme", "LINKEDIN_TOKEN")
        self._standard_funnel(campaign=campaign.id)

        aggregate = self.analytics.write_aggregate(now=T0)
        payload = json.dumps(aggregate)

        for forbidden in (
            "CONFIDENTIAL BODY TEXT",
            "assets/private.png",
            "VP Talent",
            "LINKEDIN_TOKEN",
            "acct-1",
            "Secret campaign name",
        ):
            self.assertNotIn(forbidden, payload, f"{forbidden} leaked into the aggregate")
        self.assertEqual(
            set(aggregate) - {"project", "generated_at", "synthetic"},
            {"counts", "totals", "rates", "deltas"},
        )

    def test_aggregate_is_written_where_the_portfolio_can_read_it(self):
        self.analytics.write_aggregate(now=T0)
        self.assertTrue((self.handle.path / "aggregates.json").exists())
        self.assertEqual(self.handle.read_aggregate()["project"], "acme")


# ---------------------------------------------------------------------------
# API / CLI
# ---------------------------------------------------------------------------


class TestAnalyticsApi(unittest.TestCase):
    def _monday(self, tmp: str):
        root = _root(tmp)
        monday = Monday(MondayConfig(project_root=root))
        monday.growth("workspace-init", project="acme")
        return root, monday

    def test_event_and_analytics_flow(self):
        with TemporaryDirectory() as tmp:
            _, monday = self._monday(tmp)
            recorded = monday.growth(
                "event-record",
                project="acme",
                event_type="impression",
                source="synthetic",
                value=500,
                platform="linkedin",
            )
            self.assertTrue(recorded.success)

            listed = monday.growth("event-list", project="acme")
            self.assertEqual(listed.data["count"], 1)

            analytics = monday.growth("analytics", project="acme")
            self.assertTrue(analytics.data["analytics"]["synthetic"])
            self.assertIn("SYNTHETIC", analytics.message)

    def test_bulk_import(self):
        with TemporaryDirectory() as tmp:
            _, monday = self._monday(tmp)
            result = monday.growth(
                "event-import",
                project="acme",
                events=[
                    {"event_type": "click", "value": 10},
                    {"event_type": "signup", "value": 2},
                ],
            )
            self.assertEqual(result.data["import"]["recorded"], 2)

    def test_platform_source_returns_a_failed_response_not_an_exception(self):
        with TemporaryDirectory() as tmp:
            _, monday = self._monday(tmp)
            result = monday.growth(
                "event-record", project="acme", event_type="click", source="platform"
            )
            self.assertFalse(result.success)
            self.assertIn("no real platform adapter", result.message)

    def test_funnel_and_trend_through_the_api(self):
        with TemporaryDirectory() as tmp:
            _, monday = self._monday(tmp)
            monday.growth(
                "event-import",
                project="acme",
                source="synthetic",
                events=[
                    {"event_type": "impression", "value": 1000},
                    {"event_type": "click", "value": 50},
                ],
            )
            funnel = monday.growth("analytics-funnel", project="acme")
            self.assertTrue(funnel.success)
            trend = monday.growth("analytics-trend", project="acme", metric="clicks")
            self.assertTrue(trend.success)

    def test_aggregate_write_through_the_api(self):
        with TemporaryDirectory() as tmp:
            _, monday = self._monday(tmp)
            result = monday.growth("aggregate-write", project="acme")
            self.assertTrue(result.success)
            self.assertIn("counts", result.data["aggregate"])


if __name__ == "__main__":
    unittest.main()
