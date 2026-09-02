"""
growth.generation — turning the Brain's conclusions into reviewable drafts.

The flow, and nothing skips a step:

    Analytics -> Growth Brain -> Recommendations -> Planner -> Generation
             -> Weekly Package -> Approval Inbox -> Publishing Connector

Generation sits ON TOP of the Brain and never reaches into it. The Brain decides
what is worth saying and why; this package turns those conclusions into drafts.
No reasoning lives here.

Three rules hold the subsystem together:

  Generation never bypasses approval. Every asset becomes a ContentItem in DRAFT
  and travels the existing lifecycle.

  Nothing appears out of nowhere. Every asset cites a recommendation, a campaign,
  or an experiment, enforced at construction.

  Brand context is required. A request without voice, audience and objective is
  refused rather than falling back to a generic template.

Drafting is deterministic and template-driven, assembled from the project's own
brand material. ``ContentWriter`` is a protocol, so a provider-backed writer plugs
in behind it without changing anything above this module.
"""

from __future__ import annotations

from typing import Any

from growth.generation.approval_inbox import (
    INBOX_STATUSES,
    PRIORITY_ORDER,
    ApprovalInbox,
    InboxItem,
)
from growth.generation.blog import article_metadata, generate_article, outline
from growth.generation.claim_risk import (
    ClaimAssessment,
    ClaimFlag,
    ClaimRisk,
    classify_claims,
)
from growth.generation.copywriter import (
    EFFORT_MINUTES,
    ContentWriter,
    Copywriter,
    TemplateContentWriter,
    check_safety,
    detect_escalations,
    enhanced_review_for,
    gate_for_review,
    summarize_effort,
)
from growth.generation.formatter import (
    PLATFORM_LIMITS,
    PLATFORM_STYLE,
    FormatResult,
    UnsupportedPlatformFormatError,
    adapt,
    make_variants,
    variants_differ,
)
from growth.generation.image_briefs import (
    ASPECT_RATIOS,
    AssetRequest,
    AssetRequestStatus,
    InvalidAssetRequestTransitionError,
    carousel_brief,
    image_brief,
)
from growth.generation.model_writer import (
    DEFAULT_MAX_TOKENS,
    GENERATION_METHOD_MODEL,
    GENERATION_METHOD_TEMPLATE,
    GENERATION_MODES,
    ModelContentWriter,
    ModelDraft,
    ModelGenerationError,
    ModelOutputInvalidError,
    build_prompt,
    parse_response,
)
from growth.generation.models import (
    FORBIDDEN_CLAIM_PATTERNS,
    SENSITIVE_CATEGORIES,
    AssetKind,
    BrandContext,
    GeneratedAsset,
    InvalidPackageTransitionError,
    MissingBrandContextError,
    MissingProvenanceError,
    PackageStatus,
    PlannedPost,
    SafetyFinding,
    asset_id,
    package_id,
)
from growth.generation.newsletter import (
    MIN_ITEMS_FOR_ISSUE,
    EmptyNewsletterError,
    assemble_issue,
    issue_summary,
)
from growth.generation.planner import (
    DEFAULT_MIX,
    EXPERIMENT_ALLOCATION,
    ContentPlanner,
    WeeklyPlan,
)
from growth.generation.video_briefs import TARGET_SECONDS, script_check, video_brief
from growth.generation.weekly_package import (
    PackagePost,
    WeeklyPackage,
    WeeklyPackageBuilder,
    build_plan,
    week_bounds,
)

__all__ = [
    "enhanced_review_for",
    "classify_claims",
    "GENERATION_MODES",
    "DEFAULT_MAX_TOKENS",
    "ClaimRisk",
    "ClaimFlag",
    "ClaimAssessment",
    "parse_response",
    "build_prompt",
    "ModelOutputInvalidError",
    "ModelGenerationError",
    "ModelDraft",
    "ModelContentWriter",
    "GENERATION_METHOD_TEMPLATE",
    "GENERATION_METHOD_MODEL",
    "ASPECT_RATIOS",
    "DEFAULT_MIX",
    "EFFORT_MINUTES",
    "EXPERIMENT_ALLOCATION",
    "FORBIDDEN_CLAIM_PATTERNS",
    "INBOX_STATUSES",
    "MIN_ITEMS_FOR_ISSUE",
    "PLATFORM_LIMITS",
    "PLATFORM_STYLE",
    "PRIORITY_ORDER",
    "SENSITIVE_CATEGORIES",
    "TARGET_SECONDS",
    "ApprovalInbox",
    "AssetKind",
    "AssetRequest",
    "AssetRequestStatus",
    "BrandContext",
    "ContentPlanner",
    "ContentWriter",
    "Copywriter",
    "EmptyNewsletterError",
    "FormatResult",
    "GeneratedAsset",
    "InboxItem",
    "InvalidAssetRequestTransitionError",
    "InvalidPackageTransitionError",
    "MissingBrandContextError",
    "MissingProvenanceError",
    "PackagePost",
    "PackageStatus",
    "PlannedPost",
    "SafetyFinding",
    "TemplateContentWriter",
    "UnsupportedPlatformFormatError",
    "WeeklyPackage",
    "WeeklyPackageBuilder",
    "WeeklyPlan",
    "adapt",
    "article_metadata",
    "assemble_issue",
    "asset_id",
    "build_plan",
    "carousel_brief",
    "check_safety",
    "detect_escalations",
    "gate_for_review",
    "generate_article",
    "image_brief",
    "issue_summary",
    "make_variants",
    "outline",
    "package_id",
    "script_check",
    "summarize_effort",
    "variants_differ",
    "video_brief",
    "brand_context_for",
    "week_bounds",
]


def brand_context_for(handle: Any) -> BrandContext:
    """
    Assemble a BrandContext from a workspace.

    One place builds it, so a caller cannot quietly generate with half the brand
    missing. Validation is the caller's to trigger - this returns whatever the
    project has actually recorded, and ``BrandContext.validate()`` decides whether
    that is enough to write against.
    """
    workspace = handle.read()
    onboarding = workspace.onboarding
    return BrandContext(
        project=handle.slug,
        voice=workspace.brand.voice,
        tone=workspace.brand.tone,
        style_rules=tuple(workspace.brand.style_rules),
        audience=(workspace.audience.personas[0] if workspace.audience.personas else ""),
        personas=tuple(workspace.audience.personas),
        pain_points=tuple(workspace.audience.pain_points),
        objective=(workspace.marketing.objectives[0] if workspace.marketing.objectives else ""),
        content_pillars=tuple(workspace.marketing.content_pillars),
        ctas=tuple(workspace.marketing.ctas),
        approved_assets=tuple(workspace.brand.approved_imagery) + tuple(workspace.brand.logos),
        prohibited=tuple(onboarding.prohibited_content),
        products=tuple(workspace.business.products),
        website=workspace.business.website,
    )
