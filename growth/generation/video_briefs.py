"""
Video briefs - scripts and shot direction, not generated video.

MondayOS generates no video. This module produces a brief a human or a future
Video role can execute: a hook, a script broken into beats, a duration target and
the shot direction that goes with it.

The hook is separated out deliberately. On short-form video the first line
decides whether anything else is seen, so it is a named field a reviewer can
judge on its own rather than a sentence buried at the top of a script.
"""

from __future__ import annotations

from typing import Any

from growth.generation.image_briefs import ASPECT_RATIOS, AssetRequest
from growth.generation.models import (
    BrandContext,
    GeneratedAsset,
    MissingProvenanceError,
    asset_id,
)

# Target durations by platform, in seconds.
TARGET_SECONDS: dict[str, int] = {
    "tiktok": 30,
    "youtube": 45,
    "instagram": 30,
    "linkedin": 60,
    "facebook": 60,
    "x": 45,
}

# Words per second of spoken delivery, used to sanity-check script length.
SPEAKING_RATE_WPS = 2.5


def video_brief(asset: GeneratedAsset, brand: BrandContext, seconds: int = 0) -> AssetRequest:
    """Request one short-form video for a drafted piece."""
    if not (asset.recommendation_ids or asset.campaign):
        raise MissingProvenanceError(f"video brief for {asset.title}")
    brand.validate()

    target = seconds or TARGET_SECONDS.get(asset.platform, 45)
    beats = [p.strip() for p in asset.draft.split("\n\n") if p.strip()]
    hook = beats[0] if beats else asset.title
    script_beats = beats[1:] or [asset.title]

    budget_words = int(target * SPEAKING_RATE_WPS)
    script = "\n".join(
        f"  [{index}] {beat[:160]}" for index, beat in enumerate(script_beats, start=1)
    )
    brief = (
        f"{target}s video for: {asset.title}\n"
        f"HOOK (first 3 seconds, must stand alone): {hook}\n"
        f"SCRIPT BEATS:\n{script}\n"
        f"Word budget: ~{budget_words} words at {SPEAKING_RATE_WPS} words/sec.\n"
        f"Close on: {asset.cta or 'the campaign CTA'}."
    )

    return AssetRequest(
        id=asset_id(brand.project, "video-brief", asset.id),
        project=brand.project,
        campaign=asset.campaign,
        content_id=asset.id,
        asset_type="video",
        dimensions=ASPECT_RATIOS.get(asset.platform, "1080x1920 (9:16)"),
        brief=brief,
        required_copy=[asset.cta] if asset.cta else [],
        brand_constraints=[f"Voice: {brand.voice}"] + list(brand.style_rules),
        reference_assets=list(brand.approved_assets),
    )


def script_check(brief: AssetRequest, target_seconds: int) -> dict[str, Any]:
    """
    Whether a script fits its duration, at a stated speaking rate.

    Arithmetic, not judgement: word count divided by a stated rate. The rate is
    returned so a reader can disagree with it rather than with the verdict.
    """
    words = len(brief.brief.split())
    estimated = words / SPEAKING_RATE_WPS
    return {
        "word_count": words,
        "speaking_rate_words_per_second": SPEAKING_RATE_WPS,
        "estimated_seconds": round(estimated, 1),
        "target_seconds": target_seconds,
        "fits": estimated <= target_seconds,
        "note": "Estimate only; delivery pace varies by speaker.",
    }
