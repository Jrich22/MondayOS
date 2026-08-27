"""
PublishDispatcher - the deterministic path from approved content to a live post.

There is no reasoning here and no model call. The dispatcher is an ordered
sequence of gates, and every one of them can only refuse. It never decides what
to say, never chooses an account, never adjusts a time, and never edits copy: by
the time content reaches this module a human has approved an exact version, and
the dispatcher's entire job is to publish that version or refuse.

Gate order, evaluated in this sequence and no other:

    1. Global emergency stop      portfolio-wide, overrides everything
    2. Project pause
    3. Platform pause
    4. Post pause
    5. Status validation          is this item admissible for this action
    6. Approval fingerprint       recomputed now, never trusted from storage
    7. Workspace/account isolation
    8. Scheduled time             timezone-aware, injected clock
    9. Idempotency                already published under this key -> reconcile
   10. Connector publish

Gates 1-8 are local: nothing reaches a connector while any of them refuses, which
is what makes a pause a real stop rather than a request. Gate 9 issues a read to
the connector because reconciliation cannot be answered locally, and it sits
after every pause so a paused item never touches the network at all.

Time is injected. ``now`` defaults to real UTC, and jitter is supplied by the
caller, so scheduling and backoff are exactly reproducible in tests and never
depend on the machine's local zone.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from core.redaction import redact_text
from growth.audit import (
    EVENT_ATTEMPT,
    EVENT_RECONCILED,
    EVENT_REFUSED,
    EVENT_TRANSITION,
    AuditRecord,
    AuditTrail,
)
from growth.content import ContentItem, ContentStatus
from growth.publication import (
    MAX_ATTEMPTS,
    PublicationAttempt,
    PublicationRecord,
    backoff_seconds,
    idempotency_key,
    is_transient,
)
from growth.store import WorkspaceHandle
from integrations.publishing.connector import (
    PublishError,
    PublishingConnector,
    PublishRequest,
)
from integrations.publishing.factory import build_connector

Clock = Callable[[], datetime]


@dataclass
class DispatchResult:
    """
    Outcome of one dispatch action.

    ``refused_at`` names the gate that stopped it, which is the single most useful
    thing an operator needs: it says what to fix.
    """

    ok: bool
    action: str
    content_id: str
    status: str = ""
    refused_at: str = ""
    failure_code: str = ""
    message: str = ""
    external_id: str = ""
    external_url: str = ""
    reconciled: bool = False
    retry_count: int = 0
    next_attempt_at: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "action": self.action,
            "content_id": self.content_id,
            "status": self.status,
            "refused_at": self.refused_at,
            "failure_code": self.failure_code,
            "message": self.message,
            "external_id": self.external_id,
            "external_url": self.external_url,
            "reconciled": self.reconciled,
            "retry_count": self.retry_count,
            "next_attempt_at": self.next_attempt_at,
            "data": dict(self.data),
        }


class PublishDispatcher:
    """Executes approved publishing instructions for one Growth workspace."""

    def __init__(
        self,
        handle: WorkspaceHandle,
        project_root: Path,
        connector: PublishingConnector | None = None,
        now: Clock | None = None,
        jitter: float = 0.0,
        actor: str = "human:cli",
    ) -> None:
        self._handle = handle
        self._root = Path(project_root)
        self._connector_override = connector
        self._now: Clock = now or (lambda: datetime.now(tz=UTC))
        self._jitter = jitter
        self._actor = actor
        self._pauses = handle.pause_controller(self._root)
        self._audit = AuditTrail(self._root, handle.slug, self._secret_env_keys())

    # ------------------------------------------------------------------
    # Public actions
    # ------------------------------------------------------------------

    def schedule(self, content_id: str) -> DispatchResult:
        """Move an approved, future-dated item to Scheduled."""
        item = self._handle.get_content(content_id)

        refusal = self._gates_1_to_7(item, "schedule")
        if refusal is not None:
            return refusal

        if item.scheduled_at is None:
            return self._refuse(
                item,
                "schedule",
                "scheduled-time",
                "not_due",
                "Item has no scheduled_at; nothing to schedule.",
            )

        self._transition(item, ContentStatus.SCHEDULED, "scheduled for publication")
        return DispatchResult(
            ok=True,
            action="schedule",
            content_id=item.id,
            status=item.status.value,
            message=(
                f"{item.id} scheduled for "
                f"{item.scheduled_at.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}"
                + (f" ({item.scheduled_timezone})" if item.scheduled_timezone else "")
            ),
        )

    def publish(self, content_id: str, force_due: bool = False) -> DispatchResult:
        """
        Publish an item now, or reconcile if this approved version already landed.

        ``force_due`` skips only gate 8 (the not-yet-due check) for an operator
        publishing a scheduled item early. It skips no other gate - approval,
        isolation, and every pause still apply.
        """
        item = self._handle.get_content(content_id)

        refusal = self._gates_1_to_7(item, "publish")
        if refusal is not None:
            return refusal

        # --- Gate 8: scheduled time -------------------------------------
        if not force_due and item.status is ContentStatus.SCHEDULED:
            if item.scheduled_at is not None and self._now() < _as_utc(item.scheduled_at):
                return self._refuse(
                    item,
                    "publish",
                    "scheduled-time",
                    "not_due",
                    f"{item.id} is scheduled for "
                    f"{_as_utc(item.scheduled_at).strftime('%Y-%m-%dT%H:%M:%SZ')}; "
                    "not due yet. Use --force to publish early.",
                )

        binding = self._handle.read().binding_for(item.platform)
        assert binding is not None  # gate 7 established this
        record = item.publication or PublicationRecord()
        record.idempotency_key = idempotency_key(item.project, item.id, item.approved_fingerprint)
        record.platform = item.platform
        record.account_ref = binding.account_id

        if record.exhausted:
            return self._refuse(
                item,
                "publish",
                "retry-budget",
                "invalid_content",
                f"{item.id} has used all {MAX_ATTEMPTS} publish attempts. "
                "Resolve the cause and re-approve, or cancel it.",
            )

        connector = self._connector_override or build_connector(item.platform, self._root)
        request = PublishRequest(
            idempotency_key=record.idempotency_key,
            platform=item.platform,
            account_id=binding.account_id,
            copy=item.copy,
            cta=item.cta,
            media=tuple(item.media),
            destination_url=item.destination_url,
            scheduled_at=_as_utc(item.scheduled_at) if item.scheduled_at else None,
            credential=self._resolve_credential(binding.secret_name),
        )

        # --- Gate 9: idempotency ----------------------------------------
        existing = connector.status(record.idempotency_key)
        if existing is not None and existing.success:
            return self._reconcile(item, record, existing)

        if item.status is ContentStatus.PUBLISHED:
            # Local state says published but the platform does not recognise the key.
            # Publishing again could duplicate a live post, so this needs a human.
            return self._refuse(
                item,
                "publish",
                "idempotency",
                "invalid_state",
                f"{item.id} is recorded as published but the {item.platform} connector "
                "does not recognise its publication key. Refusing to publish again; "
                "verify the post manually.",
            )

        # --- Gate 10: publish -------------------------------------------
        self._transition(item, ContentStatus.PUBLISHING, "publishing")
        attempt_number = record.retry_count + 1
        attempted_at = self._now()
        record.attempted_at = attempted_at

        try:
            outcome = connector.publish(request)
        except PublishError as exc:
            return self._record_failure(
                item,
                record,
                attempt_number,
                attempted_at,
                code=exc.code,
                detail=redact_text(str(exc), self._secret_env_keys()),
            )
        except Exception as exc:  # noqa: BLE001 - an adapter bug must not lose the item
            return self._record_failure(
                item,
                record,
                attempt_number,
                attempted_at,
                code="unavailable",
                detail=redact_text(f"{type(exc).__name__}: {exc}", self._secret_env_keys()),
            )

        if not outcome.success:
            return self._record_failure(
                item,
                record,
                attempt_number,
                attempted_at,
                code=outcome.failure_code or "invalid_content",
                detail=redact_text(outcome.failure_detail, self._secret_env_keys()),
            )

        record.retry_count = attempt_number
        record.external_id = outcome.external_id
        record.external_url = outcome.external_url
        record.published_at = attempted_at
        record.failure_code = ""
        record.failure_detail = ""
        record.next_attempt_at = None
        record.reconciled = bool(outcome.already_published)
        record.connector_metadata = dict(outcome.metadata)
        record.attempts.append(
            PublicationAttempt(attempt=attempt_number, attempted_at=attempted_at, success=True)
        )
        item.publication = record

        self._audit.record(
            AuditRecord(
                event=EVENT_ATTEMPT,
                project=item.project,
                content_id=item.id,
                actor=self._actor,
                platform=item.platform,
                account_ref=record.account_ref,
                attempt=attempt_number,
                result="success",
                extra={"external_id": record.external_id},
            )
        )
        self._transition(item, ContentStatus.PUBLISHED, "published")

        return DispatchResult(
            ok=True,
            action="publish",
            content_id=item.id,
            status=item.status.value,
            external_id=record.external_id,
            external_url=record.external_url,
            retry_count=record.retry_count,
            message=f"{item.id} published as {record.external_id}.",
            data={"publication": record.to_dict()},
        )

    def retry(self, content_id: str) -> DispatchResult:
        """Re-attempt a Failed item once its backoff window has elapsed."""
        item = self._handle.get_content(content_id)
        if item.status is not ContentStatus.FAILED:
            return self._refuse(
                item,
                "retry",
                "status",
                "invalid_state",
                f"{item.id} is {item.status.value}; only a failed item can be retried.",
            )
        record = item.publication
        if record is not None and record.next_attempt_at is not None:
            if self._now() < _as_utc(record.next_attempt_at):
                return self._refuse(
                    item,
                    "retry",
                    "backoff",
                    "not_due",
                    f"{item.id} is in backoff until "
                    f"{_as_utc(record.next_attempt_at).strftime('%Y-%m-%dT%H:%M:%SZ')}.",
                )
        return self.publish(content_id, force_due=True)

    def publication_status(self, content_id: str) -> dict[str, Any]:
        """Everything an operator needs about one item's publication state."""
        item = self._handle.get_content(content_id)
        pause = self._pauses.evaluate(platform=item.platform, content_id=item.id)
        record = item.publication
        return {
            "content_id": item.id,
            "project": item.project,
            "status": item.status.value,
            "is_approved": item.is_approved,
            # Whether the recorded approval still matches the content, independent
            # of the item's state. A published item is not "approved" (it has moved
            # on) but its approval did cover exactly what went out, and an operator
            # reading a status screen needs that distinction.
            "approval_covers_content": item.approval_covers_current_content(),
            "approval_is_stale": item.approval_is_stale(),
            "scheduled_at": (
                _as_utc(item.scheduled_at).strftime("%Y-%m-%dT%H:%M:%SZ")
                if item.scheduled_at
                else ""
            ),
            "scheduled_timezone": item.scheduled_timezone,
            "pause": pause.to_dict(),
            "publication": record.to_dict() if record else None,
            "attempts": len(record.attempts) if record else 0,
            "audit": self._audit.read(content_id=item.id),
        }

    # ------------------------------------------------------------------
    # Gates 1-7, shared by schedule and publish
    # ------------------------------------------------------------------

    def _gates_1_to_7(self, item: ContentItem, action: str) -> DispatchResult | None:
        """Return a refusal, or None when the item may proceed."""
        # --- Gates 1-4: pause scopes, outermost first -------------------
        pause = self._pauses.evaluate(platform=item.platform, content_id=item.id)
        if pause.paused:
            return self._refuse(
                item,
                action,
                f"pause:{pause.scope}",
                "emergency_stop" if pause.scope == "global" else "paused",
                pause.describe(),
            )

        # --- Gate 5: status --------------------------------------------
        # PUBLISHED is admissible for publish so a repeated call reconciles rather
        # than erroring: publishing is idempotent, and the caller of a retry cannot
        # always know whether the previous attempt landed.
        admissible = (
            {ContentStatus.APPROVED}
            if action == "schedule"
            else {
                ContentStatus.APPROVED,
                ContentStatus.SCHEDULED,
                ContentStatus.FAILED,
                ContentStatus.PUBLISHED,
            }
        )
        if item.status not in admissible:
            return self._refuse(
                item,
                action,
                "status",
                "not_approved",
                f"{item.id} is {item.status.value}; {action} requires "
                f"{' or '.join(sorted(s.value for s in admissible))}.",
            )

        # --- Gate 6: approval fingerprint, recomputed now ---------------
        # Asks whether the approval still covers this content, not whether the
        # item is sitting in APPROVED - a scheduled or failed item carries a
        # perfectly valid approval, and gate 5 already ruled on the state.
        if not item.approval_covers_current_content():
            self._reset_stale_approval(item)
            return self._refuse(
                item,
                action,
                "fingerprint",
                "fingerprint_mismatch",
                f"{item.id} is no longer covered by its approval - an approved field "
                "changed since sign-off. It has been returned to ready-for-review.",
            )

        # --- Gate 7: workspace / account isolation ----------------------
        workspace = self._handle.read()
        if not item.platform:
            return self._refuse(
                item, action, "isolation", "missing_account", f"{item.id} names no platform."
            )
        binding = workspace.binding_for(item.platform)
        if binding is None:
            return self._refuse(
                item,
                action,
                "isolation",
                "missing_account",
                f"No active {item.platform!r} binding in workspace {self._handle.slug!r}. "
                "Bind an account for this project; there is no cross-project fallback.",
            )
        if item.account and item.account != binding.account_id:
            return self._refuse(
                item,
                action,
                "isolation",
                "account_mismatch",
                f"{item.id} targets account {item.account!r} but workspace "
                f"{self._handle.slug!r} binds {binding.account_id!r} for "
                f"{item.platform!r}.",
            )
        return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _reconcile(
        self, item: ContentItem, record: PublicationRecord, outcome: Any
    ) -> DispatchResult:
        """Adopt an existing platform publication instead of posting again."""
        record.external_id = outcome.external_id
        record.external_url = outcome.external_url
        record.published_at = record.published_at or self._now()
        record.reconciled = True
        record.failure_code = ""
        record.failure_detail = ""
        record.next_attempt_at = None
        record.connector_metadata = dict(outcome.metadata)
        item.publication = record

        self._audit.record(
            AuditRecord(
                event=EVENT_RECONCILED,
                project=item.project,
                content_id=item.id,
                actor=self._actor,
                platform=item.platform,
                account_ref=record.account_ref,
                result="reconciled",
                extra={"external_id": record.external_id},
            )
        )
        if item.status is not ContentStatus.PUBLISHED:
            if item.status is not ContentStatus.PUBLISHING:
                self._transition(item, ContentStatus.PUBLISHING, "reconciling")
            self._transition(item, ContentStatus.PUBLISHED, "already published; reconciled")
        else:
            self._handle.save_content(item)

        return DispatchResult(
            ok=True,
            action="publish",
            content_id=item.id,
            status=item.status.value,
            external_id=record.external_id,
            external_url=record.external_url,
            reconciled=True,
            retry_count=record.retry_count,
            message=(
                f"{item.id} was already published as {record.external_id}; "
                "reconciled without posting again."
            ),
            data={"publication": record.to_dict()},
        )

    def _record_failure(
        self,
        item: ContentItem,
        record: PublicationRecord,
        attempt_number: int,
        attempted_at: datetime,
        code: str,
        detail: str,
    ) -> DispatchResult:
        """Record a failed attempt, set backoff when the cause is transient."""
        transient = is_transient(code)
        record.retry_count = attempt_number
        record.failure_code = code
        record.failure_detail = detail
        record.attempts.append(
            PublicationAttempt(
                attempt=attempt_number,
                attempted_at=attempted_at,
                success=False,
                failure_code=code,
                failure_detail=detail,
                transient=transient,
            )
        )
        if transient and attempt_number < MAX_ATTEMPTS:
            delay = backoff_seconds(attempt_number, self._jitter)
            record.next_attempt_at = attempted_at + timedelta(seconds=delay)
        else:
            record.next_attempt_at = None
        item.publication = record

        self._audit.record(
            AuditRecord(
                event=EVENT_ATTEMPT,
                project=item.project,
                content_id=item.id,
                actor=self._actor,
                platform=item.platform,
                account_ref=record.account_ref,
                attempt=attempt_number,
                result="failure",
                failure_code=code,
                detail=detail,
                extra={"transient": transient},
            )
        )
        self._transition(item, ContentStatus.FAILED, f"publish failed: {code}")

        retryable = transient and attempt_number < MAX_ATTEMPTS
        return DispatchResult(
            ok=False,
            action="publish",
            content_id=item.id,
            status=item.status.value,
            refused_at="connector",
            failure_code=code,
            retry_count=record.retry_count,
            next_attempt_at=(
                _as_utc(record.next_attempt_at).strftime("%Y-%m-%dT%H:%M:%SZ")
                if record.next_attempt_at
                else ""
            ),
            message=(
                f"{item.id} failed ({code}): {detail}"
                + (
                    f" Retry after {_as_utc(record.next_attempt_at).strftime('%H:%M:%SZ')}."
                    if retryable and record.next_attempt_at
                    else " This failure is not retryable; it needs a human."
                )
            ),
            data={"publication": record.to_dict()},
        )

    def _reset_stale_approval(self, item: ContentItem) -> None:
        """Return an item whose approved fields changed to Ready for review."""
        if item.status in (ContentStatus.APPROVED, ContentStatus.SCHEDULED):
            self._transition(
                item,
                ContentStatus.READY_FOR_REVIEW,
                "approved fields changed; approval no longer covers this content",
                actor="system",
            )
            item.approved_fingerprint = ""
            item.approved_by = ""
            item.approved_at = None
            self._handle.save_content(item)

    def _transition(
        self, item: ContentItem, new_status: ContentStatus, reason: str, actor: str = ""
    ) -> None:
        """Apply a transition, persist it, and audit it."""
        old = item.status.value
        item.apply_transition(new_status, changed_by=actor or self._actor, reason=reason)
        self._handle.save_content(item)
        self._audit.record(
            AuditRecord(
                event=EVENT_TRANSITION,
                project=item.project,
                content_id=item.id,
                actor=actor or self._actor,
                platform=item.platform,
                old_status=old,
                new_status=new_status.value,
                reason=reason,
            )
        )

    def _refuse(
        self, item: ContentItem, action: str, gate: str, code: str, message: str
    ) -> DispatchResult:
        """Record and return a gate refusal. Nothing is mutated except the audit."""
        self._audit.record(
            AuditRecord(
                event=EVENT_REFUSED,
                project=item.project,
                content_id=item.id,
                actor=self._actor,
                platform=item.platform,
                result="refused",
                failure_code=code,
                reason=message,
                extra={"gate": gate, "action": action},
            )
        )
        return DispatchResult(
            ok=False,
            action=action,
            content_id=item.id,
            status=item.status.value,
            refused_at=gate,
            failure_code=code,
            message=message,
        )

    def _secret_env_keys(self) -> tuple[str, ...]:
        """Secret names bound in this workspace, for redaction."""
        try:
            return tuple(b.secret_name for b in self._handle.read().bindings if b.secret_name)
        except Exception:  # noqa: BLE001 - redaction must work even mid-failure
            return ()

    @staticmethod
    def _resolve_credential(secret_name: str) -> str:
        """Read the credential at call time. Never stored, never returned upward."""
        return os.environ.get(secret_name, "") if secret_name else ""


def _as_utc(value: datetime) -> datetime:
    """Interpret a stored timestamp as UTC, never as machine-local time."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
