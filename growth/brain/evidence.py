"""
Evidence - what a Brain record is standing on.

Every recommendation and every hypothesis carries one of these, and it has to
answer six questions without the reader going anywhere else:

    which metrics created it
    which observations created it
    which campaigns contributed
    which dates contributed
    what assumptions were made
    what would falsify it

The last two are the ones that make the difference. A recommendation with
metrics and no stated assumptions looks more certain than it is; one with no
falsifier can never be retired on the facts, so it just accumulates until
someone gives up on the whole feed.

Evidence is also where provenance lands. If any contributing metric came from
synthetic or imported events, the evidence says so, and every record built on it
inherits the flag. Nothing launders unverified data into a confident claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MetricCitation:
    """One metric a record is standing on, quoted with its value and provenance."""

    name: str
    value: float | None
    unit: str = "count"
    synthetic: bool = False
    sample_size: int = 0
    scope: str = ""
    formula: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "synthetic": self.synthetic,
            "sample_size": self.sample_size,
            "scope": self.scope,
            "formula": self.formula,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MetricCitation:
        raw = data.get("value")
        return cls(
            name=str(data.get("name", "")),
            value=float(raw) if isinstance(raw, (int, float)) else None,
            unit=str(data.get("unit", "count")),
            synthetic=bool(data.get("synthetic", False)),
            sample_size=int(data.get("sample_size", 0)),
            scope=str(data.get("scope", "")),
            formula=str(data.get("formula", "")),
            reason=str(data.get("reason", "")),
        )

    @classmethod
    def from_metric(cls, metric: dict[str, Any], scope: str = "") -> MetricCitation:
        """Build a citation from a growth.metrics MetricValue payload."""
        raw = metric.get("value")
        return cls(
            name=str(metric.get("name", "")),
            value=float(raw) if isinstance(raw, (int, float)) else None,
            unit=str(metric.get("unit", "count")),
            synthetic=bool(metric.get("synthetic", False)),
            sample_size=int(metric.get("sample_size", 0)),
            scope=scope,
            formula=str(metric.get("formula", "")),
            reason=str(metric.get("reason", "")),
        )


@dataclass
class Evidence:
    """The complete basis for one Brain record."""

    metrics: list[MetricCitation] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    campaigns: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    content_ids: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    falsifier: str = ""
    memory_ids: list[str] = field(default_factory=list)
    sample_size: int = 0

    @property
    def synthetic(self) -> bool:
        """True when any cited metric came from synthetic or imported events."""
        return any(m.synthetic for m in self.metrics)

    def is_empty(self) -> bool:
        """
        True when there is nothing here a reader could check.

        Assumptions alone do not count as evidence: a record standing only on
        what someone assumed is exactly the opinion this engine refuses to emit.
        """
        return not (self.metrics or self.observations or self.memory_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metrics": [m.to_dict() for m in self.metrics],
            "observations": list(self.observations),
            "campaigns": list(self.campaigns),
            "platforms": list(self.platforms),
            "content_ids": list(self.content_ids),
            "dates": list(self.dates),
            "assumptions": list(self.assumptions),
            "falsifier": self.falsifier,
            "memory_ids": list(self.memory_ids),
            "sample_size": self.sample_size,
            "synthetic": self.synthetic,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Evidence:
        return cls(
            metrics=[MetricCitation.from_dict(m) for m in (data.get("metrics") or [])],
            observations=[str(o) for o in (data.get("observations") or [])],
            campaigns=[str(c) for c in (data.get("campaigns") or [])],
            platforms=[str(p) for p in (data.get("platforms") or [])],
            content_ids=[str(c) for c in (data.get("content_ids") or [])],
            dates=[str(d) for d in (data.get("dates") or [])],
            assumptions=[str(a) for a in (data.get("assumptions") or [])],
            falsifier=str(data.get("falsifier", "")),
            memory_ids=[str(m) for m in (data.get("memory_ids") or [])],
            sample_size=int(data.get("sample_size", 0)),
        )

    def describe(self) -> str:
        """One line a human can read without opening the payload."""
        parts: list[str] = []
        if self.metrics:
            parts.append(f"{len(self.metrics)} metric(s)")
        if self.observations:
            parts.append(f"{len(self.observations)} observation(s)")
        if self.campaigns:
            parts.append(f"{len(self.campaigns)} campaign(s)")
        if self.memory_ids:
            parts.append(f"{len(self.memory_ids)} memory entr(ies)")
        basis = ", ".join(parts) if parts else "no evidence"
        return f"{basis}; n={self.sample_size}" + (" [synthetic]" if self.synthetic else "")


# Assumptions that apply to every record this engine produces while no real
# platform adapter exists. Stated on each record rather than buried in a doc,
# because a recommendation read six months from now must carry its own caveats.
STANDING_ASSUMPTIONS: tuple[str, ...] = (
    "Metrics derive from synthetic or operator-imported events; no platform has "
    "reported anything, because no platform adapter exists yet.",
    "Attribution is by recorded association between an event and a content item, "
    "not by any platform-side attribution model.",
    "Comparisons assume the periods being compared are otherwise similar; no "
    "seasonality or external-factor adjustment is applied.",
)


def standing_assumptions(extra: list[str] | None = None) -> list[str]:
    """The standing assumptions, plus any rule-specific ones."""
    return list(STANDING_ASSUMPTIONS) + list(extra or [])
