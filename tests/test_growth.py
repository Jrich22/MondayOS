"""Tests for the Growth Bot service (growth/ + Monday.growth + monday growth CLI)."""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from agents.gates import GATED_ACTIONS, ApprovalGate
from growth import (
    FINGERPRINTED_FIELDS,
    PUBLISH_ACTION,
    AmbiguousProjectError,
    ContentItem,
    ContentStatus,
    GrowthStore,
    InvalidProjectSlugError,
    InvalidTransitionError,
    PlatformBinding,
    ProjectNotRegisteredError,
    UnsupportedPlatformError,
    WorkspaceExistsError,
    WorkspaceNotFoundError,
    compute_fingerprint,
    normalize_project_slug,
    publish_action_is_gated,
)
from growth.binding import InvalidSecretNameError
from growth.content import REQUIRED_FOR_REVIEW
from growth.service import GrowthService
from monday import Monday, MondayConfig
from monday.cli import main
from monday.project import ProjectRegistry
from orchestrator.report import ExecutionMode

SECRET_VALUE = "super-secret-token-value-do-not-store"


def _make_root(tmp: str, projects: dict[str, str] | None = None) -> Path:
    """Build a MondayOS root with the given project name -> source path registrations."""
    root = Path(tmp)
    (root / "config").mkdir(parents=True, exist_ok=True)
    registry = ProjectRegistry(root / "config")
    for name, source in (projects or {"acme": "acme-src"}).items():
        source_dir = root / source
        source_dir.mkdir(parents=True, exist_ok=True)
        registry.register(name, source_dir, overwrite=True)
    return root


def _complete_content_kwargs() -> dict[str, object]:
    return {
        "platform": "linkedin",
        "account": "acct-123",
        "copy": "We shipped shortlist-first sourcing.",
        "cta": "Book a demo",
        "destination_url": "https://example.com/demo",
        "campaign": "launch",
        "expected_goal": "50 demo requests",
        "expected_audience": "VP Talent",
        "scheduled_at": datetime(2026, 9, 1, 9, 0, tzinfo=UTC),
    }


# ---------------------------------------------------------------------------
# Isolation (ADR-011)
# ---------------------------------------------------------------------------


class TestProjectSlugs(unittest.TestCase):
    def test_normalizes_case_underscores_and_spaces(self):
        self.assertEqual(normalize_project_slug("SourcingBOT"), "sourcingbot")
        self.assertEqual(normalize_project_slug("cue_app"), "cue-app")
        self.assertEqual(normalize_project_slug("Cue App"), "cue-app")

    def test_rejects_path_traversal_as_a_name(self):
        for hostile in ("../other", "..", "a/b", "/etc/passwd", "a\\b", "."):
            with self.subTest(name=hostile), self.assertRaises(InvalidProjectSlugError):
                normalize_project_slug(hostile)

    def test_rejects_empty_and_overlong(self):
        with self.assertRaises(InvalidProjectSlugError):
            normalize_project_slug("")
        with self.assertRaises(InvalidProjectSlugError):
            normalize_project_slug("a" * 65)


class TestProjectResolution(unittest.TestCase):
    def test_unregistered_project_is_refused(self):
        with TemporaryDirectory() as tmp:
            root = _make_root(tmp, {"acme": "acme-src"})
            with self.assertRaises(ProjectNotRegisteredError):
                GrowthStore(root).open("not-registered")

    def test_duplicate_names_same_source_path_resolve_to_one_workspace(self):
        # config/projects.json really does contain both "weatherbot" and "WeatherBot".
        with TemporaryDirectory() as tmp:
            root = _make_root(tmp, {"weatherbot": "wb", "WeatherBot": "wb"})
            handle = GrowthStore(root).open("WeatherBot")
            self.assertEqual(handle.slug, "weatherbot")

    def test_duplicate_names_different_source_paths_are_ambiguous(self):
        with TemporaryDirectory() as tmp:
            root = _make_root(tmp, {"weatherbot": "wb-one", "WeatherBot": "wb-two"})
            with self.assertRaises(AmbiguousProjectError):
                GrowthStore(root).open("weatherbot")

    def test_workspace_path_stays_under_the_growth_directory(self):
        with TemporaryDirectory() as tmp:
            root = _make_root(tmp)
            handle = GrowthStore(root).open("acme")
            expected = (root / "growth" / "workspaces" / "acme").resolve()
            self.assertEqual(handle.path.resolve(), expected)


class TestWorkspaceIsolation(unittest.TestCase):
    def test_content_ids_do_not_leak_volume_between_projects(self):
        """A shared counter would let one project infer another's publishing volume."""
        with TemporaryDirectory() as tmp:
            root = _make_root(tmp, {"alpha": "alpha-src", "beta": "beta-src"})
            store = GrowthStore(root)
            store.init_workspace("alpha")
            store.init_workspace("beta")

            alpha = store.open("alpha")
            beta = store.open("beta")

            for _ in range(5):
                alpha.create_content()
            first_beta = beta.create_content()

            self.assertEqual(first_beta.id, "CONTENT-0001")
            self.assertEqual(alpha.list_content()[-1].id, "CONTENT-0005")

    def test_a_workspace_lists_only_its_own_content(self):
        with TemporaryDirectory() as tmp:
            root = _make_root(tmp, {"alpha": "alpha-src", "beta": "beta-src"})
            store = GrowthStore(root)
            store.init_workspace("alpha")
            store.init_workspace("beta")
            store.open("alpha").create_content(copy="alpha copy")

            self.assertEqual(len(store.open("alpha").list_content()), 1)
            self.assertEqual(store.open("beta").list_content(), [])

    def test_init_is_not_idempotent_by_accident(self):
        with TemporaryDirectory() as tmp:
            root = _make_root(tmp)
            GrowthStore(root).init_workspace("acme")
            with self.assertRaises(WorkspaceExistsError):
                GrowthStore(root).init_workspace("acme")

    def test_reading_an_uninitialized_workspace_raises(self):
        with TemporaryDirectory() as tmp:
            root = _make_root(tmp)
            with self.assertRaises(WorkspaceNotFoundError):
                GrowthStore(root).open("acme").read()


# ---------------------------------------------------------------------------
# Credentials (ADR-011)
# ---------------------------------------------------------------------------


class TestCredentialsAreReferencesOnly(unittest.TestCase):
    def test_binding_stores_the_secret_name_not_the_value(self):
        with TemporaryDirectory() as tmp:
            root = _make_root(tmp)
            store = GrowthStore(root)
            store.init_workspace("acme")
            handle = store.open("acme")
            handle.bind("linkedin", "acct-1", "acme-co", "LINKEDIN_TOKEN")

            stored = (handle.path / "workspace.md").read_text(encoding="utf-8")
            self.assertIn("LINKEDIN_TOKEN", stored)
            self.assertNotIn(SECRET_VALUE, stored)

    def test_no_growth_file_ever_contains_a_credential_value(self):
        with TemporaryDirectory() as tmp:
            root = _make_root(tmp)
            store = GrowthStore(root)
            store.init_workspace("acme")
            handle = store.open("acme")
            handle.bind("linkedin", "acct-1", "acme-co", "LINKEDIN_TOKEN")
            handle.create_content(**_complete_content_kwargs())

            for path in (root / "growth").rglob("*"):
                if path.is_file():
                    self.assertNotIn(
                        SECRET_VALUE, path.read_text(encoding="utf-8"), f"leak in {path}"
                    )

    def test_redacted_exposes_only_the_reference(self):
        binding = PlatformBinding("linkedin", "acct-1", "acme-co", "LINKEDIN_TOKEN")
        self.assertEqual(binding.redacted()["secret_name"], "LINKEDIN_TOKEN")
        self.assertNotIn(SECRET_VALUE, json.dumps(binding.redacted()))

    def test_credential_check_reads_the_environment_without_returning_the_value(self):
        binding = PlatformBinding("linkedin", "acct-1", secret_name="LINKEDIN_TOKEN")
        missing = binding.credential_check({})
        self.assertFalse(missing.ok)
        self.assertEqual(missing.missing, ["LINKEDIN_TOKEN"])

        present = binding.credential_check({"LINKEDIN_TOKEN": SECRET_VALUE})
        self.assertTrue(present.ok)
        self.assertNotIn(SECRET_VALUE, present.instructions())

    def test_a_pasted_credential_is_rejected_as_a_secret_name(self):
        with self.assertRaises(InvalidSecretNameError):
            PlatformBinding("linkedin", "acct-1", secret_name=SECRET_VALUE)

    def test_unsupported_platform_is_rejected(self):
        with self.assertRaises(UnsupportedPlatformError):
            PlatformBinding("myspace", "acct-1")


# ---------------------------------------------------------------------------
# Fingerprint (ADR-013)
# ---------------------------------------------------------------------------


class TestFingerprint(unittest.TestCase):
    def _item(self) -> ContentItem:
        item = ContentItem(id="CONTENT-0001", project="acme")
        for key, value in _complete_content_kwargs().items():
            setattr(item, key, value)
        item.media = ["media/a.png", "media/b.png"]
        return item

    def test_is_stable_across_repeated_computation(self):
        item = self._item()
        self.assertEqual(item.current_fingerprint(), item.current_fingerprint())

    def test_is_stable_across_a_storage_round_trip(self):
        item = self._item()
        before = item.current_fingerprint()
        restored = ContentItem.from_dict(item.to_dict())
        self.assertEqual(restored.current_fingerprint(), before)

    def test_every_fingerprinted_field_changes_the_hash(self):
        """Iterates the real contract in FINGERPRINTED_FIELDS, not a copy of it."""
        base = self._item()
        original = base.current_fingerprint()
        mutations = {
            "project": lambda i: setattr(i, "project", "other"),
            "platform": lambda i: setattr(i, "platform", "x"),
            "account": lambda i: setattr(i, "account", "acct-999"),
            "media": lambda i: setattr(i, "media", ["media/b.png", "media/a.png"]),
            "copy_and_cta": lambda i: setattr(i, "copy", i.copy + "!"),
            "destination_url": lambda i: setattr(i, "destination_url", "https://example.com/x"),
            "scheduled_at": lambda i: setattr(
                i, "scheduled_at", i.scheduled_at + timedelta(hours=1)
            ),
        }
        self.assertEqual(set(mutations), set(FINGERPRINTED_FIELDS))

        for field_name, mutate in mutations.items():
            with self.subTest(field=field_name):
                item = self._item()
                mutate(item)
                self.assertNotEqual(item.current_fingerprint(), original)

    def test_cta_alone_changes_the_hash(self):
        item = self._item()
        original = item.current_fingerprint()
        item.cta = "Talk to sales"
        self.assertNotEqual(item.current_fingerprint(), original)

    def test_media_order_is_significant(self):
        item = self._item()
        original = item.current_fingerprint()
        item.media = list(reversed(item.media))
        self.assertNotEqual(item.current_fingerprint(), original)

    def test_non_approved_fields_do_not_change_the_hash(self):
        item = self._item()
        original = item.current_fingerprint()
        item.notes = "internal reminder"
        item.tags = ["q3", "launch"]
        item.campaign = "renamed-campaign"
        item.expected_goal = "80 demo requests"
        item.warnings = ["tone check"]
        self.assertEqual(item.current_fingerprint(), original)

    def test_naive_and_aware_schedules_agree(self):
        """A naive timestamp must not fingerprint differently by machine timezone."""
        aware = compute_fingerprint(
            project="a",
            platform="linkedin",
            account="1",
            media=[],
            copy="c",
            cta="d",
            destination_url="u",
            scheduled_at=datetime(2026, 9, 1, 9, 0, tzinfo=UTC),
        )
        naive = compute_fingerprint(
            project="a",
            platform="linkedin",
            account="1",
            media=[],
            copy="c",
            cta="d",
            destination_url="u",
            scheduled_at=datetime(2026, 9, 1, 9, 0),
        )
        self.assertEqual(aware, naive)

    def test_whitespace_is_not_normalized_away(self):
        item = self._item()
        original = item.current_fingerprint()
        item.copy = item.copy + " "
        self.assertNotEqual(item.current_fingerprint(), original)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle(unittest.TestCase):
    def _handle(self, tmp: str):
        root = _make_root(tmp)
        store = GrowthStore(root)
        store.init_workspace("acme")
        return store.open("acme")

    def test_happy_path_reaches_approved(self):
        with TemporaryDirectory() as tmp:
            handle = self._handle(tmp)
            item = handle.create_content(**_complete_content_kwargs())
            handle.transition_content(item.id, ContentStatus.AI_REVIEW)
            handle.transition_content(item.id, ContentStatus.READY_FOR_REVIEW)
            approved = handle.approve_content(item.id, approved_by="human:jrich")

            self.assertIs(approved.status, ContentStatus.APPROVED)
            self.assertTrue(approved.is_approved)
            self.assertEqual(approved.approved_by, "human:jrich")

    def test_illegal_transitions_are_rejected(self):
        with TemporaryDirectory() as tmp:
            handle = self._handle(tmp)
            item = handle.create_content(**_complete_content_kwargs())
            with self.assertRaises(InvalidTransitionError):
                handle.transition_content(item.id, ContentStatus.APPROVED)

    def test_cancelled_is_terminal(self):
        with TemporaryDirectory() as tmp:
            handle = self._handle(tmp)
            item = handle.create_content(**_complete_content_kwargs())
            handle.transition_content(item.id, ContentStatus.CANCELLED)
            with self.assertRaises(InvalidTransitionError):
                handle.transition_content(item.id, ContentStatus.DRAFT)

    def test_history_records_every_transition(self):
        with TemporaryDirectory() as tmp:
            handle = self._handle(tmp)
            item = handle.create_content(**_complete_content_kwargs())
            handle.transition_content(item.id, ContentStatus.AI_REVIEW, changed_by="agent:x")
            final = handle.transition_content(item.id, ContentStatus.READY_FOR_REVIEW)

            statuses = [t.to_status.value for t in final.status_history]
            self.assertEqual(statuses, ["draft", "ai-review", "ready-for-review"])
            self.assertEqual(final.status_history[1].changed_by, "agent:x")

    def test_missing_required_fields_are_reported(self):
        with TemporaryDirectory() as tmp:
            handle = self._handle(tmp)
            item = handle.create_content()
            self.assertEqual(set(item.missing_required_fields()), set(REQUIRED_FOR_REVIEW))

    def test_media_is_not_required_for_review(self):
        with TemporaryDirectory() as tmp:
            handle = self._handle(tmp)
            item = handle.create_content(**_complete_content_kwargs())
            self.assertEqual(item.missing_required_fields(), [])


# ---------------------------------------------------------------------------
# Approval resets (ADR-013)
# ---------------------------------------------------------------------------


class TestApprovalReset(unittest.TestCase):
    def _approved(self, tmp: str):
        root = _make_root(tmp)
        store = GrowthStore(root)
        store.init_workspace("acme")
        handle = store.open("acme")
        item = handle.create_content(**_complete_content_kwargs())
        handle.transition_content(item.id, ContentStatus.AI_REVIEW)
        handle.transition_content(item.id, ContentStatus.READY_FOR_REVIEW)
        return handle, handle.approve_content(item.id, approved_by="human:jrich")

    def test_editing_approved_copy_revokes_the_approval(self):
        with TemporaryDirectory() as tmp:
            handle, approved = self._approved(tmp)
            edited = handle.update_content(approved.id, copy="Completely different copy.")

            self.assertFalse(edited.is_approved)
            self.assertIs(edited.status, ContentStatus.READY_FOR_REVIEW)
            self.assertEqual(edited.approved_fingerprint, "")
            self.assertEqual(edited.approved_by, "")

    def test_rescheduling_revokes_the_approval(self):
        with TemporaryDirectory() as tmp:
            handle, approved = self._approved(tmp)
            edited = handle.update_content(
                approved.id, scheduled_at=datetime(2026, 9, 5, 18, 0, tzinfo=UTC)
            )
            self.assertFalse(edited.is_approved)
            self.assertIs(edited.status, ContentStatus.READY_FOR_REVIEW)

    def test_editing_an_internal_note_preserves_the_approval(self):
        with TemporaryDirectory() as tmp:
            handle, approved = self._approved(tmp)
            edited = handle.update_content(approved.id, notes="check with legal", tags=["q3"])

            self.assertTrue(edited.is_approved)
            self.assertIs(edited.status, ContentStatus.APPROVED)

    def test_the_reset_is_audited(self):
        with TemporaryDirectory() as tmp:
            handle, approved = self._approved(tmp)
            edited = handle.update_content(approved.id, cta="Talk to sales")
            last = edited.status_history[-1]
            self.assertIs(last.to_status, ContentStatus.READY_FOR_REVIEW)
            self.assertEqual(last.changed_by, "system")
            self.assertIn("no longer covers", last.reason)

    def test_a_tampered_file_cannot_claim_approval(self):
        """is_approved recomputes, so editing the stored copy directly does not survive."""
        with TemporaryDirectory() as tmp:
            handle, approved = self._approved(tmp)
            path = handle.path / "content" / f"{approved.id}.md"
            raw = path.read_text(encoding="utf-8")
            path.write_text(
                raw.replace("We shipped shortlist-first sourcing.", "Buy now, limited offer!"),
                encoding="utf-8",
            )

            reloaded = handle.get_content(approved.id)
            self.assertEqual(reloaded.status, ContentStatus.APPROVED)
            self.assertFalse(reloaded.is_approved)
            self.assertTrue(reloaded.approval_is_stale())

    def test_requesting_changes_clears_the_fingerprint(self):
        with TemporaryDirectory() as tmp:
            handle, approved = self._approved(tmp)
            changed = handle.transition_content(
                approved.id, ContentStatus.CHANGES_REQUESTED, reason="tone"
            )
            self.assertEqual(changed.approved_fingerprint, "")
            self.assertFalse(changed.is_approved)


# ---------------------------------------------------------------------------
# The increment boundary: nothing can publish
# ---------------------------------------------------------------------------


class TestApprovalIsTheOnlyWayIn(unittest.TestCase):
    """
    Increment 3 opened the publishing states. The boundary that replaced
    "nothing can publish" is narrower and more important: nothing reaches a
    publishing state without passing through Approved.
    """

    def test_publishing_is_reachable_only_from_approved_or_scheduled_or_failed(self):
        from growth.content import _VALID_TRANSITIONS

        sources = {
            state
            for state, targets in _VALID_TRANSITIONS.items()
            if ContentStatus.PUBLISHING in targets
        }
        self.assertEqual(
            sources,
            {ContentStatus.APPROVED, ContentStatus.SCHEDULED, ContentStatus.FAILED},
        )

    def test_published_is_reachable_only_from_publishing(self):
        from growth.content import _VALID_TRANSITIONS

        sources = {
            state
            for state, targets in _VALID_TRANSITIONS.items()
            if ContentStatus.PUBLISHED in targets
        }
        self.assertEqual(sources, {ContentStatus.PUBLISHING})

    def test_no_pre_approval_state_can_reach_publishing_or_published(self):
        from growth.content import _VALID_TRANSITIONS

        pre_approval = (
            ContentStatus.DRAFT,
            ContentStatus.AI_REVIEW,
            ContentStatus.READY_FOR_REVIEW,
            ContentStatus.CHANGES_REQUESTED,
        )
        for state in pre_approval:
            with self.subTest(state=state.value):
                targets = _VALID_TRANSITIONS[state]
                self.assertNotIn(ContentStatus.PUBLISHING, targets)
                self.assertNotIn(ContentStatus.PUBLISHED, targets)
                self.assertNotIn(ContentStatus.SCHEDULED, targets)

    def test_no_real_platform_adapter_is_registered(self):
        from integrations.publishing.factory import REAL_ADAPTERS

        self.assertEqual(REAL_ADAPTERS, {})

    def test_measurement_states_do_not_exist_yet(self):
        values = {s.value for s in ContentStatus}
        for forbidden in ("measured", "archived"):
            self.assertNotIn(forbidden, values)


# ---------------------------------------------------------------------------
# Gate integration (ADR-012)
# ---------------------------------------------------------------------------


class TestPublishGate(unittest.TestCase):
    def test_publish_content_is_a_gated_action(self):
        self.assertIn(PUBLISH_ACTION, GATED_ACTIONS)
        self.assertTrue(publish_action_is_gated())

    def test_gate_blocks_publishing_without_approval(self):
        decision = ApprovalGate().evaluate(
            mode=ExecutionMode.REVIEW, requested_actions=[PUBLISH_ACTION]
        )
        self.assertFalse(decision.allowed)
        self.assertIn(PUBLISH_ACTION, decision.gated_actions)

    def test_gate_permits_publishing_with_approval(self):
        decision = ApprovalGate().evaluate(
            mode=ExecutionMode.REVIEW, requested_actions=[PUBLISH_ACTION], approved=True
        )
        self.assertTrue(decision.allowed)

    def test_existing_gated_actions_are_unchanged(self):
        self.assertTrue(
            {"commit", "push", "secrets", "live_trade", "destructive"}.issubset(GATED_ACTIONS)
        )


# ---------------------------------------------------------------------------
# Monday.growth()
# ---------------------------------------------------------------------------


class TestMondayGrowth(unittest.TestCase):
    def _monday(self, tmp: str) -> Monday:
        root = _make_root(tmp)
        return Monday(MondayConfig(project_root=root))

    def test_workspace_lifecycle_through_the_public_api(self):
        with TemporaryDirectory() as tmp:
            monday = self._monday(tmp)
            created = monday.growth("workspace-init", project="acme")
            self.assertTrue(created.success)
            self.assertEqual(created.project, "acme")

            listed = monday.growth("workspace-list")
            self.assertEqual(listed.data["workspaces"], ["acme"])

            fetched = monday.growth("workspace-get", project="acme")
            self.assertTrue(fetched.success)

    def test_approval_flow_through_the_public_api(self):
        with TemporaryDirectory() as tmp:
            monday = self._monday(tmp)
            monday.growth("workspace-init", project="acme")
            created = monday.growth("content-create", project="acme", **_complete_content_kwargs())
            content_id = created.content_id

            reviewed = monday.growth("review", project="acme", content_id=content_id)
            self.assertEqual(reviewed.status, "ready-for-review")

            approved = monday.growth(
                "approve", project="acme", content_id=content_id, approved_by="human:jrich"
            )
            self.assertTrue(approved.is_approved)

            edited = monday.growth(
                "content-update", project="acme", content_id=content_id, copy="new copy"
            )
            self.assertFalse(edited.is_approved)
            self.assertEqual(edited.status, "ready-for-review")

    def test_review_refuses_an_incomplete_item(self):
        with TemporaryDirectory() as tmp:
            monday = self._monday(tmp)
            monday.growth("workspace-init", project="acme")
            created = monday.growth("content-create", project="acme", copy="just copy")
            result = monday.growth("review", project="acme", content_id=created.content_id)

            self.assertFalse(result.success)
            self.assertIn("not reviewable", result.message)

    def test_unknown_action_returns_a_failed_response_rather_than_raising(self):
        with TemporaryDirectory() as tmp:
            result = self._monday(tmp).growth("teleport", project="acme")
            self.assertFalse(result.success)
            self.assertIn("Unknown action", result.message)

    def test_unregistered_project_returns_a_failed_response(self):
        with TemporaryDirectory() as tmp:
            result = self._monday(tmp).growth("workspace-init", project="ghost")
            self.assertFalse(result.success)
            self.assertIn("not registered", result.message)

    def test_bindings_returned_through_the_api_are_redacted(self):
        with TemporaryDirectory() as tmp:
            monday = self._monday(tmp)
            monday.growth("workspace-init", project="acme")
            monday.growth(
                "bind",
                project="acme",
                platform="linkedin",
                account_id="acct-1",
                secret_name="LINKEDIN_TOKEN",
            )
            listed = monday.growth("bindings", project="acme")
            self.assertNotIn(SECRET_VALUE, json.dumps(listed.data))
            self.assertEqual(listed.data["bindings"][0]["secret_name"], "LINKEDIN_TOKEN")

    def test_credentials_action_reports_missing_secrets(self):
        with TemporaryDirectory() as tmp:
            monday = self._monday(tmp)
            monday.growth("workspace-init", project="acme")
            monday.growth(
                "bind",
                project="acme",
                platform="linkedin",
                account_id="acct-1",
                secret_name="LINKEDIN_TOKEN",
            )
            result = monday.growth("credentials", project="acme", environ={})
            self.assertEqual(result.data["ready"], 0)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestGrowthCLI(unittest.TestCase):
    def _run(self, root: Path, *argv: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = main(["--project-root", str(root), "growth", *argv])
        return code, buffer.getvalue()

    def test_end_to_end_cli_flow(self):
        with TemporaryDirectory() as tmp:
            root = _make_root(tmp)

            code, out = self._run(root, "workspace-init", "--project", "acme")
            self.assertEqual(code, 0, out)

            code, out = self._run(
                root,
                "bind",
                "--project",
                "acme",
                "--platform",
                "linkedin",
                "--account-id",
                "acct-1",
                "--secret-name",
                "LINKEDIN_TOKEN",
            )
            self.assertEqual(code, 0, out)

            code, out = self._run(
                root,
                "content-create",
                "--project",
                "acme",
                "--platform",
                "linkedin",
                "--account",
                "acct-1",
                "--copy",
                "hello",
                "--cta",
                "Book a demo",
                "--destination-url",
                "https://example.com",
                "--campaign",
                "launch",
                "--expected-goal",
                "demos",
                "--expected-audience",
                "VP Talent",
                "--scheduled-at",
                "2026-09-01T09:00:00Z",
            )
            self.assertEqual(code, 0, out)
            self.assertIn("CONTENT-0001", out)

            code, out = self._run(root, "review", "--project", "acme", "--content", "CONTENT-0001")
            self.assertEqual(code, 0, out)

            code, out = self._run(
                root, "approve", "--project", "acme", "--content", "CONTENT-0001", "--by", "jrich"
            )
            self.assertEqual(code, 0, out)

            code, out = self._run(
                root, "content-get", "--project", "acme", "--content", "CONTENT-0001"
            )
            self.assertIn("Approved   : True", out)

            code, out = self._run(
                root,
                "content-update",
                "--project",
                "acme",
                "--content",
                "CONTENT-0001",
                "--copy",
                "edited",
            )
            self.assertEqual(code, 0, out)

            code, out = self._run(
                root, "content-get", "--project", "acme", "--content", "CONTENT-0001"
            )
            self.assertIn("Approved   : False", out)

    def test_cli_reports_an_unregistered_project_as_an_error(self):
        with TemporaryDirectory() as tmp:
            root = _make_root(tmp)
            code, out = self._run(root, "workspace-init", "--project", "ghost")
            self.assertEqual(code, 1)
            self.assertIn("not registered", out)


if __name__ == "__main__":
    unittest.main()
