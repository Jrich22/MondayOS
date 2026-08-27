"""
Tests for the Content Library, onboarding, and demo seeding (increment 4).

The library is a query layer over existing content storage, so a central concern
here is proving it writes nothing and that cataloguing cannot disturb approvals.
"""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from growth import GrowthStore
from growth.campaign import CampaignStatus
from growth.content import ContentStatus, ContentType
from growth.demo import SYNTHETIC_FLAG, is_synthetic, seed_workspace
from growth.library import ContentLibrary
from growth.onboarding import (
    REQUIRED_STEPS,
    AccountLabelError,
    PlatformIntent,
    WeeklyReview,
    evaluate_readiness,
)
from growth.service import GrowthService
from monday import Monday, MondayConfig
from monday.project import ProjectRegistry

EPOCH = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


def _root(tmp: str, projects: dict[str, str] | None = None) -> Path:
    root = Path(tmp)
    (root / "config").mkdir(parents=True, exist_ok=True)
    registry = ProjectRegistry(root / "config")
    for name, source in (projects or {"acme": "acme-src"}).items():
        (root / source).mkdir(parents=True, exist_ok=True)
        registry.register(name, root / source, overwrite=True)
    return root


class LibraryCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = _root(self._tmp.name)
        self.store = GrowthStore(self.root)
        self.store.init_workspace("acme")
        self.handle = self.store.open("acme")
        self.library = ContentLibrary(self.handle)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _item(self, **kw):
        fields = {
            "platform": "linkedin",
            "account": "acct-1",
            "copy": "body text",
            "cta": "Book a demo",
            "destination_url": "https://example.com",
            "expected_goal": "g",
            "expected_audience": "a",
            "scheduled_at": EPOCH,
        }
        fields.update(kw)
        return self.handle.create_content(**fields)


# ---------------------------------------------------------------------------
# The library stores nothing
# ---------------------------------------------------------------------------


class TestLibraryIsAQueryLayer(LibraryCase):
    def test_library_writes_no_files(self):
        self._item(title="A")
        before = sorted(p.name for p in (self.handle.path / "content").glob("*"))
        mtimes = {p.name: p.stat().st_mtime_ns for p in (self.handle.path / "content").glob("*")}

        self.library.all()
        self.library.search(text="body")
        self.library.summary()
        self.library.highest_performing()

        after = sorted(p.name for p in (self.handle.path / "content").glob("*"))
        self.assertEqual(before, after)
        for path in (self.handle.path / "content").glob("*"):
            self.assertEqual(mtimes[path.name], path.stat().st_mtime_ns)

    def test_library_reads_the_same_records_the_store_wrote(self):
        item = self._item(title="Exactly this")
        entry = self.library.all()[0]
        self.assertEqual(entry.content_id, item.id)
        self.assertEqual(entry.body, item.copy)
        self.assertEqual(entry.title, "Exactly this")

    def test_cataloguing_does_not_invalidate_an_approval(self):
        """Library metadata is outside the fingerprint, by construction."""
        item = self._item()
        self.handle.transition_content(item.id, ContentStatus.AI_REVIEW)
        self.handle.transition_content(item.id, ContentStatus.READY_FOR_REVIEW)
        approved = self.handle.approve_content(item.id, approved_by="human:j")
        before = approved.current_fingerprint()

        updated = self.handle.update_content(
            item.id,
            content_type=ContentType.CAROUSEL,
            title="Catalogued",
            themes=["ai"],
            audience="VPs",
            variant_group_id="vg-1",
            reuse_eligible=True,
        )
        self.assertEqual(updated.current_fingerprint(), before)
        self.assertTrue(updated.is_approved)
        self.assertIs(updated.status, ContentStatus.APPROVED)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


class TestLibraryQueries(LibraryCase):
    def setUp(self) -> None:
        super().setUp()
        self.campaign = self.handle.create_campaign(name="Launch")
        self._item(
            title="Sourcing craft",
            themes=["sourcing"],
            campaign=self.campaign.id,
            content_type=ContentType.CAROUSEL,
            tags=["q3"],
            reuse_eligible=True,
        )
        self._item(
            title="Hiring data",
            themes=["data"],
            platform="x",
            content_type=ContentType.BLOG_ARTICLE,
        )
        self._item(
            title="Customer proof",
            themes=["sourcing", "proof"],
            content_type=ContentType.EDUCATIONAL_POST,
        )

    def test_by_theme(self):
        self.assertEqual(len(self.library.by_theme("sourcing")), 2)
        self.assertEqual(len(self.library.by_theme("data")), 1)
        self.assertEqual(self.library.by_theme("nonexistent"), [])

    def test_by_campaign(self):
        self.assertEqual(len(self.library.by_campaign(self.campaign.id)), 1)

    def test_by_platform(self):
        self.assertEqual(len(self.library.by_platform("linkedin")), 2)
        self.assertEqual(len(self.library.by_platform("x")), 1)

    def test_by_content_type(self):
        self.assertEqual(len(self.library.search(content_type=ContentType.CAROUSEL)), 1)

    def test_text_search_spans_title_body_cta_tags_themes(self):
        self.assertEqual(len(self.library.search(text="customer")), 1)
        self.assertEqual(len(self.library.search(text="book a demo")), 3)
        self.assertEqual(len(self.library.search(text="q3")), 1)

    def test_filters_narrow_and_never_widen(self):
        both = self.library.search(theme="sourcing", platform="linkedin")
        self.assertEqual(len(both), 2)
        self.assertEqual(self.library.search(theme="data", platform="linkedin"), [])

    def test_reusable(self):
        self.assertEqual(len(self.library.reusable()), 1)

    def test_not_reused_since_includes_never_reused(self):
        stale = self.library.not_reused_since(days=30, now=EPOCH)
        self.assertEqual(len(stale), 1)

    def test_not_reused_since_excludes_recently_reused(self):
        item = self.library.reusable()[0]
        self.handle.update_content(item.content_id, last_reused_at=EPOCH - timedelta(days=1))
        self.assertEqual(self.library.not_reused_since(days=30, now=EPOCH), [])

    def test_themes_counts(self):
        self.assertEqual(self.library.themes(), {"data": 1, "proof": 1, "sourcing": 2})

    def test_summary(self):
        s = self.library.summary()
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["reusable"], 1)
        self.assertEqual(s["by_platform"], {"linkedin": 2, "x": 1})

    def test_highest_performing_declares_its_basis_when_no_metrics_exist(self):
        entries, basis = self.library.highest_performing()
        self.assertEqual(basis, "recency-fallback")
        self.assertEqual(len(entries), 3)

    def test_library_is_project_scoped(self):
        with TemporaryDirectory() as tmp:
            root = _root(tmp, {"alpha": "a", "beta": "b"})
            store = GrowthStore(root)
            store.init_workspace("alpha")
            store.init_workspace("beta")
            store.open("alpha").create_content(platform="linkedin", copy="alpha only")
            self.assertEqual(len(ContentLibrary(store.open("alpha")).all()), 1)
            self.assertEqual(ContentLibrary(store.open("beta")).all(), [])


# ---------------------------------------------------------------------------
# Platform variants
# ---------------------------------------------------------------------------


class TestVariants(LibraryCase):
    def test_variants_are_separate_items_sharing_a_group(self):
        a = self._item(platform="linkedin", copy="long form", variant_group_id="vg-1")
        b = self._item(platform="x", copy="short form", variant_group_id="vg-1")

        self.assertNotEqual(a.id, b.id)
        self.assertEqual(len(self.library.variants("vg-1")), 2)

    def test_approving_one_variant_does_not_approve_its_sibling(self):
        """ADR-013: approval binds one platform, one account, one copy."""
        a = self._item(platform="linkedin", copy="long form", variant_group_id="vg-1")
        b = self._item(platform="x", copy="short form", variant_group_id="vg-1")

        for cid in (a.id,):
            self.handle.transition_content(cid, ContentStatus.AI_REVIEW)
            self.handle.transition_content(cid, ContentStatus.READY_FOR_REVIEW)
            self.handle.approve_content(cid, approved_by="human:j")

        self.assertTrue(self.handle.get_content(a.id).is_approved)
        self.assertFalse(self.handle.get_content(b.id).is_approved)

    def test_variants_have_distinct_fingerprints(self):
        a = self._item(platform="linkedin", copy="long form", variant_group_id="vg-1")
        b = self._item(platform="x", copy="short form", variant_group_id="vg-1")
        self.assertNotEqual(a.current_fingerprint(), b.current_fingerprint())

    def test_empty_group_returns_nothing(self):
        self._item(variant_group_id="")
        self.assertEqual(self.library.variants(""), [])


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------


class TestOnboarding(LibraryCase):
    def _complete(self, service: GrowthService) -> dict:
        return service.onboard(
            "acme",
            brand_voice="Direct and evidence-led.",
            objectives=["50 demos per quarter"],
            audience_personas=["VP Talent"],
            brand_assets=["assets/logo.svg"],
            prohibited_content=["Unverified statistics"],
            cadence_per_week=4,
            platforms=[{"platform": "linkedin", "account_label": "Acme company page"}],
            weekly_review_day="sunday",
            weekly_review_hour_utc=17,
        )

    def test_a_fresh_workspace_is_not_ready(self):
        service = GrowthService(self.root)
        status = service.onboarding_status("acme")
        self.assertFalse(status["growth_ready_for_planning"])
        self.assertEqual(set(status["missing_steps"]), set(REQUIRED_STEPS))

    def test_completing_onboarding_sets_planning_ready(self):
        status = self._complete(GrowthService(self.root))
        self.assertTrue(status["growth_ready_for_planning"])
        self.assertEqual(status["missing_steps"], [])

    def test_onboarding_never_sets_real_publishing_ready(self):
        """The distance between the two flags is the honest state of the system."""
        status = self._complete(GrowthService(self.root))
        self.assertFalse(status["growth_ready_for_real_publishing"])

    def test_no_code_path_in_growth_sets_real_publishing_ready(self):
        import subprocess

        hits = subprocess.run(
            ["grep", "-rn", "growth_ready_for_real_publishing", "growth/"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parent.parent,
        ).stdout
        assignments = [
            line
            for line in hits.splitlines()
            if "growth_ready_for_real_publishing = True" in line
            or "growth_ready_for_real_publishing=True" in line
        ]
        self.assertEqual(assignments, [], f"something claims real publishing readiness: {hits}")

    def test_partial_onboarding_reports_exactly_what_is_missing(self):
        service = GrowthService(self.root)
        status = service.onboard("acme", brand_voice="Direct.", cadence_per_week=3)
        self.assertFalse(status["growth_ready_for_planning"])
        self.assertNotIn("brand_voice", status["missing_steps"])
        self.assertNotIn("cadence", status["missing_steps"])
        self.assertIn("platforms", status["missing_steps"])

    def test_platform_intents_record_labels_not_credentials(self):
        status = self._complete(GrowthService(self.root))
        intent = status["platform_intents"][0]
        self.assertEqual(set(intent), {"platform", "account_label"})
        self.assertNotIn("secret_name", intent)
        self.assertNotIn("account_id", intent)

    def test_a_credential_shaped_account_label_is_refused(self):
        for bad in (
            "sk-abcdefghijklmnopqrstuvwx",
            "my api_key for linkedin",
            "ghp_aaaaaaaaaaaaaaaaaaaaaaaaa",
            "x" * 45,
        ):
            with self.subTest(label=bad), self.assertRaises(AccountLabelError):
                PlatformIntent(platform="linkedin", account_label=bad)

    def test_ordinary_account_labels_are_accepted(self):
        for good in ("Acme company page", "@acme", "Acme HQ - marketing"):
            with self.subTest(label=good):
                self.assertTrue(PlatformIntent(platform="linkedin", account_label=good))

    def test_weekly_review_validates_its_inputs(self):
        self.assertEqual(WeeklyReview(weekday="Monday").weekday, "monday")
        with self.assertRaises(ValueError):
            WeeklyReview(weekday="funday")
        with self.assertRaises(ValueError):
            WeeklyReview(hour_utc=99)

    def test_readiness_reads_existing_workspace_sections(self):
        """Onboarding does not duplicate brand/audience/objectives storage."""
        workspace = self.handle.read()
        workspace.brand.voice = "Direct."
        workspace.audience.personas = ["VP Talent"]
        workspace.marketing.objectives = ["50 demos"]
        self.handle.write(workspace)

        satisfied, _ = evaluate_readiness(self.handle.read())
        for step in ("brand_voice", "audience", "objectives"):
            self.assertIn(step, satisfied)


# ---------------------------------------------------------------------------
# Demo data
# ---------------------------------------------------------------------------


class TestDemoData(LibraryCase):
    def test_seeding_is_deterministic(self):
        with TemporaryDirectory() as tmp_a, TemporaryDirectory() as tmp_b:
            summaries = []
            for tmp in (tmp_a, tmp_b):
                root = _root(tmp)
                store = GrowthStore(root)
                store.init_workspace("acme")
                summaries.append(seed_workspace(store.open("acme")))
            self.assertEqual(summaries[0], summaries[1])

    def test_every_seeded_record_is_marked_synthetic(self):
        seed_workspace(self.handle)
        for campaign in self.handle.list_campaigns():
            self.assertTrue(is_synthetic(campaign.to_dict()), campaign.id)
        for item in self.handle.list_content():
            self.assertTrue(is_synthetic(item.to_dict()), item.id)
        self.assertTrue(self.handle.read().metadata.get(SYNTHETIC_FLAG))

    def test_demo_creates_no_publications_and_no_bindings(self):
        seed_workspace(self.handle)
        for item in self.handle.list_content():
            self.assertIsNone(item.publication)
            self.assertIs(item.status, ContentStatus.DRAFT)
        self.assertEqual(self.handle.read().bindings, [])

    def test_demo_creates_platform_variants_of_one_idea(self):
        summary = seed_workspace(self.handle)
        group = summary["variant_groups"][0]
        variants = ContentLibrary(self.handle).variants(group)
        self.assertEqual(len(variants), 2)
        self.assertNotEqual(variants[0].body, variants[1].body)
        self.assertNotEqual(variants[0].platform, variants[1].platform)

    def test_demo_does_not_make_a_project_publish_ready(self):
        seed_workspace(self.handle)
        status = GrowthService(self.root).onboarding_status("acme")
        self.assertFalse(status["growth_ready_for_real_publishing"])

    def test_demo_campaign_is_active_and_holds_its_content(self):
        summary = seed_workspace(self.handle)
        launch = self.handle.get_campaign(summary["campaigns"][0])
        self.assertIs(launch.status, CampaignStatus.ACTIVE)
        self.assertTrue(launch.content_item_ids)

    def test_demo_data_carries_no_secret_shaped_values(self):
        seed_workspace(self.handle)
        for path in (self.root / "growth").rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8").lower()
                for marker in ("api_key", "secret_name", "bearer ", "sk-", "ghp_"):
                    self.assertNotIn(marker, text, f"{marker} in {path}")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class TestLibraryApi(unittest.TestCase):
    def test_library_and_onboarding_through_monday_growth(self):
        with TemporaryDirectory() as tmp:
            root = _root(tmp)
            monday = Monday(MondayConfig(project_root=root))
            monday.growth("workspace-init", project="acme")
            seeded = monday.growth("seed-demo", project="acme")
            self.assertTrue(seeded.success)
            self.assertTrue(seeded.data["demo"]["synthetic"])

            summary = monday.growth("library-summary", project="acme")
            self.assertEqual(summary.data["summary"]["total"], 4)

            found = monday.growth("library-search", project="acme", theme="sourcing craft")
            self.assertEqual(found.data["count"], 2)

            top = monday.growth("library-top", project="acme")
            self.assertEqual(top.data["basis"], "recency-fallback")

            status = monday.growth("onboarding-status", project="acme")
            self.assertFalse(status.data["onboarding"]["growth_ready_for_real_publishing"])

    def test_library_payloads_never_carry_a_credential(self):
        with TemporaryDirectory() as tmp:
            root = _root(tmp)
            monday = Monday(MondayConfig(project_root=root))
            monday.growth("workspace-init", project="acme")
            monday.growth(
                "bind",
                project="acme",
                platform="linkedin",
                account_id="acct-1",
                secret_name="LINKEDIN_TOKEN",
            )
            monday.growth("seed-demo", project="acme")
            payload = json.dumps(monday.growth("library-search", project="acme").data)
            self.assertNotIn("LINKEDIN_TOKEN", payload)


if __name__ == "__main__":
    unittest.main()
