"""
The approval inbox - the review queue, and the owner actions that act on it.

The inbox is a projection over ContentItems that already exist. It stores nothing
and decides nothing: it groups what is waiting, shows a reviewer what they need
to judge each item, and routes their decision back through the existing lifecycle.

The rule that matters most is what "approve the week" means. It is **not** a
blanket authorisation. It approves each listed post individually, against that
post's own fingerprint, and authorises nothing that was not in front of the human
when they clicked. Approving five posts produces five per-item approvals — the
same five, and no others. Anything blocked by a safety finding or awaiting a
sensitive-category decision is skipped and reported, never swept along with the
rest (ADR-013).

Changing a schedule goes through ``growth.store.update_content``, so a reschedule
resets that item's approval by the existing fingerprint rule rather than by a
special case written here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from growth.content import ContentItem, ContentStatus
from growth.generation.models import PackageStatus
from growth.generation.weekly_package import WeeklyPackage, WeeklyPackageBuilder
from growth.store import WorkspaceHandle

# The statuses the inbox groups by, in the order a reviewer works through them.
INBOX_STATUSES: tuple[ContentStatus, ...] = (
    ContentStatus.DRAFT,
    ContentStatus.AI_REVIEW,
    ContentStatus.READY_FOR_REVIEW,
    ContentStatus.CHANGES_REQUESTED,
    ContentStatus.APPROVED,
    ContentStatus.SCHEDULED,
    ContentStatus.PUBLISHED,
    ContentStatus.FAILED,
    ContentStatus.CANCELLED,
)

# Priority bands for the queue. Blocked and escalated work first, because those
# are the items that will otherwise sit unnoticed while a reviewer approves the
# easy ones.
PRIORITY_ORDER: tuple[str, ...] = (
    "blocked",
    "escalated",
    "enhanced-review",
    "awaiting-review",
    "scheduled",
    "done",
)


@dataclass
class InboxItem:
    """One row in the review queue, with everything needed to judge it."""

    content_id: str
    project: str
    campaign: str
    platform: str
    account_reference: str
    title: str
    caption: str
    media: list[str] = field(default_factory=list)
    cta: str = ""
    link: str = ""
    scheduled_at: str = ""
    scheduled_timezone: str = ""
    warnings: list[str] = field(default_factory=list)
    claim_risks: list[str] = field(default_factory=list)
    requires_enhanced_review: bool = False
    claim_review_summary: str = ""
    expected_goal: str = ""
    status: str = ""
    approval_state: str = ""
    priority: str = "awaiting-review"
    week: str = ""
    recommendation_ids: list[str] = field(default_factory=list)
    rationale: str = ""
    generated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "content_id": self.content_id,
            "project": self.project,
            "campaign": self.campaign,
            "platform": self.platform,
            "account_reference": self.account_reference,
            "title": self.title,
            "caption": self.caption,
            "media": list(self.media),
            "cta": self.cta,
            "link": self.link,
            "scheduled_at": self.scheduled_at,
            "scheduled_timezone": self.scheduled_timezone,
            "warnings": list(self.warnings),
            "claim_risks": list(self.claim_risks),
            "requires_enhanced_review": self.requires_enhanced_review,
            "claim_review_summary": self.claim_review_summary,
            "expected_goal": self.expected_goal,
            "status": self.status,
            "approval_state": self.approval_state,
            "priority": self.priority,
            "week": self.week,
            "recommendation_ids": list(self.recommendation_ids),
            "rationale": self.rationale,
            "generated": self.generated,
        }


class ApprovalInbox:
    """A projection over one workspace's content, plus the owner actions on it."""

    def __init__(self, handle: WorkspaceHandle) -> None:
        self._handle = handle

    @property
    def project(self) -> str:
        return self._handle.slug

    # ------------------------------------------------------------------
    # Reading the queue
    # ------------------------------------------------------------------

    def items(
        self, status: ContentStatus | None = None, campaign: str = "", platform: str = ""
    ) -> list[InboxItem]:
        """Every item awaiting attention, newest schedule first within a status."""
        workspace = self._handle.read()
        rows: list[InboxItem] = []
        for item in self._handle.list_content(status):
            if campaign and item.campaign != campaign:
                continue
            if platform and item.platform != platform:
                continue
            rows.append(self._project_item(item, workspace))
        rows.sort(key=lambda r: (PRIORITY_ORDER.index(r.priority), r.scheduled_at, r.content_id))
        return rows

    def grouped(self) -> dict[str, Any]:
        """
        The queue grouped the four ways a reviewer actually works.

        By campaign when planning, by platform when checking conventions, by week
        when reviewing a package, by priority when short of time.
        """
        rows = self.items()
        return {
            "project": self.project,
            "total": len(rows),
            "by_status": _group(rows, lambda r: r.status),
            "by_campaign": _group(rows, lambda r: r.campaign or "(none)"),
            "by_platform": _group(rows, lambda r: r.platform or "(none)"),
            "by_week": _group(rows, lambda r: r.week or "(unscheduled)"),
            "by_priority": _group(rows, lambda r: r.priority),
        }

    def summary(self) -> dict[str, Any]:
        """Counts by status, for a dashboard tile."""
        rows = self.items()
        counts = {status.value: 0 for status in INBOX_STATUSES}
        for row in rows:
            counts[row.status] = counts.get(row.status, 0) + 1
        return {
            "project": self.project,
            "total": len(rows),
            "counts": counts,
            "blocked": sum(1 for r in rows if r.priority == "blocked"),
            "escalated": sum(1 for r in rows if r.priority == "escalated"),
            "enhanced_review": sum(1 for r in rows if r.requires_enhanced_review),
            "awaiting_review": sum(1 for r in rows if r.priority == "awaiting-review"),
        }

    # ------------------------------------------------------------------
    # Owner actions
    # ------------------------------------------------------------------

    def submit_for_review(self, content_id: str, by: str = "human:cli") -> dict[str, Any]:
        """
        Move one item to Ready for Review.

        Refuses an item carrying a blocking warning: a blocked draft that reaches
        a reviewer looks reviewable, and the whole point of blocking it is that it
        is not.
        """
        item = self._handle.get_content(content_id)
        if item.warnings:
            return {
                "content_id": content_id,
                "ok": False,
                "reason": (
                    "Blocked by a brand-safety finding or a sensitive category: "
                    + "; ".join(item.warnings)
                ),
            }
        if item.status is ContentStatus.DRAFT:
            self._handle.transition_content(content_id, ContentStatus.AI_REVIEW, changed_by=by)
        updated = self._handle.transition_content(
            content_id, ContentStatus.READY_FOR_REVIEW, changed_by=by
        )
        return {"content_id": content_id, "ok": True, "status": updated.status.value}

    def approve(self, content_id: str, by: str, reason: str = "") -> dict[str, Any]:
        """Approve one item against its own fingerprint."""
        item = self._handle.get_content(content_id)
        if item.status is not ContentStatus.READY_FOR_REVIEW:
            return {
                "content_id": content_id,
                "ok": False,
                "reason": f"is {item.status.value}; only a ready-for-review item can be approved",
            }
        approved = self._handle.approve_content(content_id, approved_by=by, reason=reason)
        return {
            "content_id": content_id,
            "ok": True,
            "status": approved.status.value,
            "fingerprint": approved.approved_fingerprint,
        }

    def approve_week(
        self, package: WeeklyPackage, by: str, reason: str = "", only: list[str] | None = None
    ) -> dict[str, Any]:
        """
        Approve the posts in one week's package, individually.

        Each approval binds that post's own fingerprint. ``only`` narrows to a
        selected subset; without it every listed post is attempted. Nothing
        outside the package is touched, and blocked or escalated posts are skipped
        with a stated reason rather than swept along.
        """
        selected = set(only) if only else {p.content_id for p in package.posts}
        approved: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []

        for post in package.posts:
            if post.content_id not in selected:
                continue
            if post.blocked or post.escalations:
                skipped.append(
                    {
                        "content_id": post.content_id,
                        "reason": (
                            "blocked by a safety finding"
                            if post.blocked
                            else f"awaiting a decision on: {', '.join(post.escalations)}"
                        ),
                    }
                )
                continue
            submitted = self.submit_for_review(post.content_id, by=by)
            if not submitted["ok"]:
                skipped.append({"content_id": post.content_id, "reason": submitted["reason"]})
                continue
            result = self.approve(post.content_id, by=by, reason=reason or "approved with the week")
            (approved if result["ok"] else skipped).append(result)

        return {
            "package_id": package.id,
            "requested": len(selected),
            "approved": approved,
            "approved_count": len(approved),
            "skipped": skipped,
            "skipped_count": len(skipped),
            "note": (
                "Each post was approved individually against its own fingerprint. "
                "This authorises exactly these posts and nothing else."
            ),
        }

    def request_changes(self, content_id: str, by: str, reason: str) -> dict[str, Any]:
        """Send one item back to its author with notes."""
        updated = self._handle.transition_content(
            content_id, ContentStatus.CHANGES_REQUESTED, changed_by=by, reason=reason
        )
        return {"content_id": content_id, "ok": True, "status": updated.status.value}

    def reject(self, content_id: str, by: str, reason: str) -> dict[str, Any]:
        """Terminally cancel one item."""
        updated = self._handle.transition_content(
            content_id, ContentStatus.CANCELLED, changed_by=by, reason=reason
        )
        return {"content_id": content_id, "ok": True, "status": updated.status.value}

    def reschedule(
        self, content_id: str, scheduled_at: datetime, by: str = "human:cli"
    ) -> dict[str, Any]:
        """
        Change one item's publish time.

        Routed through update_content so the existing fingerprint rule resets the
        approval. Rescheduling an approved post is a material change: a launch
        post approved for Tuesday morning is not approved for Friday evening.
        """
        updated = self._handle.update_content(content_id, changed_by=by, scheduled_at=scheduled_at)
        return {
            "content_id": content_id,
            "ok": True,
            "status": updated.status.value,
            "is_approved": updated.is_approved,
            "note": (
                "Approval reset: the publish time is part of what was approved."
                if not updated.is_approved
                else "Item was not approved, so nothing was reset."
            ),
        }

    def mark_package_reviewed(
        self, package: WeeklyPackage, builder: WeeklyPackageBuilder, now: datetime
    ) -> WeeklyPackage:
        """Move a package's status to match how much of it has been approved."""
        approved = sum(
            1
            for post in package.posts
            if self._handle.get_content(post.content_id).status is ContentStatus.APPROVED
        )
        if approved == 0:
            target = PackageStatus.READY_FOR_REVIEW
        elif approved == len(package.posts):
            target = PackageStatus.APPROVED
        else:
            target = PackageStatus.PARTIALLY_APPROVED

        if package.status is target:
            return package

        # A package built this session is still DRAFT. Walk it through
        # READY_FOR_REVIEW rather than jumping the graph, so the history shows the
        # package was actually reviewed rather than teleporting to approved.
        reason = f"{approved}/{len(package.posts)} approved"
        if package.status is PackageStatus.DRAFT and target is not PackageStatus.READY_FOR_REVIEW:
            package.apply_transition(
                PackageStatus.READY_FOR_REVIEW, "human:review", "opened for review", now
            )
        if package.status is not target and _reachable(package.status, target):
            package.apply_transition(target, "human:review", reason, now)
        builder.save(package)
        return package

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _project_item(self, item: ContentItem, workspace: Any) -> InboxItem:
        binding = workspace.binding_for(item.platform) if item.platform else None
        metadata = item.metadata or {}
        return InboxItem(
            content_id=item.id,
            project=item.project,
            campaign=item.campaign,
            platform=item.platform,
            # A placeholder until account connection exists. Shows what WOULD
            # publish it, without implying anything is connected.
            account_reference=(
                f"{binding.platform}:{binding.account_id}"
                if binding
                else "(no account bound — connection arrives in a later increment)"
            ),
            title=item.title,
            caption=item.copy,
            media=list(item.media),
            cta=item.cta,
            link=item.destination_url,
            scheduled_at=(
                item.scheduled_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                if item.scheduled_at
                else ""
            ),
            scheduled_timezone=item.scheduled_timezone,
            warnings=list(item.warnings),
            claim_risks=[str(c) for c in (metadata.get("claim_risks") or [])],
            requires_enhanced_review=bool(metadata.get("requires_enhanced_review", False)),
            claim_review_summary=str(metadata.get("claim_review_summary", "")),
            expected_goal=item.expected_goal,
            status=item.status.value,
            approval_state=(
                "approved"
                if item.is_approved
                else ("stale" if item.approval_is_stale() else "unapproved")
            ),
            priority=_priority_for(item),
            week=(
                item.scheduled_at.astimezone(UTC).strftime("%G-W%V") if item.scheduled_at else ""
            ),
            recommendation_ids=[str(r) for r in (metadata.get("recommendation_ids") or [])],
            rationale=str(metadata.get("rationale", "")),
            generated=bool(metadata.get("generated", False)),
        )


def _priority_for(item: ContentItem) -> str:
    """Where an item sits in the reviewer's queue."""
    if item.warnings and item.status in (ContentStatus.DRAFT, ContentStatus.AI_REVIEW):
        return "blocked"
    if bool((item.metadata or {}).get("requires_enhanced_review")) and item.status in (
        ContentStatus.DRAFT,
        ContentStatus.AI_REVIEW,
        ContentStatus.READY_FOR_REVIEW,
    ):
        # Ranked above ordinary review, below blocked: these are publishable
        # drafts that carry a claim somebody must verify first.
        return "enhanced-review"
    if item.status in (ContentStatus.PUBLISHED, ContentStatus.CANCELLED):
        return "done"
    if item.status in (ContentStatus.SCHEDULED, ContentStatus.APPROVED):
        return "scheduled"
    if item.status is ContentStatus.FAILED:
        return "escalated"
    return "awaiting-review"


def _group(rows: list[InboxItem], key: Any) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(key(row), []).append(row.to_dict())
    return dict(sorted(grouped.items()))


def _reachable(current: PackageStatus, target: PackageStatus) -> bool:
    from growth.generation.models import can_transition

    return can_transition(current, target)
