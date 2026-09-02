"""
Newsletter generation - assembling an issue from what already exists.

A newsletter is not a new idea; it is a selection from the week's content plus
whatever the project has published recently. So this module assembles rather than
invents: each section points at a real asset or a real piece of library content,
and an issue with nothing to point at is refused rather than padded.

That refusal is the whole design. A newsletter that goes out because it is
Thursday, filled with content nobody chose, is how a list gets unsubscribed from.
"""

from __future__ import annotations

from typing import Any

from growth.generation.copywriter import check_safety, detect_escalations
from growth.generation.models import (
    AssetKind,
    BrandContext,
    GeneratedAsset,
    MissingProvenanceError,
    asset_id,
)

# An issue below this many items is not worth sending.
MIN_ITEMS_FOR_ISSUE = 2


class EmptyNewsletterError(ValueError):
    """Raised when there is not enough real content to assemble an issue."""

    def __init__(self, available: int) -> None:
        super().__init__(
            f"Refusing to assemble a newsletter from {available} item(s); "
            f"{MIN_ITEMS_FOR_ISSUE} is the minimum. An issue sent because it is "
            "Thursday, padded with content nobody chose, costs subscribers."
        )


def assemble_issue(
    brand: BrandContext,
    week_label: str,
    items: list[dict[str, Any]],
    campaign: str = "",
    recommendation_ids: list[str] | None = None,
) -> GeneratedAsset:
    """
    Build one newsletter issue from real content.

    ``items`` are dicts carrying at least ``title`` and one of ``content_id`` or
    ``url``. Nothing is written that does not point at something.
    """
    brand.validate()
    usable = [i for i in items if str(i.get("title", "")).strip()]
    if len(usable) < MIN_ITEMS_FOR_ISSUE:
        raise EmptyNewsletterError(len(usable))

    cited = list(recommendation_ids or [])
    if not (cited or campaign):
        raise MissingProvenanceError(f"newsletter {week_label}")

    persona = brand.personas[0] if brand.personas else (brand.audience or "readers")
    lines = [
        f"# {brand.project} — {week_label}",
        "",
        f"For {persona}. {brand.objective}",
        "",
        "## In this issue",
        "",
    ]
    for index, item in enumerate(usable, start=1):
        pointer = item.get("content_id") or item.get("url") or ""
        summary = str(item.get("summary", "")).strip()
        lines.append(f"{index}. **{item['title']}**" + (f" — {summary}" if summary else ""))
        if pointer:
            lines.append(f"   _source: {pointer}_")
    lines.extend(["", brand.ctas[0] if brand.ctas else "Reply and tell us what you think."])

    draft = "\n".join(lines)
    title = f"{brand.project} newsletter — {week_label}"
    asset = GeneratedAsset(
        id=asset_id(brand.project, AssetKind.NEWSLETTER.value, week_label),
        project=brand.project,
        kind=AssetKind.NEWSLETTER,
        title=title,
        platform="",
        campaign=campaign,
        theme="newsletter",
        cta=brand.ctas[0] if brand.ctas else "",
        goal="newsletter_engagement",
        draft=draft,
        recommendation_ids=cited,
        destination_url=brand.website,
        audience=brand.audience,
        rationale=(
            f"Assembled from {len(usable)} existing item(s) for {week_label}. "
            "No content was invented for this issue."
        ),
        estimated_minutes=90,
    )
    asset.safety_findings = check_safety(draft, title, brand)
    asset.escalations = detect_escalations(f"{title}\n{draft}")
    return asset


def issue_summary(asset: GeneratedAsset) -> dict[str, Any]:
    """Reviewable summary of an assembled issue."""
    sources = [
        line.strip().removeprefix("_source:").strip(" _")
        for line in asset.draft.splitlines()
        if line.strip().startswith("_source:")
    ]
    return {
        "content_id": asset.id,
        "item_count": len(
            [
                line
                for line in asset.draft.splitlines()
                if line[:2].rstrip(".").isdigit() and line.lstrip().startswith(tuple("0123456789"))
            ]
        ),
        "sources": sources,
        "all_items_sourced": len(sources) > 0,
    }
