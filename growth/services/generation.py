"""
Content generation, the weekly package and the approval inbox.

One domain of GrowthService, split out per issue #35. Behaviour is unchanged:
these are the same methods, composed back into the public facade.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from growth.services.base import _as_datetime
from growth.services.brain import BrainServiceMixin


# Generation sits ON TOP of the Brain: the planner allocates against
# recommendations and experiments the Brain produced. Inheriting the Brain
# facade states that dependency rather than leaving it implicit - the edge is
# real, and the architecture is Analytics -> Brain -> Planner -> Generation.
class GenerationServiceMixin(BrainServiceMixin):
    """Content generation, the weekly package and the approval inbox."""

    def plan_week(self, project: str, **fields: Any) -> dict[str, Any]:
        """Plan one Monday-Sunday week against the Brain's recommendations."""
        from growth.generation import build_plan

        handle = self._store.open(project)
        moment = _as_datetime(fields.get("week_start")) or datetime.now(tz=UTC)
        brain = self._brain(project)
        return build_plan(
            handle=handle,
            week_start=moment,
            cadence=int(fields.get("cadence") or handle.read().onboarding.cadence_per_week),
            recommendations=[r.to_dict() for r in brain.recommendations(moment)],
            campaigns=[self._describe_campaign(handle, c) for c in handle.list_campaigns()],
            platforms=[p.platform for p in handle.read().onboarding.platform_intents]
            or [b.platform for b in handle.read().bindings],
            experiments=self.brain_experiments(project, moment),
            pillars=list(handle.read().marketing.content_pillars),
        ).to_dict()

    def generate_week(self, project: str, **fields: Any) -> dict[str, Any]:
        """
        Draft a full weekly package.

        Every asset becomes a ContentItem in DRAFT; nothing is approved,
        scheduled, or published by this call.
        """
        from growth.generation import (
            ContentPlanner,
            WeeklyPackageBuilder,
            brand_context_for,
        )

        handle = self._store.open(project)
        moment = _as_datetime(fields.get("week_start")) or datetime.now(tz=UTC)
        brand = brand_context_for(handle)
        brand.validate()

        brain = self._brain(project)
        recommendations = [r.to_dict() for r in brain.recommendations(moment)]
        experiments = self.brain_experiments(project, moment)
        workspace = handle.read()

        plan = ContentPlanner(project).plan_week(
            week_start=moment,
            cadence=int(fields.get("cadence") or workspace.onboarding.cadence_per_week),
            recommendations=recommendations,
            campaigns=[self._describe_campaign(handle, c) for c in handle.list_campaigns()],
            platforms=[p.platform for p in workspace.onboarding.platform_intents]
            or [b.platform for b in workspace.bindings],
            experiments=experiments,
            pillars=list(workspace.marketing.content_pillars),
        )
        builder = WeeklyPackageBuilder(handle, self._select_writer(fields))
        package = builder.build(
            brand=brand,
            plan=plan,
            recommendations=recommendations,
            experiments=experiments,
            now=moment,
            multi_platform=bool(fields.get("multi_platform", False)),
        )
        return package.to_dict()

    def list_packages(self, project: str) -> list[dict[str, Any]]:
        """Every weekly package in a workspace."""
        from growth.generation import WeeklyPackageBuilder

        return [
            p.to_dict() for p in WeeklyPackageBuilder(self._store.open(project)).list_packages()
        ]

    def get_package(self, project: str, package_ref: str) -> dict[str, Any]:
        """Read one weekly package."""
        from growth.generation import WeeklyPackageBuilder

        return WeeklyPackageBuilder(self._store.open(project)).load(package_ref).to_dict()

    def approve_week(self, project: str, package_ref: str, **fields: Any) -> dict[str, Any]:
        """
        Approve a week's posts, each against its own fingerprint.

        Authorises exactly the posts listed in the package and nothing else.

        Takes no generation mode, and must not: approving copy a human has just
        read is not a drafting act. The builder here only loads and saves the
        package - nothing is written - so demanding a mode would make approval
        fail for a package that was already generated.
        """
        from growth.generation import ApprovalInbox, WeeklyPackageBuilder

        handle = self._store.open(project)
        builder = WeeklyPackageBuilder(handle)
        package = builder.load(package_ref)
        inbox = ApprovalInbox(handle)
        result = inbox.approve_week(
            package,
            by=str(fields.get("by", "human:cli")),
            reason=str(fields.get("reason", "")),
            only=list(fields.get("only") or []) or None,
        )
        inbox.mark_package_reviewed(package, builder, datetime.now(tz=UTC))
        return result

    def inbox(self, project: str, **filters: Any) -> dict[str, Any]:
        """The approval queue, grouped for review."""
        from growth.generation import ApprovalInbox

        return ApprovalInbox(self._store.open(project)).grouped()

    def inbox_summary(self, project: str) -> dict[str, Any]:
        """Counts by status for the approval queue."""
        from growth.generation import ApprovalInbox

        return ApprovalInbox(self._store.open(project)).summary()

    def inbox_action(
        self, project: str, action: str, content_id: str, **fields: Any
    ) -> dict[str, Any]:
        """One owner action on one queued item."""
        from growth.generation import ApprovalInbox

        inbox = ApprovalInbox(self._store.open(project))
        by = str(fields.get("by", "human:cli"))
        reason = str(fields.get("reason", ""))
        if action == "submit":
            return inbox.submit_for_review(content_id, by=by)
        if action == "approve":
            return inbox.approve(content_id, by=by, reason=reason)
        if action == "request-changes":
            return inbox.request_changes(content_id, by=by, reason=reason)
        if action == "reject":
            return inbox.reject(content_id, by=by, reason=reason)
        if action == "reschedule":
            when = _as_datetime(fields.get("scheduled_at"))
            if when is None:
                raise ValueError("reschedule requires scheduled_at")
            return inbox.reschedule(content_id, when, by=by)
        raise ValueError(
            f"Unknown inbox action {action!r}. Valid: submit, approve, "
            "request-changes, reject, reschedule"
        )

    def _select_writer(self, fields: dict[str, Any]) -> Any:
        """
        Choose the drafting writer. The mode is explicit, always.

        There is no implicit fallback in either direction. Asking for model
        drafting without a provider is an error, and the absence of a provider
        never quietly selects templates: an operator reviewing what they believe
        is model-written copy must never be handed template output unannounced,
        and the reverse is equally misleading.

        mode="model"     requires a configured provider
        mode="template"  deterministic, offline, no provider consulted
        """
        from growth.generation import Copywriter, TemplateContentWriter
        from growth.generation.model_writer import (
            GENERATION_MODES,
            ModelContentWriter,
            ModelGenerationError,
        )

        mode = str(fields.get("mode", "")).strip().lower()
        if mode not in GENERATION_MODES:
            raise ModelGenerationError(
                f"generation mode must be stated explicitly as one of "
                f"{', '.join(sorted(GENERATION_MODES))}; got {mode or '(none)'!r}. "
                "The caller must know which path produced the drafts it is reviewing, "
                "so neither mode is chosen by default."
            )

        if mode == "template":
            return Copywriter(TemplateContentWriter())

        provider = self._writer_provider
        if provider is None:
            raise ModelGenerationError(
                "model drafting was requested but no AI provider is configured for this "
                "MondayOS instance. Configure one, or pass mode='template' to draft "
                "deterministically."
            )
        return Copywriter(ModelContentWriter(provider))
