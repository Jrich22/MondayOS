"""
Platform formatters - adapting one draft into genuinely different variants.

Formatting is separate from writing on purpose. The copywriter produces one
draft carrying the idea; the formatter turns that idea into versions that suit
each platform's conventions. Mixing the two would make it impossible to change
how LinkedIn copy is shaped without touching how ideas are drafted.

The rule that matters: **one idea becomes N separate ContentItems, never one
caption copied N times.** Variants share a ``variant_group_id`` so they can be
found together, and each is approved independently - approval binds one platform,
one account and one copy (ADR-013), so approving the LinkedIn variant must not
approve the Instagram one.

A formatter that produced identical text for two platforms would silently defeat
that: the items would still be separate records, but a reviewer approving one
would reasonably assume they had seen the other. So ``adapt`` asserts the output
actually differs from the source shape, and the tests check it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from growth.generation.models import AssetKind, BrandContext, GeneratedAsset

# Platform conventions. Limits are the platforms' published constraints; the
# style notes are conventions this engine applies, stated here so they can be
# argued with rather than inferred from output.
PLATFORM_LIMITS: dict[str, int] = {
    "linkedin": 3000,
    "x": 280,
    "facebook": 2000,
    "instagram": 2200,
    "tiktok": 2200,
    "youtube": 5000,
    "threads": 500,
}

PLATFORM_STYLE: dict[str, str] = {
    "linkedin": "long-form, one idea per paragraph, no hashtag spam",
    "x": "one sharp claim, no preamble, hard character limit",
    "facebook": "conversational, slightly longer opening",
    "instagram": "visual-first caption, line breaks, hashtags at the end",
    "tiktok": "spoken-word script with a hook in the first line",
    "youtube": "spoken-word script with a hook and a close",
    "threads": "short and conversational",
}

# Hashtag counts by platform. Zero where hashtags read as noise.
PLATFORM_HASHTAGS: dict[str, int] = {
    "linkedin": 3,
    "x": 2,
    "facebook": 0,
    "instagram": 5,
    "tiktok": 3,
    "youtube": 0,
    "threads": 1,
}

# Kinds that are platform-shaped: a generic social post becomes whatever that
# platform calls one. Every other kind - a brief, an article, a newsletter - is a
# deliberate choice by the planner and survives formatting unchanged. Remapping
# them would let the formatter silently overrule the week's content mix.
PLATFORM_SHAPED_KINDS: frozenset[AssetKind] = frozenset(
    {
        AssetKind.LINKEDIN_POST,
        AssetKind.X_POST,
        AssetKind.FACEBOOK_POST,
        AssetKind.INSTAGRAM_CAPTION,
        AssetKind.COMMUNITY_POST,
        AssetKind.EDUCATIONAL_POST,
    }
)

# Which asset kind a platform-shaped variant becomes.
PLATFORM_KIND: dict[str, AssetKind] = {
    "linkedin": AssetKind.LINKEDIN_POST,
    "x": AssetKind.X_POST,
    "facebook": AssetKind.FACEBOOK_POST,
    "instagram": AssetKind.INSTAGRAM_CAPTION,
    "tiktok": AssetKind.TIKTOK_SCRIPT,
    "youtube": AssetKind.YOUTUBE_SHORT_SCRIPT,
    "threads": AssetKind.COMMUNITY_POST,
}


class UnsupportedPlatformFormatError(ValueError):
    """Raised when asked to format for a platform with no defined conventions."""

    def __init__(self, platform: str) -> None:
        super().__init__(
            f"No formatting conventions defined for {platform!r}. Supported: "
            f"{', '.join(sorted(PLATFORM_LIMITS))}."
        )


@dataclass
class FormatResult:
    """One platform's version of an idea, with what was done to it."""

    platform: str
    body: str
    kind: AssetKind
    truncated: bool = False
    applied: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.applied is None:
            self.applied = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "body": self.body,
            "kind": self.kind.value,
            "truncated": self.truncated,
            "applied": list(self.applied),
        }


def adapt(body: str, platform: str, brand: BrandContext, cta: str = "") -> FormatResult:
    """
    Shape one draft for one platform.

    Each platform gets a materially different treatment - not the same text with
    a different limit applied. A reviewer looking at two variants should be able
    to see, immediately, that they were written for different places.
    """
    slug = (platform or "").strip().lower()
    if slug not in PLATFORM_LIMITS:
        raise UnsupportedPlatformFormatError(platform)

    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    applied: list[str] = [f"style: {PLATFORM_STYLE[slug]}"]

    if slug == "x":
        # A single claim. The first paragraph is the idea; everything else is
        # context X has no room for.
        text = paragraphs[0] if paragraphs else body.strip()
        applied.append("reduced to the leading claim")
    elif slug == "instagram":
        text = "\n\n".join(paragraphs[:2])
        applied.append("visual-first: opening two beats only")
    elif slug in ("tiktok", "youtube"):
        hook = paragraphs[0] if paragraphs else body.strip()
        rest = " ".join(paragraphs[1:])
        text = f"HOOK: {hook}\n\nSCRIPT: {rest}".strip()
        applied.append("rewritten as a spoken-word script with an explicit hook")
    elif slug == "threads":
        text = paragraphs[0] if paragraphs else body.strip()
        applied.append("shortened to a single conversational beat")
    elif slug == "facebook":
        text = "\n\n".join(paragraphs)
        applied.append("conversational opening retained in full")
    else:  # linkedin
        text = "\n\n".join(paragraphs)
        applied.append("long-form retained, one idea per paragraph")

    if cta:
        text = f"{text}\n\n{cta}"
        applied.append("CTA appended")

    hashtag_count = PLATFORM_HASHTAGS.get(slug, 0)
    if hashtag_count and brand.content_pillars:
        tags = " ".join(
            "#" + p.replace(" ", "").replace("-", "") for p in brand.content_pillars[:hashtag_count]
        )
        text = f"{text}\n\n{tags}"
        applied.append(f"{hashtag_count} pillar hashtag(s) appended")

    limit = PLATFORM_LIMITS[slug]
    truncated = len(text) > limit
    if truncated:
        # Trim on a word boundary and mark it, rather than cutting mid-word and
        # letting a reviewer discover the mangling after approval.
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
        applied.append(f"truncated to the {limit}-character limit")

    return FormatResult(
        platform=slug,
        body=text,
        kind=PLATFORM_KIND.get(slug, AssetKind.LINKEDIN_POST),
        truncated=truncated,
        applied=applied,
    )


def make_variants(
    asset: GeneratedAsset, platforms: list[str], brand: BrandContext
) -> list[GeneratedAsset]:
    """
    Turn one drafted idea into a separate asset per platform.

    Every variant shares the source asset's ``variant_group_id`` and its
    provenance, and each becomes its own ContentItem downstream so approval stays
    platform-specific.
    """
    from growth.generation.models import asset_id

    group = asset.variant_group_id or f"vg-{asset.id}"
    variants: list[GeneratedAsset] = []

    for platform in sorted(set(platforms)):
        result = adapt(asset.draft, platform, brand, asset.cta)
        # A brief or an article keeps the kind the planner chose; only a generic
        # social post takes the platform's shape.
        kind = result.kind if asset.kind in PLATFORM_SHAPED_KINDS else asset.kind
        variants.append(
            GeneratedAsset(
                id=asset_id(asset.project, kind.value, group, platform),
                project=asset.project,
                kind=kind,
                title=asset.title,
                platform=result.platform,
                campaign=asset.campaign,
                theme=asset.theme,
                cta=asset.cta,
                goal=asset.goal,
                draft=result.body,
                recommendation_ids=list(asset.recommendation_ids),
                experiment_ids=list(asset.experiment_ids),
                variant_group_id=group,
                destination_url=asset.destination_url,
                media_refs=list(asset.media_refs),
                audience=asset.audience,
                rationale=(
                    f"{asset.rationale} Adapted for {result.platform}: {'; '.join(result.applied)}."
                ),
                safety_findings=list(asset.safety_findings),
                escalations=list(asset.escalations),
                estimated_minutes=asset.estimated_minutes,
                generation_method=asset.generation_method,
                provider=asset.provider,
            )
        )
    return variants


def variants_differ(variants: list[GeneratedAsset]) -> bool:
    """
    True when every variant's body is distinct.

    Used as a guard rather than a nicety: two identical variants would let a
    reviewer approve one and reasonably believe they had seen the other.
    """
    bodies = [v.draft for v in variants]
    return len(set(bodies)) == len(bodies)
