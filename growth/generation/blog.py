"""
Long-form article generation - blog posts and SEO articles.

Long-form differs from social in a way that matters here: it has structure a
reviewer needs to see before the prose is worth reading. So these generators
produce an outline plus a draft, and the outline is assembled entirely from the
project's own material - its pillars, pain points, products and objective.

An SEO article carries a target keyword and states plainly that no keyword
research was performed. Inventing search volume would be exactly the fabricated
statistic the safety gate exists to catch, so the keyword is whatever the caller
supplied and the article says so.
"""

from __future__ import annotations

from typing import Any

from growth.generation.copywriter import Copywriter, check_safety, detect_escalations
from growth.generation.models import (
    AssetKind,
    BrandContext,
    GeneratedAsset,
    PlannedPost,
    asset_id,
)

# Section headings for a standard article, in order. Fixed so two runs over the
# same project produce the same outline.
ARTICLE_SECTIONS: tuple[str, ...] = (
    "The problem",
    "Why the usual approach falls short",
    "What we do instead",
    "What this changes",
    "Where to start",
)


def outline(brand: BrandContext, theme: str) -> list[dict[str, str]]:
    """Build an article outline from the project's own material."""
    pain = brand.pain_points[0] if brand.pain_points else "the problem this addresses"
    product = brand.products[0] if brand.products else "our approach"
    persona = brand.personas[0] if brand.personas else (brand.audience or "the reader")

    prompts = {
        "The problem": f"State the problem {persona} actually has: {pain}.",
        "Why the usual approach falls short": (
            "Name the common approach and the specific way it breaks. No competitor "
            "names in a negative claim."
        ),
        "What we do instead": f"Describe {product} concretely, without superlatives.",
        "What this changes": (
            "Describe the observable difference. Do not state a figure that cannot "
            "be sourced from measured data."
        ),
        "Where to start": (
            f"One concrete next step, ending in: {brand.ctas[0] if brand.ctas else 'a clear CTA'}."
        ),
    }
    return [
        {"heading": heading, "guidance": prompts[heading], "theme": theme}
        for heading in ARTICLE_SECTIONS
    ]


def generate_article(
    post: PlannedPost,
    brand: BrandContext,
    keyword: str = "",
    writer: Copywriter | None = None,
) -> GeneratedAsset:
    """
    Draft a blog or SEO article with its outline.

    ``keyword`` is used verbatim when supplied. No search-volume or difficulty
    figure is produced, because no keyword research was performed and inventing
    one would be a fabricated statistic.
    """
    brand.validate()
    copywriter = writer or Copywriter()
    base = copywriter.draft(post, brand, angle=keyword or post.theme)

    sections = outline(brand, post.theme)
    body_parts = [base.draft, ""]
    for section in sections:
        body_parts.append(f"## {section['heading']}")
        body_parts.append(f"_{section['guidance']}_")
        body_parts.append("")

    if keyword:
        body_parts.append(
            f"_Target keyword: {keyword!r}. No keyword research was performed; this is "
            "the term supplied by the caller, not a researched recommendation._"
        )

    kind = AssetKind.SEO_ARTICLE if keyword else AssetKind.BLOG_ARTICLE
    draft = "\n".join(body_parts).strip()
    asset = GeneratedAsset(
        id=asset_id(brand.project, kind.value, post.campaign, post.theme, keyword),
        project=brand.project,
        kind=kind,
        title=base.title,
        platform=post.platform,
        campaign=post.campaign,
        theme=post.theme,
        cta=base.cta,
        goal=post.goal,
        draft=draft,
        recommendation_ids=list(post.recommendation_ids),
        experiment_ids=list(post.experiment_ids),
        destination_url=base.destination_url,
        audience=brand.audience,
        rationale=post.rationale,
        estimated_minutes=base.estimated_minutes,
    )
    asset.safety_findings = check_safety(draft, asset.title, brand)
    asset.escalations = detect_escalations(f"{asset.title}\n{draft}")
    return asset


def article_metadata(asset: GeneratedAsset, keyword: str = "") -> dict[str, Any]:
    """Reviewable metadata for a long-form asset."""
    words = len(asset.draft.split())
    return {
        "content_id": asset.id,
        "kind": asset.kind.value,
        "word_count": words,
        "estimated_read_minutes": max(1, round(words / 200)),
        "target_keyword": keyword,
        "keyword_research_performed": False,
        "sections": [line[3:] for line in asset.draft.splitlines() if line.startswith("## ")],
    }
