"""
The Growth Workspace — the complete marketing state for exactly one project.

The workspace is the unit of isolation (ADR-011), not a filter over shared data.
Nothing in this module can address more than one project: a Workspace instance is
built for a single slug and holds only that project's business, brand, audience,
marketing, and platform bindings.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from core.types import Timestamp
from growth.binding import PlatformBinding
from growth.onboarding import Onboarding


@dataclass
class Business:
    """What the project sells and who it competes with."""

    name: str = ""
    description: str = ""
    website: str = ""
    industry: str = ""
    products: list[str] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    pricing: str = ""
    competitors: list[str] = field(default_factory=list)


@dataclass
class Brand:
    """How the project sounds and looks. Inputs to drafting, criteria in review."""

    voice: str = ""
    tone: str = ""
    style_rules: list[str] = field(default_factory=list)
    colors: list[str] = field(default_factory=list)
    fonts: list[str] = field(default_factory=list)
    logos: list[str] = field(default_factory=list)
    design_system: str = ""
    approved_imagery: list[str] = field(default_factory=list)


@dataclass
class Audience:
    """Who the project is talking to."""

    icps: list[str] = field(default_factory=list)
    personas: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    job_titles: list[str] = field(default_factory=list)
    company_sizes: list[str] = field(default_factory=list)
    geographies: list[str] = field(default_factory=list)
    interests: list[str] = field(default_factory=list)
    pain_points: list[str] = field(default_factory=list)


@dataclass
class Marketing:
    """What the project is trying to achieve and how it measures that."""

    objectives: list[str] = field(default_factory=list)
    kpis: list[str] = field(default_factory=list)
    campaigns: list[str] = field(default_factory=list)
    funnels: list[str] = field(default_factory=list)
    content_pillars: list[str] = field(default_factory=list)
    ctas: list[str] = field(default_factory=list)


@dataclass
class Workspace:
    """
    One project's complete growth state.

    ``slug`` is authoritative: it is the directory name, the isolation key, and the
    value fingerprinted into every approval as the item's project.
    """

    slug: str
    registered_name: str = ""
    business: Business = field(default_factory=Business)
    brand: Brand = field(default_factory=Brand)
    audience: Audience = field(default_factory=Audience)
    marketing: Marketing = field(default_factory=Marketing)
    bindings: list[PlatformBinding] = field(default_factory=list)
    onboarding: Onboarding = field(default_factory=Onboarding)
    created: Timestamp = field(default_factory=lambda: datetime.now(tz=UTC))
    updated: Timestamp = field(default_factory=lambda: datetime.now(tz=UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    def binding_for(self, platform: str) -> PlatformBinding | None:
        """Return the active binding for a platform, or None."""
        from growth.binding import normalize_platform

        target = normalize_platform(platform)
        for binding in self.bindings:
            if binding.platform == target and binding.status == "active":
                return binding
        return None

    def to_dict(self) -> dict[str, Any]:
        """Serialise for storage. Bindings carry secret names only, never values."""
        return {
            "slug": self.slug,
            "registered_name": self.registered_name,
            "business": asdict(self.business),
            "brand": asdict(self.brand),
            "audience": asdict(self.audience),
            "marketing": asdict(self.marketing),
            "bindings": [b.to_dict() for b in self.bindings],
            "onboarding": self.onboarding.to_dict(),
            "created": _fmt_dt(self.created),
            "updated": _fmt_dt(self.updated),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Workspace:
        return cls(
            slug=str(data.get("slug", "")),
            registered_name=str(data.get("registered_name", "")),
            business=Business(**_section(data, "business", Business)),
            brand=Brand(**_section(data, "brand", Brand)),
            audience=Audience(**_section(data, "audience", Audience)),
            marketing=Marketing(**_section(data, "marketing", Marketing)),
            bindings=[PlatformBinding.from_dict(b) for b in (data.get("bindings") or [])],
            onboarding=Onboarding.from_dict(data.get("onboarding") or {}),
            created=_parse_dt(data.get("created")),
            updated=_parse_dt(data.get("updated")),
            metadata=dict(data.get("metadata") or {}),
        )


def _section(data: dict[str, Any], key: str, kind: type) -> dict[str, Any]:
    """Filter a stored section to the fields the dataclass declares."""
    raw = data.get(key) or {}
    if not isinstance(raw, dict):
        return {}
    known = set(kind.__dataclass_fields__)  # type: ignore[attr-defined]
    return {k: v for k, v in raw.items() if k in known}


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.rstrip("Z")).replace(tzinfo=UTC)
    return datetime.now(tz=UTC)


def _fmt_dt(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
