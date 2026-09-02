"""
Workspace, bindings, onboarding and demo seeding.

One domain of GrowthService, split out per issue #35. Behaviour is unchanged:
these are the same methods, composed back into the public facade.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from growth.onboarding import (
    PlatformIntent,
    WeeklyReview,
    evaluate_readiness,
    supported_platform_names,
)
from growth.services.base import GrowthServiceBase


class WorkspaceServiceMixin(GrowthServiceBase):
    """Workspace, bindings, onboarding and demo seeding."""

    def init_workspace(self, project: str) -> dict[str, Any]:
        """Create an empty growth workspace for a registered project."""
        workspace = self._store.init_workspace(project)
        return workspace.to_dict()

    def get_workspace(self, project: str) -> dict[str, Any]:
        """Read one project's workspace."""
        return self._store.open(project).read().to_dict()

    def list_workspaces(self) -> list[str]:
        """Slugs of every initialized workspace."""
        return self._store.list_workspaces()

    def bind(
        self,
        project: str,
        platform: str,
        account_id: str,
        account_handle: str = "",
        secret_name: str = "",
    ) -> dict[str, str]:
        """Bind a publishing account to a workspace, by secret name only."""
        binding = self._store.open(project).bind(
            platform=platform,
            account_id=account_id,
            account_handle=account_handle,
            secret_name=secret_name,
        )
        return binding.redacted()

    def list_bindings(self, project: str) -> list[dict[str, str]]:
        """Every binding in a workspace, redacted."""
        return [b.redacted() for b in self._store.open(project).read().bindings]

    def credential_status(
        self, project: str, environ: dict[str, str] | None = None
    ) -> list[dict[str, Any]]:
        """Per-binding report of whether its credential is present in the environment."""
        rows: list[dict[str, Any]] = []
        for binding in self._store.open(project).read().bindings:
            check = binding.credential_check(environ)
            rows.append(
                {
                    "platform": binding.platform,
                    "secret_name": binding.secret_name,
                    "ready": check.ok,
                    "detail": check.instructions(),
                }
            )
        return rows

    def onboard(self, project: str, **fields: Any) -> dict[str, Any]:
        """
        Record growth onboarding for a project.

        Accepts platform intents as {platform, account_label} pairs. It records no
        credential of any kind: account connection is a later increment.
        """
        handle = self._store.open(project)
        workspace = handle.read()
        onboarding = workspace.onboarding

        if "platforms" in fields:
            onboarding.platform_intents = [
                PlatformIntent(
                    platform=str(p.get("platform", "")),
                    account_label=str(p.get("account_label", "")),
                )
                for p in (fields.get("platforms") or [])
            ]
        if "cadence_per_week" in fields:
            onboarding.cadence_per_week = int(fields["cadence_per_week"])
        if "prohibited_content" in fields:
            onboarding.prohibited_content = [str(x) for x in (fields["prohibited_content"] or [])]
        if "weekly_review_day" in fields or "weekly_review_hour_utc" in fields:
            onboarding.weekly_review = WeeklyReview(
                weekday=str(fields.get("weekly_review_day", "sunday")),
                hour_utc=int(fields.get("weekly_review_hour_utc", 17)),
            )
        if "objectives" in fields:
            workspace.marketing.objectives = [str(o) for o in (fields["objectives"] or [])]
        if "brand_voice" in fields:
            workspace.brand.voice = str(fields["brand_voice"])
        if "brand_assets" in fields:
            workspace.brand.approved_imagery = [str(a) for a in (fields["brand_assets"] or [])]
        if "audience_personas" in fields:
            workspace.audience.personas = [str(a) for a in (fields["audience_personas"] or [])]
        if "audience_icps" in fields:
            workspace.audience.icps = [str(a) for a in (fields["audience_icps"] or [])]

        satisfied, missing = evaluate_readiness(workspace)
        onboarding.completed_steps = satisfied
        onboarding.growth_ready_for_planning = not missing
        if not missing and onboarding.completed_at is None:
            onboarding.completed_at = datetime.now(tz=UTC)
        # Never set here. Account connection is a later increment and nothing in
        # growth/ has the authority to declare real publishing ready.
        onboarding.growth_ready_for_real_publishing = False

        handle.write(workspace)
        return self.onboarding_status(project)

    def onboarding_status(self, project: str) -> dict[str, Any]:
        """Readiness, satisfied steps, and what is still missing."""
        workspace = self._store.open(project).read()
        satisfied, missing = evaluate_readiness(workspace)
        return {
            "project": workspace.slug,
            "growth_ready_for_planning": workspace.onboarding.growth_ready_for_planning,
            "growth_ready_for_real_publishing": (
                workspace.onboarding.growth_ready_for_real_publishing
            ),
            "completed_steps": satisfied,
            "missing_steps": missing,
            "platform_intents": [p.to_dict() for p in workspace.onboarding.platform_intents],
            "supported_platforms": list(supported_platform_names()),
            "weekly_review": (
                workspace.onboarding.weekly_review.to_dict()
                if workspace.onboarding.weekly_review
                else None
            ),
            "note": (
                "Real publishing readiness requires an account connection, which does "
                "not exist yet. Publishing runs against the fake connector only."
            ),
        }

    def seed_demo(self, project: str) -> dict[str, Any]:
        """Populate a workspace with clearly-marked synthetic demo data."""
        from growth.demo import seed_workspace

        return seed_workspace(self._store.open(project))
