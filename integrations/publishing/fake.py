"""
Deterministic in-memory publishing connector.

The only connector that exists in this increment. It performs no network I/O and
behaves identically on every run, so the dispatcher's gate order, retry policy,
and idempotency handling are all testable without a platform account.

Failure injection is explicit rather than random: a caller queues the failures it
wants, in order, and the connector raises them. A connector that failed randomly
would make the retry tests flaky, which is the opposite of what they are for.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from integrations.publishing.connector import (
    PermanentPublishError,
    PublishingConnector,
    PublishOutcome,
    PublishRequest,
    TransientPublishError,
)

_MAX_COPY_LENGTH = 3000
_MAX_MEDIA_ITEMS = 10


@dataclass
class QueuedFailure:
    """One failure to raise on the next publish call."""

    code: str
    detail: str = ""
    transient: bool = True


class FakePublishingConnector(PublishingConnector):
    """
    Offline stand-in for a real platform adapter.

    Pass ``store_path`` to persist accepted publications to JSON so state survives
    across separate process invocations — two CLI calls in a smoke test see the
    same published post, which is what makes the idempotency path demonstrable
    outside a single test process.
    """

    def __init__(
        self,
        platform: str = "linkedin",
        store_path: Path | None = None,
        base_url: str = "https://example.invalid",
    ) -> None:
        self.platform = platform
        self._base = base_url.rstrip("/")
        self._store_path = Path(store_path) if store_path else None
        self._posts: dict[str, dict[str, Any]] = {}
        self._failures: list[QueuedFailure] = []
        self._counter = 0
        self._load()

    # ------------------------------------------------------------------
    # Test controls
    # ------------------------------------------------------------------

    def queue_failure(self, code: str, detail: str = "", transient: bool = True) -> None:
        """Queue one failure to raise on the next publish call."""
        self._failures.append(QueuedFailure(code=code, detail=detail, transient=transient))

    @property
    def publish_call_count(self) -> int:
        """How many times publish() actually created a post (not reconciled)."""
        return self._counter

    # ------------------------------------------------------------------
    # PublishingConnector
    # ------------------------------------------------------------------

    def validate(self, request: PublishRequest) -> PublishOutcome:
        if request.platform != self.platform:
            return PublishOutcome(
                success=False,
                failure_code="account_mismatch",
                failure_detail=(
                    f"Request targets {request.platform!r} but this connector serves "
                    f"{self.platform!r}."
                ),
            )
        if not request.account_id:
            return PublishOutcome(
                success=False,
                failure_code="permission_denied",
                failure_detail="No account id on the request.",
            )
        if len(request.copy) > _MAX_COPY_LENGTH:
            return PublishOutcome(
                success=False,
                failure_code="invalid_content",
                failure_detail=(
                    f"Copy is {len(request.copy)} characters; the limit is {_MAX_COPY_LENGTH}."
                ),
            )
        if len(request.media) > _MAX_MEDIA_ITEMS:
            return PublishOutcome(
                success=False,
                failure_code="media_rejected",
                failure_detail=(
                    f"{len(request.media)} media items; the limit is {_MAX_MEDIA_ITEMS}."
                ),
            )
        return PublishOutcome(success=True)

    def publish(self, request: PublishRequest) -> PublishOutcome:
        # Idempotency first: a retry of an accepted key must never create a second
        # post, so this check precedes both failure injection and validation.
        existing = self.status(request.idempotency_key)
        if existing is not None:
            existing.already_published = True
            return existing

        if self._failures:
            failure = self._failures.pop(0)
            message = failure.detail or f"injected {failure.code}"
            if failure.transient:
                raise TransientPublishError(message, code=failure.code)
            raise PermanentPublishError(message, code=failure.code)

        precheck = self.validate(request)
        if not precheck.success:
            raise PermanentPublishError(
                precheck.failure_detail or "request failed validation",
                code=precheck.failure_code,
            )

        self._counter += 1
        external_id = f"{self.platform}-post-{self._counter}"
        self._posts[request.idempotency_key] = {
            "external_id": external_id,
            "external_url": f"{self._base}/{self.platform}/{external_id}",
            "account_id": request.account_id,
            # The exact approved payload, stored so tests can prove it was not
            # altered anywhere on the path. The credential is deliberately absent.
            "copy": request.copy,
            "cta": request.cta,
            "media": list(request.media),
            "destination_url": request.destination_url,
        }
        self._save()
        return PublishOutcome(
            success=True,
            external_id=external_id,
            external_url=self._posts[request.idempotency_key]["external_url"],
            metadata={"platform": self.platform, "account_id": request.account_id},
        )

    def status(self, idempotency_key: str) -> PublishOutcome | None:
        post = self._posts.get(idempotency_key)
        if post is None:
            return None
        return PublishOutcome(
            success=True,
            external_id=post["external_id"],
            external_url=post["external_url"],
            already_published=True,
            metadata={"platform": self.platform, "account_id": post.get("account_id", "")},
        )

    def published_payload(self, idempotency_key: str) -> dict[str, Any] | None:
        """What was actually sent to the platform, for verification in tests."""
        post = self._posts.get(idempotency_key)
        return dict(post) if post else None

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if self._store_path is None or not self._store_path.exists():
            return
        try:
            data = json.loads(self._store_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if isinstance(data, dict):
            self._posts = dict(data.get("posts") or {})
            self._counter = int(data.get("counter") or 0)

    def _save(self) -> None:
        if self._store_path is None:
            return
        try:
            self._store_path.parent.mkdir(parents=True, exist_ok=True)
            self._store_path.write_text(
                json.dumps(
                    {"posts": self._posts, "counter": self._counter}, indent=2, sort_keys=True
                ),
                encoding="utf-8",
            )
        except OSError:
            # Losing the fake's own state must not fail a publish the caller
            # already observed as successful.
            pass
