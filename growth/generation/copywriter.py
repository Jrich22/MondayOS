"""
The copywriter - drafting against a project's brand, and the safety gate.

Drafting is deterministic and template-driven. That is a deliberate choice for
this increment, not a placeholder for something smarter: a template assembled
from the project's own voice, pillars, pain points and CTAs produces a draft an
editor can work with, and it produces the *same* draft every run, which is what
lets a weekly package be diffed against last week's.

``ContentWriter`` is a protocol. A provider-backed writer plugs in behind it
without changing anything above this module - the same seam
``integrations/publishing`` uses for connectors. Nothing in this package assumes
the writer is deterministic except the tests, which supply their own.

The safety gate is the part that matters most. It runs at generation AND again
before an item may reach review, because a rule enforced in one place is a rule
the second code path forgets. It refuses fabricated statistics, invented
testimonials, unsupported superlatives, and anything matching the project's own
prohibited list; and it escalates legal, medical, financial, reputational and PII
content to a human rather than letting it move on.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from growth.generation.claim_risk import classify_claims
from growth.generation.model_writer import (
    GENERATION_METHOD_MODEL,
    GENERATION_METHOD_TEMPLATE,
)
from growth.generation.models import (
    FORBIDDEN_CLAIM_PATTERNS,
    SENSITIVE_CATEGORIES,
    AssetKind,
    BrandContext,
    GeneratedAsset,
    PlannedPost,
    SafetyFinding,
    asset_id,
)

_COMPILED_FORBIDDEN = tuple(
    (rule, re.compile(pattern, re.IGNORECASE)) for rule, pattern in FORBIDDEN_CLAIM_PATTERNS
)
_COMPILED_SENSITIVE = tuple(
    (category, re.compile(pattern, re.IGNORECASE)) for category, pattern in SENSITIVE_CATEGORIES
)

# Rough drafting effort per asset kind, in minutes. Used for the week's estimate.
EFFORT_MINUTES: dict[AssetKind, int] = {
    AssetKind.LINKEDIN_POST: 20,
    AssetKind.X_POST: 10,
    AssetKind.X_THREAD: 30,
    AssetKind.FACEBOOK_POST: 15,
    AssetKind.INSTAGRAM_CAPTION: 15,
    AssetKind.TIKTOK_SCRIPT: 40,
    AssetKind.YOUTUBE_SHORT_SCRIPT: 40,
    AssetKind.BLOG_ARTICLE: 180,
    AssetKind.NEWSLETTER: 90,
    AssetKind.PRODUCT_ANNOUNCEMENT: 45,
    AssetKind.EDUCATIONAL_POST: 30,
    AssetKind.LAUNCH_CAMPAIGN: 120,
    AssetKind.COMMUNITY_POST: 15,
    AssetKind.PARTNER_OUTREACH: 30,
    AssetKind.SEO_ARTICLE: 200,
    AssetKind.CAROUSEL_BRIEF: 45,
    AssetKind.IMAGE_BRIEF: 20,
    AssetKind.VIDEO_BRIEF: 30,
}


class ContentWriter(Protocol):
    """
    The drafting seam.

    An implementation turns a planned slot plus brand context into body copy. It
    makes no decision about whether the content should exist - the planner already
    settled that - and it never publishes, schedules, or approves anything.
    """

    def write(self, post: PlannedPost, brand: BrandContext, angle: str) -> tuple[str, str]:
        """Return (title, body) for one planned slot."""
        ...


class TemplateContentWriter:
    """
    Deterministic drafting from the project's own brand material.

    Every sentence is assembled from something the project actually said about
    itself during onboarding - its voice, pillars, pain points, products and CTAs.
    Nothing is invented, which is why this writer cannot produce a fabricated
    statistic even before the safety gate looks at it.
    """

    def write(self, post: PlannedPost, brand: BrandContext, angle: str) -> tuple[str, str]:
        brand.validate()
        pain = brand.pain_points[0] if brand.pain_points else "the problem this solves"
        persona = brand.personas[0] if brand.personas else (brand.audience or "your audience")
        product = brand.products[0] if brand.products else ""

        # Avoid "Sourcing Craft: sourcing craft" when the angle is just the theme.
        title = (
            f"{post.theme.title()}: {angle}"
            if angle and angle.strip().lower() != post.theme.strip().lower()
            else post.theme.title()
        )
        opening = f"{persona} keep running into {pain}."
        middle = (
            f"{product} addresses it directly." if product else "There is a better way to work."
        )
        pillar_note = f"This is part of our {post.theme} work." if post.theme else ""
        voice_note = f"Written in our voice: {brand.voice}" if brand.voice else ""

        body = "\n\n".join(
            part for part in (opening, middle, pillar_note, voice_note) if part.strip()
        )
        return title, body


class Copywriter:
    """
    Drafts planned slots into assets, and gates every draft on safety.

    Indifferent to which writer produced the text. It records HOW a draft was
    made - template or model, and which provider - because a reviewer needs that
    for provenance, and then treats both identically: the same safety gate runs
    on the output either way. The prompt is never the enforcement.
    """

    def __init__(self, writer: ContentWriter | None = None) -> None:
        self._writer = writer or TemplateContentWriter()

    @property
    def generation_method(self) -> str:
        """ "template" or "model", from the writer actually in use."""
        return (
            GENERATION_METHOD_MODEL
            if hasattr(self._writer, "provider_name")
            else GENERATION_METHOD_TEMPLATE
        )

    @property
    def provider_name(self) -> str:
        """The provider identifier when a model wrote the draft, else empty."""
        name = getattr(self._writer, "provider_name", "")
        return str(name) if name else ""

    def draft(
        self,
        post: PlannedPost,
        brand: BrandContext,
        angle: str = "",
        variant_group_id: str = "",
        destination_url: str = "",
    ) -> GeneratedAsset:
        """
        Draft one planned slot.

        Raises MissingBrandContextError when the project has not stated enough
        about itself to be written for, and MissingProvenanceError when the slot
        cites nothing - both refusals rather than a generic fallback.
        """
        brand.validate()
        title, body = self._writer.write(post, brand, angle)

        asset = GeneratedAsset(
            id=asset_id(brand.project, post.kind.value, post.campaign, str(post.slot), title),
            project=brand.project,
            kind=post.kind,
            title=title,
            platform=post.platform,
            campaign=post.campaign,
            theme=post.theme,
            cta=post.cta or (brand.ctas[0] if brand.ctas else ""),
            goal=post.goal,
            draft=body,
            recommendation_ids=list(post.recommendation_ids),
            experiment_ids=list(post.experiment_ids),
            variant_group_id=variant_group_id,
            destination_url=destination_url or brand.website,
            audience=brand.audience,
            rationale=post.rationale,
            estimated_minutes=EFFORT_MINUTES.get(post.kind, 30),
            generation_method=self.generation_method,
            provider=self.provider_name,
            created_at=None,
        )
        asset.safety_findings = check_safety(body, title, brand)
        asset.escalations = detect_escalations(f"{title}\n{body}")
        assessment = classify_claims(f"{title}\n{body}")
        asset.claim_risks = assessment.risks
        asset.claim_flags = [f.to_dict() for f in assessment.flags]
        asset.requires_enhanced_review = assessment.requires_enhanced_review
        return asset


def check_safety(body: str, title: str, brand: BrandContext) -> list[SafetyFinding]:
    """
    Every brand-safety problem in a draft.

    Called at generation and again before an item may reach review. The second
    call is not redundant - it catches text a human edited in after the draft was
    written, which is exactly where an unsupported claim tends to arrive.
    """
    text = f"{title}\n{body}"
    findings: list[SafetyFinding] = []

    for rule, pattern in _COMPILED_FORBIDDEN:
        match = pattern.search(text)
        if match:
            findings.append(
                SafetyFinding(
                    rule=rule,
                    category="unsupported-claim",
                    detail=(
                        f"Matched {match.group(0)!r}. This engine may not state a figure or "
                        "testimonial it cannot source from measured data."
                    ),
                    blocking=True,
                )
            )

    for phrase in brand.prohibited:
        if phrase.strip() and phrase.strip().lower() in text.lower():
            findings.append(
                SafetyFinding(
                    rule="project-prohibited",
                    category="brand",
                    detail=f"Contains {phrase!r}, which this project prohibits.",
                    blocking=True,
                )
            )
    return findings


def detect_escalations(text: str) -> list[str]:
    """
    Sensitive categories that must reach a human before anything else happens.

    Escalation is not the same as blocking. The content may be perfectly fine -
    it simply must not move on automatically, because the cost of being wrong in
    these categories is not symmetric with the cost of a delay.
    """
    found: list[str] = []
    for category, pattern in _COMPILED_SENSITIVE:
        if pattern.search(text):
            found.append(category)
    return sorted(set(found))


def gate_for_review(asset: GeneratedAsset, brand: BrandContext) -> tuple[bool, list[str]]:
    """
    Whether an asset may proceed to review, and why not if it may not.

    Re-runs the safety check on the CURRENT draft rather than trusting the
    findings recorded at generation time.
    """
    reasons: list[str] = []
    findings = check_safety(asset.draft, asset.title, brand)
    escalations = detect_escalations(f"{asset.title}\n{asset.draft}")

    for finding in findings:
        if finding.blocking:
            reasons.append(f"{finding.rule}: {finding.detail}")
    for category in escalations:
        reasons.append(f"{category}: sensitive category requires a human decision before review.")
    return (not reasons), reasons


def enhanced_review_for(asset: GeneratedAsset) -> tuple[bool, list[str], str]:
    """
    Whether this draft needs enhanced human review, and why.

    Re-classifies the CURRENT draft rather than trusting what was recorded at
    generation time, so text edited in afterwards is assessed too.

    Distinct from gate_for_review: this does not block, it escalates. A flagged
    draft is a normal draft that may reach review; what it may not do is clear
    review purely because the deterministic safety regexes found nothing. This is
    a review escalation layer, not a fact-checking engine.
    """
    assessment = classify_claims(f"{asset.title}\n{asset.draft}")
    return assessment.requires_enhanced_review, assessment.risks, assessment.summary()


def summarize_effort(assets: list[GeneratedAsset]) -> dict[str, Any]:
    """Total drafting effort for a set of assets, and the split by kind."""
    by_kind: dict[str, int] = {}
    for asset in assets:
        by_kind[asset.kind.value] = by_kind.get(asset.kind.value, 0) + asset.estimated_minutes
    total = sum(by_kind.values())
    return {
        "total_minutes": total,
        "total_hours": round(total / 60.0, 2),
        "by_kind": dict(sorted(by_kind.items())),
        "basis": "per-kind drafting estimates in growth.generation.copywriter.EFFORT_MINUTES",
    }
