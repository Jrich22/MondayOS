"""
The deterministic Growth Brain and marketing memory.

One domain of GrowthService, split out per issue #35. Behaviour is unchanged:
these are the same methods, composed back into the public facade.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from growth.brain.memory import MemoryCategory
from growth.brain.models import RecommendationStatus
from growth.services.base import GrowthServiceBase, _as_datetime


class BrainServiceMixin(GrowthServiceBase):
    """The deterministic Growth Brain and marketing memory."""

    def brain_analyze(self, project: str, now: datetime | None = None) -> dict[str, Any]:
        """The full deterministic pass over one project's measurements."""
        return self._brain(project).analyze(now or datetime.now(tz=UTC))

    def brain_recommendations(
        self, project: str, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Findings that cleared the sample threshold."""
        return [
            r.to_dict() for r in self._brain(project).recommendations(now or datetime.now(tz=UTC))
        ]

    def brain_hypotheses(self, project: str, now: datetime | None = None) -> list[dict[str, Any]]:
        """Candidate explanations that did not clear the threshold."""
        return [h.to_dict() for h in self._brain(project).hypotheses(now or datetime.now(tz=UTC))]

    def brain_opportunities(
        self, project: str, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Every detected finding."""
        return [
            o.to_dict() for o in self._brain(project).opportunities(now or datetime.now(tz=UTC))
        ]

    def brain_scores(self, project: str) -> dict[str, Any]:
        """Workspace, campaign and channel health scores."""
        return self._brain(project).scores()

    def brain_forecasts(self, project: str, now: datetime | None = None) -> dict[str, Any]:
        """Rule-based projections."""
        return self._brain(project).forecasts(now or datetime.now(tz=UTC))

    def brain_experiments(self, project: str, now: datetime | None = None) -> list[dict[str, Any]]:
        """Experiments the Brain proposes, all requiring human approval to run."""
        from growth.brain.experiments import suggest_experiments

        moment = now or datetime.now(tz=UTC)
        brain = self._brain(project)
        return [
            e.to_dict() for e in suggest_experiments(project, brain.opportunities(moment), moment)
        ]

    def memory_record(self, project: str, **fields: Any) -> dict[str, Any]:
        """Record a tentative claim in this project's marketing memory."""
        brain = self._brain(project)
        entry = brain.remember(
            category=MemoryCategory(str(fields.get("category", "recurring-pattern"))),
            statement=str(fields.get("statement", "")),
            sample_size=int(fields.get("sample_size", 0)),
            now=_as_datetime(fields.get("now")) or datetime.now(tz=UTC),
            confidence=str(fields.get("confidence", "low")),
            metric_affected=str(fields.get("metric_affected", "")),
            date_range=str(fields.get("date_range", "")),
            synthetic=bool(fields.get("synthetic", False)),
        )
        return entry.to_dict()

    def memory_list(self, project: str, status: str = "") -> list[dict[str, Any]]:
        """Memory entries, optionally filtered by status."""
        from growth.brain.memory import MemoryStatus

        memory = self._brain(project).memory
        entries = memory.query(status=MemoryStatus(status)) if status else memory.all()
        return [e.to_dict() for e in entries]

    def memory_validate(self, project: str, entry_id: str, by: str, reason: str) -> dict[str, Any]:
        """Promote a tentative claim to validated."""
        return (
            self._brain(project)
            .memory.validate(entry_id, datetime.now(tz=UTC), by, reason)
            .to_dict()
        )

    def memory_invalidate(
        self, project: str, entry_id: str, by: str, reason: str
    ) -> dict[str, Any]:
        """Mark a claim contradicted by later evidence."""
        return (
            self._brain(project)
            .memory.invalidate(entry_id, datetime.now(tz=UTC), by, reason)
            .to_dict()
        )

    def review_recommendation(
        self, project: str, recommendation: dict[str, Any], status: str, by: str, reason: str
    ) -> dict[str, Any]:
        """Record a human decision on a recommendation."""
        from growth.brain.models import Recommendation

        record = Recommendation.from_dict(recommendation)
        return (
            self._brain(project)
            .review_recommendation(
                record, RecommendationStatus(status), by, reason, datetime.now(tz=UTC)
            )
            .to_dict()
        )
