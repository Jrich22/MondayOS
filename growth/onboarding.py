"""
Growth onboarding - what a project must state before it can be planned for.

Onboarding collects the context every later increment depends on: who the
audience is, how the brand sounds, which platforms are wanted, what the
objectives are, how often to post, which assets are approved, what may never be
said, and when the weekly review happens.

Two readiness flags, and the distance between them is the point:

    growth_ready_for_planning         set by completing onboarding
    growth_ready_for_real_publishing  NOT settable here, at all

Onboarding records desired platforms and human-readable ACCOUNT LABELS. It never
records a credential, a token, or an account id issued by a platform, because no
account connection exists yet. A project can therefore be fully planned, fully
reviewed and fully approved, and still be structurally unable to claim it is
ready to publish for real - which is the honest state of the system today.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from growth.binding import SUPPORTED_PLATFORMS, normalize_platform

# Everything a project must supply before it counts as planning-ready. Ordered as
# an operator would work through it.
REQUIRED_STEPS: tuple[str, ...] = (
    "audience",
    "brand_voice",
    "platforms",
    "account_labels",
    "objectives",
    "cadence",
    "brand_assets",
    "prohibited_content",
    "weekly_review",
)

# Weekday names accepted for the weekly review slot.
WEEKDAYS: tuple[str, ...] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


class AccountLabelError(ValueError):
    """Raised when an account placeholder looks like a real credential."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"{detail} Onboarding records a human-readable account LABEL only. "
            "Account connection arrives in a later increment; no credential, token, "
            "or secret may be entered here."
        )


@dataclass
class PlatformIntent:
    """A platform the project wants to publish to, and what it calls the account."""

    platform: str
    account_label: str = ""
    # Deliberately absent: account_id, secret_name, token, or any credential.
    # A PlatformBinding (growth/binding.py) carries the secret NAME, and that is
    # created during account connection, not during onboarding.

    def __post_init__(self) -> None:
        self.platform = normalize_platform(self.platform)
        _reject_credential_shaped(self.account_label)

    def to_dict(self) -> dict[str, str]:
        return {"platform": self.platform, "account_label": self.account_label}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlatformIntent:
        return cls(
            platform=str(data.get("platform", "")),
            account_label=str(data.get("account_label", "")),
        )


@dataclass
class WeeklyReview:
    """When the owner expects to review the coming week's plan."""

    weekday: str = "sunday"
    hour_utc: int = 17

    def __post_init__(self) -> None:
        day = (self.weekday or "").strip().lower()
        if day not in WEEKDAYS:
            raise ValueError(f"Unknown weekday {self.weekday!r}. Valid: {', '.join(WEEKDAYS)}")
        self.weekday = day
        if not 0 <= self.hour_utc <= 23:
            raise ValueError(f"hour_utc must be 0-23, got {self.hour_utc}.")

    def to_dict(self) -> dict[str, Any]:
        return {"weekday": self.weekday, "hour_utc": self.hour_utc}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WeeklyReview:
        return cls(
            weekday=str(data.get("weekday", "sunday")),
            hour_utc=int(data.get("hour_utc", 17)),
        )


@dataclass
class Onboarding:
    """A project's growth onboarding state."""

    platform_intents: list[PlatformIntent] = field(default_factory=list)
    cadence_per_week: int = 0
    prohibited_content: list[str] = field(default_factory=list)
    weekly_review: WeeklyReview | None = None
    completed_steps: list[str] = field(default_factory=list)
    completed_at: datetime | None = None
    # Set only by completing onboarding.
    growth_ready_for_planning: bool = False
    # Never set in this increment. Account connection is a later increment, and
    # nothing in growth/ currently has the authority to flip it.
    growth_ready_for_real_publishing: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform_intents": [p.to_dict() for p in self.platform_intents],
            "cadence_per_week": self.cadence_per_week,
            "prohibited_content": list(self.prohibited_content),
            "weekly_review": self.weekly_review.to_dict() if self.weekly_review else None,
            "completed_steps": list(self.completed_steps),
            "completed_at": (
                self.completed_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                if self.completed_at
                else ""
            ),
            "growth_ready_for_planning": self.growth_ready_for_planning,
            "growth_ready_for_real_publishing": self.growth_ready_for_real_publishing,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Onboarding:
        review = data.get("weekly_review")
        completed = data.get("completed_at")
        return cls(
            platform_intents=[
                PlatformIntent.from_dict(p) for p in (data.get("platform_intents") or [])
            ],
            cadence_per_week=int(data.get("cadence_per_week", 0)),
            prohibited_content=[str(p) for p in (data.get("prohibited_content") or [])],
            weekly_review=WeeklyReview.from_dict(review) if review else None,
            completed_steps=[str(s) for s in (data.get("completed_steps") or [])],
            completed_at=(
                datetime.fromisoformat(str(completed).rstrip("Z")).replace(tzinfo=UTC)
                if completed
                else None
            ),
            growth_ready_for_planning=bool(data.get("growth_ready_for_planning", False)),
            # Read but never written True by this module. Present so a hand-set
            # value round-trips rather than being silently dropped, which would
            # hide it from an operator inspecting the file.
            growth_ready_for_real_publishing=bool(
                data.get("growth_ready_for_real_publishing", False)
            ),
        )


def evaluate_readiness(workspace: Any) -> tuple[list[str], list[str]]:
    """
    Return (satisfied, missing) onboarding steps for a workspace.

    Reads the workspace sections that already exist rather than duplicating the
    data: audience and brand live in Workspace.audience / Workspace.brand, and
    objectives live in Workspace.marketing.
    """
    onboarding = workspace.onboarding
    audience = workspace.audience
    brand = workspace.brand
    marketing = workspace.marketing

    satisfied: list[str] = []
    missing: list[str] = []

    checks: list[tuple[str, bool]] = [
        ("audience", bool(audience.icps or audience.personas or audience.job_titles)),
        ("brand_voice", bool(brand.voice.strip())),
        ("platforms", bool(onboarding.platform_intents)),
        (
            "account_labels",
            bool(onboarding.platform_intents)
            and all(p.account_label.strip() for p in onboarding.platform_intents),
        ),
        ("objectives", bool(marketing.objectives)),
        ("cadence", onboarding.cadence_per_week > 0),
        ("brand_assets", bool(brand.approved_imagery or brand.logos)),
        ("prohibited_content", bool(onboarding.prohibited_content)),
        ("weekly_review", onboarding.weekly_review is not None),
    ]
    for step, ok in checks:
        (satisfied if ok else missing).append(step)
    return satisfied, missing


def supported_platform_names() -> tuple[str, ...]:
    """Platforms a project may express an intent for. Not connections."""
    return SUPPORTED_PLATFORMS


def _reject_credential_shaped(label: str) -> None:
    """
    Refuse an account label that looks like a secret.

    Crude on purpose. Onboarding is the step where a well-meaning operator is
    most likely to paste a token into a field called "account", and a shallow
    check that catches the obvious cases is worth more than none.
    """
    value = (label or "").strip()
    if not value:
        return
    lowered = value.lower()
    for marker in ("token", "secret", "api_key", "apikey", "password", "bearer"):
        if marker in lowered:
            raise AccountLabelError(f"Account label {label!r} contains {marker!r}.")
    for prefix in ("sk-", "ghp_", "xox", "akia"):
        if lowered.startswith(prefix):
            raise AccountLabelError(f"Account label {label!r} looks like a credential.")
    if len(value) >= 40 and " " not in value:
        raise AccountLabelError(f"Account label of {len(value)} characters looks like a secret.")
