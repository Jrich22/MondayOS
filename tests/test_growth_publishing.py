"""
Tests for the Growth publishing connector layer (increment 3).

Kept separate from tests/test_growth.py so the foundation suite stays focused on
workspaces and approval. Everything here exercises the dispatcher's gate order,
retry policy, idempotency, pause controls, and audit trail against the
deterministic fake connector.
"""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from growth import GrowthStore
from growth.content import ContentStatus
from growth.dispatch import PublishDispatcher
from growth.publication import (
    MAX_ATTEMPTS,
    backoff_seconds,
    idempotency_key,
)
from growth.service import GrowthService
from integrations.publishing import FakePublishingConnector
from monday import Monday, MondayConfig
from monday.project import ProjectRegistry

SECRET_VALUE = "super-secret-token-value-do-not-store"
SECRET_NAME = "LINKEDIN_TOKEN"

BASE_NOW = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
DUE_AT = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)

APPROVED_COPY = "Shortlist-first sourcing is live."


def _root(tmp: str, projects: dict[str, str] | None = None) -> Path:
    root = Path(tmp)
    (root / "config").mkdir(parents=True, exist_ok=True)
    registry = ProjectRegistry(root / "config")
    for name, source in (projects or {"acme": "acme-src"}).items():
        (root / source).mkdir(parents=True, exist_ok=True)
        registry.register(name, root / source, overwrite=True)
    return root


def _clock(start: datetime = BASE_NOW):
    """A movable clock. Returns (now_fn, advance_fn)."""
    state = {"t": start}

    def now() -> datetime:
        return state["t"]

    def advance(**kw) -> None:
        state["t"] = state["t"] + timedelta(**kw)

    return now, advance


class PublishingCase(unittest.TestCase):
    """Builds an approved, bound, schedulable item in an isolated workspace."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = _root(self._tmp.name)
        self.store = GrowthStore(self.root)
        self.store.init_workspace("acme")
        self.handle = self.store.open("acme")
        self.handle.bind("linkedin", "acct-1", "acme-co", SECRET_NAME)
        self.connector = FakePublishingConnector(platform="linkedin")
        self.now, self.advance = _clock()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _dispatcher(self, jitter: float = 0.0) -> PublishDispatcher:
        return PublishDispatcher(
            handle=self.store.open("acme"),
            project_root=self.root,
            connector=self.connector,
            now=self.now,
            jitter=jitter,
            actor="human:test",
        )

    def _approved_item(self, scheduled_at: datetime | None = DUE_AT, **overrides):
        fields = {
            "platform": "linkedin",
            "account": "acct-1",
            "copy": APPROVED_COPY,
            "cta": "Book a demo",
            "destination_url": "https://example.com/demo",
            "campaign": "launch",
            "expected_goal": "50 demos",
            "expected_audience": "VP Talent",
            "scheduled_at": scheduled_at,
        }
        fields.update(overrides)
        item = self.handle.create_content(**fields)
        self.handle.transition_content(item.id, ContentStatus.AI_REVIEW)
        self.handle.transition_content(item.id, ContentStatus.READY_FOR_REVIEW)
        return self.handle.approve_content(item.id, approved_by="human:jrich")


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class TestSuccessfulPublish(PublishingCase):
    def test_approved_exact_payload_publishes(self):
        item = self._approved_item()
        result = self._dispatcher().publish(item.id, force_due=True)

        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.status, "published")
        self.assertTrue(result.external_id)

        sent = self.connector.published_payload(
            idempotency_key("acme", item.id, item.approved_fingerprint)
        )
        self.assertIsNotNone(sent)
        # The exact approved bytes, unmodified anywhere on the path.
        self.assertEqual(sent["copy"], APPROVED_COPY)
        self.assertEqual(sent["cta"], "Book a demo")
        self.assertEqual(sent["destination_url"], "https://example.com/demo")

    def test_successful_scheduled_publish(self):
        item = self._approved_item()
        d = self._dispatcher()

        scheduled = d.schedule(item.id)
        self.assertTrue(scheduled.ok, scheduled.message)
        self.assertEqual(scheduled.status, "scheduled")

        early = d.publish(item.id)
        self.assertFalse(early.ok)
        self.assertEqual(early.refused_at, "scheduled-time")
        self.assertEqual(early.failure_code, "not_due")

        self.advance(hours=2)
        due = self._dispatcher().publish(item.id)
        self.assertTrue(due.ok, due.message)
        self.assertEqual(due.status, "published")

    def test_published_result_is_persisted_on_the_item(self):
        item = self._approved_item()
        self._dispatcher().publish(item.id, force_due=True)

        reloaded = self.store.open("acme").get_content(item.id)
        record = reloaded.publication
        self.assertIsNotNone(record)
        self.assertEqual(record.platform, "linkedin")
        self.assertEqual(record.account_ref, "acct-1")
        self.assertTrue(record.external_id)
        self.assertTrue(record.external_url)
        self.assertIsNotNone(record.attempted_at)
        self.assertIsNotNone(record.published_at)
        self.assertEqual(record.retry_count, 1)
        self.assertEqual(record.failure_code, "")

    def test_timezone_is_stored_and_publishing_uses_the_instant(self):
        item = self._approved_item()
        self.handle.update_content(item.id, scheduled_at=DUE_AT)
        stored = self.store.open("acme").get_content(item.id)
        # A naive stored time must be read as UTC, never as machine-local.
        self.assertEqual(stored.scheduled_at.tzinfo, UTC)


# ---------------------------------------------------------------------------
# Gate 6 — approval / fingerprint
# ---------------------------------------------------------------------------


class TestApprovalGates(PublishingCase):
    def test_unapproved_content_refuses(self):
        item = self.handle.create_content(
            platform="linkedin",
            account="acct-1",
            copy="draft",
            cta="x",
            destination_url="https://example.com",
            campaign="c",
            expected_goal="g",
            expected_audience="a",
            scheduled_at=DUE_AT,
        )
        result = self._dispatcher().publish(item.id, force_due=True)
        self.assertFalse(result.ok)
        self.assertEqual(result.refused_at, "status")
        self.assertEqual(result.failure_code, "not_approved")

    def _assert_refused_on_change(self, item, **edit):
        self.handle.update_content(item.id, **edit)
        # The edit already reset approval; the dispatcher must refuse regardless.
        result = self._dispatcher().publish(item.id, force_due=True)
        self.assertFalse(result.ok)
        self.assertIn(result.refused_at, ("status", "fingerprint"))
        self.assertEqual(self.connector.publish_call_count, 0)

    def test_changed_caption_after_approval_refuses(self):
        self._assert_refused_on_change(self._approved_item(), copy="different copy")

    def test_changed_media_after_approval_refuses(self):
        self._assert_refused_on_change(self._approved_item(), media=["a.png"])

    def test_changed_url_after_approval_refuses(self):
        self._assert_refused_on_change(
            self._approved_item(), destination_url="https://example.com/other"
        )

    def test_changed_datetime_after_approval_refuses(self):
        self._assert_refused_on_change(
            self._approved_item(), scheduled_at=datetime(2026, 9, 5, 9, 0, tzinfo=UTC)
        )

    def test_tampered_approved_item_is_refused_and_reset(self):
        """A hand-edited file must not publish on a stale fingerprint."""
        item = self._approved_item()
        path = self.handle.path / "content" / f"{item.id}.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace(APPROVED_COPY, "Buy now!"),
            encoding="utf-8",
        )
        result = self._dispatcher().publish(item.id, force_due=True)

        self.assertFalse(result.ok)
        self.assertEqual(result.refused_at, "fingerprint")
        self.assertEqual(result.failure_code, "fingerprint_mismatch")
        self.assertEqual(self.connector.publish_call_count, 0)
        self.assertEqual(
            self.store.open("acme").get_content(item.id).status,
            ContentStatus.READY_FOR_REVIEW,
        )


# ---------------------------------------------------------------------------
# Gate 7 — isolation
# ---------------------------------------------------------------------------


class TestIsolationGates(PublishingCase):
    def test_missing_account_refuses(self):
        item = self._approved_item()
        # Drop the binding this item depends on.
        workspace = self.handle.read()
        workspace.bindings = []
        self.handle.write(workspace)

        result = self._dispatcher().publish(item.id, force_due=True)
        self.assertFalse(result.ok)
        self.assertEqual(result.refused_at, "isolation")
        self.assertEqual(result.failure_code, "missing_account")

    def test_wrong_platform_account_refuses(self):
        """An item on a platform this workspace has not bound cannot publish."""
        item = self._approved_item(platform="x", account="acct-1")
        result = self._dispatcher().publish(item.id, force_due=True)
        self.assertFalse(result.ok)
        self.assertEqual(result.refused_at, "isolation")
        self.assertEqual(result.failure_code, "missing_account")

    def test_account_from_another_project_refuses(self):
        """No cross-project fallback and no default account."""
        item = self._approved_item(account="someone-elses-account")
        result = self._dispatcher().publish(item.id, force_due=True)
        self.assertFalse(result.ok)
        self.assertEqual(result.refused_at, "isolation")
        self.assertEqual(result.failure_code, "account_mismatch")
        self.assertEqual(self.connector.publish_call_count, 0)

    def test_a_second_project_cannot_publish_this_projects_content(self):
        with TemporaryDirectory() as tmp:
            root = _root(tmp, {"alpha": "a-src", "beta": "b-src"})
            store = GrowthStore(root)
            store.init_workspace("alpha")
            store.init_workspace("beta")
            store.open("alpha").bind("linkedin", "alpha-acct", "", SECRET_NAME)
            self.assertIsNone(store.open("beta").read().binding_for("linkedin"))


# ---------------------------------------------------------------------------
# Gates 1-4 — pause scopes
# ---------------------------------------------------------------------------


class TestPauseGates(PublishingCase):
    def _pauses(self):
        return self.store.open("acme").pause_controller(self.root)

    def test_post_pause_refuses(self):
        item = self._approved_item()
        self._pauses().set_pause("post", True, target=item.id, reason="legal check")
        result = self._dispatcher().publish(item.id, force_due=True)
        self.assertFalse(result.ok)
        self.assertEqual(result.refused_at, "pause:post")
        self.assertEqual(self.connector.publish_call_count, 0)

    def test_platform_pause_refuses(self):
        item = self._approved_item()
        self._pauses().set_pause("platform", True, target="linkedin", reason="outage")
        result = self._dispatcher().publish(item.id, force_due=True)
        self.assertFalse(result.ok)
        self.assertEqual(result.refused_at, "pause:platform")

    def test_project_pause_refuses(self):
        item = self._approved_item()
        self._pauses().set_pause("project", True, reason="rebrand")
        result = self._dispatcher().publish(item.id, force_due=True)
        self.assertFalse(result.ok)
        self.assertEqual(result.refused_at, "pause:project")

    def test_global_emergency_stop_refuses_and_wins(self):
        item = self._approved_item()
        self._pauses().set_emergency_stop(True, reason="incident 42")
        result = self._dispatcher().publish(item.id, force_due=True)
        self.assertFalse(result.ok)
        self.assertEqual(result.refused_at, "pause:global")
        self.assertEqual(result.failure_code, "emergency_stop")
        self.assertIn("EMERGENCY STOP", result.message)
        self.assertEqual(self.connector.publish_call_count, 0)

    def test_emergency_stop_overrides_a_cleared_narrow_scope(self):
        item = self._approved_item()
        p = self._pauses()
        p.set_pause("project", False)
        p.set_emergency_stop(True, reason="incident")
        self.assertEqual(
            self._dispatcher().publish(item.id, force_due=True).refused_at, "pause:global"
        )

    def test_pause_blocks_scheduling_too(self):
        item = self._approved_item()
        self._pauses().set_pause("project", True, reason="hold")
        self.assertFalse(self._dispatcher().schedule(item.id).ok)

    def test_resume_restores_publishing(self):
        item = self._approved_item()
        p = self._pauses()
        p.set_emergency_stop(True, reason="incident")
        self.assertFalse(self._dispatcher().publish(item.id, force_due=True).ok)
        p.set_emergency_stop(False)
        self.assertTrue(self._dispatcher().publish(item.id, force_due=True).ok)

    def test_pausing_does_not_mutate_the_item(self):
        item = self._approved_item()
        self._pauses().set_pause("project", True, reason="hold")
        self._dispatcher().publish(item.id, force_due=True)
        after = self.store.open("acme").get_content(item.id)
        self.assertEqual(after.status, ContentStatus.APPROVED)
        self.assertTrue(after.is_approved)
        self.assertEqual(after.copy, APPROVED_COPY)


# ---------------------------------------------------------------------------
# Retry and idempotency
# ---------------------------------------------------------------------------


class TestRetryPolicy(PublishingCase):
    def test_temporary_failure_records_backoff_and_retries(self):
        item = self._approved_item()
        self.connector.queue_failure("rate_limited", "slow down", transient=True)

        first = self._dispatcher().publish(item.id, force_due=True)
        self.assertFalse(first.ok)
        self.assertEqual(first.failure_code, "rate_limited")
        self.assertEqual(first.status, "failed")
        self.assertTrue(first.next_attempt_at)

        too_soon = self._dispatcher().retry(item.id)
        self.assertFalse(too_soon.ok)
        self.assertEqual(too_soon.failure_code, "not_due")

        self.advance(hours=2)
        retried = self._dispatcher().retry(item.id)
        self.assertTrue(retried.ok, retried.message)
        self.assertEqual(retried.status, "published")

    def test_permanent_failure_does_not_schedule_a_retry(self):
        item = self._approved_item()
        self.connector.queue_failure("invalid_credentials", "bad token", transient=False)

        result = self._dispatcher().publish(item.id, force_due=True)
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_code, "invalid_credentials")
        self.assertEqual(result.next_attempt_at, "")
        self.assertIn("not retryable", result.message)

        record = self.store.open("acme").get_content(item.id).publication
        self.assertIsNone(record.next_attempt_at)
        self.assertFalse(record.attempts[-1].transient)

    def test_retries_are_bounded(self):
        item = self._approved_item()
        for _ in range(MAX_ATTEMPTS):
            self.connector.queue_failure("timeout", "slow", transient=True)

        d = self._dispatcher()
        d.publish(item.id, force_due=True)
        for _ in range(MAX_ATTEMPTS - 1):
            self.advance(hours=2)
            self._dispatcher().retry(item.id)

        record = self.store.open("acme").get_content(item.id).publication
        self.assertEqual(record.retry_count, MAX_ATTEMPTS)
        self.assertTrue(record.exhausted)

        self.advance(hours=4)
        exhausted = self._dispatcher().retry(item.id)
        self.assertFalse(exhausted.ok)
        self.assertEqual(exhausted.refused_at, "retry-budget")

    def test_backoff_is_exponential_capped_and_jittered(self):
        self.assertEqual(backoff_seconds(1), 60.0)
        self.assertEqual(backoff_seconds(2), 120.0)
        self.assertEqual(backoff_seconds(3), 240.0)
        self.assertEqual(backoff_seconds(99), 3600.0)  # capped
        self.assertLess(backoff_seconds(1, jitter=-1.0), 60.0)
        self.assertGreater(backoff_seconds(1, jitter=1.0), 60.0)

    def test_retry_refuses_a_non_failed_item(self):
        item = self._approved_item()
        result = self._dispatcher().retry(item.id)
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_code, "invalid_state")


class TestIdempotency(PublishingCase):
    def test_key_is_derived_from_the_approved_version_not_the_clock(self):
        item = self._approved_item()
        a = idempotency_key("acme", item.id, item.approved_fingerprint)
        b = idempotency_key("acme", item.id, item.approved_fingerprint)
        self.assertEqual(a, b)
        self.assertNotEqual(a, idempotency_key("acme", item.id, "other-fingerprint"))

    def test_duplicate_publish_is_prevented(self):
        item = self._approved_item()
        first = self._dispatcher().publish(item.id, force_due=True)
        self.assertTrue(first.ok)
        self.assertEqual(self.connector.publish_call_count, 1)

        second = self._dispatcher().publish(item.id, force_due=True)
        self.assertTrue(second.ok)
        self.assertTrue(second.reconciled)
        self.assertEqual(second.external_id, first.external_id)
        self.assertEqual(self.connector.publish_call_count, 1)

    def test_retry_after_an_accepted_post_reconciles_instead_of_reposting(self):
        """The failure happened after the platform accepted; retry must not duplicate."""
        item = self._approved_item()
        key = idempotency_key("acme", item.id, item.approved_fingerprint)

        # Platform accepted, then the connection dropped before we heard back.
        self.connector.publish(_request_for(key, item))
        self.assertEqual(self.connector.publish_call_count, 1)

        result = self._dispatcher().publish(item.id, force_due=True)
        self.assertTrue(result.ok)
        self.assertTrue(result.reconciled)
        self.assertEqual(self.connector.publish_call_count, 1)
        self.assertEqual(
            self.store.open("acme").get_content(item.id).status, ContentStatus.PUBLISHED
        )

    def test_external_id_reconciliation_is_persisted(self):
        item = self._approved_item()
        self._dispatcher().publish(item.id, force_due=True)
        self._dispatcher().publish(item.id, force_due=True)

        record = self.store.open("acme").get_content(item.id).publication
        self.assertTrue(record.reconciled)
        self.assertTrue(record.external_id)


def _request_for(key: str, item):
    from integrations.publishing.connector import PublishRequest

    return PublishRequest(
        idempotency_key=key,
        platform=item.platform,
        account_id=item.account,
        copy=item.copy,
        cta=item.cta,
        media=tuple(item.media),
        destination_url=item.destination_url,
        scheduled_at=item.scheduled_at,
    )


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


class TestStateMachine(PublishingCase):
    def test_invalid_transition_is_refused(self):
        from growth.errors import InvalidTransitionError

        item = self.handle.create_content(platform="linkedin", account="acct-1")
        with self.assertRaises(InvalidTransitionError):
            self.handle.transition_content(item.id, ContentStatus.PUBLISHING)

    def test_cancellation_is_allowed_before_publishing(self):
        for state in (ContentStatus.APPROVED, ContentStatus.SCHEDULED):
            with self.subTest(state=state.value):
                item = self._approved_item()
                if state is ContentStatus.SCHEDULED:
                    self._dispatcher().schedule(item.id)
                cancelled = self.handle.transition_content(
                    item.id, ContentStatus.CANCELLED, reason="pulled"
                )
                self.assertEqual(cancelled.status, ContentStatus.CANCELLED)

    def test_cancellation_is_refused_once_published(self):
        from growth.errors import InvalidTransitionError

        item = self._approved_item()
        self._dispatcher().publish(item.id, force_due=True)
        with self.assertRaises(InvalidTransitionError):
            self.store.open("acme").transition_content(item.id, ContentStatus.CANCELLED)

    def test_a_published_item_cannot_be_edited(self):
        from growth.errors import InvalidTransitionError

        item = self._approved_item()
        self._dispatcher().publish(item.id, force_due=True)
        with self.assertRaises(InvalidTransitionError):
            self.store.open("acme").update_content(item.id, copy="after the fact")

    def test_failed_item_can_be_cancelled(self):
        item = self._approved_item()
        self.connector.queue_failure("invalid_content", "nope", transient=False)
        self._dispatcher().publish(item.id, force_due=True)
        cancelled = self.store.open("acme").transition_content(
            item.id, ContentStatus.CANCELLED, reason="abandoned"
        )
        self.assertEqual(cancelled.status, ContentStatus.CANCELLED)


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


class TestSecrecy(PublishingCase):
    def test_credential_never_lands_in_any_growth_or_log_file(self):
        import os

        os.environ[SECRET_NAME] = SECRET_VALUE
        try:
            item = self._approved_item()
            self.connector.queue_failure(
                "server_error", f"upstream rejected token {SECRET_VALUE}", transient=True
            )
            self._dispatcher().publish(item.id, force_due=True)
            self.advance(hours=2)
            self._dispatcher().retry(item.id)

            for base in (self.root / "growth", self.root / "logs"):
                for path in base.rglob("*"):
                    if path.is_file():
                        self.assertNotIn(
                            SECRET_VALUE,
                            path.read_text(encoding="utf-8"),
                            f"credential leaked into {path}",
                        )
        finally:
            os.environ.pop(SECRET_NAME, None)

    def test_connector_exception_detail_is_redacted_before_persistence(self):
        import os

        os.environ[SECRET_NAME] = SECRET_VALUE
        try:
            item = self._approved_item()
            self.connector.queue_failure("server_error", f"boom: {SECRET_VALUE}", transient=True)
            result = self._dispatcher().publish(item.id, force_due=True)

            self.assertNotIn(SECRET_VALUE, result.message)
            record = self.store.open("acme").get_content(item.id).publication
            self.assertNotIn(SECRET_VALUE, record.failure_detail)
            self.assertIn("REDACTED", record.failure_detail)
        finally:
            os.environ.pop(SECRET_NAME, None)

    def test_publish_request_redacted_view_omits_the_credential(self):
        from integrations.publishing.connector import PublishRequest

        req = PublishRequest(
            idempotency_key="k",
            platform="linkedin",
            account_id="a",
            credential=SECRET_VALUE,
        )
        self.assertNotIn(SECRET_VALUE, json.dumps(req.redacted()))
        self.assertNotIn("credential", req.redacted())


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


class TestAuditTrail(PublishingCase):
    def test_audit_events_are_created_for_transitions_and_attempts(self):
        item = self._approved_item()
        self._dispatcher().publish(item.id, force_due=True)

        records = self._dispatcher().publication_status(item.id)["audit"]
        events = [r["event"] for r in records]
        self.assertIn("transition", events)
        self.assertIn("attempt", events)

        attempt = next(r for r in records if r["event"] == "attempt")
        for field_name in ("actor", "project", "content_id", "platform", "account_ref", "at"):
            self.assertTrue(attempt[field_name], f"{field_name} missing from audit record")
        self.assertEqual(attempt["result"], "success")

    def test_refusals_are_audited(self):
        item = self._approved_item()
        self.store.open("acme").pause_controller(self.root).set_pause(
            "project", True, reason="hold"
        )
        self._dispatcher().publish(item.id, force_due=True)

        records = self._dispatcher().publication_status(item.id)["audit"]
        refusals = [r for r in records if r["event"] == "refused"]
        self.assertTrue(refusals)
        self.assertEqual(refusals[-1]["failure_code"], "paused")

    def test_audit_is_partitioned_by_project(self):
        item = self._approved_item()
        self._dispatcher().publish(item.id, force_due=True)
        trail = self.root / "logs" / "growth" / "acme" / "audit.jsonl"
        self.assertTrue(trail.exists())
        self.assertFalse((self.root / "logs" / "growth" / "other").exists())


# ---------------------------------------------------------------------------
# API / CLI
# ---------------------------------------------------------------------------


class TestConnectorFactory(unittest.TestCase):
    """The factory is what production uses; the other tests inject a connector."""

    def test_injected_connector_wins(self):
        from integrations.publishing.factory import build_connector

        injected = FakePublishingConnector(platform="x")
        self.assertIs(build_connector("linkedin", connector=injected), injected)

    def test_unregistered_platform_falls_back_to_the_fake(self):
        from integrations.publishing.factory import build_connector

        with TemporaryDirectory() as tmp:
            c = build_connector("linkedin", project_root=Path(tmp))
            self.assertIsInstance(c, FakePublishingConnector)
            self.assertEqual(c.platform, "linkedin")

    def test_fake_state_persists_across_constructions(self):
        """Two CLI invocations must see the same published post."""
        from integrations.publishing.factory import build_connector

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = build_connector("linkedin", project_root=root)
            first.publish(_bare_request("idem:persisted"))
            second = build_connector("linkedin", project_root=root)
            self.assertIsNotNone(second.status("idem:persisted"))

    def test_empty_platform_is_refused(self):
        from integrations.publishing.connector import PermanentPublishError
        from integrations.publishing.factory import build_connector

        with self.assertRaises(PermanentPublishError):
            build_connector("")

    def test_schedule_and_cancel_defaults_are_safe(self):
        """A connector with no scheduling API must refuse, never publish early."""
        from integrations.publishing.connector import PermanentPublishError

        c = FakePublishingConnector(platform="linkedin")
        with self.assertRaises(PermanentPublishError):
            c.schedule(_bare_request("idem:x"))
        self.assertFalse(c.cancel("idem:x"))

    def test_validate_rejects_oversized_copy_without_truncating(self):
        c = FakePublishingConnector(platform="linkedin")
        req = _bare_request("idem:long", copy="x" * 5000)
        outcome = c.validate(req)
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.failure_code, "invalid_content")
        # The request is frozen; validation cannot have altered it.
        self.assertEqual(len(req.copy), 5000)


def _bare_request(key: str, **overrides):
    from integrations.publishing.connector import PublishRequest

    fields = {
        "idempotency_key": key,
        "platform": "linkedin",
        "account_id": "acct-1",
        "copy": "hello",
        "cta": "Book a demo",
    }
    fields.update(overrides)
    return PublishRequest(**fields)


class TestPublishingApi(unittest.TestCase):
    def test_publish_flow_through_monday_growth(self):
        with TemporaryDirectory() as tmp:
            root = _root(tmp)
            connector = FakePublishingConnector(platform="linkedin")
            monday = Monday(MondayConfig(project_root=root))
            monday.growth("workspace-init", project="acme")
            monday.growth(
                "bind",
                project="acme",
                platform="linkedin",
                account_id="acct-1",
                secret_name=SECRET_NAME,
            )
            created = monday.growth(
                "content-create",
                project="acme",
                platform="linkedin",
                account="acct-1",
                copy=APPROVED_COPY,
                cta="Book a demo",
                destination_url="https://example.com/demo",
                campaign="launch",
                expected_goal="50 demos",
                expected_audience="VP Talent",
                scheduled_at=DUE_AT,
            )
            cid = created.content_id
            monday.growth("review", project="acme", content_id=cid)
            monday.growth("approve", project="acme", content_id=cid, approved_by="human:j")

            # Drive the dispatcher with the injected fake through the service.
            service = GrowthService(root, connector=connector, now=lambda: DUE_AT)
            result = service.publish_content_now("acme", cid)
            self.assertTrue(result["ok"], result["message"])

            status = monday.growth("publication-status", project="acme", content_id=cid)
            self.assertTrue(status.success)
            self.assertEqual(status.data["publication_status"]["status"], "published")

    def test_pause_and_resume_through_the_api(self):
        with TemporaryDirectory() as tmp:
            root = _root(tmp)
            monday = Monday(MondayConfig(project_root=root))
            monday.growth("workspace-init", project="acme")

            paused = monday.growth("pause", project="acme", scope="global", reason="incident")
            self.assertTrue(paused.success)
            listed = monday.growth("pauses", project="acme")
            self.assertIn("global", listed.data["pauses"])

            monday.growth("resume", project="acme", scope="global")
            self.assertEqual(monday.growth("pauses", project="acme").data["count"], 0)

    def test_unknown_publishing_action_fails_closed(self):
        with TemporaryDirectory() as tmp:
            monday = Monday(MondayConfig(project_root=_root(tmp)))
            r = monday.growth("launch-nukes", project="acme")
            self.assertFalse(r.success)
            self.assertIn("Unknown action", r.message)


if __name__ == "__main__":
    unittest.main()
