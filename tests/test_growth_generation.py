"""
Tests for content generation and the weekly package (increment 7).

The properties this subsystem must not lose: generation never bypasses approval,
nothing appears without provenance, brand context is required rather than
optional, variants are genuinely different, and approving a week authorises
exactly the posts in that week and nothing else.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from growth import GrowthStore
from growth.campaign import CampaignStatus
from growth.content import ContentStatus, ContentType
from growth.generation import (
    MIN_ITEMS_FOR_ISSUE,
    PLATFORM_LIMITS,
    ApprovalInbox,
    AssetKind,
    AssetRequestStatus,
    BrandContext,
    ContentPlanner,
    Copywriter,
    EmptyNewsletterError,
    InvalidAssetRequestTransitionError,
    MissingBrandContextError,
    MissingProvenanceError,
    PackageStatus,
    PlannedPost,
    UnsupportedPlatformFormatError,
    WeeklyPackageBuilder,
    adapt,
    assemble_issue,
    brand_context_for,
    carousel_brief,
    check_safety,
    detect_escalations,
    gate_for_review,
    generate_article,
    image_brief,
    make_variants,
    variants_differ,
    video_brief,
)
from growth.generation.models import GeneratedAsset
from monday import Monday, MondayConfig
from monday.project import ProjectRegistry

T0 = datetime(2026, 9, 7, 9, 0, tzinfo=UTC)  # a Monday


def _root(tmp: str, projects: dict[str, str] | None = None) -> Path:
    root = Path(tmp)
    (root / "config").mkdir(parents=True, exist_ok=True)
    registry = ProjectRegistry(root / "config")
    for name, source in (projects or {"acme": "acme-src"}).items():
        (root / source).mkdir(parents=True, exist_ok=True)
        registry.register(name, root / source, overwrite=True)
    return root


def _brand(**kw) -> BrandContext:
    fields = {
        "project": "acme",
        "voice": "Direct, evidence-led, allergic to hype.",
        "tone": "Plain-spoken",
        "style_rules": ("No exclamation marks.",),
        "audience": "VP Talent",
        "personas": ("VP Talent", "Head of Recruiting"),
        "pain_points": ("re-sourcing people they already evaluated",),
        "objective": "Generate 50 demo requests",
        "content_pillars": ("sourcing craft", "hiring data"),
        "ctas": ("Book a demo",),
        "approved_assets": ("assets/logo.svg",),
        "prohibited": ("Unverified hiring statistics",),
        "products": ("Sourcing copilot",),
        "website": "https://example.com",
    }
    fields.update(kw)
    return BrandContext(**fields)


def _planned(**kw) -> PlannedPost:
    fields = {
        "slot": 1,
        "platform": "linkedin",
        "kind": AssetKind.LINKEDIN_POST,
        "campaign": "CAMPAIGN-0001",
        "theme": "sourcing craft",
        "scheduled_at": T0,
        "goal": "demo_request",
        "cta": "Book a demo",
        "recommendation_ids": ["REC-abc"],
        "rationale": "Addresses REC-abc.",
    }
    fields.update(kw)
    return PlannedPost(**fields)


class GenerationCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = _root(self._tmp.name)
        self.store = GrowthStore(self.root)
        self.store.init_workspace("acme")
        self.handle = self.store.open("acme")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _campaign(self, **kw):
        fields = {
            "name": "Launch",
            "objective": "Generate 50 demos",
            "primary_conversion_goal": "demo_request",
            "cta": "Book a demo",
            "theme": "sourcing craft",
        }
        fields.update(kw)
        campaign = self.handle.create_campaign(**fields)
        self.handle.transition_campaign(campaign.id, CampaignStatus.ACTIVE)
        return self.handle.get_campaign(campaign.id)


# ---------------------------------------------------------------------------
# Provenance and brand context
# ---------------------------------------------------------------------------


class TestProvenanceAndContext(unittest.TestCase):
    def test_an_asset_without_provenance_is_refused(self):
        with self.assertRaises(MissingProvenanceError):
            GeneratedAsset(
                id="GEN-1",
                project="acme",
                kind=AssetKind.LINKEDIN_POST,
                title="t",
                platform="linkedin",
                campaign="",
                theme="x",
                cta="c",
                goal="g",
                draft="d",
            )

    def test_a_campaign_alone_is_sufficient_provenance(self):
        asset = GeneratedAsset(
            id="GEN-1",
            project="acme",
            kind=AssetKind.LINKEDIN_POST,
            title="t",
            platform="linkedin",
            campaign="CAMPAIGN-0001",
            theme="x",
            cta="c",
            goal="g",
            draft="d",
        )
        self.assertEqual(asset.campaign, "CAMPAIGN-0001")

    def test_generation_without_brand_voice_is_refused(self):
        with self.assertRaises(MissingBrandContextError):
            Copywriter().draft(_planned(), _brand(voice=""))

    def test_generation_without_an_audience_is_refused(self):
        with self.assertRaises(MissingBrandContextError):
            Copywriter().draft(_planned(), _brand(audience="", personas=()))

    def test_generation_without_an_objective_is_refused(self):
        with self.assertRaises(MissingBrandContextError):
            Copywriter().draft(_planned(), _brand(objective=""))

    def test_the_refusal_names_what_is_missing(self):
        with self.assertRaises(MissingBrandContextError) as ctx:
            Copywriter().draft(_planned(), _brand(voice="", objective=""))
        self.assertIn("brand voice", ctx.exception.missing)
        self.assertIn("campaign objective", ctx.exception.missing)

    def test_a_draft_carries_its_citations(self):
        asset = Copywriter().draft(_planned(), _brand())
        self.assertEqual(asset.recommendation_ids, ["REC-abc"])
        self.assertEqual(asset.campaign, "CAMPAIGN-0001")

    def test_a_draft_uses_the_projects_own_material(self):
        asset = Copywriter().draft(_planned(), _brand())
        self.assertIn("re-sourcing people they already evaluated", asset.draft)
        self.assertIn("Sourcing copilot", asset.draft)


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------


class TestSafety(unittest.TestCase):
    def test_a_fabricated_statistic_is_flagged(self):
        findings = check_safety("87% of customers report faster hiring.", "t", _brand())
        self.assertTrue(findings)
        self.assertTrue(any(f.blocking for f in findings))
        self.assertEqual(findings[0].rule, "fabricated-statistic")

    def test_a_fabricated_testimonial_is_flagged(self):
        findings = check_safety('Customers say: "this changed everything".', "t", _brand())
        self.assertTrue(any(f.rule == "fabricated-testimonial" for f in findings))

    def test_an_unsupported_superlative_is_flagged(self):
        findings = check_safety("Guaranteed to double your pipeline.", "t", _brand())
        self.assertTrue(any(f.blocking for f in findings))

    def test_a_project_prohibited_phrase_is_flagged(self):
        findings = check_safety("Here are some Unverified hiring statistics.", "t", _brand())
        self.assertTrue(any(f.rule == "project-prohibited" for f in findings))

    def test_clean_copy_produces_no_findings(self):
        self.assertEqual(check_safety("We store the person once.", "t", _brand()), [])

    def test_sensitive_categories_escalate(self):
        for text, expected in (
            ("This raises GDPR compliance questions.", "legal"),
            ("Contact jane@example.com for details.", "pii"),
            ("Our clinical treatment data shows.", "medical"),
        ):
            with self.subTest(text=text):
                self.assertIn(expected, detect_escalations(text))

    def test_a_blocked_draft_cannot_pass_the_review_gate(self):
        asset = GeneratedAsset(
            id="GEN-1",
            project="acme",
            kind=AssetKind.LINKEDIN_POST,
            title="t",
            platform="linkedin",
            campaign="CAMPAIGN-0001",
            theme="x",
            cta="c",
            goal="g",
            draft="93% of teams saw results.",
        )
        allowed, reasons = gate_for_review(asset, _brand())
        self.assertFalse(allowed)
        self.assertTrue(reasons)

    def test_the_gate_rechecks_the_current_draft_not_the_stored_findings(self):
        """Text edited in after generation must still be caught."""
        asset = Copywriter().draft(_planned(), _brand())
        self.assertTrue(gate_for_review(asset, _brand())[0])
        asset.draft += "\n\n99% of customers agree."
        self.assertFalse(gate_for_review(asset, _brand())[0])


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


class TestPlanner(GenerationCase):
    def _plan(self, **kw):
        campaign = self._campaign()
        fields = {
            "week_start": T0,
            "cadence": 4,
            "recommendations": [
                {"id": "REC-1", "title": "Do the thing", "priority": "P1", "status": "proposed"},
                {"id": "REC-2", "title": "Other thing", "priority": "P2", "status": "proposed"},
            ],
            "campaigns": [{**campaign.to_dict(), "accepts_content": True}],
            "platforms": ["linkedin", "x"],
        }
        fields.update(kw)
        return ContentPlanner("acme").plan_week(**fields)

    def test_a_plan_produces_the_configured_cadence(self):
        self.assertEqual(len(self._plan(cadence=4).posts), 4)

    def test_a_plan_cites_the_recommendations_it_used(self):
        plan = self._plan()
        self.assertEqual(sorted(plan.cited_recommendations), ["REC-1", "REC-2"])
        for post in plan.posts:
            self.assertTrue(post.recommendation_ids)
            self.assertIn("REC-", post.rationale)

    def test_higher_priority_recommendations_are_used_first(self):
        plan = self._plan()
        self.assertEqual(plan.posts[0].recommendation_ids, ["REC-1"])

    def test_rejected_recommendations_are_not_planned_against(self):
        plan = self._plan(
            recommendations=[
                {"id": "REC-1", "title": "x", "priority": "P1", "status": "rejected"},
                {"id": "REC-2", "title": "y", "priority": "P2", "status": "invalidated"},
            ]
        )
        self.assertEqual(plan.cited_recommendations, [])
        self.assertTrue(any("no Brain recommendation" in p.rationale for p in plan.posts))

    def test_a_plan_without_recommendations_says_so(self):
        plan = self._plan(recommendations=[])
        self.assertTrue(any("no recommendations" in w.lower() for w in plan.warnings))

    def test_zero_cadence_produces_no_plan_and_explains_why(self):
        plan = self._plan(cadence=0)
        self.assertEqual(plan.posts, [])
        self.assertIn("cadence", plan.rationale.lower())

    def test_no_platforms_produces_no_plan(self):
        plan = self._plan(platforms=[])
        self.assertEqual(plan.posts, [])
        self.assertIn("platforms", plan.rationale.lower())

    def test_a_closed_campaign_is_not_planned_into(self):
        campaign = self._campaign()
        plan = self._plan(campaigns=[{**campaign.to_dict(), "accepts_content": False}])
        self.assertEqual(plan.posts, [])
        self.assertIn("campaign", plan.rationale.lower())

    def test_the_week_always_starts_on_monday(self):
        plan = self._plan(week_start=datetime(2026, 9, 10, 15, 0, tzinfo=UTC))  # Thursday
        self.assertEqual(plan.week_start.weekday(), 0)

    def test_planning_is_deterministic(self):
        campaign = self._campaign()
        shared = {
            "campaigns": [{**campaign.to_dict(), "accepts_content": True}],
        }
        self.assertEqual(self._plan(**shared).to_dict(), self._plan(**shared).to_dict())

    def test_experiment_slots_are_allocated_when_experiments_exist(self):
        plan = self._plan(experiments=[{"id": "EXP-1", "hypothesis": "h", "metric": "ctr"}])
        self.assertEqual(plan.experiment_slots, 1)
        self.assertTrue(plan.posts[0].experiment_ids)

    def test_the_mix_is_varied_not_repeated(self):
        kinds = {p.kind for p in self._plan(cadence=4).posts}
        self.assertGreater(len(kinds), 1)


# ---------------------------------------------------------------------------
# Formatter and variants
# ---------------------------------------------------------------------------


class TestFormatter(unittest.TestCase):
    BODY = "First idea here.\n\nSecond supporting beat.\n\nThird closing beat."

    def test_each_platform_gets_a_different_treatment(self):
        bodies = {
            platform: adapt(self.BODY, platform, _brand()).body
            for platform in ("linkedin", "x", "instagram", "tiktok")
        }
        self.assertEqual(len(set(bodies.values())), len(bodies))

    def test_x_is_reduced_to_the_leading_claim(self):
        result = adapt(self.BODY, "x", _brand())
        self.assertIn("First idea here.", result.body)
        self.assertNotIn("Third closing beat", result.body)

    def test_video_platforms_become_scripts_with_a_hook(self):
        result = adapt(self.BODY, "tiktok", _brand())
        self.assertIn("HOOK:", result.body)
        self.assertIn("SCRIPT:", result.body)

    def test_over_length_copy_is_truncated_on_a_word_boundary(self):
        result = adapt("word " * 200, "x", _brand())
        self.assertTrue(result.truncated)
        self.assertLessEqual(len(result.body), PLATFORM_LIMITS["x"])
        self.assertTrue(result.body.endswith("…"))

    def test_an_unknown_platform_is_refused(self):
        with self.assertRaises(UnsupportedPlatformFormatError):
            adapt(self.BODY, "myspace", _brand())

    def test_formatting_records_what_it_did(self):
        self.assertTrue(adapt(self.BODY, "linkedin", _brand()).applied)

    def test_variants_are_separate_assets_sharing_a_group(self):
        source = Copywriter().draft(_planned(), _brand())
        variants = make_variants(source, ["linkedin", "x", "instagram"], _brand())
        self.assertEqual(len(variants), 3)
        self.assertEqual(len({v.variant_group_id for v in variants}), 1)
        self.assertEqual(len({v.id for v in variants}), 3)

    def test_variants_genuinely_differ(self):
        source = Copywriter().draft(_planned(), _brand())
        variants = make_variants(source, ["linkedin", "x", "tiktok"], _brand())
        self.assertTrue(variants_differ(variants))

    def test_a_brief_keeps_the_kind_the_planner_chose(self):
        """The formatter must not silently overrule the week's content mix."""
        source = Copywriter().draft(_planned(kind=AssetKind.CAROUSEL_BRIEF), _brand())
        for variant in make_variants(source, ["linkedin", "x"], _brand()):
            self.assertIs(variant.kind, AssetKind.CAROUSEL_BRIEF)

    def test_a_generic_social_post_takes_the_platform_shape(self):
        source = Copywriter().draft(_planned(kind=AssetKind.LINKEDIN_POST), _brand())
        kinds = {v.kind for v in make_variants(source, ["linkedin", "x"], _brand())}
        self.assertEqual(kinds, {AssetKind.LINKEDIN_POST, AssetKind.X_POST})

    def test_a_title_does_not_repeat_the_theme(self):
        asset = Copywriter().draft(_planned(theme="sourcing craft"), _brand())
        self.assertEqual(asset.title, "Sourcing Craft")

    def test_variants_inherit_provenance(self):
        source = Copywriter().draft(_planned(), _brand())
        for variant in make_variants(source, ["linkedin", "x"], _brand()):
            self.assertEqual(variant.recommendation_ids, ["REC-abc"])


# ---------------------------------------------------------------------------
# Long-form, newsletter, briefs
# ---------------------------------------------------------------------------


class TestLongFormAndBriefs(unittest.TestCase):
    def test_an_article_carries_an_outline(self):
        asset = generate_article(_planned(kind=AssetKind.BLOG_ARTICLE), _brand())
        self.assertIn("## The problem", asset.draft)
        self.assertIs(asset.kind, AssetKind.BLOG_ARTICLE)

    def test_an_seo_article_states_no_research_was_done(self):
        asset = generate_article(
            _planned(kind=AssetKind.SEO_ARTICLE), _brand(), keyword="ai sourcing"
        )
        self.assertIs(asset.kind, AssetKind.SEO_ARTICLE)
        self.assertIn("No keyword research was performed", asset.draft)

    def test_a_newsletter_needs_real_items(self):
        with self.assertRaises(EmptyNewsletterError):
            assemble_issue(_brand(), "W37", [{"title": "only one"}], campaign="C-1")

    def test_a_newsletter_points_at_its_sources(self):
        asset = assemble_issue(
            _brand(),
            "W37",
            [
                {"title": "A", "content_id": "CONTENT-0001"},
                {"title": "B", "content_id": "CONTENT-0002"},
            ],
            campaign="CAMPAIGN-0001",
        )
        self.assertIn("CONTENT-0001", asset.draft)
        self.assertIn("CONTENT-0002", asset.draft)

    def test_a_newsletter_without_provenance_is_refused(self):
        with self.assertRaises(MissingProvenanceError):
            assemble_issue(
                _brand(),
                "W37",
                [{"title": "A"}, {"title": "B"}],
            )

    def test_minimum_issue_size_is_enforced(self):
        self.assertEqual(MIN_ITEMS_FOR_ISSUE, 2)

    def test_an_image_brief_uses_only_approved_assets(self):
        source = Copywriter().draft(_planned(), _brand())
        brief = image_brief(source, _brand())
        self.assertEqual(brief.reference_assets, ["assets/logo.svg"])
        self.assertTrue(any("Do not source imagery" in c for c in brief.brand_constraints))

    def test_a_carousel_brief_breaks_the_draft_into_slides(self):
        source = Copywriter().draft(_planned(), _brand())
        brief = carousel_brief(source, _brand(), slides=3)
        self.assertIn("Slide 1:", brief.brief)

    def test_a_video_brief_separates_the_hook(self):
        source = Copywriter().draft(_planned(), _brand())
        brief = video_brief(source, _brand())
        self.assertIn("HOOK", brief.brief)
        self.assertIn("Word budget", brief.brief)

    def test_asset_request_lifecycle(self):
        source = Copywriter().draft(_planned(), _brand())
        request = image_brief(source, _brand())
        self.assertIs(request.status, AssetRequestStatus.REQUESTED)
        request.apply_transition(AssetRequestStatus.IN_PROGRESS, "human:j", "started", "now")
        request.apply_transition(AssetRequestStatus.READY_FOR_REVIEW, "human:j", "done", "now")
        request.apply_transition(AssetRequestStatus.APPROVED, "human:j", "ok", "now")
        self.assertIs(request.status, AssetRequestStatus.APPROVED)

    def test_an_illegal_asset_request_transition_raises(self):
        source = Copywriter().draft(_planned(), _brand())
        request = image_brief(source, _brand())
        with self.assertRaises(InvalidAssetRequestTransitionError):
            request.apply_transition(AssetRequestStatus.APPROVED, "human:j", "skip", "now")

    def test_a_brief_without_provenance_is_refused(self):
        orphan = GeneratedAsset(
            id="GEN-1",
            project="acme",
            kind=AssetKind.LINKEDIN_POST,
            title="t",
            platform="linkedin",
            campaign="CAMPAIGN-0001",
            theme="x",
            cta="c",
            goal="g",
            draft="d",
        )
        orphan.campaign = ""
        orphan.recommendation_ids = []
        with self.assertRaises(MissingProvenanceError):
            image_brief(orphan, _brand())


# ---------------------------------------------------------------------------
# Weekly package
# ---------------------------------------------------------------------------


class TestWeeklyPackage(GenerationCase):
    def _package(self, cadence: int = 3, **kw):
        campaign = self._campaign()
        recommendations = [
            {
                "id": "REC-1",
                "title": "Do the thing",
                "priority": "P1",
                "status": "proposed",
                "success_metric": "conversions",
                "falsifier": "f",
            },
        ]
        plan = ContentPlanner("acme").plan_week(
            week_start=T0,
            cadence=cadence,
            recommendations=recommendations,
            campaigns=[{**campaign.to_dict(), "accepts_content": True}],
            platforms=["linkedin"],
        )
        builder = WeeklyPackageBuilder(self.handle)
        return builder, builder.build(
            brand=_brand(),
            plan=plan,
            recommendations=recommendations,
            experiments=[],
            now=T0,
            **kw,
        )

    def test_every_post_becomes_a_content_item_in_draft(self):
        _, package = self._package()
        self.assertEqual(len(package.posts), 3)
        for post in package.posts:
            item = self.handle.get_content(post.content_id)
            self.assertIs(item.status, ContentStatus.DRAFT)
            self.assertFalse(item.is_approved)

    def test_generated_items_carry_their_provenance(self):
        _, package = self._package()
        item = self.handle.get_content(package.posts[0].content_id)
        self.assertTrue(item.metadata["generated"])
        self.assertEqual(item.metadata["recommendation_ids"], ["REC-1"])
        self.assertTrue(item.metadata["rationale"])

    def test_the_package_covers_a_monday_to_sunday_week(self):
        _, package = self._package()
        self.assertTrue(package.week_start.startswith("2026-09-07"))
        self.assertTrue(package.week_end.startswith("2026-09-13"))

    def test_the_package_carries_its_reasoning_and_supporting_recommendations(self):
        _, package = self._package()
        self.assertTrue(package.reasoning_summary)
        self.assertEqual(package.supporting_recommendations[0]["id"], "REC-1")

    def test_expected_outcomes_are_not_quantified(self):
        _, package = self._package()
        self.assertFalse(package.expected_outcomes["quantified"])
        self.assertIn("invented", package.expected_outcomes["reason"])

    def test_effort_is_estimated_with_a_stated_basis(self):
        _, package = self._package()
        self.assertGreater(package.estimated_effort["total_minutes"], 0)
        self.assertIn("EFFORT_MINUTES", package.estimated_effort["basis"])

    def test_a_package_round_trips(self):
        builder, package = self._package()
        self.assertEqual(builder.load(package.id).to_dict(), package.to_dict())

    def test_multi_platform_produces_a_variant_per_platform(self):
        campaign = self._campaign()
        plan = ContentPlanner("acme").plan_week(
            week_start=T0,
            cadence=2,
            recommendations=[{"id": "REC-1", "title": "x", "priority": "P1", "status": "proposed"}],
            campaigns=[{**campaign.to_dict(), "accepts_content": True}],
            platforms=["linkedin", "x"],
        )
        builder = WeeklyPackageBuilder(self.handle)
        package = builder.build(
            brand=_brand(),
            plan=plan,
            recommendations=[],
            experiments=[],
            now=T0,
            multi_platform=True,
        )
        self.assertEqual(len(package.posts), 4)
        groups = {p.variant_group_id for p in package.posts}
        self.assertEqual(len(groups), 2)

    def test_package_lifecycle(self):
        _, package = self._package()
        self.assertIs(package.status, PackageStatus.DRAFT)
        package.apply_transition(PackageStatus.READY_FOR_REVIEW, "human:j", "r", T0)
        package.apply_transition(PackageStatus.APPROVED, "human:j", "ok", T0)
        self.assertIs(package.status, PackageStatus.APPROVED)


# ---------------------------------------------------------------------------
# Approval inbox
# ---------------------------------------------------------------------------


class TestApprovalInbox(GenerationCase):
    def _package(self, cadence: int = 3):
        campaign = self._campaign()
        plan = ContentPlanner("acme").plan_week(
            week_start=T0,
            cadence=cadence,
            recommendations=[{"id": "REC-1", "title": "x", "priority": "P1", "status": "proposed"}],
            campaigns=[{**campaign.to_dict(), "accepts_content": True}],
            platforms=["linkedin"],
        )
        builder = WeeklyPackageBuilder(self.handle)
        return builder, builder.build(
            brand=_brand(), plan=plan, recommendations=[], experiments=[], now=T0
        )

    def test_the_inbox_shows_everything_a_reviewer_needs(self):
        self._package(cadence=1)
        row = ApprovalInbox(self.handle).items()[0]
        payload = row.to_dict()
        for key in (
            "content_id",
            "campaign",
            "platform",
            "account_reference",
            "caption",
            "cta",
            "link",
            "scheduled_at",
            "expected_goal",
            "approval_state",
        ):
            self.assertIn(key, payload)

    def test_the_account_reference_is_a_placeholder_when_nothing_is_bound(self):
        self._package(cadence=1)
        row = ApprovalInbox(self.handle).items()[0]
        self.assertIn("no account bound", row.account_reference)

    def test_the_inbox_groups_four_ways(self):
        self._package()
        grouped = ApprovalInbox(self.handle).grouped()
        for key in ("by_campaign", "by_platform", "by_week", "by_priority"):
            self.assertIn(key, grouped)

    def test_approving_a_week_approves_each_post_individually(self):
        builder, package = self._package(cadence=3)
        result = ApprovalInbox(self.handle).approve_week(package, by="human:j")
        self.assertEqual(result["approved_count"], 3)
        for post in package.posts:
            item = self.handle.get_content(post.content_id)
            self.assertTrue(item.is_approved)
            self.assertTrue(item.approved_fingerprint)

    def test_approving_a_week_authorises_nothing_outside_it(self):
        builder, package = self._package(cadence=2)
        outsider = self.handle.create_content(
            platform="linkedin", copy="not in the package", campaign=package.posts[0].campaign
        )
        ApprovalInbox(self.handle).approve_week(package, by="human:j")
        self.assertFalse(self.handle.get_content(outsider.id).is_approved)

    def test_a_selected_subset_approves_only_those_posts(self):
        builder, package = self._package(cadence=3)
        target = package.posts[0].content_id
        result = ApprovalInbox(self.handle).approve_week(package, by="human:j", only=[target])
        self.assertEqual(result["approved_count"], 1)
        self.assertTrue(self.handle.get_content(target).is_approved)
        self.assertFalse(self.handle.get_content(package.posts[1].content_id).is_approved)

    def test_a_blocked_post_is_skipped_with_a_reason_not_swept_along(self):
        builder, package = self._package(cadence=2)
        blocked = package.posts[0]
        item = self.handle.get_content(blocked.content_id)
        item.warnings = ["fabricated-statistic: 93% of teams"]
        self.handle.save_content(item)
        blocked.blocked = True

        result = ApprovalInbox(self.handle).approve_week(package, by="human:j")
        self.assertEqual(result["approved_count"], 1)
        self.assertEqual(result["skipped_count"], 1)
        self.assertFalse(self.handle.get_content(blocked.content_id).is_approved)

    def test_rescheduling_resets_approval(self):
        builder, package = self._package(cadence=1)
        inbox = ApprovalInbox(self.handle)
        inbox.approve_week(package, by="human:j")
        content_id = package.posts[0].content_id
        self.assertTrue(self.handle.get_content(content_id).is_approved)

        result = inbox.reschedule(content_id, T0 + timedelta(days=3))
        self.assertFalse(result["is_approved"])
        self.assertIn("Approval reset", result["note"])

    def test_request_changes_and_reject(self):
        builder, package = self._package(cadence=2)
        inbox = ApprovalInbox(self.handle)
        first, second = package.posts[0].content_id, package.posts[1].content_id
        inbox.submit_for_review(first)
        inbox.request_changes(first, "human:j", "tone")
        self.assertIs(self.handle.get_content(first).status, ContentStatus.CHANGES_REQUESTED)
        inbox.reject(second, "human:j", "not now")
        self.assertIs(self.handle.get_content(second).status, ContentStatus.CANCELLED)

    def test_a_blocked_item_cannot_be_submitted_for_review(self):
        self._package(cadence=1)
        item = self.handle.list_content()[0]
        item.warnings = ["fabricated-statistic"]
        self.handle.save_content(item)
        result = ApprovalInbox(self.handle).submit_for_review(item.id)
        self.assertFalse(result["ok"])

    def test_package_status_reflects_partial_approval(self):
        builder, package = self._package(cadence=3)
        inbox = ApprovalInbox(self.handle)
        inbox.approve_week(package, by="human:j", only=[package.posts[0].content_id])
        updated = inbox.mark_package_reviewed(package, builder, T0)
        self.assertIs(updated.status, PackageStatus.PARTIALLY_APPROVED)


# ---------------------------------------------------------------------------
# Isolation, API, and the no-bypass rule
# ---------------------------------------------------------------------------


class TestIsolationAndApi(unittest.TestCase):
    def test_packages_are_workspace_scoped(self):
        with TemporaryDirectory() as tmp:
            root = _root(tmp, {"alpha": "a", "beta": "b"})
            store = GrowthStore(root)
            store.init_workspace("alpha")
            store.init_workspace("beta")
            alpha = store.open("alpha")
            campaign = alpha.create_campaign(name="A")
            alpha.transition_campaign(campaign.id, CampaignStatus.ACTIVE)
            plan = ContentPlanner("alpha").plan_week(
                week_start=T0,
                cadence=1,
                recommendations=[],
                campaigns=[{**alpha.get_campaign(campaign.id).to_dict(), "accepts_content": True}],
                platforms=["linkedin"],
            )
            WeeklyPackageBuilder(alpha).build(
                brand=_brand(project="alpha"),
                plan=plan,
                recommendations=[],
                experiments=[],
                now=T0,
            )
            self.assertEqual(WeeklyPackageBuilder(store.open("beta")).list_packages(), [])
            self.assertEqual(ApprovalInbox(store.open("beta")).items(), [])

    def test_brand_context_is_assembled_from_the_workspace(self):
        with TemporaryDirectory() as tmp:
            root = _root(tmp)
            store = GrowthStore(root)
            store.init_workspace("acme")
            handle = store.open("acme")
            workspace = handle.read()
            workspace.brand.voice = "Direct."
            workspace.audience.personas = ["VP Talent"]
            workspace.marketing.objectives = ["50 demos"]
            handle.write(workspace)

            brand = brand_context_for(handle)
            self.assertEqual(brand.voice, "Direct.")
            brand.validate()

    def test_generation_through_the_api_creates_only_drafts(self):
        with TemporaryDirectory() as tmp:
            root = _root(tmp)
            monday = Monday(MondayConfig(project_root=root))
            monday.growth("workspace-init", project="acme")
            monday.growth("seed-demo", project="acme")
            monday.growth(
                "onboard",
                project="acme",
                brand_voice="Direct and evidence-led.",
                objectives=["Generate 50 demos"],
                audience_personas=["VP Talent"],
                brand_assets=["assets/logo.svg"],
                prohibited_content=["Unverified stats"],
                cadence_per_week=3,
                platforms=[{"platform": "linkedin", "account_label": "Acme page"}],
                weekly_review_day="sunday",
                weekly_review_hour_utc=17,
            )
            result = monday.growth("generate-week", project="acme", week_start=T0, mode="template")
            self.assertTrue(result.success)
            package = result.data["package"]
            self.assertTrue(package["posts"])
            self.assertIn("nothing is approved", result.message.lower())

            listed = monday.growth("content-list", project="acme", status="draft")
            self.assertGreaterEqual(listed.data["count"], len(package["posts"]))

    def test_generation_without_onboarding_returns_a_failed_response(self):
        with TemporaryDirectory() as tmp:
            root = _root(tmp)
            monday = Monday(MondayConfig(project_root=root))
            monday.growth("workspace-init", project="acme")
            result = monday.growth("generate-week", project="acme", week_start=T0, mode="template")
            self.assertFalse(result.success)
            self.assertIn("brand context", result.message.lower())

    def test_no_generated_item_can_skip_review(self):
        """There is no path from generation to approved without a human action."""
        with TemporaryDirectory() as tmp:
            root = _root(tmp)
            store = GrowthStore(root)
            store.init_workspace("acme")
            handle = store.open("acme")
            campaign = handle.create_campaign(name="Launch")
            handle.transition_campaign(campaign.id, CampaignStatus.ACTIVE)
            plan = ContentPlanner("acme").plan_week(
                week_start=T0,
                cadence=2,
                recommendations=[],
                campaigns=[{**handle.get_campaign(campaign.id).to_dict(), "accepts_content": True}],
                platforms=["linkedin"],
            )
            package = WeeklyPackageBuilder(handle).build(
                brand=_brand(), plan=plan, recommendations=[], experiments=[], now=T0
            )
            for post in package.posts:
                item = handle.get_content(post.content_id)
                self.assertIs(item.status, ContentStatus.DRAFT)
                self.assertEqual(item.approved_fingerprint, "")

    def test_content_types_map_correctly(self):
        with TemporaryDirectory() as tmp:
            root = _root(tmp)
            store = GrowthStore(root)
            store.init_workspace("acme")
            handle = store.open("acme")
            campaign = handle.create_campaign(name="Launch")
            handle.transition_campaign(campaign.id, CampaignStatus.ACTIVE)
            plan = ContentPlanner("acme").plan_week(
                week_start=T0,
                cadence=3,
                recommendations=[],
                campaigns=[{**handle.get_campaign(campaign.id).to_dict(), "accepts_content": True}],
                platforms=["linkedin"],
            )
            WeeklyPackageBuilder(handle).build(
                brand=_brand(), plan=plan, recommendations=[], experiments=[], now=T0
            )
            types = {i.content_type for i in handle.list_content()}
            self.assertTrue(types.issubset(set(ContentType)))


if __name__ == "__main__":
    unittest.main()
