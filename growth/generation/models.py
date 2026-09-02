"""
Domain types for content generation.

Generation sits on top of the Growth Brain and never reaches into it. The Brain
decides what is worth saying and why; this subsystem turns those conclusions into
drafts. No reasoning happens here - if a rule about *what to do* appears in this
package, it is in the wrong place.

Two invariants are enforced by the types rather than by review.

**Nothing appears out of nowhere.** Every generated asset must cite at least one
recommendation, campaign, or experiment id. A draft with no provenance is
content someone will later find in the queue with no idea why it exists, and the
honest thing is to refuse to create it.

**Brand context is required, not optional.** A generation request without voice,
audience and objective is refused rather than falling back to a generic
template, because a generic template published under a brand's name is worse
than nothing being published at all.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

# Claims a draft may never make. Checked at generation and again before an item
# is allowed to reach review, because a rule enforced once is a rule that gets
# bypassed by the second code path someone adds.
FORBIDDEN_CLAIM_PATTERNS: tuple[tuple[str, str], ...] = (
    ("fabricated-statistic", r"\b\d{1,3}(\.\d+)?%\s+(of\s+)?(customers|users|companies|teams)\b"),
    ("fabricated-testimonial", r'\b(customers?|users?|clients?)\s+say\s*[:"]'),
    ("unsupported-superlative", r"\b(guaranteed|proven to|#1|number one|best[- ]in[- ]class)\b"),
    ("unsupported-claim", r"\b(always|never)\s+(works|fails|delivers)\b"),
)

# Categories that must reach a human rather than moving on automatically.
SENSITIVE_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("legal", r"\b(lawsuit|litigation|patent|trademark|liability|gdpr|compliance)\b"),
    ("medical", r"\b(diagnos|treatment|patient|clinical|medication|therapy)\b"),
    ("financial", r"\b(revenue guarantee|roi guarantee|investment advice|returns of)\b"),
    ("reputational", r"\b(competitor.{0,20}(fail|worse|inferior)|scandal|allegation)\b"),
    ("pii", r"\b([\w.+-]+@[\w-]+\.\w+|\+?\d[\d\s().-]{8,}\d)\b"),
)


class AssetKind(Enum):
    """What is being produced. Broader than ContentType - includes briefs."""

    LINKEDIN_POST = "linkedin-post"
    X_POST = "x-post"
    X_THREAD = "x-thread"
    FACEBOOK_POST = "facebook-post"
    INSTAGRAM_CAPTION = "instagram-caption"
    TIKTOK_SCRIPT = "tiktok-script"
    YOUTUBE_SHORT_SCRIPT = "youtube-short-script"
    BLOG_ARTICLE = "blog-article"
    NEWSLETTER = "newsletter"
    PRODUCT_ANNOUNCEMENT = "product-announcement"
    EDUCATIONAL_POST = "educational-post"
    LAUNCH_CAMPAIGN = "launch-campaign"
    COMMUNITY_POST = "community-post"
    PARTNER_OUTREACH = "partner-outreach"
    SEO_ARTICLE = "seo-article"
    CAROUSEL_BRIEF = "carousel-brief"
    IMAGE_BRIEF = "image-brief"
    VIDEO_BRIEF = "video-brief"


class PackageStatus(Enum):
    """Lifecycle of a weekly marketing package."""

    DRAFT = "draft"
    READY_FOR_REVIEW = "ready-for-review"
    PARTIALLY_APPROVED = "partially-approved"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


_PACKAGE_TRANSITIONS: dict[PackageStatus, set[PackageStatus]] = {
    PackageStatus.DRAFT: {PackageStatus.READY_FOR_REVIEW, PackageStatus.CANCELLED},
    PackageStatus.READY_FOR_REVIEW: {
        PackageStatus.PARTIALLY_APPROVED,
        PackageStatus.APPROVED,
        PackageStatus.REJECTED,
        PackageStatus.CANCELLED,
    },
    PackageStatus.PARTIALLY_APPROVED: {
        PackageStatus.APPROVED,
        PackageStatus.REJECTED,
        PackageStatus.CANCELLED,
    },
    PackageStatus.APPROVED: set(),
    PackageStatus.REJECTED: set(),
    PackageStatus.CANCELLED: set(),
}


class MissingProvenanceError(ValueError):
    """Raised when an asset would be created with nothing to explain why."""

    def __init__(self, title: str) -> None:
        super().__init__(
            f"Refusing to generate {title!r}: it cites no recommendation, campaign, or "
            "experiment. Content with no provenance is content nobody can later explain, "
            "and this subsystem does not create it."
        )


class MissingBrandContextError(ValueError):
    """Raised when a generation request has no brand context to work from."""

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(
            f"Refusing to generate without brand context. Missing: {', '.join(missing)}. "
            "A generic template published under a brand's name is worse than publishing "
            "nothing; complete the project's growth onboarding first."
        )


class InvalidPackageTransitionError(ValueError):
    """Raised when a weekly package is moved along an illegal edge."""

    def __init__(self, from_status: str, to_status: str) -> None:
        super().__init__(f"Illegal package transition {from_status} -> {to_status}.")


@dataclass(frozen=True)
class BrandContext:
    """
    Everything a draft must be written against.

    Assembled from the workspace rather than passed around ad hoc, so a caller
    cannot quietly generate with half the brand missing.
    """

    project: str
    voice: str
    tone: str = ""
    style_rules: tuple[str, ...] = ()
    audience: str = ""
    personas: tuple[str, ...] = ()
    pain_points: tuple[str, ...] = ()
    objective: str = ""
    content_pillars: tuple[str, ...] = ()
    ctas: tuple[str, ...] = ()
    approved_assets: tuple[str, ...] = ()
    prohibited: tuple[str, ...] = ()
    products: tuple[str, ...] = ()
    website: str = ""

    def validate(self) -> None:
        """Refuse a context too thin to write against. Raises MissingBrandContextError."""
        missing: list[str] = []
        if not self.voice.strip():
            missing.append("brand voice")
        if not (self.audience.strip() or self.personas):
            missing.append("audience")
        if not self.objective.strip():
            missing.append("campaign objective")
        if missing:
            raise MissingBrandContextError(missing)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "voice": self.voice,
            "tone": self.tone,
            "style_rules": list(self.style_rules),
            "audience": self.audience,
            "personas": list(self.personas),
            "pain_points": list(self.pain_points),
            "objective": self.objective,
            "content_pillars": list(self.content_pillars),
            "ctas": list(self.ctas),
            "approved_assets": list(self.approved_assets),
            "prohibited": list(self.prohibited),
            "products": list(self.products),
            "website": self.website,
        }


@dataclass
class SafetyFinding:
    """One brand-safety problem found in a draft."""

    rule: str
    category: str
    detail: str
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "category": self.category,
            "detail": self.detail,
            "blocking": self.blocking,
        }


@dataclass
class GeneratedAsset:
    """
    One drafted piece, with everything needed to explain why it exists.

    ``draft`` is the body. It enters the content lifecycle as DRAFT and is subject
    to the same review and approval as anything written by hand - generation
    creates no shortcut past a human.
    """

    id: str
    project: str
    kind: AssetKind
    title: str
    platform: str
    campaign: str
    theme: str
    cta: str
    goal: str
    draft: str
    recommendation_ids: list[str] = field(default_factory=list)
    experiment_ids: list[str] = field(default_factory=list)
    variant_group_id: str = ""
    destination_url: str = ""
    media_refs: list[str] = field(default_factory=list)
    audience: str = ""
    rationale: str = ""
    safety_findings: list[SafetyFinding] = field(default_factory=list)
    escalations: list[str] = field(default_factory=list)
    # Claim SHAPES a human must verify. Escalation, not blocking: the draft is
    # normal and may reach review, but it may not clear review on the strength
    # of a regex having found nothing wrong.
    claim_risks: list[str] = field(default_factory=list)
    claim_flags: list[dict[str, Any]] = field(default_factory=list)
    requires_enhanced_review: bool = False
    estimated_minutes: int = 0
    # How this draft was made. Recorded for provenance, never for branching -
    # and deliberately NOT part of the approval fingerprint: approval is about
    # the exact output, not about which writer produced it.
    generation_method: str = "template"
    provider: str = ""
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if not (self.recommendation_ids or self.campaign or self.experiment_ids):
            raise MissingProvenanceError(self.title)

    @property
    def is_blocked(self) -> bool:
        """True when a safety finding prevents this draft reaching review."""
        return any(f.blocking for f in self.safety_findings)

    @property
    def needs_escalation(self) -> bool:
        """True when a sensitive category means a human must look before anything else."""
        return bool(self.escalations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project": self.project,
            "kind": self.kind.value,
            "title": self.title,
            "platform": self.platform,
            "campaign": self.campaign,
            "theme": self.theme,
            "cta": self.cta,
            "goal": self.goal,
            "draft": self.draft,
            "recommendation_ids": list(self.recommendation_ids),
            "experiment_ids": list(self.experiment_ids),
            "variant_group_id": self.variant_group_id,
            "destination_url": self.destination_url,
            "media_refs": list(self.media_refs),
            "audience": self.audience,
            "rationale": self.rationale,
            "safety_findings": [f.to_dict() for f in self.safety_findings],
            "escalations": list(self.escalations),
            "claim_risks": list(self.claim_risks),
            "claim_flags": list(self.claim_flags),
            "requires_enhanced_review": self.requires_enhanced_review,
            "blocked": self.is_blocked,
            "needs_escalation": self.needs_escalation,
            "estimated_minutes": self.estimated_minutes,
            "generation_method": self.generation_method,
            "provider": self.provider,
            "created_at": _fmt(self.created_at) if self.created_at else "",
        }


@dataclass
class PlannedPost:
    """One slot in a week's plan, before anything is written."""

    slot: int
    platform: str
    kind: AssetKind
    campaign: str
    theme: str
    scheduled_at: datetime
    goal: str = ""
    cta: str = ""
    recommendation_ids: list[str] = field(default_factory=list)
    experiment_ids: list[str] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "platform": self.platform,
            "kind": self.kind.value,
            "campaign": self.campaign,
            "theme": self.theme,
            "scheduled_at": _fmt(self.scheduled_at),
            "goal": self.goal,
            "cta": self.cta,
            "recommendation_ids": list(self.recommendation_ids),
            "experiment_ids": list(self.experiment_ids),
            "rationale": self.rationale,
        }


def asset_id(project: str, kind: str, *parts: str) -> str:
    """A stable id derived from what the asset is, never from a clock."""
    material = "|".join([project, kind, *[p.strip().lower() for p in parts]])
    return f"GEN-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:12]}"


def package_id(project: str, week_start: datetime) -> str:
    """A weekly package id derived from the project and the week it covers."""
    return f"WEEK-{project}-{week_start.astimezone(UTC).strftime('%Y-%m-%d')}"


def can_transition(current: PackageStatus, target: PackageStatus) -> bool:
    """Validate a package edge against the transition graph."""
    return target in _PACKAGE_TRANSITIONS.get(current, set())


def _fmt(value: datetime) -> str:
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.strftime("%Y-%m-%dT%H:%M:%SZ")
