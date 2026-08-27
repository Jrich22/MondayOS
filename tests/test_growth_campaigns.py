"""
Tests for Growth campaigns (increment 4).

Campaign lifecycle, storage, per-workspace isolation, and the rule that content
never drifts between campaigns or across projects.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from growth import GrowthStore
from growth.campaign import OPEN_STATES, Campaign, CampaignStatus
from growth.content import ContentStatus
from growth.errors import (
    CampaignNotFoundError,
    CrossCampaignError,
    InvalidTransitionError,
)
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


class CampaignCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = _root(self._tmp.name)
        self.store = GrowthStore(self.root)
        self.store.init_workspace("acme")
        self.handle = self.store.open("acme")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _campaign(self, **kw) -> Campaign:
        fields = {"objective": "50 demos", "theme": "sourcing", "channels": ["linkedin"]}
        fields.update(kw)
        return self.handle.create_campaign(name=fields.pop("name", "Launch"), **fields)

    def _content(self, **kw):
        fields = {
            "platform": "linkedin",
            "account": "acct-1",
            "copy": "hello",
            "cta": "Book a demo",
            "destination_url": "https://example.com",
            "expected_goal": "g",
            "expected_audience": "a",
            "scheduled_at": EPOCH,
        }
        fields.update(kw)
        return self.handle.create_content(**fields)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestCampaignLifecycle(CampaignCase):
    def test_campaign_starts_in_draft_with_an_audit_record(self):
        c = self._campaign()
        self.assertIs(c.status, CampaignStatus.DRAFT)
        self.assertEqual(len(c.status_history), 1)
        self.assertEqual(c.status_history[0].reason, "created")

    def test_full_lifecycle(self):
        c = self._campaign()
        self.handle.transition_campaign(c.id, CampaignStatus.ACTIVE)
        self.handle.transition_campaign(c.id, CampaignStatus.PAUSED)
        self.handle.transition_campaign(c.id, CampaignStatus.ACTIVE)
        done = self.handle.transition_campaign(c.id, CampaignStatus.COMPLETED)
        self.assertIs(done.status, CampaignStatus.COMPLETED)
        self.assertTrue(done.is_terminal())

    def test_illegal_transition_is_refused(self):
        c = self._campaign()
        with self.assertRaises(InvalidTransitionError):
            self.handle.transition_campaign(c.id, CampaignStatus.COMPLETED)

    def test_completed_campaign_cannot_reopen(self):
        c = self._campaign()
        self.handle.transition_campaign(c.id, CampaignStatus.ACTIVE)
        self.handle.transition_campaign(c.id, CampaignStatus.COMPLETED)
        for target in (CampaignStatus.DRAFT, CampaignStatus.ACTIVE, CampaignStatus.PAUSED):
            with self.subTest(target=target.value), self.assertRaises(InvalidTransitionError):
                self.handle.transition_campaign(c.id, target)

    def test_cancelled_is_terminal(self):
        c = self._campaign()
        self.handle.transition_campaign(c.id, CampaignStatus.CANCELLED)
        with self.assertRaises(InvalidTransitionError):
            self.handle.transition_campaign(c.id, CampaignStatus.ACTIVE)

    def test_open_states_match_the_graph(self):
        self.assertEqual(
            OPEN_STATES,
            frozenset({CampaignStatus.DRAFT, CampaignStatus.ACTIVE, CampaignStatus.PAUSED}),
        )

    def test_history_records_every_transition(self):
        c = self._campaign()
        self.handle.transition_campaign(c.id, CampaignStatus.ACTIVE, changed_by="human:j")
        final = self.handle.transition_campaign(c.id, CampaignStatus.PAUSED, reason="budget")
        self.assertEqual(
            [t.to_status.value for t in final.status_history], ["draft", "active", "paused"]
        )
        self.assertEqual(final.status_history[-1].reason, "budget")


# ---------------------------------------------------------------------------
# Storage and isolation
# ---------------------------------------------------------------------------


class TestCampaignStorage(CampaignCase):
    def test_round_trip(self):
        c = self._campaign(
            description="d",
            target_audience="VP Talent",
            primary_conversion_goal="demo_request",
            cta="Book a demo",
            destination="https://example.com/demo",
            kpis=["ctr"],
            start_date=EPOCH,
        )
        again = self.handle.get_campaign(c.id)
        self.assertEqual(again.to_dict(), c.to_dict())

    def test_missing_campaign_raises(self):
        with self.assertRaises(CampaignNotFoundError):
            self.handle.get_campaign("CAMPAIGN-9999")

    def test_list_filters_by_status(self):
        a = self._campaign(name="A")
        self._campaign(name="B")
        self.handle.transition_campaign(a.id, CampaignStatus.ACTIVE)
        self.assertEqual(len(self.handle.list_campaigns()), 2)
        self.assertEqual(len(self.handle.list_campaigns(CampaignStatus.ACTIVE)), 1)

    def test_campaign_ids_do_not_leak_volume_between_projects(self):
        with TemporaryDirectory() as tmp:
            root = _root(tmp, {"alpha": "a", "beta": "b"})
            store = GrowthStore(root)
            store.init_workspace("alpha")
            store.init_workspace("beta")
            for _ in range(4):
                store.open("alpha").create_campaign(name="x")
            first_beta = store.open("beta").create_campaign(name="y")
            self.assertEqual(first_beta.id, "CAMPAIGN-0001")

    def test_campaign_and_content_sequences_are_independent(self):
        self._content()
        c = self._campaign()
        self.assertEqual(c.id, "CAMPAIGN-0001")
        self.assertEqual(self.handle.list_content()[0].id, "CONTENT-0001")

    def test_a_workspace_lists_only_its_own_campaigns(self):
        with TemporaryDirectory() as tmp:
            root = _root(tmp, {"alpha": "a", "beta": "b"})
            store = GrowthStore(root)
            store.init_workspace("alpha")
            store.init_workspace("beta")
            store.open("alpha").create_campaign(name="alpha only")
            self.assertEqual(len(store.open("alpha").list_campaigns()), 1)
            self.assertEqual(store.open("beta").list_campaigns(), [])


# ---------------------------------------------------------------------------
# Content <-> campaign binding
# ---------------------------------------------------------------------------


class TestCampaignAssignment(CampaignCase):
    def test_assigning_content_links_both_directions(self):
        c = self._campaign()
        item = self._content()
        assigned = self.handle.assign_campaign(item.id, c.id)
        self.assertEqual(assigned.campaign, c.id)
        self.assertIn(item.id, self.handle.get_campaign(c.id).content_item_ids)

    def test_creating_content_with_a_campaign_attaches_it(self):
        c = self._campaign()
        item = self._content(campaign=c.id)
        self.assertIn(item.id, self.handle.get_campaign(c.id).content_item_ids)

    def test_reassignment_detaches_from_the_previous_campaign(self):
        first = self._campaign(name="First")
        second = self._campaign(name="Second")
        item = self._content(campaign=first.id)
        self.handle.assign_campaign(item.id, second.id)

        self.assertNotIn(item.id, self.handle.get_campaign(first.id).content_item_ids)
        self.assertIn(item.id, self.handle.get_campaign(second.id).content_item_ids)

    def test_detaching_with_an_empty_id(self):
        c = self._campaign()
        item = self._content(campaign=c.id)
        detached = self.handle.assign_campaign(item.id, "")
        self.assertEqual(detached.campaign, "")
        self.assertNotIn(item.id, self.handle.get_campaign(c.id).content_item_ids)

    def test_unknown_campaign_id_is_refused(self):
        item = self._content()
        with self.assertRaises(CrossCampaignError):
            self.handle.assign_campaign(item.id, "CAMPAIGN-9999")

    def test_a_campaign_from_another_project_is_refused(self):
        """The core isolation rule: content never crosses a workspace boundary."""
        with TemporaryDirectory() as tmp:
            root = _root(tmp, {"alpha": "a", "beta": "b"})
            store = GrowthStore(root)
            store.init_workspace("alpha")
            store.init_workspace("beta")
            beta_campaign = store.open("beta").create_campaign(name="beta launch")
            alpha_item = store.open("alpha").create_content(platform="linkedin", copy="x")

            with self.assertRaises(CrossCampaignError):
                store.open("alpha").assign_campaign(alpha_item.id, beta_campaign.id)

    def test_creating_content_against_a_foreign_campaign_is_refused(self):
        with TemporaryDirectory() as tmp:
            root = _root(tmp, {"alpha": "a", "beta": "b"})
            store = GrowthStore(root)
            store.init_workspace("alpha")
            store.init_workspace("beta")
            beta_campaign = store.open("beta").create_campaign(name="beta launch")
            with self.assertRaises(CrossCampaignError):
                store.open("alpha").create_content(platform="linkedin", campaign=beta_campaign.id)

    def test_a_closed_campaign_refuses_new_content(self):
        c = self._campaign()
        self.handle.transition_campaign(c.id, CampaignStatus.ACTIVE)
        self.handle.transition_campaign(c.id, CampaignStatus.COMPLETED)
        item = self._content()
        with self.assertRaises(InvalidTransitionError):
            self.handle.assign_campaign(item.id, c.id)

    def test_free_text_campaign_labels_still_work(self):
        """Backwards compatible: only CAMPAIGN-shaped values are validated."""
        item = self._content(campaign="spring-launch")
        self.assertEqual(item.campaign, "spring-launch")

    def test_published_content_cannot_be_recampaigned(self):
        c = self._campaign()
        item = self._content()
        self.handle.transition_content(item.id, ContentStatus.AI_REVIEW)
        self.handle.transition_content(item.id, ContentStatus.READY_FOR_REVIEW)
        self.handle.approve_content(item.id, approved_by="human:j")
        self.handle.transition_content(item.id, ContentStatus.PUBLISHING)
        self.handle.transition_content(item.id, ContentStatus.PUBLISHED)
        with self.assertRaises(InvalidTransitionError):
            self.handle.assign_campaign(item.id, c.id)

    def test_assignment_does_not_disturb_approval(self):
        """Campaign is not fingerprinted, so attaching content keeps its approval."""
        c = self._campaign()
        item = self._content()
        self.handle.transition_content(item.id, ContentStatus.AI_REVIEW)
        self.handle.transition_content(item.id, ContentStatus.READY_FOR_REVIEW)
        approved = self.handle.approve_content(item.id, approved_by="human:j")
        before = approved.current_fingerprint()

        reassigned = self.handle.assign_campaign(item.id, c.id)
        self.assertEqual(reassigned.current_fingerprint(), before)
        self.assertTrue(reassigned.is_approved)


# ---------------------------------------------------------------------------
# API / CLI
# ---------------------------------------------------------------------------


class TestCampaignApi(unittest.TestCase):
    def test_campaign_flow_through_monday_growth(self):
        with TemporaryDirectory() as tmp:
            root = _root(tmp)
            monday = Monday(MondayConfig(project_root=root))
            monday.growth("workspace-init", project="acme")

            created = monday.growth(
                "campaign-create", project="acme", name="Launch", objective="50 demos"
            )
            self.assertTrue(created.success)
            cid = created.data["campaign"]["id"]

            listed = monday.growth("campaign-list", project="acme")
            self.assertEqual(listed.data["count"], 1)

            activated = monday.growth(
                "campaign-status", project="acme", campaign_id=cid, status="active"
            )
            self.assertEqual(activated.status, "active")

            fetched = monday.growth("campaign-get", project="acme", campaign_id=cid)
            self.assertEqual(fetched.data["campaign"]["content_count"], 0)

    def test_foreign_campaign_returns_a_failed_response_not_an_exception(self):
        with TemporaryDirectory() as tmp:
            root = _root(tmp, {"alpha": "a", "beta": "b"})
            monday = Monday(MondayConfig(project_root=root))
            monday.growth("workspace-init", project="alpha")
            monday.growth("workspace-init", project="beta")
            beta = monday.growth("campaign-create", project="beta", name="Beta")
            item = monday.growth("content-create", project="alpha", platform="linkedin")

            result = monday.growth(
                "campaign-assign",
                project="alpha",
                content_id=item.content_id,
                campaign_id=beta.data["campaign"]["id"],
            )
            self.assertFalse(result.success)
            self.assertIn("never moves between projects", result.message)


if __name__ == "__main__":
    unittest.main()
