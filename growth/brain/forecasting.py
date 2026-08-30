"""
Rule-based forecasting - straight-line projection, stated as such.

No machine learning, no probabilistic modelling, no curve fitting. Every forecast
here is arithmetic a reader can redo on paper: take the rate observed so far,
extend it over the time remaining, and say plainly that this assumes nothing
changes.

That assumption is almost always wrong in marketing, which is exactly why it is
attached to every forecast rather than left implicit. A projection that looks
like a prediction invites someone to plan against it; a projection that says "at
the current rate, and only if nothing changes" invites them to check whether
anything has.

Two refusals matter as much as the arithmetic:

* A forecast needs elapsed time to have a rate. Nothing is projected from a
  campaign that started today.
* A forecast needs a minimum number of observations. Projecting a quarter from
  two data points is not a forecast, it is a guess with a decimal point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# Minimum observations before anything is projected.
MIN_OBSERVATIONS_FOR_FORECAST = 3

# Minimum elapsed fraction of a campaign before its completion is projected.
# Before this, the run-rate is dominated by launch-day noise.
MIN_ELAPSED_FRACTION = 0.10


@dataclass
class Forecast:
    """
    A projection, its basis, and what it assumes.

    ``value`` is None when the forecast cannot honestly be made, and ``reason``
    says which precondition failed.
    """

    name: str
    value: float | None
    unit: str = "count"
    basis: str = ""
    method: str = "linear-run-rate"
    assumptions: list[str] = field(default_factory=list)
    synthetic: bool = False
    sample_size: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "basis": self.basis,
            "method": self.method,
            "assumptions": list(self.assumptions),
            "synthetic": self.synthetic,
            "sample_size": self.sample_size,
            "reason": self.reason,
        }


STANDING_FORECAST_ASSUMPTIONS: tuple[str, ...] = (
    "Linear projection: the rate observed so far continues unchanged.",
    "No seasonality, campaign fatigue, or external factor is modelled.",
    "Publishing cadence is assumed to continue at its observed rate.",
)


def _undefined(name: str, reason: str, synthetic: bool, sample: int) -> Forecast:
    return Forecast(
        name=name,
        value=None,
        synthetic=synthetic,
        sample_size=sample,
        reason=reason,
        assumptions=list(STANDING_FORECAST_ASSUMPTIONS),
    )


def project_at_run_rate(
    name: str,
    achieved: float,
    elapsed_days: float,
    remaining_days: float,
    sample_size: int,
    synthetic: bool = False,
    unit: str = "count",
) -> Forecast:
    """
    Project a total from the rate achieved so far.

    forecast = achieved + (achieved / elapsed_days) * remaining_days
    """
    if sample_size < MIN_OBSERVATIONS_FOR_FORECAST:
        return _undefined(
            name,
            f"undefined: {sample_size} observation(s); "
            f"{MIN_OBSERVATIONS_FOR_FORECAST} is the minimum to project a rate",
            synthetic,
            sample_size,
        )
    if elapsed_days <= 0:
        return _undefined(
            name,
            "undefined: no elapsed time, so there is no rate to project",
            synthetic,
            sample_size,
        )
    if remaining_days <= 0:
        return Forecast(
            name=name,
            value=achieved,
            unit=unit,
            basis=f"{achieved:g} achieved with no time remaining",
            method="observed-total",
            synthetic=synthetic,
            sample_size=sample_size,
            assumptions=list(STANDING_FORECAST_ASSUMPTIONS),
            reason="period has ended; reporting the observed total, not a projection",
        )

    per_day = achieved / elapsed_days
    projected = achieved + (per_day * remaining_days)
    return Forecast(
        name=name,
        value=round(projected, 2),
        unit=unit,
        basis=(
            f"{achieved:g} in {elapsed_days:g}d = {per_day:.3g}/day, "
            f"extended over {remaining_days:g}d remaining"
        ),
        synthetic=synthetic,
        sample_size=sample_size,
        assumptions=list(STANDING_FORECAST_ASSUMPTIONS),
    )


def forecast_campaign_completion(
    campaign_analytics: dict[str, Any],
    start: datetime | None,
    end: datetime | None,
    now: datetime,
) -> Forecast:
    """
    Project a campaign's final conversion count against its objective.

    Uses the campaign's own window. Without both dates there is nothing to
    project over, and the forecast says so rather than assuming a duration.
    """
    progress = campaign_analytics.get("objective_progress", {}) or {}
    achieved = float(progress.get("achieved") or 0.0)
    metrics = campaign_analytics.get("metrics", {})
    sample = _sample(metrics)
    synthetic = bool(campaign_analytics.get("synthetic"))

    if start is None or end is None:
        return _undefined(
            "campaign_completion",
            "undefined: the campaign has no start and end date to project across",
            synthetic,
            sample,
        )

    total_days = (_utc(end) - _utc(start)).total_seconds() / 86400.0
    elapsed_days = (_utc(now) - _utc(start)).total_seconds() / 86400.0
    if total_days <= 0:
        return _undefined(
            "campaign_completion",
            "undefined: campaign window is zero or negative",
            synthetic,
            sample,
        )
    if elapsed_days < total_days * MIN_ELAPSED_FRACTION:
        return _undefined(
            "campaign_completion",
            f"undefined: only {elapsed_days:.1f}d of {total_days:.1f}d elapsed; "
            "too early for the run-rate to mean anything",
            synthetic,
            sample,
        )

    remaining = max(0.0, total_days - elapsed_days)
    return project_at_run_rate(
        "campaign_completion",
        achieved,
        min(elapsed_days, total_days),
        remaining,
        sample,
        synthetic,
    )


def forecast_goal_completion(
    campaign_analytics: dict[str, Any],
    start: datetime | None,
    end: datetime | None,
    now: datetime,
) -> dict[str, Any]:
    """
    Whether a campaign is on track for the number in its objective.

    Returns the projection plus a verdict. The verdict is "unknown" whenever the
    projection is undefined or the objective states no number, because "not on
    track" and "we cannot tell" are different messages and only one of them
    should make someone change plan.
    """
    progress = campaign_analytics.get("objective_progress", {}) or {}
    target = progress.get("target")
    forecast = forecast_campaign_completion(campaign_analytics, start, end, now)

    if forecast.value is None or not isinstance(target, (int, float)) or target <= 0:
        verdict = "unknown"
        shortfall: float | None = None
    else:
        shortfall = round(float(target) - forecast.value, 2)
        verdict = "on-track" if forecast.value >= float(target) else "behind"

    return {
        "forecast": forecast.to_dict(),
        "target": target,
        "achieved": progress.get("achieved"),
        "projected_shortfall": shortfall,
        "verdict": verdict,
        "reason": forecast.reason,
    }


def forecast_metric(
    metric_name: str,
    metrics: dict[str, Any],
    elapsed_days: float,
    horizon_days: float,
    synthetic: bool = False,
) -> Forecast:
    """Project any counted metric forward at its observed run rate."""
    entry = metrics.get(metric_name) or {}
    value = entry.get("value")
    if not isinstance(value, (int, float)):
        return _undefined(
            f"expected_{metric_name}",
            f"undefined: {metric_name} is not measured ({entry.get('reason') or 'no data'})",
            synthetic or bool(entry.get("synthetic")),
            int(entry.get("sample_size", 0)),
        )
    return project_at_run_rate(
        f"expected_{metric_name}",
        float(value),
        elapsed_days,
        horizon_days,
        int(entry.get("sample_size", 0)),
        synthetic or bool(entry.get("synthetic")),
    )


def _sample(metrics: dict[str, Any]) -> int:
    return max(
        (int((m or {}).get("sample_size", 0)) for m in metrics.values() if isinstance(m, dict)),
        default=0,
    )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
