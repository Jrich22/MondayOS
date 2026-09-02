"""
Image and carousel briefs - requests for design work, not generated images.

MondayOS has no image generation, and this module does not pretend otherwise. It
produces a *brief*: what the visual needs to communicate, at what dimensions,
with which approved assets and what copy must appear. A designer or a future
Design role picks it up from there.

Briefs draw only on assets the project has actually approved. A brief that
referenced imagery nobody cleared would send a designer to source something the
brand has not agreed to, which is how off-brand work gets published with everyone
believing it was sanctioned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from growth.generation.models import (
    BrandContext,
    GeneratedAsset,
    MissingProvenanceError,
    asset_id,
)


class AssetRequestStatus(Enum):
    """Lifecycle of a creative asset request."""

    REQUESTED = "requested"
    IN_PROGRESS = "in-progress"
    READY_FOR_REVIEW = "ready-for-review"
    APPROVED = "approved"
    REJECTED = "rejected"


_REQUEST_TRANSITIONS: dict[AssetRequestStatus, set[AssetRequestStatus]] = {
    AssetRequestStatus.REQUESTED: {
        AssetRequestStatus.IN_PROGRESS,
        AssetRequestStatus.REJECTED,
    },
    AssetRequestStatus.IN_PROGRESS: {
        AssetRequestStatus.READY_FOR_REVIEW,
        AssetRequestStatus.REJECTED,
    },
    AssetRequestStatus.READY_FOR_REVIEW: {
        AssetRequestStatus.APPROVED,
        AssetRequestStatus.IN_PROGRESS,
        AssetRequestStatus.REJECTED,
    },
    AssetRequestStatus.APPROVED: set(),
    AssetRequestStatus.REJECTED: set(),
}

# Aspect ratios by platform, as the platforms publish them.
ASPECT_RATIOS: dict[str, str] = {
    "linkedin": "1200x627 (1.91:1)",
    "instagram": "1080x1080 (1:1)",
    "facebook": "1200x630 (1.91:1)",
    "x": "1600x900 (16:9)",
    "tiktok": "1080x1920 (9:16)",
    "youtube": "1280x720 (16:9)",
    "threads": "1080x1080 (1:1)",
}

DEFAULT_CAROUSEL_SLIDES = 5


class InvalidAssetRequestTransitionError(ValueError):
    """Raised when an asset request is moved along an illegal edge."""

    def __init__(self, from_status: str, to_status: str) -> None:
        super().__init__(f"Illegal asset request transition {from_status} -> {to_status}.")


@dataclass
class AssetRequest:
    """A request for creative work, with everything a designer needs."""

    id: str
    project: str
    campaign: str
    content_id: str
    asset_type: str
    dimensions: str
    brief: str
    required_copy: list[str] = field(default_factory=list)
    brand_constraints: list[str] = field(default_factory=list)
    reference_assets: list[str] = field(default_factory=list)
    status: AssetRequestStatus = AssetRequestStatus.REQUESTED
    output_reference: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)

    def can_transition_to(self, status: AssetRequestStatus) -> bool:
        return status in _REQUEST_TRANSITIONS.get(self.status, set())

    def apply_transition(self, status: AssetRequestStatus, by: str, reason: str, at: str) -> None:
        """Move the request along, with an audit entry. Raises on an illegal edge."""
        if not self.can_transition_to(status):
            raise InvalidAssetRequestTransitionError(self.status.value, status.value)
        self.history.append(
            {
                "from_status": self.status.value,
                "to_status": status.value,
                "changed_by": by,
                "reason": reason,
                "at": at,
            }
        )
        self.status = status

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project": self.project,
            "campaign": self.campaign,
            "content_id": self.content_id,
            "asset_type": self.asset_type,
            "dimensions": self.dimensions,
            "brief": self.brief,
            "required_copy": list(self.required_copy),
            "brand_constraints": list(self.brand_constraints),
            "reference_assets": list(self.reference_assets),
            "status": self.status.value,
            "output_reference": self.output_reference,
            "history": list(self.history),
        }


def image_brief(asset: GeneratedAsset, brand: BrandContext) -> AssetRequest:
    """Request one image for a drafted piece."""
    if not (asset.recommendation_ids or asset.campaign):
        raise MissingProvenanceError(f"image brief for {asset.title}")
    brand.validate()

    return AssetRequest(
        id=asset_id(brand.project, "image-brief", asset.id),
        project=brand.project,
        campaign=asset.campaign,
        content_id=asset.id,
        asset_type="image",
        dimensions=ASPECT_RATIOS.get(asset.platform, "1200x627 (1.91:1)"),
        brief=(
            f"One image supporting: {asset.title}. Theme: {asset.theme}. "
            f"Audience: {asset.audience or brand.audience}. "
            f"It must communicate the post's single idea without restating the caption."
        ),
        required_copy=[asset.cta] if asset.cta else [],
        brand_constraints=_constraints(brand),
        reference_assets=list(brand.approved_assets),
    )


def carousel_brief(
    asset: GeneratedAsset, brand: BrandContext, slides: int = DEFAULT_CAROUSEL_SLIDES
) -> AssetRequest:
    """Request a carousel, one idea per slide."""
    if not (asset.recommendation_ids or asset.campaign):
        raise MissingProvenanceError(f"carousel brief for {asset.title}")
    brand.validate()

    beats = [p.strip() for p in asset.draft.split("\n\n") if p.strip()][:slides]
    outline = "\n".join(
        f"  Slide {index}: {beat[:120]}" for index, beat in enumerate(beats, start=1)
    )
    return AssetRequest(
        id=asset_id(brand.project, "carousel-brief", asset.id),
        project=brand.project,
        campaign=asset.campaign,
        content_id=asset.id,
        asset_type="carousel",
        dimensions=ASPECT_RATIOS.get(asset.platform, "1080x1080 (1:1)"),
        brief=(
            f"{len(beats)}-slide carousel for: {asset.title}. One idea per slide, "
            f"no slide restating another.\n{outline}"
        ),
        required_copy=[asset.cta] if asset.cta else [],
        brand_constraints=_constraints(brand),
        reference_assets=list(brand.approved_assets),
    )


def _constraints(brand: BrandContext) -> list[str]:
    """Brand rules a designer must work inside."""
    rules = [f"Voice: {brand.voice}"] if brand.voice else []
    rules.extend(brand.style_rules)
    rules.extend(f"Never: {p}" for p in brand.prohibited)
    rules.append(
        "Use only the reference assets listed. Do not source imagery the project has not approved."
    )
    return rules
