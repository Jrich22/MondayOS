"""
Approval fingerprints for content items (ADR-013).

The specification rule is "if anything changes, approval resets". Taken literally
that is unusable — an internal note would revoke an approval. This module makes the
boundary exact: an approval is a hash over the fields a human actually approved, and
an item is approved only while its current hash equals the approved one.

Nothing here normalizes its inputs. The fingerprint covers exactly the bytes that
would be published, so a trailing space in the copy is a different fingerprint. Any
normalization would open a gap between what a human approved and what goes out.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime

# Bumping this invalidates every standing approval. That is the intended direction:
# if the field set changes, previously approved items must be re-approved rather
# than appear to match under a contract nobody reviewed them against.
FINGERPRINT_SCHEME = 1

# The approved fields, as data. ADR-013 makes this list a security-relevant contract,
# so it lives in one place and the tests iterate it rather than restating it.
FINGERPRINTED_FIELDS: tuple[str, ...] = (
    "project",
    "platform",
    "account",
    "media",
    "copy_and_cta",
    "destination_url",
    "scheduled_at",
)


def canonical_payload(
    *,
    project: str,
    platform: str,
    account: str,
    media: Sequence[str],
    copy: str,
    cta: str,
    destination_url: str,
    scheduled_at: datetime | None,
) -> dict[str, object]:
    """Build the exact structure that gets hashed. Media order is significant."""
    return {
        "_scheme": FINGERPRINT_SCHEME,
        "project": project,
        "platform": platform,
        "account": account,
        "media": list(media),
        "copy_and_cta": {"copy": copy, "cta": cta},
        "destination_url": destination_url,
        "scheduled_at": format_scheduled_at(scheduled_at),
    }


def compute_fingerprint(
    *,
    project: str,
    platform: str,
    account: str,
    media: Sequence[str],
    copy: str,
    cta: str,
    destination_url: str,
    scheduled_at: datetime | None,
) -> str:
    """Return the approval fingerprint, prefixed with its algorithm."""
    payload = canonical_payload(
        project=project,
        platform=platform,
        account=account,
        media=media,
        copy=copy,
        cta=cta,
        destination_url=destination_url,
        scheduled_at=scheduled_at,
    )
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def format_scheduled_at(value: datetime | None) -> str:
    """
    Render a schedule time as a stable UTC string ("" when unscheduled).

    A naive datetime is taken as already-UTC rather than converted from local time:
    MondayOS timestamps are UTC by contract (core.types.Timestamp), and converting
    from whatever timezone the machine happens to be in would make the same stored
    item fingerprint differently on two machines.
    """
    if value is None:
        return ""
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.strftime("%Y-%m-%dT%H:%M:%SZ")
